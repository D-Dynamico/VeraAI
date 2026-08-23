"""Compose for every trigger in the expanded dataset and report validator failures.

No server. This is what the composition layers are iterated against.

    python tools/local_harness.py            templates only, no LLM
    python tools/local_harness.py --llm      run the Gemini layer too, side by side
    python tools/local_harness.py --llm 12   ...over the first 12 triggers only
"""

import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # bodies carry the rupee sign

from vera.cohort import build_cohort
from vera.compose import ComposeJob, Composer, api_key
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


def _compare_with_llm(categories, merchants, customers, triggers, all_merchants) -> None:
    """Same triggers through the Gemini layer, printed against the template output."""
    limit = next((int(arg) for arg in sys.argv if arg.isdigit()), 8)
    composer = Composer()
    if not api_key():
        print("\nGEMINI_API_KEY not set — skipping the LLM comparison")
        return

    chosen = sorted(triggers.values(), key=lambda item: item["id"])[:limit]
    started = time.monotonic()
    for trigger in chosen:
        merchant = merchants.get(trigger.get("merchant_id"))
        if not merchant:
            continue
        category = categories[merchant["category_slug"]]
        customer = customers.get(trigger.get("customer_id")) if trigger.get("customer_id") else None

        template_pack = build_fact_pack(category, merchant, trigger, customer, today=TODAY)
        cohort = build_cohort(merchant, all_merchants)
        template_message = compose_from_template(template_pack, cohort)
        if template_message is None:
            continue

        llm_pack = build_fact_pack(category, merchant, trigger, customer, today=TODAY)
        job = ComposeJob(pack=llm_pack, category=category, cohort=cohort, cache_key=trigger["id"])
        llm_message = composer.compose(job)

        print(f"\n{'=' * 74}\n{trigger['id']}  ({trigger['kind']})")
        print(f"\n[template]\n{template_message.body}")
        marker = "same as template" if llm_message.body == template_message.body else "gemini"
        print(f"\n[{marker}]\n{llm_message.body}")

    print(f"\nllm calls {composer.calls_made} | fell back {composer.fallbacks} | {time.monotonic() - started:.1f}s total")
    for trigger_id, problems in composer.rejections:
        print(f"  rejected {trigger_id}: {problems}")


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

    if "--llm" in sys.argv:
        _compare_with_llm(categories, merchants, customers, triggers, all_merchants)

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
