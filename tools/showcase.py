"""Build docs/index.html, the page GitHub Pages serves: an interactive,
self-contained demo of Vera for people who will never run the server.

Everything on the page is produced by the real composer at build time — the same
fact pack, templates, validator and reply state machine the server runs — then
baked into the page as JSON. No server, no LLM, no network, so the link never
goes dead and no API key is exposed.

    python tools/showcase.py                      -> docs/index.html
    python tools/showcase.py --out other.html
"""

import argparse
import copy
import html
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # bodies carry the rupee sign

from vera.cohort import Cohort, build_cohort
from vera.conversation import ConversationStore, respond
from vera.factpack import build_fact_pack, numbers_in
from vera.templates import compose_from_template
from vera.validate import check

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "expanded"
TEMPLATE = Path(__file__).resolve().parent / "showcase_template.html"
TODAY = date(2026, 8, 23)

# The merchant used for the "what changed" and conversation demos: the dataset's
# best-known case, so a reader can line it up with the challenge's own examples.
DEMO_MERCHANT = "m_001_drmeera_dentist_delhi"
DEMO_TRIGGER = "trg_001_research_digest_dentists"


def _load(folder: str, key: str) -> dict:
    return {
        item[key]: item
        for item in (json.loads(path.read_text(encoding="utf-8")) for path in sorted((DATASET / folder).glob("*.json")))
    }


