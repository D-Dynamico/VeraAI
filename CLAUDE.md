# CLAUDE.md — magicpin AI Challenge ("Vera")

Context file for any session working in this repo. Everything below was derived by reading the briefs, the dataset, and `judge_simulator.py` end-to-end. Read this first; it saves a full re-derivation pass.

---

## 1. What this repo is

A challenge submission workspace. The task: **build a merchant-engagement AI bot ("Vera") that talks to Indian local-commerce merchants on WhatsApp**, composed from four context layers, and is scored by an LLM judge harness.

As of this writing the repo contains **spec + data only — no implementation code yet**.

### Files present (all provided by the challenge, none written by us)

| Path | What it is |
|---|---|
| `challenge-brief.md` | The *what*: 4-context framework, `compose()` contract, 5-dimension rubric, anti-patterns, compulsion levers |
| `challenge-testing-brief.md` | The *how it's tested*: 5 HTTP endpoints, judge harness lifecycle, rate limits, failure-mode penalties |
| `judge_simulator.py` | Local rehearsal of the judge harness. **The single most informative file in the repo** — see §4 |
| `dataset/categories/*.json` | 5 fully-populated CategoryContexts (dentists, salons, restaurants, gyms, pharmacies) |
| `dataset/*_seed.json` | 10 merchant / 15 customer / 25 trigger seeds |
| `dataset/generate_dataset.py` | Expands seeds → 50 merchants, 200 customers, 100 triggers, + `test_pairs.json` (the canonical 30 test pairs) |
| `examples/api-call-examples.md` | Every request/response shape, failure modes, curl block |
| `examples/case-studies.md` | 10 worked "what good looks like" compositions + the cross-case patterns the judge rewards |
| `engagement-design.md`, `engagement-research.md` | magicpin's internal design docs for the real Vera — background, not requirements |

---

## 2. The contract in one screen

```
compose(category, merchant, trigger, customer?) -> {body, cta, send_as, suppression_key, rationale}
```

- `send_as`: `"vera"` (merchant-facing) or `"merchant_on_behalf"` (customer-facing).
- The bot must be a **stateful HTTP server** exposing 5 endpoints:
  `POST /v1/context`, `POST /v1/tick`, `POST /v1/reply`, `GET /v1/healthz`, `GET /v1/metadata`
  (plus optional `POST /v1/teardown`).
- `/v1/context` is idempotent on `(context_id, version)`; a higher version replaces atomically; equal-or-lower returns `{"accepted": false, "reason": "stale_version", "current_version": n}`.
- `/v1/tick` returns `actions[]` (may be empty — **restraint is explicitly rewarded, spam penalized**).
- `/v1/reply` returns exactly one of `{"action":"send"|"wait"|"end"}`.

### Scoring (5 dimensions × 0-10 = 50)

Specificity · Category fit · Merchant fit · Trigger relevance (the simulator calls it **`decision_quality`**) · Engagement compulsion.

Plus: Phase 3 adaptation bonus (max +5/dim), Phase 4 replay (top 10 only, max +30), operational penalties (max -20).

---

## 3. Non-obvious facts worth remembering

1. **The judged surface is the live server, not a file.** `challenge-brief.md` §7 also asks for offline artifacts (`bot.py` with `compose()`, `submission.jsonl`, `README.md`), but the harness in `challenge-testing-brief.md` is what actually produces the score.
2. **Fabrication is the biggest scoring cliff.** Per `examples/case-studies.md`: any fabricated number or missing source citation **caps the case at 5/dimension** regardless of prose quality, plus -2. Every number in a message must be traceable to a pushed context.
3. **URLs in the body are a hard fail: -3 each** (`examples/api-call-examples.md` F.4) — even though `challenge-brief.md` §5 constraint 4 says URLs are "allowed when they add value". The testing brief wins; emit no URLs.
4. **No hard body-length cap** (F.3 supersedes any earlier length rule).
5. **Repetition: -2** for the same body verbatim twice in one `conversation_id`.
6. **Missing required action keys = scored 0 plus -2.** Required: `conversation_id, merchant_id, send_as, trigger_id, cta, suppression_key, rationale`.
7. **`rationale` is read and scored** — the judge cross-checks it against the message; mismatch is penalized. It must be real reasoning, not boilerplate.
8. **Offer format matters**: service+price ("Haircut @ ₹99") scores; percentage discounts ("30% off") are an explicit anti-pattern for this audience.
9. **Language**: the briefs prefer Hindi-English code-mix where `identity.languages` includes `hi`, and penalize pure English there. **We override this — English only. See §9**, which records the decision and its accepted cost.
10. **Production Vera's known weak spots** = the stated opportunities to outscore it: social proof and asking-the-merchant levers barely fire; auto-reply handling burns 2-3 turns; intent handoff regresses to qualifying questions.

