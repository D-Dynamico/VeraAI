"""An opt-out must stop proactive sends, not just replies.

The two paths are gated by different state: /v1/reply checks PartnerMemory, while
/v1/tick checks the SendLedger. Nothing connected them, so the bot answered "we
are suppressing future sends" and then sent more on the next tick.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from vera import app as app_module

NOW = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
MERCHANT_ID = "m_test_optout"

CATEGORY = {
    "slug": "dentists",
    "voice": {"vocab_taboo": []},
    "peer_stats": {},
    "digest": [],
}
MERCHANT = {
    "merchant_id": MERCHANT_ID,
    "category_slug": "dentists",
    "identity": {"name": "Test Dental", "owner_first_name": "Asha", "city": "Delhi", "locality": "Saket"},
    "performance": {"views": 1000, "calls": 20, "ctr": 0.02, "delta_7d": {"calls_pct": 0.1}},
    "offers": [],
    "signals": ["stale_posts:20d"],
    "review_themes": [],
}


def _trigger(trigger_id: str, kind: str) -> dict:
    return {
        "id": trigger_id,
        "scope": "merchant",
        "kind": kind,
        "merchant_id": MERCHANT_ID,
        "customer_id": None,
        "payload": {},
        "urgency": 3,
        "suppression_key": f"{kind}:{MERCHANT_ID}",
        "expires_at": "2026-12-31T00:00:00Z",
    }


def _push(client: TestClient, scope: str, context_id: str, payload: dict) -> None:
    client.post(
        "/v1/context",
        json={
            "scope": scope,
            "context_id": context_id,
            "version": 1,
            "payload": payload,
            "delivered_at": NOW.isoformat(),
        },
    )


def test_opt_out_stops_the_next_proactive_send():
    client = TestClient(app_module.app)
    client.post("/v1/teardown")

    _push(client, "category", "dentists", CATEGORY)
    _push(client, "merchant", MERCHANT_ID, MERCHANT)
    first = _trigger("trg_optout_1", "stale_posts")
    _push(client, "trigger", first["id"], first)

    opening = client.post("/v1/tick", json={"now": NOW.isoformat(), "available_triggers": [first["id"]]})
    assert opening.json()["actions"], "no opening message, so the test proves nothing"

    reply = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_optout",
            "merchant_id": MERCHANT_ID,
            "from_role": "merchant",
            "message": "stop messaging me, unsubscribe",
            "received_at": NOW.isoformat(),
            "turn_number": 2,
        },
    )
    assert reply.json()["action"] == "end"

    # A different kind, a different suppression key, and hours past the cooldown:
    # the opt-out is the only thing left that can stop this.
    later = NOW + timedelta(hours=3)
    second = _trigger("trg_optout_2", "competitor_opened")
    _push(client, "trigger", second["id"], second)
    after = client.post("/v1/tick", json={"now": later.isoformat(), "available_triggers": [second["id"]]})

    assert after.json()["actions"] == []
    client.post("/v1/teardown")
