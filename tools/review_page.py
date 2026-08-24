"""Render every template composition as a WhatsApp bubble in a self-contained review.html.

No server, no LLM, no network. Reading bodies as JSON in a terminal hides the two
things that decide whether a merchant reads them: where the line breaks fall, and
whether the anchor fact survives the notification preview.

    python tools/review_page.py                  all 100 triggers -> review.html
    python tools/review_page.py --limit 20        the first 20 only
    python tools/review_page.py --out other.html
"""

import argparse
import html
import json
import re
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

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "expanded"
TODAY = date(2026, 8, 23)

# WhatsApp shows roughly two wrapped lines in a notification. Anything past this
# is invisible until the merchant opens the chat.
PREVIEW_CHARS = 90


def _load(folder: str, key: str) -> dict:
    return {
        item[key]: item
        for item in (json.loads(path.read_text(encoding="utf-8")) for path in (DATASET / folder).glob("*.json"))
    }


def _bubble_html(body: str) -> str:
    """Escape, then apply WhatsApp's own markup — *bold* and _italic_ only."""
    escaped = html.escape(body)
    escaped = re.sub(r"\*([^*\n]+)\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"<em>\1</em>", escaped)
    return escaped.replace("\n", "<br>")


def _preview(body: str) -> str:
    flattened = " ".join(body.split())
    return flattened if len(flattened) <= PREVIEW_CHARS else flattened[:PREVIEW_CHARS].rstrip() + "…"


def _payload_html(payload: dict) -> str:
    if not payload:
        return "<em>empty</em>"
    rows = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(str(value))}</td></tr>" for key, value in payload.items()
    )
    return f"<table class='payload'>{rows}</table>"


def _card_html(case: dict) -> str:
    problems = case["problems"]
    status = "fail" if problems else "pass"
    checks = (
        "".join(f"<li class='bad'>{html.escape(problem)}</li>" for problem in problems)
        if problems
        else "<li class='good'>all checks pass</li>"
    )
    audience = "customer" if case["send_as"] == "merchant_on_behalf" else "merchant"
    haystack = html.escape(f"{case['trigger_id']} {case['kind']} {case['merchant_name']} {case['body']}".lower())

    return f"""
<article class="case" data-status="{status}" data-category="{case['category_slug']}"
         data-kind="{html.escape(case['kind'])}" data-audience="{audience}" data-text="{haystack}">
  <section class="trigger">
    <div class="tag-row">
      <span class="tag kind">{html.escape(case['kind'])}</span>
      <span class="tag">urgency {case['urgency']}</span>
      <span class="tag">{html.escape(case['scope'])}-scoped</span>
      <span class="tag status-{status}">{status}</span>
    </div>
    <h2>{html.escape(case['merchant_name'])}</h2>
    <p class="where">{html.escape(case['locality'])}, {html.escape(case['city'])}
       &middot; {html.escape(case['category_slug'])}</p>
    <dl>
      <dt>trigger</dt><dd>{html.escape(case['trigger_id'])}</dd>
      <dt>to</dt><dd>{html.escape(case['recipient'])} <span class="muted">({html.escape(case['send_as'])})</span></dd>
      <dt>cta</dt><dd>{html.escape(case['cta'])}</dd>
      <dt>suppression</dt><dd class="mono">{html.escape(case['suppression_key'])}</dd>
    </dl>
    <h3>payload</h3>
    {_payload_html(case['payload'])}
  </section>

  <section class="message">
    <div class="notification">
      <span class="notif-label">notification preview</span>
      <p>{html.escape(_preview(case['body']))}</p>
    </div>
    <div class="chat">
      <div class="bubble">{_bubble_html(case['body'])}<span class="ticks">✓✓</span></div>
    </div>
    <div class="meta">
      <h3>rationale</h3>
      <p>{html.escape(case['rationale'])}</p>
      <h3>checks</h3>
      <ul class="checks">{checks}</ul>
      <p class="counts">{case['words']} words &middot; {case['lines']} blocks
         &middot; {len(case['allowed_numbers'])} licensed numbers</p>
    </div>
  </section>
</article>"""


def _page_html(cases: list[dict], blocked: list[dict]) -> str:
    by_category = sorted({case["category_slug"] for case in cases})
    by_kind = sorted({case["kind"] for case in cases})
    failing = sum(1 for case in cases if case["problems"])
    kind_options = "".join(f"<option value='{html.escape(kind)}'>{html.escape(kind)}</option>" for kind in by_kind)
    category_buttons = "".join(
        f"<button data-filter='category' data-value='{html.escape(slug)}'>{html.escape(slug)}</button>"
        for slug in by_category
    )
    blocked_html = (
        "<details class='blocked'><summary>"
        f"{len(blocked)} triggers composed nothing</summary><ul>"
        + "".join(
            f"<li><span class='mono'>{html.escape(item['trigger_id'])}</span> "
            f"({html.escape(item['kind'])}) — {html.escape(item['reason'])}</li>"
            for item in blocked
        )
        + "</ul></details>"
        if blocked
        else ""
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vera — composition review</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #eef1f4; color: #14202b;
       font: 15px/1.55 -apple-system, "Segoe UI", Roboto, sans-serif; }}