---

## 4. What `judge_simulator.py` reveals (read this before tuning anything)

- **Client timeouts are 15s for `/v1/tick` and `/v1/reply`** (`BotClient.tick`/`.reply`, lines ~424-434) — tighter than the 30s the brief promises. Design to a ~8s compose budget. `/v1/healthz` and `/v1/metadata` get only **5s**.
- **The replay scenarios are graded by literal keyword checks, not by an LLM** — free points:
  - `_intent()` (~lines 740-749): passes only if the reply body contains one of `done, sending, draft, here, confirm, proceed, next` **and contains none of** `would you, do you, can you tell, what if, how about`.
  - `_hostile()`: passes on `action:"end"`, or `action:"send"` whose body contains `sorry`, `apolog`, or the contraction of "will not".
  - `_auto_reply()`: sends the same canned text 4 times; passes only if the bot returns `action:"end"` within those 4 turns.
- **The scorer's 4th key is `decision_quality`** (the parser accepts `trigger_relevance` as a fallback, line ~555).
- The scorer prompt reads these exact fields, so they must be populated and used: `identity.owner_first_name`, `identity.locality`, `identity.languages`, `voice.vocab_taboo`, `performance.{views,calls,ctr}`, `signals`, active `offers[].title`, `trigger.{kind,payload,urgency}`.
- Scenarios: `warmup`, `phase2_short`, `auto_reply_hell`, `intent_transition`, `hostile`, `all`, `full_evaluation`. Config is **edited in-file** (lines 24-39): `BOT_URL`, `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `TEST_SCENARIO`.
- The simulator loads the dataset from `./dataset` — the **seed** files, not `expanded/`.

---

## 5. Dataset shapes (actual field names, not the brief's simplified ones)

```
merchant: merchant_id, category_slug, identity{name, city, locality, place_id, verified,
          languages[], owner_first_name, established_year}, subscription{status, plan,
          days_remaining}, performance{window_days, views, calls, directions, ctr, leads,
          delta_7d{views_pct, calls_pct, ctr_pct}}, offers[{id,title,status}],
          conversation_history[{ts,from,body,engagement}], customer_aggregate{...},
          signals[] (e.g. "stale_posts:22d", "ctr_below_peer_median", "engaged_in_last_48h"),
          review_themes[{theme,sentiment,occurrences_30d,common_quote}]

category: slug, display_name, voice{tone, register, code_mix, vocab_allowed[], vocab_taboo[],
          salutation_examples[], tone_examples[]}, offer_catalog[{id,title,value,audience,type}],
          peer_stats{avg_rating, avg_review_count, avg_views_30d, avg_calls_30d, avg_ctr, ...},
          digest[{id,kind,title,source,summary,actionable,...}], patient_content_library[],
          seasonal_beats[], trend_signals[], regulatory_authorities[], professional_journals[]

trigger:  id, scope("merchant"|"customer"), kind, source("external"|"internal"), merchant_id,
          customer_id, payload{...kind-specific}, urgency(1-5), suppression_key, expires_at
          -- research/compliance triggers reference a digest item via payload.top_item_id

customer: customer_id, merchant_id, identity{name, phone_redacted, language_pref, age_band},
          relationship{first_visit, last_visit, visits_total, services_received[], lifetime_value},
          state("new"|"active"|"lapsed_soft"|"lapsed_hard"|"churned"), preferences{...},
          consent{opted_in_at, scope[]}
