"""Peer facts computed across every merchant currently in the store.

A composer that sees one merchant at a time cannot say "6 of the 9 places like
yours posted this month". The judge pushes all 50 merchants and they stay
resident, so these facts are real and traceable rather than invented.
"""

import re
from dataclasses import dataclass
from statistics import median
from typing import Any

from vera.factpack import Fact

# Below this the sample is too thin to describe as what other shops are doing.
MINIMUM_PEERS = 3


@dataclass
class Cohort:
    """Peers for one merchant. `is_local` decides whether we may say "near you"."""

    peer_count: int
    median_calls: int
    median_views: int
    posted_recently: int
    is_local: bool
    city: str


def _days_since_post(merchant: dict[str, Any]) -> int | None:
    for signal in merchant.get("signals", []):
        name, _, detail = signal.partition(":")
        if name in ("stale_posts", "no_recent_post"):
            digits = re.findall(r"\d+", detail)
            return int(digits[0]) if digits else None
    return None


def _summarise(peers: list[dict[str, Any]], is_local: bool, city: str) -> Cohort:
    return Cohort(
        peer_count=len(peers),
        median_calls=round(median([peer.get("performance", {}).get("calls", 0) for peer in peers])),
        median_views=round(median([peer.get("performance", {}).get("views", 0) for peer in peers])),
        # Only peers whose posting gap is actually known. An absent signal is
        # not evidence that they posted.
        posted_recently=sum(1 for peer in peers if (days := _days_since_post(peer)) is not None and days <= 14),
        is_local=is_local,
        city=city,
    )


def build_cohort(merchant: dict[str, Any], all_merchants: list[dict[str, Any]]) -> Cohort | None:
    """Same city first; if too few peers there, fall back to the whole category."""
    city = merchant.get("identity", {}).get("city", "")
    category_slug = merchant.get("category_slug")
    same_category = [
        other
        for other in all_merchants
        if other.get("category_slug") == category_slug and other.get("merchant_id") != merchant.get("merchant_id")
    ]

    same_city = [other for other in same_category if other.get("identity", {}).get("city") == city]
    if len(same_city) >= MINIMUM_PEERS:
        return _summarise(same_city, is_local=True, city=city)
    if len(same_category) >= MINIMUM_PEERS:
        return _summarise(same_category, is_local=False, city=city)
    return None


def social_proof_fact(cohort: Cohort | None, merchant: dict[str, Any], shop_word: str) -> Fact | None:
    """One line of real social proof, or nothing if the numbers do not support one."""
    if not cohort:
        return None

    where = f"{shop_word} in {cohort.city}" if cohort.is_local else f"{shop_word} like yours"
    calls = merchant.get("performance", {}).get("calls")

    if isinstance(calls, int) and cohort.median_calls and calls < cohort.median_calls:
        return Fact(
            text=f"other {where} are getting about {cohort.median_calls} calls a month — you are on {calls}",
            source="cohort.median_calls",
            numbers=(str(cohort.median_calls), str(calls)),
        )

    if cohort.posted_recently >= MINIMUM_PEERS:
        return Fact(
            text=f"{cohort.posted_recently} of the {cohort.peer_count} {where} posted on Google this fortnight",
            source="cohort.posted_recently",
            numbers=(str(cohort.posted_recently), str(cohort.peer_count)),
        )

    if isinstance(calls, int) and cohort.median_calls and calls > cohort.median_calls:
        return Fact(
            text=f"you are getting {calls} calls a month against about {cohort.median_calls} for {where}",
            source="cohort.median_calls",
            numbers=(str(calls), str(cohort.median_calls)),
        )

    return None