header {{ position: sticky; top: 0; z-index: 5; background: #fff; padding: 16px 24px;
          border-bottom: 1px solid #d5dbe1; box-shadow: 0 1px 4px rgba(0,0,0,.05); }}
h1 {{ margin: 0 0 4px; font-size: 19px; }}
.summary {{ margin: 0 0 12px; color: #5a6b7b; font-size: 13px; }}
.summary strong {{ color: #14202b; }}
.controls {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
button, select, input {{ font: inherit; font-size: 13px; padding: 5px 11px; border-radius: 999px;
          border: 1px solid #c3ccd6; background: #fff; color: #33475b; cursor: pointer; }}
input {{ cursor: text; min-width: 200px; }}
button:hover {{ border-color: #7d8f9f; }}
button.on {{ background: #14202b; border-color: #14202b; color: #fff; }}
.spacer {{ flex: 1; }}
main {{ padding: 20px 24px 60px; display: flex; flex-direction: column; gap: 18px; }}
.case {{ display: grid; grid-template-columns: minmax(260px, 2fr) 3fr; gap: 0;
         background: #fff; border: 1px solid #d5dbe1; border-radius: 12px; overflow: hidden; }}
.case.hidden {{ display: none; }}
.trigger {{ padding: 16px 18px; background: #f7f9fb; border-right: 1px solid #e3e8ed; }}
.trigger h2 {{ margin: 8px 0 2px; font-size: 16px; }}
.where {{ margin: 0 0 12px; color: #5a6b7b; font-size: 13px; }}
.tag-row {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.tag {{ font-size: 11px; padding: 2px 8px; border-radius: 999px;
        background: #e5eaef; color: #45596c; letter-spacing: .02em; }}
.tag.kind {{ background: #dbe7f5; color: #1d4a7a; font-weight: 600; }}
.status-pass {{ background: #d6f0dc; color: #1c6b34; }}
.status-fail {{ background: #fbdada; color: #9a2222; }}
dl {{ display: grid; grid-template-columns: 84px 1fr; gap: 3px 10px; margin: 0 0 14px; font-size: 13px; }}
dt {{ color: #7a8b9a; }}
dd {{ margin: 0; }}
h3 {{ margin: 12px 0 6px; font-size: 11px; text-transform: uppercase;
      letter-spacing: .07em; color: #7a8b9a; }}
.payload {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
.payload td {{ padding: 3px 6px; border-top: 1px solid #e3e8ed; vertical-align: top; }}
.payload td:first-child {{ color: #7a8b9a; width: 40%; }}
.mono {{ font-family: ui-monospace, Consolas, monospace; font-size: 12px; }}
.muted {{ color: #8a99a7; }}
.message {{ padding: 16px 18px; }}
.notification {{ background: #fff8e3; border: 1px solid #f0e0ac; border-radius: 8px;
                 padding: 8px 12px; margin-bottom: 14px; }}
.notif-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: .07em; color: #9a8433; }}
.notification p {{ margin: 2px 0 0; font-size: 13.5px; }}
.chat {{ background: #e4ddd4; border-radius: 10px; padding: 14px; }}
.bubble {{ position: relative; max-width: 460px; margin-left: auto; background: #d9fdd3;
           border-radius: 8px 2px 8px 8px; padding: 8px 44px 8px 11px; font-size: 14.5px;
           box-shadow: 0 1px 1px rgba(0,0,0,.13); white-space: normal; }}
.ticks {{ position: absolute; right: 10px; bottom: 6px; font-size: 11px; color: #53bdeb; }}
.meta h3:first-child {{ margin-top: 14px; }}
.meta p {{ margin: 0; font-size: 13.5px; color: #43535f; }}
.checks {{ margin: 0; padding-left: 18px; font-size: 13px; }}
.checks .good {{ color: #1c6b34; }}
.checks .bad {{ color: #9a2222; }}
.counts {{ margin-top: 10px !important; font-size: 12px !important; color: #8a99a7 !important; }}
.blocked {{ margin-top: 6px; font-size: 13px; color: #5a6b7b; }}
.blocked summary {{ cursor: pointer; }}
@media (max-width: 860px) {{ .case {{ grid-template-columns: 1fr; }}
  .trigger {{ border-right: none; border-bottom: 1px solid #e3e8ed; }} }}
</style></head><body>
<header>
  <h1>Vera — composition review</h1>
  <p class="summary"><strong>{len(cases)}</strong> composed &middot;
     <strong>{failing}</strong> failing checks &middot;
     <strong>{len(by_kind)}</strong> trigger kinds &middot;
     <strong>{len({case['body'] for case in cases})}</strong> distinct bodies</p>
  <div class="controls">
    <button data-filter="status" data-value="all" class="on">all</button>
    <button data-filter="status" data-value="fail">failing</button>
    <button data-filter="audience" data-value="merchant">to merchant</button>
    <button data-filter="audience" data-value="customer">to customer</button>
    {category_buttons}
    <select id="kind"><option value="">every kind</option>{kind_options}</select>
    <span class="spacer"></span>
    <input id="search" type="search" placeholder="search body, merchant, trigger">
  </div>
  {blocked_html}
</header>
<main>{"".join(_card_html(case) for case in cases)}</main>
<script>
const active = {{}};
const cards = [...document.querySelectorAll('.case')];
const kindSelect = document.getElementById('kind');
const search = document.getElementById('search');

function apply() {{
  const term = search.value.trim().toLowerCase();
  for (const card of cards) {{
    let show = true;
    for (const [name, value] of Object.entries(active)) {{
      if (value && card.dataset[name] !== value) show = false;
    }}
    if (kindSelect.value && card.dataset.kind !== kindSelect.value) show = false;
    if (term && !card.dataset.text.includes(term)) show = false;
    card.classList.toggle('hidden', !show);
  }}
}}

for (const button of document.querySelectorAll('button[data-filter]')) {{
  button.addEventListener('click', () => {{
    const {{ filter, value }} = button.dataset;
    const turningOff = active[filter] === value || value === 'all';
    for (const sibling of document.querySelectorAll(`button[data-filter="${{filter}}"]`)) {{
      sibling.classList.remove('on');
    }}
    active[filter] = turningOff ? null : value;
    button.classList.add('on');
    apply();
  }});
}}
kindSelect.addEventListener('change', apply);
search.addEventListener('input', apply);
</script>
</body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="compose only the first N triggers")
    parser.add_argument("--out", default=str(REPO / "review.html"))
    args = parser.parse_args()

    categories = _load("categories", "slug")
    merchants = _load("merchants", "merchant_id")
    customers = _load("customers", "customer_id")
    triggers = _load("triggers", "id")
    all_merchants = list(merchants.values())

    chosen = sorted(triggers.values(), key=lambda item: item["id"])
    if args.limit:
        chosen = chosen[: args.limit]

    cases: list[dict] = []
    blocked: list[dict] = []
    for trigger in chosen:
        merchant = merchants.get(trigger.get("merchant_id"))
        if not merchant:
            continue
        category = categories[merchant["category_slug"]]
        customer = customers.get(trigger.get("customer_id")) if trigger.get("customer_id") else None

        pack = build_fact_pack(category, merchant, trigger, customer, today=TODAY)
        message = compose_from_template(pack, build_cohort(merchant, all_merchants))
        if message is None:
            blocked.append(
                {
                    "trigger_id": trigger["id"],
                    "kind": trigger["kind"],
                    "reason": pack.blocked_reason or "no message planned for this kind",
                }
            )
            continue

        identity = merchant.get("identity", {})
        cases.append(
            {
                "trigger_id": trigger["id"],
                "kind": trigger["kind"],
                "scope": trigger.get("scope", "merchant"),
                "urgency": trigger.get("urgency", 0),
                "payload": trigger.get("payload", {}),
                "merchant_name": identity.get("name", merchant["merchant_id"]),
                "locality": identity.get("locality", ""),
                "city": identity.get("city", ""),
                "category_slug": merchant["category_slug"],
                "recipient": pack.customer_name or pack.owner_name or "owner",
                "body": message.body,
                "cta": message.cta,
                "send_as": message.send_as,
                "suppression_key": message.suppression_key,
                "rationale": message.rationale,
                "problems": check(message.body, pack, category),
                "allowed_numbers": pack.allowed_numbers,
                "words": len(message.body.split()),
                "lines": len([block for block in message.body.split("\n") if block.strip()]),
            }
        )

    destination = Path(args.out)
    destination.write_text(_page_html(cases, blocked), encoding="utf-8")

    failing = Counter(problem.split(" — ")[0] for case in cases for problem in case["problems"])
    print(f"{len(cases)} compositions -> {destination}")
    if blocked:
        print(f"{len(blocked)} blocked")
    for rule, count in failing.most_common():
        print(f"  {count:4}  {rule}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
