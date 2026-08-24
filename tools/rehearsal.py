"""Drive a running bot through the judge's real Phase 1-3 lifecycle.

`judge_simulator.py` warms up with 10 contexts and needs an LLM key to run at all.
The real harness pushes 255 and fails the bot on a count mismatch, then injects
context mid-window. This rehearses both, with no LLM and no API cost.

    python tools/rehearsal.py --url http://127.0.0.1:8125

Asserts, in order:
  phase 1  all 255 base contexts land and healthz reports them
  phase 2  twelve 5-minute ticks; every action carries the 7 required keys
  phase 3  a version bump is accepted and its new numbers reach the next message;
           a customer pushed 2 minutes before its recall_due trigger gets used;
           a customer-scoped trigger with no customer context stays silent
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "expanded"
REQUIRED_ACTION_KEYS = (
    "conversation_id",
    "merchant_id",
    "send_as",
    "trigger_id",
    "cta",
    "suppression_key",
    "rationale",
)

failures: list[str] = []


def check(condition: bool, description: str, detail: str = "") -> bool:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {description}{'' if condition else f' — {detail}'}")
    if not condition:
        failures.append(description)
    return condition


class Bot:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def _call(self, path: str, body: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def healthz(self) -> dict:
        return self._call("/v1/healthz")[1]

    def push(self, scope: str, context_id: str, version: int, payload: dict, at: datetime) -> tuple[int, dict]:
        return self._call(
            "/v1/context",
            {
                "scope": scope,
                "context_id": context_id,
                "version": version,
                "payload": payload,
                "delivered_at": at.isoformat().replace("+00:00", "Z"),
            },
        )

    def tick(self, now: datetime, triggers: list[str]) -> dict:
        return self._call(
            "/v1/tick",
            {"now": now.isoformat().replace("+00:00", "Z"), "available_triggers": triggers},
        )[1]

    def teardown(self) -> None:
        self._call("/v1/teardown", {})


def load(folder: str, key: str) -> dict:
    return {
        item[key]: item
        for item in (json.loads(path.read_text(encoding="utf-8")) for path in (DATASET / folder).glob("*.json"))
    }


def phase_one(bot: Bot, categories: dict, merchants: dict, customers: dict, t0: datetime) -> None:
    """255 base contexts, zero triggers, then the count check that gates the run."""
    print("\n--- phase 1: warmup ---")
    rejected = 0
    for scope, items in (("category", categories), ("merchant", merchants), ("customer", customers)):
        for context_id, payload in items.items():
            status, _ = bot.push(scope, context_id, 1, payload, t0 - timedelta(minutes=15))
            rejected += status != 200
    check(rejected == 0, "all 255 base contexts accepted", f"{rejected} rejected")

    counts = bot.healthz()["contexts_loaded"]
    check(
        counts == {"category": 5, "merchant": 50, "customer": 200, "trigger": 0},
        "healthz reports the full base dataset",
        json.dumps(counts),
    )


def phase_two(bot: Bot, triggers: dict, t0: datetime) -> list[dict]:
    """Twelve 5-minute ticks. Triggers go live in waves, as the judge releases them."""
    print("\n--- phase 2: twelve ticks ---")
    merchant_triggers = [t for t in triggers.values() if t.get("scope") != "customer"]
    ordered = sorted(merchant_triggers, key=lambda item: item["id"])
    sent: list[dict] = []

    for index in range(12):
        now = t0 + timedelta(minutes=5 * index)
        live = [t["id"] for t in ordered[: (index + 1) * 5]]
        for trigger in ordered[index * 5 : (index + 1) * 5]:
            bot.push("trigger", trigger["id"], 1, trigger, now)
        actions = bot.tick(now, live)["actions"]
        sent.extend(actions)
        print(f"  tick {index + 1:2}  {len(live):3} live  ->  {len(actions)} action(s)")

    missing = [key for action in sent for key in REQUIRED_ACTION_KEYS if key not in action]
    check(not missing, "every action carries the 7 required keys", f"missing {set(missing)}")
    check(bool(sent), "the bot sent something across the window", "no actions at all")

    bodies = [action["body"] for action in sent]
    check(len(bodies) == len(set(bodies)), "no verbatim repeat across the window", f"{len(bodies) - len(set(bodies))} repeats")
    return sent


def phase_three(bot: Bot, categories: dict, merchants: dict, customers: dict, triggers: dict, t0: datetime) -> None:
    """The three injections the judge interleaves, each checked on its own terms."""
    print("\n--- phase 3: context injection ---")
    now = t0 + timedelta(minutes=62)

    # 1. A performance snapshot lands as version 2. The next message should name the change.
    merchant_id, merchant = next(iter(sorted(merchants.items())))
    updated = json.loads(json.dumps(merchant))
    old_calls = updated["performance"]["calls"]
    new_calls = old_calls + 19
    updated["performance"]["calls"] = new_calls
    status, _ = bot.push("merchant", merchant_id, 2, updated, now)
    check(status == 200, "a version-2 merchant snapshot is accepted", f"status {status}")

    stale_status, stale_body = bot.push("merchant", merchant_id, 1, merchant, now)
    check(
        stale_status == 409 and stale_body.get("current_version") == 2,
        "a re-push at version 1 is refused as stale",
        f"status {stale_status} {stale_body}",
    )

    fresh = next(
        t
        for t in sorted(triggers.values(), key=lambda item: item["id"])
        if t.get("merchant_id") == merchant_id and t.get("scope") != "customer"
    )
    bot.push("trigger", fresh["id"], 2, fresh, now)
    actions = bot.tick(now, [fresh["id"]])["actions"]
    body = actions[0]["body"] if actions else ""
    check(
        str(new_calls) in body and str(old_calls) in body,
        "the message names the change, both old and new number",
        f"looking for {old_calls} -> {new_calls} in: {body[:120]!r}",
    )

    # 2. A customer arrives, then its recall_due trigger two minutes later.
    later = now + timedelta(minutes=2)
    recall = next(t for t in sorted(triggers.values(), key=lambda i: i["id"]) if t.get("kind") == "recall_due")
    customer = customers[recall["customer_id"]]
    bot.push("customer", customer["customer_id"], 1, customer, now)
    bot.push("trigger", recall["id"], 1, recall, later)
    actions = bot.tick(later, [recall["id"]])["actions"]
    check(bool(actions), "the recall_due trigger fires once its customer is known", "no action")
    if actions:
        action = actions[0]
        check(
            action["send_as"] == "merchant_on_behalf",
            "it is addressed to the customer, not the merchant",
            action["send_as"],
        )
        first_name = customer["identity"]["name"].split()[0]
        check(first_name in action["body"], "it greets the customer by name", action["body"][:90])

    # 3. A customer-scoped trigger whose customer was never pushed must stay silent.
    bot.teardown()
    orphan = next(
        t
        for t in sorted(triggers.values(), key=lambda i: i["id"])
        if t.get("scope") == "customer" and t.get("customer_id")
    )
    merchant = merchants[orphan["merchant_id"]]
    bot.push("category", merchant["category_slug"], 1, categories[merchant["category_slug"]], now)
    bot.push("merchant", merchant["merchant_id"], 1, merchant, now)
    bot.push("trigger", orphan["id"], 1, orphan, now)
    actions = bot.tick(now, [orphan["id"]])["actions"]
    check(
        not actions,
        "a customer trigger with no customer context sends nothing",
        f"sent anyway: {actions[0]['body'][:90] if actions else ''}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8125")
    args = parser.parse_args()

    bot = Bot(args.url)
    bot.teardown()

    categories = load("categories", "slug")
    merchants = load("merchants", "merchant_id")
    customers = load("customers", "customer_id")
    triggers = load("triggers", "id")
    t0 = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)

    phase_one(bot, categories, merchants, customers, t0)
    sent = phase_two(bot, triggers, t0)
    phase_three(bot, categories, merchants, customers, triggers, t0)

    print(f"\n{len(sent)} actions across the test window")
    if failures:
        print(f"\n{len(failures)} FAILED:")
        for description in failures:
            print(f"  - {description}")
        return 1
    print("all checks pass")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
