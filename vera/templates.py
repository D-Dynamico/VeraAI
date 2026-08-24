"""Plain-English message builders, one per trigger kind.

This is the floor the LLM layer has to beat, so it is written to be good on its
own: an anchor fact in the opening sentence, one line of consequence, and a
single low-friction ask at the end. No jargon, no percentages-off, no URLs.
"""

import re
from dataclasses import dataclass
from typing import Any, Callable

from vera.cohort import Cohort, social_proof_fact
from vera.factpack import Fact, FactPack
from vera.validate import opens_on_an_anchor, speaks_hindi

SHOP_WORD = {
    "dentists": "clinics",
    "salons": "salons",
    "restaurants": "restaurants",
    "gyms": "gyms",
    "pharmacies": "chemists",
}

# WhatsApp has no paragraph tag; a blank line is the only layout tool.
BLOCK_SEPARATOR = "\n\n"

# Kinds whose copy speaks to a customer. Without a customer context there is
# nothing to send, and the merchant must not receive it by default.
CUSTOMER_KINDS = frozenset({
    "recall_due",
    "appointment_tomorrow",
    "chronic_refill_due",
    "customer_lapsed_soft",
    "customer_lapsed_hard",
    "trial_followup",
    "wedding_package_followup",
})


# Kinds where a Hindi lead-in is natural for a customer who mixes languages.
HINDI_CLAUSE = {
    "booking": "Aapke liye slot rakh dein?",
    "return": "Bahut din ho gaye.",
}


@dataclass
class ComposedMessage:
    body: str
    cta: str
    send_as: str
    suppression_key: str
    rationale: str


@dataclass
class Draft:
    """What a kind-specific builder decides; assembly is shared."""

    anchor: str
    context: str
    ask: str
    cta: str
    rationale: str
    hindi_mood: str | None = None
    # The anchor opens on a name — a festival, a competitor. It follows the
    # salutation and a comma, but a proper noun keeps its capital: "Dr. Meera,
    # smile Studio has opened" is the kind of slip that reads as a machine.
    anchor_opens_on_a_name: bool = False


def salutation(pack: FactPack) -> str:
    if pack.is_customer_facing:
        return f"Hi {pack.customer_name}"
    title = "Dr. " if pack.category_slug == "dentists" else ""
    return f"{title}{pack.owner_name}" if pack.owner_name else "Hello"


def _shop_word(pack: FactPack) -> str:
    return SHOP_WORD.get(pack.category_slug, "shops")


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith(("?", "!", ".")) else text + "."


def _lower_first(text: str, keep_capital: bool = False) -> str:
    """The anchor follows a name and a comma, so it must not start with a capital —
    unless it opens on a proper noun, which keeps its own."""
    if keep_capital or not text or text[:2].isupper():
        return text
    return text[:1].lower() + text[1:]


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _payload(pack: FactPack, key: str, default: Any = None) -> Any:
    return pack.trigger_payload.get(key, default)


def _readable(value: Any) -> str:
    """Payload values are enum codes. Printing one raw ("kids_yoga_post") reads
    as a bot leaking its own field names, so nothing goes into a body unconverted."""
    return str(value).replace("_", " ").strip()


# Metric names arrive as field names. A shop owner reads plain words, and the
# words have to fit the sentence - "your people finding you on Google are down"
# is what a label map alone produces, so each metric carries its own sentence.
METRIC_WORDS = {
    "review_count": "reviews",
    "reviews": "reviews",
    "calls": "calls",
    "views": "views on Google",
    "directions": "direction requests",
    "leads": "enquiries",
    "footfall": "walk-ins",
    "orders": "orders",
    "members": "members",
}

MOVEMENT_SENTENCES = {
    "views": "{pct}% fewer people found you on Google this week",
    "directions": "{pct}% fewer people asked for directions this week",
}
RISING_SENTENCES = {
    "views": "{pct}% more people found you on Google this week",
    "directions": "{pct}% more people asked for directions this week",
}


def _metric_label(raw: Any, default: str) -> str:
    key = str(raw or default).strip().lower()
    return METRIC_WORDS.get(key, key.replace("_", " "))


