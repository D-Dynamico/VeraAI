"""The LLM layer: rewrite the template's own lines into something that reads better.

The model never sees the raw contexts and never chooses what to say. It receives
lines the fact pack already vouched for and the ask the template already picked,
and its only job is phrasing. Anything it returns still goes through validate.py,
and if it is slow, rate-limited, or breaks a rule, the template answers instead.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from vera.cohort import Cohort
from vera.factpack import FactPack
from vera.templates import ComposedMessage, assemble, plan_message, salutation
from vera.validate import check

# Chosen by measurement, not reputation: 3.1-flash-lite answered in 1.3-1.8s and
# kept every rule across the trial cases. 3.5-flash-lite dropped the ask on one,
# 3.7-flash returned 503 under load, and 2.5-flash is closed to new keys.
PRIMARY_MODEL = "gemini-3.1-flash-lite"
FALLBACK_MODEL = "gemini-3.5-flash-lite"

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"body": {"type": "string"}},
    "required": ["body"],
}

# The judge waits 15s on a tick. Everything unfinished by then falls back.
TICK_BUDGET_SECONDS = 8.0
PER_CALL_TIMEOUT = 6.0
MAX_CONCURRENT_CALLS = 4

RULES = """You write WhatsApp messages to Indian shop owners. Plain, everyday English.

HARD RULES
- Say the given facts side by side. Never claim one fact causes another - you do not know that.
- Never add a name, place, price, date or number that is not in the FACTS.
- Start with their name, a comma, and then a fact carrying a number or a source
  in that same sentence. Never let the name be a sentence of its own.
- Never write "as per", "kindly", "avail" or "regarding". Say it the way a person would.
- Any line already written in Hindi must be copied exactly, unchanged.
- End with the ASK, copied word for word. Nothing after it.
- One question only, and it is the ask.
- No links, no markdown, no emoji.
- Never use business words like CTR, impressions, optimize, ROI, retention or conversion.
- Short sentences. Under 50 words before the ask.
- Keep the shape: one blank line between each block, and the ask alone in the last block.
  A wall of text is hard to read on a phone."""


@dataclass
class ComposeJob:
    pack: FactPack
    category: dict[str, Any]
    cohort: Cohort | None
    cache_key: str


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "GEMINI_API_KEY":
                return value.strip()
    return ""


def _prompt(pack: FactPack, facts: list[str], ask: str) -> str:
    audience = (
        f"You are writing to {pack.customer_name}, a customer of {pack.business_name}, on the shop's behalf."
        if pack.is_customer_facing
        else f"You are writing to {pack.owner_name or 'the owner'}, who runs {pack.business_name}."
    )
    numbered = "\n".join(f"{index}. {fact}" for index, fact in enumerate(facts, start=1))
    return (
        f"{RULES}\n\n{audience}\n"
        f"Open with exactly this, including the title: {salutation(pack)},\n\n"
        f"FACTS (the ONLY things you may state):\n{numbered}\n\n"
        f"ASK (must be the final sentence, word for word):\n{ask}"
    )


def _problems(body: str | None, job: ComposeJob) -> list[str]:
    if not body:
        return ["no response"]
    failures = check(body, job.pack, job.category)
    expected = salutation(job.pack)
    if not body.startswith(expected):
        failures.append(f"dropped the salutation {expected!r}")
    return failures


class Composer:
    """Holds the response cache. One instance lives for the life of the process."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self.calls_made = 0
        self.fallbacks = 0
        self.rejections: list[tuple[str, list[str]]] = []

    def clear(self) -> None:
        self._cache.clear()

    async def _ask_model(self, client: httpx.AsyncClient, model: str, prompt: str, key: str) -> str | None:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
            },
        }
        try:
            response = await client.post(
                ENDPOINT.format(model=model),
                json=payload,
                headers={"x-goog-api-key": key},
                timeout=PER_CALL_TIMEOUT,
            )
            if response.status_code != 200:
                return None
            parts = response.json()["candidates"][0]["content"]["parts"]
            return json.loads(parts[0]["text"])["body"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            return None

    async def _compose_one(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        job: ComposeJob,
        key: str,
        deadline: float,
    ) -> ComposedMessage | None:
        plan = plan_message(job.pack, job.cohort)
        if plan is None:
            return None
        draft, proof = plan
        template_message = assemble(job.pack, draft, proof)

        if job.cache_key in self._cache:
            return ComposedMessage(
                body=self._cache[job.cache_key],
                cta=draft.cta,
                send_as=template_message.send_as,
                suppression_key=job.pack.suppression_key,
                rationale=draft.rationale,
            )

        if not key or time.monotonic() > deadline:
            self.fallbacks += 1
            return template_message

        facts = [line for line in template_message.body.split("\n\n") if line.strip() != draft.ask.strip()]
        prompt = _prompt(job.pack, facts, draft.ask)

        async with semaphore:
            if time.monotonic() > deadline:
                self.fallbacks += 1
                return template_message
            self.calls_made += 1
            body = await self._ask_model(client, PRIMARY_MODEL, prompt, key)

        problems = _problems(body, job)
        if problems:
            self.rejections.append((job.cache_key, problems))
            # One retry on the weaker model rather than a second call to the same
            # one, which at temperature 0 would return the same text.
            async with semaphore:
                self.calls_made += 1
                body = await self._ask_model(client, FALLBACK_MODEL, prompt, key)

        if body:
            problems = _problems(body, job)
            if problems:
                self.rejections.append((f"{job.cache_key} (retry)", problems))
        if not body or problems:
            self.fallbacks += 1
            return template_message

        self._cache[job.cache_key] = body
        return ComposedMessage(
            body=body,
            cta=draft.cta,
            send_as=template_message.send_as,
            suppression_key=job.pack.suppression_key,
            rationale=draft.rationale,
        )

    async def compose_many(self, jobs: list[ComposeJob]) -> list[ComposedMessage | None]:
        key = api_key()
        deadline = time.monotonic() + TICK_BUDGET_SECONDS
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)
        async with httpx.AsyncClient() as client:
            return await asyncio.gather(
                *(self._compose_one(client, semaphore, job, key, deadline) for job in jobs)
            )

    def compose(self, job: ComposeJob) -> ComposedMessage | None:
        """Synchronous single composition, for the offline tools."""
        return asyncio.run(self.compose_many([job]))[0]