```

`trigger.payload.top_item_id` resolves against `category.digest[]`. Citations are **copied verbatim** from `digest[].source`, never generated.

---

## 6. Decisions already made (do not re-litigate without the user)

- **LLM for composition: Gemini free tier** (Google AI Studio key, `GEMINI_API_KEY`).
- **Deployment: cloud** (Render/Fly/Railway class) — must be **single instance, no autoscaling, no scale-to-zero**, because context state is in-memory and a restart mid-test fails warmup (3 consecutive healthz failures = disqualification for that slot).
- **Scope: full** — HTTP server *and* the §7 offline artifacts, sharing one composer core.

---

## 7. Planned architecture

Full plan: `C:\Users\dayuk\.claude\plans\how-to-take-on-witty-rabbit.md`

**Deterministic core, LLM as a wordsmithing layer** (deliberately the inverse of the obvious approach). Python selects the trigger, extracts the facts, picks the CTA, and validates the output. Gemini only turns an approved fact pack into a sentence. If it is slow, rate-limited, or emits an unverifiable number, a deterministic template answers instead — so the bot never times out and never fabricates.

```
vera/
  store.py         versioned context store, idempotency, healthz counts, cache epochs
  factpack.py      the specificity engine: only facts derivable from contexts, + allowed_numbers
  policy.py        tick selection: expiry, suppression_key dedup, cooldown, restraint, ranking
  compose.py       Gemini call (temp 0, JSON schema) + cache keyed on context epochs + 8s deadline
  templates.py     deterministic fallback composer (the guaranteed scoring floor — not a stub)
  validate.py      output gate: fabrication, URLs, taboos, single-CTA, language, repetition, keys
  conversation.py  reply-path state machine: auto-reply / intent / hostile / off-topic / question
  app.py           FastAPI, 5 endpoints + /v1/teardown
bot.py             §7.1 offline compose() over the same core
make_submission.py 30-line submission.jsonl from expanded/test_pairs.json
tools/local_harness.py  offline no-LLM run over all triggers; asserts validators
tools/review_page.py    emits a self-contained review.html: every composition as a WhatsApp
                        bubble, beside its trigger, validator status, and rationale
```

`review.html` is a **development tool only** — never served by the bot, no runtime cost during the judged window. It exists because tuning composition by reading JSON in a terminal hides the things that matter: how the message reads in a bubble, where line breaks fall, and whether the anchor fact survives WhatsApp's ~2-line notification preview.

### WhatsApp rendering rules the composer must respect

The message body is the entire user interface. Encode these in `validate.py`:

- The anchor fact (number, date, citation) must land in the **first sentence** — that is all the notification preview shows.
- WhatsApp markup is `*bold*` and `_italic_`. Never emit markdown (`**bold**`, `#` headings) — it renders literally and reads as a bot.
- No URLs (-3 each).
- Line breaks are the only layout tool. Two or three short blocks scan on a phone; one dense paragraph does not.
- Emoji: sparse and category-calibrated. Salons and restaurants can carry one; dentists and pharmacies in clinical-peer voice carry zero or one, and only customer-facing.

Build order: skeleton + store → factpack + templates + validate (no LLM) → reply path → Gemini layer → policy tuning → deploy → offline artifacts.

---

## 8. Scoring strategy — the four differentiators

Base score is 50, but **Phase 3 adaptation is +25 (max +5 per dimension) and Phase 4 replay is +30**. More than half the available points sit outside the surface every entrant will optimize. Assume the field lands at 35-42/50 on Phase 2 with a good single prompt; these four are what a single prompt structurally cannot do.

**1. Cross-merchant cohort intelligence.** The judge pushes all 50 merchant contexts at warmup and they stay resident. Everyone composes one merchant at a time and discards the other 49. Aggregate the corpus instead and compute real peer facts that are not in `peer_stats` — "across the 10 Delhi dentists I track, median CTR is 3.0%; 6 posted in the last 14 days, you last posted 22 days ago." Every figure is computed from pushed context, so it cannot be fabrication. `challenge-brief.md` §10 names social proof as production Vera's single biggest miss.