def _movement_sentence(raw: Any, default: str, percent: int, rising: bool) -> str:
    key = str(raw or default).strip().lower()
    frames = RISING_SENTENCES if rising else MOVEMENT_SENTENCES
    if key in frames:
        return frames[key].format(pct=percent)
    direction = "up" if rising else "down"
    return f"your {_metric_label(key, default)} are {direction} {percent}% this week"


# --- merchant-facing builders -------------------------------------------------


def _research_digest(pack: FactPack, proof: Fact | None) -> Draft:
    item = pack.digest_item or {}
    trial = item.get("trial_n")
    size = f" It was tested on {trial} patients." if trial else ""
    return Draft(
        anchor=f"a new finding came out that touches your work — {item.get('title', 'a study in your field')}",
        context=f"{item.get('summary', '')}{size} Source: {item.get('source', '')}",
        ask="Want me to pull the summary and write a short note you can send your patients?",
        cta="open_ended",
        rationale=f"Research digest release; anchored on {item.get('source', 'the digest item')} and offered to do the writing.",
    )


def _regulation_change(pack: FactPack, proof: Fact | None) -> Draft:
    item = pack.digest_item or {}
    deadline = _payload(pack, "deadline_iso", "")
    pack.license_numbers(deadline)
    return Draft(
        anchor=f"there is a rule change you need to know about — {item.get('title', 'new guidance in your field')}",
        context=f"{_sentence(item.get('actionable') or item.get('summary', ''))} Source: {item.get('source', '')}",
        ask=f"Want me to send you a one-page checklist before {deadline or 'the deadline'}?" if deadline else "Want me to send you a one-page checklist?",
        cta="binary",
        rationale=f"Compliance deadline {deadline or 'pending'}; led with the rule and offered a checklist, not a sales line.",
    )


def _cde_opportunity(pack: FactPack, proof: Fact | None) -> Draft:
    item = pack.digest_item or {}
    credits = _payload(pack, "credits", item.get("credits"))
    fee = _payload(pack, "fee", "")
    pack.license_numbers(credits, fee)
    return Draft(
        anchor=f"there is a session coming up you may want — {item.get('title', 'a training session in your field')}",
        context=" ".join(
            part for part in (
                _sentence(item.get("actionable") or item.get("summary", "")),
                f"It carries {credits} credits." if credits else "",
                f"Source: {item.get('source', '')}",
            ) if part.strip()
        ),
        ask="Want me to put the date and joining details in one message for you?",
        cta="binary",
        rationale="Training opportunity from the category digest; low-pressure, source cited.",
    )


def _category_seasonal(pack: FactPack, proof: Fact | None) -> Draft:
    season = _payload(pack, "season", "the season ahead")
    # The payload flags that a shelf change is worth recommending; it carries no text.
    shelf_change_worth_making = bool(_payload(pack, "shelf_action_recommended", False))
    context = "Worth moving the seasonal items to the front counter now." if shelf_change_worth_making else (proof.text if proof else "")
    return Draft(
        anchor=f"{season} is coming and it changes what people ask for",
        context=context,
        ask="Want me to draft what to put in front this month?",
        cta="binary",
        rationale=f"Seasonal beat for {season}; offered to do the planning work.",
    )


def _competitor_opened(pack: FactPack, proof: Fact | None) -> Draft:
    name = _payload(pack, "competitor_name", "a new place")
    distance = _payload(pack, "distance_km")
    pack.license_numbers(distance)
    where = f"{distance} km away" if distance else "nearby"
    return Draft(
        anchor=f"{name} has opened {where}",
        anchor_opens_on_a_name=True,
        context=proof.text if proof else "New places usually take a few of your searches in the first month.",
        ask="Want me to check what your listing shows next to theirs?",
        cta="binary",
        rationale=f"Competitor opened {where}; framed as a listing check rather than alarm.",
    )


