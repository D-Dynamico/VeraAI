"""The offline `compose()` artifact required by challenge-brief.md §7.1.

Same composer the live server runs. `vera/app.py` drives these layers over pushed
contexts; here they are driven over dicts loaded straight from the dataset JSON,
so a message produced offline is the message the server would have sent.

    from bot import compose
    result = compose(category, merchant, trigger, customer)
    # -> {"body", "cta", "send_as", "suppression_key", "rationale"}

Two things the offline contract cannot carry, and how they are handled:

**Peer facts.** Several messages cite real cross-merchant figures ("6 of the 10
Delhi dentists posted in the last 14 days"), computed from the whole corpus
rather than from `category.peer_stats`. `compose()` receives one merchant, so it
reads the corpus from `expanded/merchants/` when that directory exists and drops
the peer clause when it does not. Regenerate it with
`python dataset/generate_dataset.py --seed-dir dataset --out expanded`.

**Restraint.** The tick path answers "nothing worth sending" with `actions: []`.
A function that must return a dict has no such answer, so it returns one with an
empty body and a rationale naming the reason. Composing anyway would mean
inventing the fact the contexts withheld, which is the one thing this bot does
not do.
"""

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from vera.cohort import build_cohort
from vera.compose import ComposeJob, Composer, api_key
from vera.factpack import FactPack, build_fact_pack
from vera.templates import compose_from_template

# The dataset is a fixed snapshot, not a live feed. Anchoring the one date-derived
# figure (months since a customer's last visit) to the snapshot's own date keeps
# compose() reproducible, which date.today() would not.
DATASET_AS_OF = date(2026, 8, 23)

MERCHANT_CORPUS = Path(__file__).resolve().parent / "expanded" / "merchants"

# Module-level so the response cache survives a batch run of make_submission.py.
composer = Composer()


@lru_cache(maxsize=1)
def _peer_corpus() -> tuple[dict[str, Any], ...]:
    if not MERCHANT_CORPUS.is_dir():
        return ()
    return tuple(
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(MERCHANT_CORPUS.glob("*.json"))
    )


def compose_with_pack(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], FactPack]:
    """`compose()`, plus the fact pack it composed against.

    The builders license each number they use onto the pack as they write it, so
    re-validating a body means holding the pack that produced it. Rebuilding a
    fresh one licenses nothing and every peer figure reads as fabricated.
    """
    pack = build_fact_pack(category, merchant, trigger, customer, today=DATASET_AS_OF)
    peers = [other for other in _peer_corpus() if other.get("merchant_id") != merchant.get("merchant_id")]
    cohort = build_cohort(merchant, peers) if peers else None

    message = compose_from_template(pack, cohort)
    if message is None:
        return {
            "body": "",
            "cta": "none",
            "send_as": "merchant_on_behalf" if pack.is_customer_facing else "vera",
            "suppression_key": pack.suppression_key,
            "rationale": pack.blocked_reason
            or f"No send: the pushed contexts carry nothing that supports a {trigger.get('kind', 'this')} message.",
        }, pack

    # The LLM only rephrases lines the template already vouched for, and anything
    # it returns is re-validated before it is accepted. With no key the template
    # output stands, which is why this artifact runs without one.
    if api_key():
        # Keyed on the whole pair, not just the trigger. A trigger dict without
        # "id" would otherwise give every call the same key, and a cache hit
        # returns the cached body verbatim — so pair two would ship pair one's
        # message. The merchant and customer ids keep the key distinct even then.
        cache_key = ":".join(
            (
                "offline",
                str(merchant.get("merchant_id", "")),
                str(trigger.get("id", trigger.get("kind", ""))),
                str((customer or {}).get("customer_id", "")),
            )
        )
        job = ComposeJob(pack=pack, category=category, cohort=cohort, cache_key=cache_key)
        message = composer.compose(job) or message

    return {
        "body": message.body,
        "cta": message.cta,
        "send_as": message.send_as,
        "suppression_key": message.suppression_key,
        "rationale": message.rationale,
    }, pack


def compose(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose one message. Deterministic: no sampling, no wall-clock reads."""
    return compose_with_pack(category, merchant, trigger, customer)[0]
