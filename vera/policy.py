"""Which triggers are worth a message right now, and which are not.

Restraint is scored: the brief rewards an empty tick over a noisy one. Every
filter here exists to drop a send that would have been technically valid and
practically unwelcome.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from vera.store import ContextStore

MAX_ACTIONS_PER_TICK = 8
# Two messages to the same shop inside this window reads as pestering.
MERCHANT_COOLDOWN_SECONDS = 1800
# Below this urgency we do not interrupt a merchant who is already mid-conversation.
QUIET_URGENCY_CEILING = 1


@dataclass
class Candidate:
    trigger: dict[str, Any]
    merchant: dict[str, Any]
    category: dict[str, Any]
    customer: dict[str, Any] | None
    previous_merchant: dict[str, Any] | None


@dataclass
class SendLedger:
    """What has already gone out, so the same thing does not go out twice."""

    suppression_keys: set[str] = field(default_factory=set)
    merchant_kinds: set[tuple[str, str]] = field(default_factory=set)
    last_send_at: dict[str, datetime] = field(default_factory=dict)
    suppressed_merchants: set[str] = field(default_factory=set)

    def record(self, merchant_id: str, kind: str, suppression_key: str, now: datetime) -> None:
        self.suppression_keys.add(suppression_key)
        self.merchant_kinds.add((merchant_id, kind))
        self.last_send_at[merchant_id] = now

    def suppress_merchant(self, merchant_id: str) -> None:
        self.suppressed_merchants.add(merchant_id)

    def clear(self) -> None:
        self.suppression_keys.clear()
        self.merchant_kinds.clear()
        self.last_send_at.clear()
        self.suppressed_merchants.clear()


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _anchor_strength(trigger: dict[str, Any], merchant: dict[str, Any], now: datetime) -> int:
    """How much checkable material this message could carry. Ties broken here."""
    payload = trigger.get("payload", {})
    real_payload = [key for key in payload if key not in ("placeholder", "metric_or_topic")]
    score = len(real_payload)
    if payload.get("top_item_id"):
        score += 2
    expires_at = _parse_time(trigger.get("expires_at", ""))
    if expires_at and expires_at >= now:
        score += 3
    if merchant.get("signals"):
        score += 1
    if merchant.get("review_themes"):
        score += 1
    return score


def select(
    store: ContextStore,
    available_trigger_ids: list[str],
    now: datetime,
    ledger: SendLedger,
) -> list[Candidate]:
    """The triggers worth acting on this tick, best first."""
    candidates: list[Candidate] = []

    for trigger_id in available_trigger_ids:
        trigger = store.get("trigger", trigger_id)
        if not trigger:
            continue

        merchant_id = trigger.get("merchant_id")
        kind = trigger.get("kind", "")
        stored_merchant = store.get_stored("merchant", merchant_id) if merchant_id else None
        if not stored_merchant:
            continue
        merchant = stored_merchant.payload

        # `available_triggers` is documented as what the judge "considers active
        # right now", which outranks a stale expires_at in the payload. The
        # shipped dataset was authored for an April 2026 window, so 96 of its 100
        # triggers read as expired against any later clock; honouring the
        # timestamp would mute the bot entirely. Freshness is used for ranking.
        if merchant_id in ledger.suppressed_merchants:
            continue
        if trigger.get("suppression_key") in ledger.suppression_keys:
            continue
        # The dataset emits several triggers of one kind per merchant with different
        # suppression keys, so the key alone does not stop a duplicate message.
        if (merchant_id, kind) in ledger.merchant_kinds:
            continue

        last_send = ledger.last_send_at.get(merchant_id)
        if last_send and (now - last_send).total_seconds() < MERCHANT_COOLDOWN_SECONDS:
            continue

        urgency = trigger.get("urgency", 1)
        engaged_recently = any(signal.startswith("engaged_in_last") for signal in merchant.get("signals", []))
        if engaged_recently and urgency <= QUIET_URGENCY_CEILING:
            continue

        category = store.get("category", merchant.get("category_slug", ""))
        if not category:
            continue

        customer_id = trigger.get("customer_id")
        candidates.append(
            Candidate(
                trigger=trigger,
                merchant=merchant,
                category=category,
                customer=store.get("customer", customer_id) if customer_id else None,
                previous_merchant=stored_merchant.previous_payload,
            )
        )

    candidates.sort(
        key=lambda item: (item.trigger.get("urgency", 1), _anchor_strength(item.trigger, item.merchant, now)),
        reverse=True,
    )

    chosen: list[Candidate] = []
    merchants_this_tick: set[str] = set()
    for candidate in candidates:
        merchant_id = candidate.merchant.get("merchant_id")
        if merchant_id in merchants_this_tick:
            continue
        merchants_this_tick.add(merchant_id)
        chosen.append(candidate)
        if len(chosen) >= MAX_ACTIONS_PER_TICK:
            break
    return chosen