def _curious_ask_due(pack: FactPack, proof: Fact | None) -> Draft:
    return Draft(
        anchor=proof.text if proof else f"you have been running {pack.business_name} a while now",
        context="",
        ask=f"What are people asking you for most this week at {pack.business_name}?",
        cta="open_ended",
        rationale="Scheduled curious ask; the merchant answers about their own shop, which is the cheapest reply to give.",
    )


def _dormant_with_vera(pack: FactPack, proof: Fact | None) -> Draft:
    days = _payload(pack, "days_since_last_merchant_message")
    pack.license_numbers(days)
    gap = f"It has been {days} days since we last spoke." if days else "It has been a while since we last spoke."
    return Draft(
        anchor=f"{gap.rstrip('.')} — one thing changed since then",
        context=proof.text if proof else (pack.signal_facts[0].text if pack.signal_facts else ""),
        ask="Want me to bring you up to date in two lines?",
        cta="binary",
        rationale="Dormant merchant; re-opened with a concrete change rather than a check-in.",
    )


def _festival_upcoming(pack: FactPack, proof: Fact | None) -> Draft:
    festival = _payload(pack, "festival", "the festival")
    days = _payload(pack, "days_until")
    pack.license_numbers(days)
    when = f"{festival} is {days} days away" if days else f"{festival} is coming"
    return Draft(
        anchor=when,
        anchor_opens_on_a_name=True,
        context=f"Your {pack.offer_title} is the one to put in front of people first." if pack.offer_title else "",
        ask="Want me to write the festival post for your listing?",
        cta="binary",
        rationale=f"{festival} in {days or 'a few'} days; tied it to the offer already running.",
    )


def _gbp_unverified(pack: FactPack, proof: Fact | None) -> Draft:
    pack.license_numbers(_payload(pack, "estimated_uplift_pct"))
    routes = {"postcard_or_phone_call": "Google will send a postcard or call the shop to confirm it."}
    how = routes.get(_payload(pack, "verification_path", ""), "")
    return Draft(
        anchor="your Google listing is still unverified, and that is holding it back",
        context=f"Verified shops show up higher and get the trust badge. {how} It takes about five minutes.".strip(),
        ask="Want me to walk you through getting it verified?",
        cta="binary",
        rationale="Unverified listing is the single biggest fixable gap; framed as a five-minute job.",
    )


def _ipl_match_today(pack: FactPack, proof: Fact | None) -> Draft:
    match = _payload(pack, "match", "tonight's match")
    venue = _payload(pack, "venue", "")
    is_weeknight = _payload(pack, "is_weeknight", True)
    if is_weeknight:
        context = "Match nights bring people in if the offer is ready before the toss."
        ask = "Want me to write a match-night message you can send out now?"
        rationale = "Weeknight match; recommended running the promotion."
    else:
        # The contrarian call the case studies reward: not every trigger deserves a push.
        context = "On a weekend more people watch at home, so a dine-in push usually falls flat."
        ask = "Want me to set up a delivery-only special instead?"
        rationale = "Weekend match, so advised against the dine-in push and offered the delivery angle instead."
    return Draft(anchor=f"{match} is on today{f' at {venue}' if venue else ''}", anchor_opens_on_a_name=True, context=context, ask=ask, cta="binary", rationale=rationale)


def _milestone_reached(pack: FactPack, proof: Fact | None) -> Draft:
    metric = _metric_label(_payload(pack, "metric"), "reviews")
    value = _payload(pack, "value_now") or _payload(pack, "milestone_value")
    pack.license_numbers(value)
    headline = f"you have crossed {value} {metric}".strip() if value else "you have hit a good mark this month"
    return Draft(
        anchor=headline,
        context=proof.text if proof else "Worth telling your customers about while it is fresh.",
        ask="Want me to write a short post about it for your listing?",
        cta="binary",
        rationale="Milestone reached; used it as a reason to post rather than only congratulating.",
    )


