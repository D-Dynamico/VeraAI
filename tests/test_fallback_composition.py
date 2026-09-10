"""Every builder must survive its no-data fallback path.

The judge scores on a single context push, so there is no previous version to
diff (changed_metrics is empty) and often no cohort — which means the composer
runs with proof=None and each builder falls through to its own fallback branch.
Two shipped bugs lived in exactly that path and the suite missed both:

  - `_perf_dip` called an unimported helper and crashed /v1/tick with a 500,
    but only when no proof fact was present.
  - `_curious_ask_due` opened on filler whose only proper noun was the shop's
    own name; it passed every validator and still scored 3/10 on specificity.

Coverage here is two-tier, because those are two different failure shapes:

  1. A crash is intolerable for ANY input. So every one of the 26 builders is
     driven with an empty payload — the harshest fallback — and must not raise.
     (Empty payloads produce weak, sometimes anchorless copy the validators
     rightly reject; that is a payload-shape the real dataset never sends, so it
     is not asserted valid here.)
  2. On realistic payloads the template output must be clean. So every seed
     trigger is composed the way the judge composes — real payloads, the real
     cohort built from all seed merchants — and must pass the validators. This
     is the single-push scoring path, minus the LLM wordsmithing layer.

The curious-ask case gets a third assertion the validators cannot make: its
opener must carry a real number, not filler.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from vera.cohort import build_cohort
from vera.factpack import build_fact_pack
from vera.templates import BUILDERS, CUSTOMER_KINDS, compose_from_template
from vera.validate import check

TODAY = date(2026, 8, 23)
DATASET = Path(__file__).resolve().parent.parent / "dataset"
MERCHANT_ID = "m_fallback_test"
CUSTOMER_ID = "c_fallback_test"

CATEGORY = {
    "slug": "dentists",
    "voice": {"tone": "clinical", "vocab_taboo": [], "vocab_allowed": ["scaling", "fluoride"]},
    "peer_stats": {"avg_calls_30d": 40, "avg_views_30d": 3000, "avg_ctr": 0.03},
    "offer_catalog": [{"id": "oc_1", "title": "Scaling @ ₹499", "value": 499, "type": "service_at_price"}],
    # Digest-backed kinds (regulation_change, cde_opportunity, research_digest)
    # resolve an item here; the citation is copied from its `source`.
    "digest": [
        {
            "id": "dig_1",
            "kind": "research",
            "title": "New fluoride varnish guidance",
            "source": "Indian Dental Association, Apr 2026",
            "summary": "Varnish now advised twice a year for high-risk adults.",
            "actionable": "Offer it at the next recall.",
            "trial_n": 120,
            "credits": 2,
        }
    ],
}

MERCHANT = {
    "merchant_id": MERCHANT_ID,
    "category_slug": "dentists",
    "identity": {
        "name": "Test Dental Clinic",
        "owner_first_name": "Asha",
        "city": "Delhi",
        "locality": "Saket",
        "languages": ["en", "hi"],
        "established_year": 2018,
    },
    "subscription": {"status": "active", "plan": "Pro", "days_remaining": 30},
    "performance": {
        "window_days": 30,
        "views": 2000, "calls": 44, "directions": 90, "ctr": 0.022, "leads": 20,
        "delta_7d": {"views_pct": 0.1, "calls_pct": 0.12, "ctr_pct": 0.01},
    },
    "offers": [{"id": "o_1", "title": "Consultation @ ₹199", "status": "active"}],
    "signals": ["above_peer_median_calls", "growing_views_7d", "stale_posts:20d"],
    "review_themes": [
        {"theme": "wait_time", "sentiment": "neg", "occurrences_30d": 3, "common_quote": "long wait on Saturdays"}
    ],
    "customer_aggregate": {"total_unique_ytd": 300, "lapsed_180d_plus": 40},
}

CUSTOMER = {
    "customer_id": CUSTOMER_ID,
    "merchant_id": MERCHANT_ID,
    "identity": {"name": "Rohan", "language_pref": "en", "age_band": "25-34"},
    "relationship": {
        "first_visit": "2024-01-10",
        "last_visit": "2026-05-10",
        "visits_total": 5,
        "services_received": ["scaling", "checkup"],
        "lifetime_value": 8000,
    },
    "state": "lapsed_soft",
    "preferences": {"preferred_slots": "weekday_evening"},
    # promotional_offers is the broad consent scope, so it covers every kind.
    "consent": {"opted_in_at": "2024-01-10", "scope": ["promotional_offers"]},
}


def _empty_trigger(kind: str) -> dict:
    is_customer = kind in CUSTOMER_KINDS
    return {
        "id": f"trg_{kind}",
        "scope": "customer" if is_customer else "merchant",
        "kind": kind,
        "source": "internal",
        "merchant_id": MERCHANT_ID,
        "customer_id": CUSTOMER_ID if is_customer else None,
        "payload": {},  # empty on purpose: this is the no-data fallback
        "urgency": 3,
        "suppression_key": f"{kind}:{MERCHANT_ID}",
        "expires_at": "2026-12-31T00:00:00Z",
    }


def _seed(name: str, list_key: str, id_key: str) -> dict:
    data = json.loads((DATASET / f"{name}.json").read_text(encoding="utf-8"))
    return {item[id_key]: item for item in data[list_key]}


def _seed_categories() -> dict:
    out = {}
    for path in (DATASET / "categories").glob("*.json"):
        category = json.loads(path.read_text(encoding="utf-8"))
        out[category["slug"]] = category
    return out


MERCHANTS = _seed("merchants_seed", "merchants", "merchant_id")
CUSTOMERS = _seed("customers_seed", "customers", "customer_id")
TRIGGERS = _seed("triggers_seed", "triggers", "id")
CATEGORIES = _seed_categories()


@pytest.mark.parametrize("kind", sorted(BUILDERS))
def test_builder_fallback_never_crashes(kind):
    """No builder may raise on an empty payload with proof=None — the shape the
    perf_dip NameError crashed on. Weak output is fine here; an exception is not."""
    trigger = _empty_trigger(kind)
    customer = CUSTOMER if kind in CUSTOMER_KINDS else None
    pack = build_fact_pack(CATEGORY, MERCHANT, trigger, customer, today=TODAY)
    compose_from_template(pack, cohort=None)  # must not raise


@pytest.mark.parametrize("kind", ["perf_dip", "curious_ask_due"])
def test_previously_broken_kinds_still_speak(kind):
    """Both regressions must produce a message on the fallback path — a silent
    None would hide the very branch this file exists to protect."""
    pack = build_fact_pack(CATEGORY, MERCHANT, _empty_trigger(kind), None, today=TODAY)
    assert compose_from_template(pack, cohort=None) is not None


@pytest.mark.parametrize("trigger_id", sorted(TRIGGERS))
def test_seed_trigger_composes_valid_copy(trigger_id):
    """Every seed trigger, composed the way the judge does (real payloads, real
    cohort, no LLM), must pass the validators — the single-push scoring path."""
    trigger = TRIGGERS[trigger_id]
    merchant = MERCHANTS.get(trigger.get("merchant_id"))
    assert merchant, f"seed trigger {trigger_id} references an unknown merchant"
    category = CATEGORIES[merchant["category_slug"]]
    customer = CUSTOMERS.get(trigger.get("customer_id")) if trigger.get("customer_id") else None

    pack = build_fact_pack(category, merchant, trigger, customer, today=TODAY)
    cohort = build_cohort(merchant, list(MERCHANTS.values()))
    message = compose_from_template(pack, cohort)
    if message is not None:
        problems = check(message.body, pack, category)
        assert problems == [], f"{trigger_id} ({trigger['kind']}) failed validation: {problems}\n{message.body}"


def test_curious_ask_opens_on_a_real_number():
    """The bug the validators could not catch: the opener must carry a real
    number (the shop's own call volume), not filler ending in the shop's name."""
    pack = build_fact_pack(CATEGORY, MERCHANT, _empty_trigger("curious_ask_due"), None, today=TODAY)
    message = compose_from_template(pack, cohort=None)
    assert message is not None
    first_line = message.body.splitlines()[0]
    assert any(char.isdigit() for char in first_line), f"curious-ask opener has no number: {first_line!r}"