**2. Version-delta awareness.** Keep the previous version on every `/v1/context` write, not just the latest, and diff them. When a v2 performance snapshot lands mid-test, name the change: "since Tuesday your calls went 18 → 27." Phase 3 rewards incorporating new context and penalizes stale composition; most bots will recompose silently and never signal that they noticed. Highest point-per-line item on this list.

**3. Provenance ledger.** Every fact carries its source path (`merchant.performance.calls`, `category.digest[2].source`). `validate.py` rejects any digit absent from the ledger, so fabrication is structurally impossible rather than prompt-discouraged — and fabrication caps a case at 5/dimension. The same ledger writes the `rationale`, which the judge reads and cross-checks: naming *why this trigger and not the competing one* scores `decision_quality` directly.

**4. Judgment, including restraint.** Case Study 5 has the bot advising a restaurant *against* the obvious IPL promo, and the brief calls that "the highest signal of category understanding." Pair a contrarian check at composition with returning `actions: []` when nothing is worth sending. Six excellent messages beat twenty adequate ones on a per-message average, and the FAQ states restraint is rewarded while spam is penalized.

---

## 9. Plain language rules (how the bot talks to merchants)

**Merchant messages: English only. Customer messages: match the customer's `language_pref`.**

This split is the scoring-optimal one, confirmed against `examples/case-studies.md`:

- Both merchant-facing cases that scored a perfect **50/50 are pure English** — Case 1 (Dr. Meera, dentist) and Case 5 (Suresh, restaurant), each 10/10 on merchant fit. Merchants carry `identity.languages: ["en","hi"]`, a list of what they can read; English is a valid match.
- Hindi appears in exactly one message across all ten cases: **Case 2, Priya**, whose `language_pref` is literally `"hi-en mix"` — one Hindi clause, 10/10 merchant fit. Case 8 (Rashmi, no `hi` preference) is pure English and also 10/10.
- Customers carry a stated `language_pref`, and ignoring it is the one explicitly priced penalty: *"Treating every customer the same loses 2 points on customer fit"* (`case-studies.md:319`).

So: `send_as: "vera"` → always English. `send_as: "merchant_on_behalf"` → honor `customer.identity.language_pref` (`"en"`, `"hi-en mix"`, `"hi"`, `"te-en mix"`), keeping the Hindi light — one clause, as Case 2 does, never a full Hindi sentence.

Simple English is the answer to the same problem the briefs were solving — a shop owner reading quickly on a phone. Short everyday words, not formal English.

The reader is a shop owner, not an analyst. A salon owner in Kapra, a chemist in Malviya Nagar. Write the way a helpful person in the same trade would talk. This is not a style preference — `judge_simulator.py`'s scorer penalizes **"Exposing internal jargon to merchant: -1"**.

**Banned — our words, not theirs.** CTR, impressions, conversion rate, funnel, engagement, optimize, leverage, utilize, KPI, ROI, metrics, insights, analytics, retention rate, cohort, segment, benchmark. Also formal English: kindly, hereby, avail, as per, regarding, at your earliest convenience.

**Always translate a number into a plain outcome.** The number stays (specificity is scored); the label goes.

| Do not write | Write |
|---|---|
| "Your CTR is 2.1% vs peer median 3.0%" | "100 people see your page, 2 call you. Nearby clinics get 3." |
| "Retention is 38%" | "Only 4 of every 10 patients come back" |
| "You have 78 lapsed customers" | "78 people have not visited in 6 months" |
| "Optimize your listing" | "Add your photos and timings" |

**Trade words are fine — they are not jargon to the merchant.** Anything in `category.voice.vocab_allowed` is the merchant's own shop-floor vocabulary: a dentist knows *scaling* and *fluoride*, a salon owner knows *balayage*, a gym owner knows *PT session*. Use those freely. The rubric explicitly wants them (Category fit: "technical OK" for dentists). The ban is on **business jargon**, not **trade vocabulary**.