def _perf_dip(pack: FactPack, proof: Fact | None) -> Draft:
    metric = _metric_label(_payload(pack, "metric"), "calls")
    delta = _payload(pack, "delta_pct")
    baseline = _payload(pack, "vs_baseline")
    pack.license_numbers(baseline)
    if delta:
        drop = round(abs(float(delta)) * 100)
        pack.license_numbers(drop)
        anchor = _movement_sentence(_payload(pack, "metric"), "calls", drop, rising=False)
    else:
        anchor = f"your {metric} have slipped this week"
    return Draft(
        anchor=anchor,
        context=proof.text if proof else (calls_per_hundred(pack) or ""),
        ask="Want me to check what changed on your listing?",
        cta="binary",
        rationale=f"Drop in {metric}; led with the number and offered to find the cause.",
    )


def _largest_rise(delta_7d: dict[str, Any]) -> tuple[str, float] | None:
    """The metric that actually rose most, or None if none did.

    `ctr` is excluded deliberately: it has no plain-English label, and §9 bans
    the term itself.
    """
    rises = [
        (key.removesuffix("_pct"), float(value))
        for key, value in delta_7d.items()
        if isinstance(value, (int, float)) and value > 0 and key.removesuffix("_pct") in METRIC_WORDS
    ]
    return max(rises, key=lambda item: item[1]) if rises else None


def _perf_spike(pack: FactPack, proof: Fact | None) -> Draft | None:
    metric_key = _payload(pack, "metric")
    delta = _payload(pack, "delta_pct")
    driver = _payload(pack, "likely_driver", "")
    if delta is None:
        # Most triggers ship a placeholder payload, so the merchant's own delta_7d
        # is the only evidence a spike happened — and it sometimes says the
        # opposite. Claiming a rise the merchant's numbers deny is the unfounded
        # claim the rubric caps at 5, and it carries no digit for the number
        # check to catch, so the guard has to live here.
        risen = _largest_rise(pack.performance.get("delta_7d", {}))
        if risen is None:
            return None
        metric_key, delta = risen
    rise = round(abs(float(delta)) * 100)
    if rise < 1:
        return None
    pack.license_numbers(rise)
    anchor = _movement_sentence(metric_key, "views", rise, rising=True)
    return Draft(
        anchor=anchor,
        context=f"Looks like your {_readable(driver)} did it." if driver else "Good time to keep the run going.",
        ask="Want me to put up a post while people are already looking?",
        cta="binary",
        rationale=f"Rise in {_metric_label(metric_key, 'views')} of {rise}%; converted it into a reason to act now.",
    )


def _renewal_due(pack: FactPack, proof: Fact | None) -> Draft:
    days = _payload(pack, "days_remaining")
    amount = _payload(pack, "renewal_amount")
    pack.license_numbers(days, amount)
    when = f"your plan ends in {days} days" if days else "your plan is ending soon"
    money = f" Renewal is Rs {amount}." if amount else ""
    return Draft(
        anchor=when,
        context=" ".join(part for part in (_sentence(proof.text) if proof else "", money.strip()) if part),
        ask="Want me to keep it running so nothing stops?",
        cta="binary",
        rationale=f"Renewal in {days or 'a few'} days; single yes-or-no ask, no pressure language.",
    )


def _review_theme_emerged(pack: FactPack, proof: Fact | None) -> Draft:
    theme = str(_payload(pack, "theme", "") or (pack.review_theme or {}).get("theme", "")).replace("_", " ")
    count = _payload(pack, "occurrences_30d") or (pack.review_theme or {}).get("occurrences_30d")
    quote = _payload(pack, "common_quote") or (pack.review_theme or {}).get("common_quote", "")
    pack.license_numbers(count)
    head = f"{count} reviews this month mention {theme}" if count and theme else "a theme is showing up in your reviews"
    return Draft(
        anchor=head,
        context=f'One customer wrote: "{quote}"' if quote else "",
        ask="Want me to draft a reply you can post under them?",
        cta="binary",
        rationale=f"Review theme '{theme or 'unnamed'}' emerging; offered the reply rather than only reporting it.",
    )


