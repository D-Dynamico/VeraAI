"""Write `submission.jsonl` — one line per canonical test pair (challenge-brief.md §7.2).

Reads the 30 pairs from the expanded dataset and runs each one through `bot.compose`,
which is the same composer the live server uses.

    python dataset/generate_dataset.py --seed-dir dataset --out expanded
    PYTHONIOENCODING=utf-8 python make_submission.py

Every body is re-checked by `vera.validate` before it is written. A validator
failure is fatal here on purpose: this file is graded offline with no chance to
retry, so a bad line must stop the run rather than ship.
"""

import json
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")  # bodies carry the rupee sign

from bot import compose_with_pack, composer
from vera.validate import check

DATASET = Path(__file__).resolve().parent / "expanded"
OUTPUT = Path(__file__).resolve().parent / "submission.jsonl"

FIELDS = ("test_id", "body", "cta", "send_as", "suppression_key", "rationale")


def _load(folder: str, key: str) -> dict[str, Any]:
    return {
        item[key]: item
        for item in (json.loads(path.read_text(encoding="utf-8")) for path in (DATASET / folder).glob("*.json"))
    }


def main() -> int:
    if not DATASET.is_dir():
        print(f"{DATASET} not found — run dataset/generate_dataset.py first")
        return 1

    categories = _load("categories", "slug")
    merchants = _load("merchants", "merchant_id")
    customers = _load("customers", "customer_id")
    triggers = _load("triggers", "id")
    pairs = json.loads((DATASET / "test_pairs.json").read_text(encoding="utf-8"))["pairs"]

    lines = []
    problems = []
    for pair in pairs:
        merchant = merchants[pair["merchant_id"]]
        trigger = triggers[pair["trigger_id"]]
        category = categories[merchant["category_slug"]]
        customer = customers[pair["customer_id"]] if pair.get("customer_id") else None

        result, pack = compose_with_pack(category, merchant, trigger, customer)
        if not result["body"]:
            problems.append(f"{pair['test_id']}: no body — {result['rationale']}")
            continue

        for failure in check(result["body"], pack, category):
            problems.append(f"{pair['test_id']}: {failure}")

        lines.append({"test_id": pair["test_id"], **{field: result[field] for field in FIELDS[1:]}})

    if problems:
        print(f"{len(problems)} problem(s) — nothing written")
        for problem in problems:
            print(f"  ! {problem}")
        return 1

    OUTPUT.write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8",
    )
    bodies = [line["body"] for line in lines]
    print(f"wrote {OUTPUT.name}: {len(lines)} lines, {len(set(bodies))} distinct bodies")
    # Worth saying out loud: a run where every call fell back is a template-only
    # submission, which is a valid file and a weaker one.
    print(f"gemini calls {composer.calls_made}, fell back to template {composer.fallbacks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