**Sentence shape.** Under 15 words. One idea per line. Everyday words over long ones — "use" not "utilize", "help" not "facilitate", "so" not "therefore".

**The read-aloud test.** If it sounds like a bank SMS or a marketing email, rewrite it. It should sound like a person who knows the trade, typing quickly on WhatsApp.

---

## 10. Build phases

Seven phases. Each ends in something runnable — no phase leaves the repo in a half-state. Estimates assume no debugging spiral.

**Phase 1 — Skeleton that passes warmup.** ~45 min
1. Run the dataset expander → `expanded/` (50 merchants, 200 customers, 100 triggers, `test_pairs.json`)
2. `vera/store.py` — versioned context store, keeps the previous version for delta detection (§8.2)
3. `vera/app.py` — all 5 endpoints, `/v1/tick` returns `[]`, `/v1/reply` returns a stub
4. Check: `judge_simulator.py` with `TEST_SCENARIO="warmup"` prints all PASS and the right context counts

**Phase 2 — Real messages, no LLM.** ~3 hours. The scoring floor.
1. `vera/factpack.py` — pull only facts that exist in the contexts, each tagged with where it came from
2. `vera/cohort.py` — aggregate all 50 merchants for real peer comparisons (§8.1)
3. `vera/templates.py` — one plain-language message builder per trigger kind
4. `vera/validate.py` — block invented numbers, URLs, taboo words, jargon, multiple CTAs, repeats
5. `tools/local_harness.py` — compose for all 100 triggers, assert zero validator failures

**Phase 3 — Conversations.** ~2 hours. Worth up to +30.
1. `vera/conversation.py` — per-conversation memory and mode
2. Classify each incoming reply: auto-reply / commitment / hostile / off-topic / question
3. Wire the three exits: end on repeated auto-reply, switch to action mode on commitment, end politely on hostile
4. Tests asserting the literal keyword lists from §4
5. Check: simulator's `auto_reply_hell`, `intent_transition`, `hostile` all PASS with zero LLM calls

**Phase 4 — Gemini layer.** ~2 hours
1. `vera/compose.py` — REST call, temperature 0, JSON schema out
2. Cache keyed on context versions; 8s deadline; fall back to the Phase 2 template on slow or bad output
3. Send every LLM output through `validate.py` — one retry, then template
4. Check: side-by-side template vs Gemini in `review.html`; keep the LLM only where it clearly wins

**Phase 5 — Review page and tuning.** ~2 hours
1. `tools/review_page.py` — every message as a WhatsApp bubble with its trigger, checks, and rationale
2. `vera/policy.py` — which triggers actually fire: expiry, dedup, cooldown, restraint
3. Run `full_evaluation` with a Gemini key, fix the lowest-scoring dimension, repeat
4. Target: average 40/50 or better

**Phase 6 — Deploy.** ~1 hour
1. `requirements.txt`, `Dockerfile`, `render.yaml` — single instance, no autoscaling, no scale-to-zero
2. `GEMINI_API_KEY` as a secret; health check on `/v1/healthz`
3. Re-run the simulator against the public URL

**Phase 7 — Submission artifacts.** ~1 hour
1. `bot.py` — the offline `compose()` over the same core
2. `make_submission.py` → `submission.jsonl`, 30 lines
3. `README.md` — approach, tradeoffs, what extra context would have helped

---

## 11. Tech stack

