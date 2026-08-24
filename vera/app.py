"""The five endpoints the judge harness drives, plus teardown."""

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vera.cohort import build_cohort
from vera.compose import ComposeJob, Composer
from vera.conversation import ConversationStore, respond
from vera.factpack import build_fact_pack
from vera.policy import SendLedger, select
from vera.store import VALID_SCOPES, ContextStore
from vera.templates import BLOCK_SEPARATOR

app = FastAPI()
store = ContextStore()
conversations = ConversationStore()
composer = Composer()
ledger = SendLedger()
started_at = time.time()

METADATA = {
    "team_name": "D-Dynamico",
    "team_members": ["D-Dynamico"],
    "model": "gemini-3.1-flash-lite",
    "approach": "Deterministic fact extraction with an LLM wordsmithing layer and a template fallback",
    "contact_email": "dayukori@gmail.com",
    "version": "1.0.0",
    "submitted_at": "2026-08-24T00:00:00Z",
}


def _parse_now(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _template_params(body: str) -> list[str]:
    """First outbound must ride a template; the blocks are its parameters."""
    return [block.strip() for block in body.split(BLOCK_SEPARATOR) if block.strip()]


class ContextPush(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


class TickRequest(BaseModel):
    now: str
    available_triggers: list[str] = []


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


@app.get("/v1/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - started_at),
        "contexts_loaded": store.counts_by_scope(),
    }


@app.get("/v1/metadata")
def metadata() -> dict[str, Any]:
    return METADATA


@app.post("/v1/context")
def push_context(request: ContextPush) -> JSONResponse:
    if request.scope not in VALID_SCOPES:
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": "invalid_scope", "details": f"unknown scope {request.scope!r}"},
        )

    result = store.put(request.scope, request.context_id, request.version, request.payload)
    if not result.accepted:
        return JSONResponse(
            status_code=409,
            content={"accepted": False, "reason": "stale_version", "current_version": result.current_version},
        )

    return JSONResponse(
        content={
            "accepted": True,
            "ack_id": f"ack_{request.context_id}_v{request.version}",
            "stored_at": request.delivered_at,
        }
    )


@app.post("/v1/tick")
async def tick(request: TickRequest) -> dict[str, Any]:
    now = _parse_now(request.now)
    candidates = select(store, request.available_triggers, now, ledger)
    if not candidates:
        return {"actions": []}

    jobs = []
    for candidate in candidates:
        pack = build_fact_pack(
            candidate.category,
            candidate.merchant,
            candidate.trigger,
            candidate.customer,
            previous_merchant=candidate.previous_merchant,
            today=now.date(),
        )
        jobs.append(
            ComposeJob(
                pack=pack,
                category=candidate.category,
                cohort=build_cohort(candidate.merchant, store.all_of("merchant")),
                cache_key=f"{candidate.trigger['id']}:{store.version_of('merchant', pack.merchant_id)}",
            )
        )

    messages = await composer.compose_many(jobs)

    actions = []
    for candidate, message in zip(candidates, messages):
        if message is None:
            continue
        merchant_id = candidate.merchant["merchant_id"]
        kind = candidate.trigger.get("kind", "")
        customer_id = candidate.trigger.get("customer_id")
        ledger.record(
            merchant_id,
            customer_id or merchant_id,
            kind,
            candidate.trigger.get("suppression_key", ""),
            now,
            store.version_of("merchant", merchant_id),
        )
        actions.append(
            {
                "conversation_id": f"conv_{merchant_id}_{kind}_{now:%Y%m%d}",
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": message.send_as,
                "trigger_id": candidate.trigger["id"],
                "template_name": f"vera_{kind}_v1",
                "template_params": _template_params(message.body),
                "body": message.body,
                "cta": message.cta,
                "suppression_key": message.suppression_key,
                "rationale": message.rationale,
            }
        )
    return {"actions": actions}


@app.post("/v1/reply")
def reply(request: ReplyRequest) -> dict[str, Any]:
    state = conversations.conversation(request.conversation_id, request.merchant_id, request.customer_id)
    memory = conversations.partner(request.merchant_id, request.customer_id)
    if state.ended or memory.opted_out:
        return {"action": "end", "rationale": "Conversation already closed; no further sends on this thread."}
    return respond(state, memory, request.message)


@app.post("/v1/teardown")
def teardown() -> dict[str, Any]:
    store.clear()
    conversations.clear()
    composer.clear()
    ledger.clear()
    return {"status": "ok"}