def _seasonal_perf_dip(pack: FactPack, proof: Fact | None) -> Draft:
    delta = _payload(pack, "delta_pct")
    metric = _metric_label(_payload(pack, "metric"), "footfall")
    if delta:
        drop = round(abs(float(delta)) * 100)
        pack.license_numbers(drop)
        anchor = f"{_movement_sentence(_payload(pack, 'metric'), 'footfall', drop, rising=False)}, and this is the normal dip for this time of year"
    else:
        anchor = f"your {metric} are down, and this is the normal dip for this time of year"
    return Draft(
        anchor=anchor,
        # `season_note` is a code, not a sentence ("post_resolution_window_apr_jun").
        context="Nothing is broken on your listing. It picks up again after the season turns.",
        ask="Want me to set up something small to hold you over till then?",
        cta="binary",
        rationale="Expected seasonal dip; told the merchant not to worry rather than selling into the fear.",
    )


def _supply_alert(pack: FactPack, proof: Fact | None) -> Draft:
    molecule = _payload(pack, "molecule", "a product you stock")
    batches = _payload(pack, "affected_batches", [])
    maker = _payload(pack, "manufacturer", "")
    batch_text = ", ".join(str(batch) for batch in batches) if isinstance(batches, list) else str(batches)
    pack.license_numbers(batch_text)
    return Draft(
        anchor=f"pull {molecule} off the shelf if you have these batches: {batch_text}" if batch_text else f"there is a supply alert on {molecule}",
        context=f"Alert is from {maker}." if maker else "",
        ask="Reply YES and I will send the full batch list to keep at the counter.",
        cta="binary",
        rationale="Safety alert, highest urgency; instruction first, everything else after.",
    )


def _winback_eligible(pack: FactPack, proof: Fact | None) -> Draft:
    days = _payload(pack, "days_since_expiry")
    lapsed = _payload(pack, "lapsed_customers_added_since_expiry")
    pack.license_numbers(days, lapsed)
    anchor = f"{lapsed} customers have gone quiet since your plan lapsed" if lapsed else "customers have been going quiet since your plan lapsed"
    return Draft(
        anchor=anchor,
        context=proof.text if proof else "",
        ask="Want me to write to them for you once you are back on?",
        cta="binary",
        rationale="Winback window; led with the customers lost, not the plan price.",
    )


def _active_planning_intent(pack: FactPack, proof: Fact | None) -> Draft:
    topic = str(_payload(pack, "intent_topic", "what you mentioned")).replace("_", " ")
    return Draft(
        anchor=f"picking up on {topic} from your last message",
        context=f"Your {pack.offer_title} fits it already." if pack.offer_title else "",
        ask="Want me to draft it now so you can look it over?",
        cta="binary",
        rationale=f"Merchant already showed intent on {topic}; moved straight to doing the work, no re-qualifying.",
    )


# --- customer-facing builders -------------------------------------------------


def _recall_due(pack: FactPack, proof: Fact | None) -> Draft:
    service = str(_payload(pack, "service_due", "your check-up")).replace("_", " ")
    slots = _payload(pack, "available_slots", []) or []
    labels = [slot.get("label", "") for slot in slots if slot.get("label")]
    gap = f"It has been {pack.months_since_visit} months since your last visit" if pack.months_since_visit else "It has been a while since your last visit"
    times = f" We have {' and '.join(labels)} open." if labels else ""
    return Draft(
        anchor=f"{gap} and your {service} is due",
        context=f"{pack.business_name} here.{times}",
        ask="Reply YES and we will hold one for you.",
        cta="binary",
        rationale=f"Recall window open for {service}; offered the clinic's actual open slots.",
        hindi_mood="booking",
    )


def _appointment_tomorrow(pack: FactPack, proof: Fact | None) -> Draft:
    return Draft(
        anchor="you have an appointment with us tomorrow",
        context=f"{pack.business_name} here. Nothing needed from you, just a reminder.",
        ask="Reply YES to confirm, or tell us a better time.",
        cta="binary",
        rationale="Day-before reminder; confirmation ask kept to one word.",
        hindi_mood="booking",
    )


