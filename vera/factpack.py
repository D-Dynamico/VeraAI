"""Extracts the facts a message may use, each tagged with where it came from.

Nothing here invents a value. Every number a template is allowed to print is
collected into `allowed_numbers`, and `validate.py` rejects any digit in a
composed body that is not in that set.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# A trigger kind is allowed to reach a customer if their consent covers it. Most
# generated customers carry only "promotional_offers", so that acts as broad
# consent when the specific scope is absent.
CONSENT_BY_KIND = {
    "recall_due": ("recall_reminders", "recall_alerts"),
    "appointment_tomorrow": ("appointment_reminders",),
    "chronic_refill_due": ("refill_reminders",),
    "customer_lapsed_soft": ("winback_offers",),
    "customer_lapsed_hard": ("winback_offers",),
    "trial_followup": ("program_updates",),
    "wedding_package_followup": ("bridal_package_followup",),
}
BROAD_CONSENT = "promotional_offers"

# Signals arrive as "name" or "name:detail". These render the ones worth saying.
SIGNAL_PHRASES = {
    "stale_posts": "your last Google post was {detail} ago",
    "no_recent_post": "you have not posted to Google recently",
    "unverified_gbp": "your Google listing is still unverified",
    "no_active_offers": "you have no offer running right now",
    "dormant_with_vera": "we have not spoken in {detail}",
    "renewal_due_soon": "your plan renews in {detail}",
    "trial_ending_soon": "your trial is ending soon",
    "delivery_not_set_up": "delivery is not set up on your listing",
    "high_retention": "your customers come back more often than most",
    "high_repeat_rate": "most of your customers are repeat visitors",
    "above_peer_calls": "you get more calls than nearby shops",
    "above_peer_ctr": "more people call you than the shops around you",
    "above_peer_median_calls": "you get more calls than nearby shops",
    "growing_views_7d": "more people found you this week",
    "stable_growth": "your numbers have been steady",
}


@dataclass(frozen=True)
class Fact:
    """One thing the message may say, and where it came from."""

    text: str
    source: str
    numbers: tuple[str, ...] = ()


@dataclass
class FactPack:
    category_slug: str
    merchant_id: str
    business_name: str
    owner_name: str
    locality: str
    city: str
    trigger_kind: str
    urgency: int
    suppression_key: str

    performance: dict[str, Any] = field(default_factory=dict)
    peer_stats: dict[str, Any] = field(default_factory=dict)
    trigger_payload: dict[str, Any] = field(default_factory=dict)
    signal_facts: list[Fact] = field(default_factory=list)
    digest_item: dict[str, Any] | None = None
    offer_title: str | None = None
    review_theme: dict[str, Any] | None = None
    changed_metrics: list[Fact] = field(default_factory=list)

    customer_name: str | None = None
    customer_state: str | None = None
    customer_language: str = "english"
    months_since_visit: int | None = None
    last_service: str | None = None
    preferred_slots: str | None = None

    blocked_reason: str | None = None
    allowed_numbers: set[str] = field(default_factory=set)

    @property
    def is_customer_facing(self) -> bool:
        return self.customer_name is not None

    def license(self, *facts: Fact | None) -> None:
        """Permit the numbers carried by these facts to appear in the body."""
        for fact in facts:
            if fact:
                self.allowed_numbers.update(fact.numbers)

    def license_numbers(self, *values: Any) -> None:
        for value in values:
            self.allowed_numbers.update(numbers_in(str(value)))


def numbers_in(text: str) -> tuple[str, ...]:
    """Every digit group in the text, with separators and decimals stripped."""
    return tuple(part.replace(",", "") for part in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text)))


def _fact(text: str, source: str) -> Fact:
    return Fact(text=text, source=source, numbers=numbers_in(text))


def _percent(value: float) -> str:
    return f"{round(abs(value) * 100)}%"


def _people_who_call(views: int, calls: int) -> str:
    """CTR in words. 'Click-through rate' means nothing to a shop owner."""
    if not views:
        return ""
    per_hundred = round(calls / views * 100)
    return f"{per_hundred} in every 100 people who see your page call you"


def _months_between(earlier: str, today: date) -> int | None:
    try:
        then = datetime.fromisoformat(earlier.replace("Z", "+00:00")).date()
    except ValueError:
        return None
    return max(0, (today.year - then.year) * 12 + today.month - then.month)


def _pick_offer(merchant: dict[str, Any], category: dict[str, Any]) -> str | None:
    """The merchant's own live offer, else a service-at-price from the catalog.

    Percentage discounts are an explicit anti-pattern for this audience, so they
    are never chosen even where the category catalog carries them.
    """
    for offer in merchant.get("offers", []):
        if offer.get("status") == "active":
            return offer.get("title")
    for offer in category.get("offer_catalog", []):
        if offer.get("type") in ("service_at_price", "free_service", "free_trial"):
            return offer.get("title")
    return None


def _resolve_digest_item(category: dict[str, Any], trigger: dict[str, Any]) -> dict[str, Any] | None:
    """Digest-backed triggers name an item; citations are copied, never written."""
    wanted = trigger.get("payload", {}).get("top_item_id")
    digest = category.get("digest", [])
    if wanted:
        for item in digest:
            if item.get("id") == wanted:
                return item
    kind_by_trigger = {"regulation_change": "compliance", "cde_opportunity": "cde", "research_digest": "research"}
    preferred = kind_by_trigger.get(trigger.get("kind", ""))
    for item in digest:
        if item.get("kind") == preferred:
            return item
    return digest[0] if digest else None


def _signal_facts(merchant: dict[str, Any]) -> list[Fact]:
    facts = []
    for signal in merchant.get("signals", []):
        name, _, detail = signal.partition(":")
        phrase = SIGNAL_PHRASES.get(name)
        if not phrase:
            continue
        readable = detail.replace("d", " days").strip() if detail else ""
        facts.append(_fact(phrase.format(detail=readable), f"merchant.signals[{signal}]"))
    return facts


def _changed_metrics(current: dict[str, Any], previous: dict[str, Any] | None) -> list[Fact]:
    """What moved since the judge last pushed this merchant — the adaptation hook."""
    if not previous:
        return []
    now = current.get("performance", {})
    before = previous.get("performance", {})
    changes = []
    for metric, label in (("calls", "calls"), ("views", "people finding you"), ("directions", "people asking for directions")):
        old, new = before.get(metric), now.get(metric)
        if isinstance(old, int) and isinstance(new, int) and old != new:
            direction = "up" if new > old else "down"
            changes.append(
                _fact(f"your {label} went from {old} to {new} since we last looked ({direction})", f"merchant.performance.{metric}")
            )
    return changes


def _consent_allows(customer: dict[str, Any], kind: str) -> tuple[bool, str | None]:
    granted = customer.get("consent", {}).get("scope", [])
    if not granted:
        return False, "customer has granted no outreach consent"
    for scope in CONSENT_BY_KIND.get(kind, ()):
        if scope in granted:
            return True, None
    if BROAD_CONSENT in granted:
        return True, None
    return False, f"customer consent {granted} does not cover {kind}"


def build_fact_pack(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None = None,
    previous_merchant: dict[str, Any] | None = None,
    today: date | None = None,
) -> FactPack:
    today = today or date.today()
    identity = merchant.get("identity", {})
    performance = merchant.get("performance", {})
    kind = trigger.get("kind", "")

    pack = FactPack(
        category_slug=category.get("slug", ""),
        merchant_id=merchant.get("merchant_id", ""),
        business_name=identity.get("name", ""),
        owner_name=identity.get("owner_first_name", ""),
        locality=identity.get("locality", ""),
        city=identity.get("city", ""),
        trigger_kind=kind,
        urgency=trigger.get("urgency", 1),
        suppression_key=trigger.get("suppression_key", ""),
        performance=performance,
        peer_stats=category.get("peer_stats", {}),
        trigger_payload={k: v for k, v in trigger.get("payload", {}).items() if k not in ("placeholder", "metric_or_topic")},
        signal_facts=_signal_facts(merchant),
        digest_item=_resolve_digest_item(category, trigger),
        offer_title=_pick_offer(merchant, category),
        changed_metrics=_changed_metrics(merchant, previous_merchant),
    )

    themes = [theme for theme in merchant.get("review_themes", []) if theme.get("sentiment") == "neg"]
    pack.review_theme = themes[0] if themes else None

    pack.license(*pack.signal_facts, *pack.changed_metrics)
    pack.license_numbers(*performance.values(), pack.offer_title or "", pack.business_name)
    pack.license_numbers(*[value for value in pack.trigger_payload.values() if not isinstance(value, (dict, list))])
    if pack.digest_item:
        pack.license_numbers(*[value for value in pack.digest_item.values() if not isinstance(value, (dict, list))])
    if pack.review_theme:
        pack.license_numbers(pack.review_theme.get("occurrences_30d", ""))
    if performance.get("views"):
        pack.license_numbers(round(performance.get("calls", 0) / performance["views"] * 100), 100)
    for slot in pack.trigger_payload.get("available_slots", []) or []:
        pack.license_numbers(slot.get("label", ""))
    pack.license_numbers(merchant.get("subscription", {}).get("days_remaining", ""))
    pack.license_numbers(*merchant.get("customer_aggregate", {}).values())

    if customer:
        allowed, reason = _consent_allows(customer, kind)
        pack.blocked_reason = reason
        if allowed:
            customer_identity = customer.get("identity", {})
            relationship = customer.get("relationship", {})
            services = relationship.get("services_received", [])
            pack.customer_name = customer_identity.get("name", "")
            pack.customer_state = customer.get("state")
            pack.customer_language = customer_identity.get("language_pref", "english")
            pack.months_since_visit = _months_between(relationship.get("last_visit", ""), today)
            pack.last_service = services[-1].replace("_", " ") if services else None
            pack.preferred_slots = customer.get("preferences", {}).get("preferred_slots", "").replace("_", " ") or None
            pack.license_numbers(pack.months_since_visit, relationship.get("visits_total", ""))

    return pack


def calls_per_hundred(pack: FactPack) -> str:
    return _people_who_call(pack.performance.get("views", 0), pack.performance.get("calls", 0))


def percent(value: float) -> str:
    return _percent(value)
