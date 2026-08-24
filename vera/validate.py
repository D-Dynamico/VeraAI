"""The output gate. Nothing reaches the judge without passing every check here.

Each failure names the rule it broke, so the harness can report causes rather
than a pass/fail count, and so the LLM layer can be re-prompted with the reason.
"""

import re
from typing import Any

from vera.conversation import ACTION_WORDS, QUALIFYING_PHRASES
from vera.factpack import FactPack, numbers_in

# Business words the merchant never uses about their own shop. The scorer
# penalises "exposing internal jargon to merchant".
JARGON = (
    "ctr", "click-through", "click through", "ad impressions", "page impressions", "conversion rate", "funnel",
    "engagement rate", "optimize", "optimise", "leverage", "utilize", "utilise", "kpi",
    "roi", "metrics", "analytics", "retention rate", "cohort", "segment", "benchmark",
    "peer median", "uplift",
)

# Formal register that reads like a bank SMS rather than a person.
STIFF = ("kindly", "hereby", "avail", "as per", "at your earliest convenience", "dear sir", "dear madam")

# Romanised Hindi that must not appear in a merchant-facing message.
HINDI_MARKERS = (
    "aap", "hain", "kar", "nahi", "kya", "mein", "hai", "liye", "rakh", "din", "gaye", "chahiye", "karte",
)

URL_PATTERN = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(com|in|org|net|co)\b", re.IGNORECASE)
MARKDOWN_PATTERN = re.compile(r"\*\*|^#{1,6}\s|\[.+\]\(.+\)", re.MULTILINE)
# Payload values are enum codes ("postcard_or_phone_call", "kids_yoga_post").
# One reaching a body has leaked a field name at the merchant; it has happened
# twice, so it is a rule rather than a habit.
RAW_CODE_PATTERN = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
DEVANAGARI_PATTERN = re.compile(r"[ऀ-ॿ]")
PROPER_NOUN_PATTERN = re.compile(r"\b[A-Z][a-zA-Z]{2,}")
CTA_PATTERN = re.compile(r"\breply\b|\bwant me to\b|\bshall i\b|\bwould you like\b", re.IGNORECASE)


# Periods that end an abbreviation, not a sentence. "Dr. Meera" is one sentence.
ABBREVIATIONS = ("Dr", "Mr", "Mrs", "Ms", "Rs", "No", "vs", "p", "Oct", "Nov", "Dec", "Jan", "Feb")
_ABBREVIATION_MARK = "\x00"


def _sentences(body: str) -> list[str]:
    protected = body
    for abbreviation in ABBREVIATIONS:
        protected = protected.replace(f"{abbreviation}.", f"{abbreviation}{_ABBREVIATION_MARK}")
    parts = re.split(r"(?<=[.?!])\s+|\n+", protected)
    return [part.replace(_ABBREVIATION_MARK, ".").strip() for part in parts if part.strip()]


def speaks_hindi(pack: FactPack) -> bool:
    """Whether this customer's stated preference asks for a Hindi clause.

    Public and single: `templates` decides whether to add the clause and this
    module decides whether its absence is a failure. Two copies of the rule can
    drift into rejecting exactly what the composer just produced.
    """
    language = pack.customer_language.lower()
    return language.startswith("hi") or "hi-en" in language


def _has_hindi(body: str) -> bool:
    words = set(re.findall(r"[a-z]+", body.lower()))
    return bool(DEVANAGARI_PATTERN.search(body)) or bool(words & set(HINDI_MARKERS))


def opens_on_an_anchor(opening: str) -> bool:
    """Something checkable in the first line: a number, a name, or a direct question.

    The salutation is stripped first, or the merchant's own name would satisfy it.
    """
    _, _, after_salutation = opening.partition(",")
    after_salutation = after_salutation or opening
    return bool(
        numbers_in(after_salutation)
        or PROPER_NOUN_PATTERN.search(after_salutation)
        or after_salutation.strip().endswith("?")
    )


def _is_ask(sentence: str) -> bool:
    return bool(CTA_PATTERN.search(sentence)) or sentence.strip().endswith("?")


def check(
    body: str,
    pack: FactPack | None,
    category: dict[str, Any],
    already_sent: list[str] | None = None,
    action_mode: bool = False,
) -> list[str]:
    """Every rule this body breaks. An empty list means it may be sent."""
    failures = []
    lowered = body.lower()

    if not body.strip():
        return ["empty body"]

    if pack:
        for number in numbers_in(body):
            if number not in pack.allowed_numbers:
                failures.append(f"unlicensed number {number!r} — not traceable to any context")

    if URL_PATTERN.search(body):
        failures.append("body contains a URL")

    if MARKDOWN_PATTERN.search(body):
        failures.append("body contains markdown, which WhatsApp renders literally")

    leaked = RAW_CODE_PATTERN.search(body)
    if leaked:
        failures.append(f"raw payload code {leaked.group()!r} — convert it to plain words")

    for taboo in category.get("voice", {}).get("vocab_taboo", []):
        if taboo.lower() in lowered:
            failures.append(f"category taboo word {taboo!r}")

    for word in JARGON:
        if word in lowered:
            failures.append(f"jargon {word!r}")

    for word in STIFF:
        if word in lowered:
            failures.append(f"stiff phrasing {word!r}")

    if pack and pack.is_customer_facing:
        if speaks_hindi(pack) and not _has_hindi(body):
            failures.append("customer prefers a Hindi mix but the body is pure English")
    elif pack and _has_hindi(body):
        failures.append("merchant messages are English only")

    if action_mode:
        # The replay grader greps for these literally; see CLAUDE.md §4.
        if not any(word in lowered for word in ACTION_WORDS):
            failures.append(f"action reply carries none of {ACTION_WORDS}")
        for phrase in QUALIFYING_PHRASES:
            if phrase in lowered:
                failures.append(f"action reply still qualifying: {phrase!r}")

    sentences = _sentences(body)
    if sentences and not opens_on_an_anchor(sentences[0]):
        failures.append("no anchor fact in the opening sentence")

    # The closing sentence must be the ask, and no earlier sentence may also ask.
    if sentences:
        if not _is_ask(sentences[-1]):
            failures.append("the last sentence is not the call to action")
        early_asks = [sentence for sentence in sentences[:-1] if CTA_PATTERN.search(sentence)]
        if early_asks:
            failures.append(f"{len(early_asks) + 1} calls to action, expected one")

    normalised = re.sub(r"\s+", " ", body.strip().lower())
    for previous in already_sent or []:
        if re.sub(r"\s+", " ", previous.strip().lower()) == normalised:
            failures.append("body repeats a message already sent in this conversation")

    return failures
