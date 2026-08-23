"""Compose for every trigger in the expanded dataset and report validator failures.

No LLM, no server. This is what the template layer is iterated against.

    python tools/local_harness.py
"""

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # bodies carry the rupee sign

from vera.cohort import build_cohort
from vera.factpack import build_fact_pack
from vera.templates import compose_from_template
from vera.validate import check

DATASET = Path(__file__).resolve().parent.parent / "expanded"
TODAY = date(2026, 8, 23)


def _load(folder: str, key: str) -> dict:
    return {
        item[key]: item
        for item in (json.loads(path.read_text(encoding="utf-8")) for path in (DATASET / folder).glob("*.json"))
    }


def main() -> int:
    categories = _load("categories", "slug")
    merchants = _load("merchants", "merchant_id")
    customers = _load("customers", "customer_id")
    triggers = _load("triggers", "id")
    all_merchants = list(merchants.values())

    failures_by_rule = Counter()
    composed_by_kind = Counter()
    blocked = 0
    bodies = []
    examples = []

    for trigger in sorted(triggers.values(), key=lambda item: item["id"]):
        merchant = merchants.get(trigger.get("merchant_id"))
        if not merchant:
            continue
        category = categories[merchant["category_slug"]]
        customer = customers.get(trigger.get("customer_id")) if trigger.get("customer_id") else None

        pack = build_fact_pack(category, merchant, trigger, customer, today=TODAY)
        message = compose_from_template(pack, build_cohort(merchant, all_merchants))
        if message is None:
            blocked += 1
            continue

        composed_by_kind[trigger["kind"]] += 1
        bodies.append(message.body)
        problems = check(message.body, pack, category)
        for problem in problems:
            failures_by_rule[problem.split(" — ")[0]] += 1
        if problems:
            examples.append((trigger["id"], problems, message.body))

    print(f"composed        {sum(composed_by_kind.values())}")
    print(f"blocked         {blocked} (consent)")
    print(f"distinct bodies {len(set(bodies))} of {len(bodies)}")
    print(f"kinds covered   {len(composed_by_kind)}")

    if failures_by_rule:
        print("\nfailures by rule")
        for rule, count in failures_by_rule.most_common():
            print(f"  {count:4}  {rule}")
        print("\nfirst three failing messages")
        for trigger_id, problems, body in examples[:3]:
            print(f"\n--- {trigger_id}")
            for problem in problems:
                print(f"    ! {problem}")
            print(body)
        return 1

    print("\nno validator failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