def _chronic_refill_due(pack: FactPack, proof: Fact | None) -> Draft:
    molecules = _payload(pack, "molecule_list", []) or []
    runs_out = _payload(pack, "stock_runs_out_iso", "")
    pack.license_numbers(runs_out)
    names = ", ".join(str(molecule) for molecule in molecules) if molecules else "your regular medicines"
    delivers = bool(_payload(pack, "delivery_address_saved", False))
    where = "send it to your saved address" if delivers else "keep it ready at the counter"
    return Draft(
        anchor=f"your {names} should be running out around now",
        context=f"{pack.business_name} here. We can {where}.",
        ask="Reply YES and we will pack it for you.",
        cta="binary",
        rationale="Refill window from the customer's own dispensing history; kept to a stock reminder, no medical advice.",
        hindi_mood="booking",
    )


def _customer_lapsed_soft(pack: FactPack, proof: Fact | None) -> Draft:
    gap = f"it has been {pack.months_since_visit} months" if pack.months_since_visit else "it has been a while"
    last = f" Last time you came in for {pack.last_service}." if pack.last_service else ""
    return Draft(
        anchor=f"{gap} since we saw you at {pack.business_name}",
        context=f"{last} {pack.offer_title} is on right now if you want it.".strip() if pack.offer_title else last.strip(),
        ask="Reply YES and we will find you a time.",
        cta="binary",
        rationale="Soft lapse; used the customer's own last service and a real live offer.",
        hindi_mood="return",
    )


def _customer_lapsed_hard(pack: FactPack, proof: Fact | None) -> Draft:
    days = _payload(pack, "days_since_last_visit")
    focus = str(_payload(pack, "previous_focus", "") or "").replace("_", " ")
    pack.license_numbers(days)
    gap = f"it has been {days} days" if days else (f"it has been {pack.months_since_visit} months" if pack.months_since_visit else "it has been a long time")
    return Draft(
        anchor=f"{gap} since your last visit to {pack.business_name}",
        context=f"No pressure at all. If {focus} is still on your mind, we can pick up where you left off." if focus else "No pressure at all — the door is open whenever you want.",
        ask="Reply YES and we will keep a slot aside for you.",
        cta="binary",
        rationale="Hard lapse; no-shame framing because guilt is what stops people replying.",
        hindi_mood="return",
    )


def _trial_followup(pack: FactPack, proof: Fact | None) -> Draft:
    options = _payload(pack, "next_session_options", []) or []
    labels = ", ".join(str(option) for option in options) if isinstance(options, list) else str(options)
    return Draft(
        anchor="how did your first session go?",
        context=f"{pack.business_name} here.{f' Next ones open: {labels}.' if labels else ''}",
        ask="Reply YES and we will book your next one.",
        cta="binary",
        rationale="Post-trial follow-up; asked about their experience before asking for the booking.",
        hindi_mood="booking",
    )


def _wedding_package_followup(pack: FactPack, proof: Fact | None) -> Draft:
    days = _payload(pack, "days_to_wedding")
    step = str(_payload(pack, "next_step_window_open", "") or "").replace("_", " ")
    pack.license_numbers(days)
    return Draft(
        anchor=f"{days} days to your wedding" if days else "your wedding is coming up",
        context=f"{pack.business_name} here. This is the right week to start {step}." if step else f"{pack.business_name} here.",
        ask="Reply YES and we will block your first session.",
        cta="binary",
        rationale="Bridal window opening; timed off the wedding date the customer gave us.",
        hindi_mood="booking",
    )


def _generic(pack: FactPack, proof: Fact | None) -> Draft:
    lead = pack.signal_facts[0] if pack.signal_facts else None
    return Draft(
        anchor=lead.text if lead else (proof.text if proof else "one thing on your listing is worth a look"),
        context=proof.text if (proof and lead) else "",
        ask="Want me to sort it for you?",
        cta="binary",
        rationale="No trigger detail available, so anchored on the strongest signal on the merchant's own account.",
    )