def _walk(value: Any, path: str):
    """Every scalar in a JSON tree, with the dotted path that reaches it."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")
    elif value is not None and not isinstance(value, bool):
        yield path, value


def _is_identifier(path: str) -> bool:
    key = path.rsplit(".", 1)[-1]
    return key == "id" or key.endswith("_id")


def _trace(number: str, sources: list[tuple[str, Any]], cohort: Cohort | None, pack) -> list[dict]:
    """Where a number in the body came from. Inputs first, then what was computed from them."""
    # Sources are ordered by how directly the message is about them: the trigger,
    # then its digest item, then the merchant. Within one, an exact value beats a
    # number quoted inside text ("JIDA Oct 2026, p.14"). Identifiers go last: the
    # digits in "d_2026W17" are a coincidence, not a citation.
    ranked, identifiers = [], []
    for root, tree in sources:
        exact, quoted = [], []
        for path, value in _walk(tree, root):
            if isinstance(value, (int, float)) and float(number) == float(value):
                exact.append((path, value))
            elif isinstance(value, str) and number in numbers_in(value):
                (identifiers if _is_identifier(path) else quoted).append((path, value))
        ranked += exact + quoted
    scalars = [item for root, tree in sources for item in _walk(tree, root)]
    found = [{"path": path, "value": str(value), "how": "read"} for path, value in (ranked + identifiers)[:2]]
    if found:
        return found

    # Week-on-week movement is stored as a fraction (-0.5) and written as "50%".
    for path, value in scalars:
        if isinstance(value, float) and abs(value) < 1 and round(abs(value) * 100) == float(number):
            return [{"path": path, "value": str(value), "how": "computed", "meaning": "stored as a fraction, written as a percent"}]

    if cohort:
        computed = {
            "median_calls": f"median monthly calls across {cohort.peer_count} peer merchants",
            "median_views": f"median views across {cohort.peer_count} peer merchants",
            "peer_count": "peer merchants in the same category" + (f" in {cohort.city}" if cohort.is_local else ""),
            "posted_recently": "peers whose last Google post was 14 days ago or less",
        }
        for field, meaning in computed.items():
            if str(getattr(cohort, field)) == number:
                return [{"path": f"cohort.{field}", "value": number, "how": "computed", "meaning": meaning}]

    views, calls = pack.performance.get("views"), pack.performance.get("calls")
    if views and number == str(round((calls or 0) / views * 100)):
        return [{"path": "performance.calls ÷ performance.views × 100", "value": number, "how": "computed",
                 "meaning": "people who call, out of every 100 who see the listing"}]
    if number == "100":
        return [{"path": "per 100 views", "value": number, "how": "computed", "meaning": "the base of the rate above"}]
    if pack.months_since_visit is not None and number == str(pack.months_since_visit):
        return [{"path": "customer.relationship.last_visit → today", "value": number, "how": "computed",
                 "meaning": "months since the customer's last visit"}]
    return []


def _compose_case(trigger, merchant, category, customer, all_merchants, previous_merchant=None) -> dict:
    pack = build_fact_pack(category, merchant, trigger, customer, previous_merchant=previous_merchant, today=TODAY)
    peers = [other for other in all_merchants if other["merchant_id"] != merchant["merchant_id"]]
    cohort = build_cohort(merchant, peers)
    message = compose_from_template(pack, cohort)

    identity = merchant.get("identity", {})
    performance = merchant.get("performance", {})
    base = {
        "id": trigger["id"],
        "kind": trigger["kind"],
        "category": merchant["category_slug"],
        "merchant": identity.get("name", merchant["merchant_id"]),
        "owner": identity.get("owner_first_name", ""),
        "where": f"{identity.get('locality', '')}, {identity.get('city', '')}",
        "urgency": trigger.get("urgency", 0),
        "payload": {key: value for key, value in trigger.get("payload", {}).items() if value not in (None, "", [])},
        "snapshot": {
            "views": performance.get("views"),
            "calls": performance.get("calls"),
            "signals": merchant.get("signals", []),
            "offers": [offer["title"] for offer in merchant.get("offers", []) if offer.get("status") == "active"],
        },
    }
    if message is None:
        base["blocked"] = pack.blocked_reason or "the contexts carry nothing that supports this message"
        return base

    sources: list[tuple[str, Any]] = [("trigger.payload", trigger.get("payload", {}))]
    if pack.digest_item:
        sources.append(("category.digest[item]", pack.digest_item))
    sources.append(("merchant", merchant))
    if customer:
        sources.append(("customer", customer))
    sources.append(("category.offer_catalog", category.get("offer_catalog", [])))

    numbers = []
    for number in dict.fromkeys(numbers_in(message.body)):
        numbers.append({"value": number, "sources": _trace(number, sources, cohort, pack)})

    customer_identity = (customer or {}).get("identity", {})
    base.update(
        {
            "audience": "customer" if message.send_as == "merchant_on_behalf" else "merchant",
            "recipient": pack.customer_name or identity.get("owner_first_name", "owner"),
            "language": customer_identity.get("language_pref") if customer else "en",
            "body": message.body,
            "cta": message.cta,
            "rationale": message.rationale,
            "problems": check(message.body, pack, category),
            "numbers": numbers,
        }
    )
    return base


def _conversations(open_topic: str) -> list[dict]:
    """The three replay scenarios, run through the real reply state machine."""
    scenarios = [
        (
            "auto-reply",
            "The owner's WhatsApp Business auto-reply answers instead of the owner.",
            ["Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly."] * 3,
        ),
        (
            "commitment",
            "The owner says yes. Vera stops pitching and starts doing.",
            ["Yes please, let's do it"],
        ),
        (
            "hostile",
            "The owner wants no more messages.",
            ["Stop messaging me. This is spam."],
        ),
        (
            "off-topic",
            "The owner asks for something Vera can't help with.",
            ["Can you help me file my GST return?"],
        ),
    ]
    store = ConversationStore()
    out = []
    for index, (name, blurb, replies) in enumerate(scenarios):
        merchant_id = f"demo_{index}"
        state = store.conversation(f"conv_{name}", merchant_id, None)
        memory = store.partner(merchant_id, None)
        turns = []
        for reply in replies:
            if state.ended:
                break
            move = respond(state, memory, reply, open_topic=open_topic)
            turns.append({"merchant": reply, **move})
        out.append({"name": name, "blurb": blurb, "turns": turns})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO / "docs" / "index.html"))
    args = parser.parse_args()

    categories = _load("categories", "slug")
    merchants = _load("merchants", "merchant_id")
    customers = _load("customers", "customer_id")
    triggers = _load("triggers", "id")
    all_merchants = list(merchants.values())

    cases = []
    for trigger in sorted(triggers.values(), key=lambda item: item["id"]):
        merchant = merchants.get(trigger.get("merchant_id"))
        if not merchant:
            continue
        customer = customers.get(trigger.get("customer_id")) if trigger.get("customer_id") else None
        cases.append(_compose_case(trigger, merchant, categories[merchant["category_slug"]], customer, all_merchants))

    # Version-delta demo: the same trigger, composed again after a newer snapshot
    # of the merchant arrives with more calls. The server does this on every
    # higher-version /v1/context push; here the older version is constructed.
    merchant = merchants[DEMO_MERCHANT]
    trigger = triggers[DEMO_TRIGGER]
    category = categories[merchant["category_slug"]]
    newer = copy.deepcopy(merchant)
    newer["performance"]["calls"] = merchant["performance"]["calls"] + 9
    before = _compose_case(trigger, merchant, category, None, all_merchants)
    after = _compose_case(trigger, newer, category, None, all_merchants, previous_merchant=merchant)

    composed = [case for case in cases if "body" in case]
    traced = sum(1 for case in composed for number in case["numbers"] if number["sources"])
    total_numbers = sum(len(case["numbers"]) for case in composed)
    data = {
        "cases": cases,
        "delta": {
            "calls_before": merchant["performance"]["calls"],
            "calls_after": newer["performance"]["calls"],
            "before": before,
            "after": after,
        },
        "conversations": _conversations("the 3 Google posts on fluoride varnish"),
        "stats": {
            "triggers": len(cases),
            "composed": len(composed),
            "distinct": len({case["body"] for case in composed}),
            "blocked": len(cases) - len(composed),
            "numbers": total_numbers,
            "traced": traced,
            "failing": sum(1 for case in composed if case["problems"]),
            "merchants": len(merchants),
            "kinds": len({case["kind"] for case in cases}),
        },
    }

    # "</" inside a JSON string would close the script tag early.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")

    stats = data["stats"]
    print(f"{stats['composed']} composed, {stats['blocked']} held back, {stats['distinct']} distinct bodies")
    print(f"{stats['traced']} of {stats['numbers']} numbers traced to a source, {stats['failing']} failing checks")
    print(f"-> {destination}")
    return 0 if stats["traced"] == stats["numbers"] and not stats["failing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