| Layer | Choice | Version present | Why |
|---|---|---|---|
| Language | Python | 3.12.10 | The brief's contract and `judge_simulator.py` are Python; the offline `bot.py` artifact must be importable Python |
| HTTP server | FastAPI | 0.111.0 | The testing brief's own reference skeleton is FastAPI; request validation comes free |
| ASGI server | uvicorn | 0.29.0 | Single worker, single instance — state is in-memory |
| Validation | pydantic v2 | 2.12.5 | Request/response models for the 5 endpoints; strict schemas keep malformed-response penalties at zero |
| HTTP client | httpx | 0.27.0 | Async Gemini calls inside the tick deadline, and `ASGITransport` for endpoint tests without a live server |
| LLM | Gemini free tier, called over its REST API with httpx | — | No SDK dependency; `temperature: 0` plus a JSON response schema |
| Tests | pytest | 8.2.0 | Validator, fact-pack, and reply-path assertions |
| Config | environment variables (`GEMINI_API_KEY`), python-dotenv for local runs | 1.0.1 | Key never lands in the repo |
| Persistence | none — in-memory dicts | — | The brief permits it; it forces the single-instance deploy constraint in §6 |
| Deploy | Docker on Render (or Fly/Railway) | — | Single instance, no autoscaling, no scale-to-zero |

Deliberately **not** used: no database, no vector store, no LangChain or agent framework, no Gemini SDK. Every one of those would add a dependency, a failure mode, and cold-start latency without earning a point on the rubric.

---

## 12. Code conventions

- **Descriptive names over abbreviations.** `merchant_context`, not `mctx`. `suppression_key`, not `sup_key`. The dataset's own field names are the vocabulary — mirror them exactly so a reader can grep from JSON to code.
- **Minimal surface. No dead code.** No unused imports, parameters, helpers, or "might need this later" branches. If it is not on a live path, it does not get written.
- **No commented-out code.** If it is not needed, delete it. Version history is the archive.
- **Comments only where the *why* is not obvious** — a non-obvious rubric constraint, a spec contradiction, a deliberate workaround. Never a comment restating what the line does.
- **No speculative generality.** Build for the 5 categories and the trigger kinds actually in the dataset; do not write plugin layers for hypothetical ones.
- **Fail loudly in development, degrade quietly in production** — the compose path falls back to a template rather than raising, but the offline harness asserts hard so problems surface before the test window.

---

## 13. Git workflow and session log

**Commit after every substep. Push after every phase, and ask before pushing.**

### Commit message style

A title line, then at most one paragraph of 4-5 lines. No bullet lists, no multi-section bodies.

```
Add versioned context store

Keeps the previous version of every context alongside the current one so
the composer can name what changed when the judge pushes an update. Chose
in-memory dicts over SQLite because the test window is a single process
and the brief permits it.
```

The title says what changed. The paragraph says **why**, and names any decision a reader would otherwise question.

### Rhythm

| When | Do |
|---|---|
| Substep finished | Write the `session.md` entry, then commit both the code and the entry together |
| Phase finished | Ask the user, then push |
| Never | Push without asking; commit a `.env` or an API key |

### `session.md`

A running log of the build, one entry per substep, written **at the same time as the commit** — same content discipline, more room for reasoning. Each entry records what was done, why that approach, what was rejected and why, and anything surprising found along the way. It is the reasoning trail the commit messages are too short to hold.

Entry format:

```markdown
## Phase N, substep M — <what it was>
*Committed: <short sha>*

**Done.** What now exists and works.

**Why this way.** The decision and its reasoning.

**Rejected.** The alternative and why it lost.

**Surprises.** Anything the briefs, the dataset, or the simulator revealed that changed the plan. Omit if none.
```

The file starts empty. The first entry is written when Phase 1 substep 1 is done.

---

## 14. Commands

```bash
# Expand seeds -> 50 merchants / 200 customers / 100 triggers / test_pairs.json
python dataset/generate_dataset.py --seed-dir dataset --out expanded

# Run the bot locally
uvicorn vera.app:app --host 0.0.0.0 --port 8080

# Rehearse the judge (edit BOT_URL / LLM_PROVIDER / LLM_API_KEY / TEST_SCENARIO in the file first)
python judge_simulator.py
```

Environment: Windows 11, Python 3.12.10, `fastapi` and `uvicorn` already installed. No LLM API keys are currently set in the environment.

---

## 15. Working agreements

- Do not commit or deploy without being asked.
- No real merchant outreach; all data here is synthetic. Do not scrape magicpin or Google.
- Case-study bodies in `examples/case-studies.md` are a **north star, not a copy source** — the judge runs a similarity check and penalizes near-duplicates.
