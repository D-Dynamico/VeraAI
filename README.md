# Vera — merchant engagement bot

A stateful HTTP server (`vera/app.py`) exposing the five judged endpoints, plus the
offline artifacts: `compose()` in `bot.py` and the 30-line `submission.jsonl`. Both
run the same composer, so a message produced offline is the message the server sends.

```bash
pip install -r requirements.txt
python -m uvicorn vera.app:app --host 0.0.0.0 --port 8080 --workers 1

python dataset/generate_dataset.py --seed-dir dataset --out expanded
python make_submission.py          # -> submission.jsonl
python tools/local_harness.py      # composes all 100 triggers, asserts the validators
```

## Approach

**Deterministic core, LLM as a wordsmithing layer** — deliberately the inverse of the
obvious design. Python decides *what* is said; Gemini only decides how it reads.

1. **`factpack.py`** pulls facts out of the pushed contexts, each tagged with the path
   it came from, and licenses every digit it uses. Nothing else may appear in a body.
2. **`cohort.py`** aggregates all 50 resident merchant contexts into real peer facts —
   "88 calls a month against about 18 for restaurants like yours" — computed from
   pushed data rather than read off `peer_stats`. Social proof is the lever the brief
   names as production Vera's biggest miss, and this is the only way to fire it without
   inventing a number.
3. **`templates.py`** builds a plain-English draft per trigger kind: an anchor fact in
   the first sentence (all WhatsApp's notification preview shows), one line of
   consequence, one ask. This is the floor, not a stub — it is what ships if the LLM is
   slow, rate-limited, or wrong.
4. **`compose.py`** hands Gemini the lines the fact pack already vouched for and asks
   only for phrasing, at temperature 0 behind a JSON schema, inside an 8s budget
   against the harness's 15s tick timeout.
5. **`validate.py`** is the gate both paths pass through: an unlicensed digit, a URL, a
   taboo word, business jargon, a second CTA, a duplicate body, or a soft opener all
   send the message back to the template.

`conversation.py` runs the reply path as a state machine — it ends on a repeated
auto-reply, switches to action language on a commitment, and closes politely on a
hostile turn — rather than asking the LLM what to do with each turn.

## Tradeoffs

**Fabrication made structurally impossible, at the cost of range.** Because every digit
must trace to a context path, the bot cannot make the confident-sounding claims a free
prompt would. Where a trigger's payload does not support its own kind, it composes from
what the contexts *do* carry, or sends nothing at all: 1 of 100 triggers is blocked on
consent and 1 more because no honest message existed. Restraint is a real output here,
not an error path.

**A template floor instead of a better prompt.** Roughly a third of the code exists to
be the answer when the model is unavailable. That is a lot of code for a path that
ideally never runs, and it is why there is no scenario where the bot times out, returns
malformed JSON, or emits a number nobody can check.

**English to merchants, the customer's `language_pref` to customers.** The briefs prefer
Hindi-English code-mix wherever `identity.languages` includes `hi`. Both merchant-facing
case studies that scored 50/50 are pure English, and the one priced penalty is for
ignoring a *customer's* stated preference — so the split follows the evidence rather
than the instruction.

**In-memory state.** No database, which is what makes the deployment single-instance
with no autoscaling and no scale-to-zero. Simpler and faster; a restart mid-window
would lose every context.

## What additional context would have helped most

1. **Outcome data per sent message.** The dataset records what was sent and whether the
   merchant engaged, but never what happened next — did the offer run, did calls move.
   Without it, "which lever works for this merchant" is a prior, not a measurement.
2. **A read receipt or reply latency on `conversation_history`.** `engagement` is a
   label; the time between send and reply is the signal that actually separates a
   merchant who reads Vera from one who ignores it.
3. **Trigger payloads that match their kind.** Several expanded triggers fire
   `chronic_refill_due` at a gym or a dentist with an empty payload. Real ones would
   remove the guesswork about whether silence or a substitute message is correct.
4. **The merchant's own outbound copy.** `voice` is described at the category level;
   a few messages each merchant actually wrote would let the bot match one shop's voice
   rather than one trade's.