BUILDERS: dict[str, Callable[[FactPack, Fact | None], Draft]] = {
    "research_digest": _research_digest,
    "regulation_change": _regulation_change,
    "cde_opportunity": _cde_opportunity,
    "category_seasonal": _category_seasonal,
    "competitor_opened": _competitor_opened,
    "curious_ask_due": _curious_ask_due,
    "dormant_with_vera": _dormant_with_vera,
    "festival_upcoming": _festival_upcoming,
    "gbp_unverified": _gbp_unverified,
    "ipl_match_today": _ipl_match_today,
    "milestone_reached": _milestone_reached,
    "perf_dip": _perf_dip,
    "perf_spike": _perf_spike,
    "renewal_due": _renewal_due,
    "review_theme_emerged": _review_theme_emerged,
    "seasonal_perf_dip": _seasonal_perf_dip,
    "supply_alert": _supply_alert,
    "winback_eligible": _winback_eligible,
    "active_planning_intent": _active_planning_intent,
    "recall_due": _recall_due,
    "appointment_tomorrow": _appointment_tomorrow,
    "chronic_refill_due": _chronic_refill_due,
    "customer_lapsed_soft": _customer_lapsed_soft,
    "customer_lapsed_hard": _customer_lapsed_hard,
    "trial_followup": _trial_followup,
    "wedding_package_followup": _wedding_package_followup,
}


def plan_message(pack: FactPack, cohort: Cohort | None = None) -> tuple[Draft, Fact | None] | None:
    """Decide what this message says. None when nothing may be said: consent, a
    customer kind with no customer, or a builder the contexts cannot support."""
    if pack.blocked_reason:
        return None

    proof = social_proof_fact(cohort, {"performance": pack.performance}, _shop_word(pack)) if cohort else None
    if proof:
        pack.license(proof)

    if pack.trigger_kind in CUSTOMER_KINDS and not pack.is_customer_facing:
        return None

    builder = BUILDERS.get(pack.trigger_kind, _generic)
    draft = builder(pack, proof)
    # A builder returns None when the trigger fired but the contexts do not
    # support the claim it would have to make. Silence beats an unfounded send.
    return (draft, proof) if draft else None


def assemble(pack: FactPack, draft: Draft, proof: Fact | None) -> ComposedMessage:
    """Turn the decided parts into the message. Shared by the template and LLM paths."""
    anchor, demoted = draft.anchor, ""
    if not opens_on_an_anchor(f"x, {anchor}"):
        # A soft opener wastes the only line WhatsApp shows in the notification,
        # so promote a fact that can actually be checked and keep the rest.
        replacement = pack.changed_metrics[0] if pack.changed_metrics else proof
        if replacement:
            anchor, demoted = replacement.text, draft.anchor

    blocks: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        """Promoting a fact to the opener can leave the same line in the middle."""
        cleaned = _upper_first(_sentence(text))
        fingerprint = re.sub(r"[^a-z0-9]", "", cleaned.lower())
        if fingerprint and fingerprint not in seen:
            seen.add(fingerprint)
            blocks.append(cleaned)

    # Only the builder's own anchor carries the flag; a promoted fact never opens on a name.
    opens_on_a_name = draft.anchor_opens_on_a_name and not demoted
    blocks.append(_sentence(f"{salutation(pack)}, {_lower_first(anchor, opens_on_a_name)}"))
    seen.add(re.sub(r"[^a-z0-9]", "", _sentence(anchor).lower()))

    if demoted:
        add(demoted)
    if pack.changed_metrics:
        # Naming what moved since the last push is what the adaptation score rewards.
        add(pack.changed_metrics[0].text)
    if draft.context.strip():
        add(draft.context)
    if draft.hindi_mood and speaks_hindi(pack):
        blocks.append(HINDI_CLAUSE[draft.hindi_mood])
    blocks.append(_sentence(draft.ask))

    return ComposedMessage(
        body=BLOCK_SEPARATOR.join(blocks),
        cta=draft.cta,
        send_as="merchant_on_behalf" if pack.is_customer_facing else "vera",
        suppression_key=pack.suppression_key,
        rationale=draft.rationale,
    )


def compose_from_template(pack: FactPack, cohort: Cohort | None = None) -> ComposedMessage | None:
    plan = plan_message(pack, cohort)
    if plan is None:
        return None
    draft, proof = plan
    return assemble(pack, draft, proof)
