"""
Chat assistant (guide Step 7): replies are handled in a fixed order. Emergency words first, then
STOP / START, then INFO, LATER, channel and time choices, then booking; anything else gets a general answer.
STOP, START and emergencies never go through any generated reply, so a message cannot trick the system into changing consent.
"""
import re

import consent
import summaries
import simulation
import utils
from agents.base import Agent
from agents.outreach import TELEHEALTH_OK, in_person_option, option_text

EMERGENCY = {"UAE": utils.config("clinical_rules")["emergency_number"]}
# one list per language the patients speak (Arabic, English, Urdu, Hindi, Tagalog). Before a pilot, each list must be
# checked by a native-speaking clinician; matching is on lower-case text, so it also works inside longer messages.
EMERGENCY_WORDS = utils.config("language")["emergency_words"]     # by language, in config/language.json
ALL_EMERGENCY_WORDS = [w.lower() for words in EMERGENCY_WORDS.values() for w in words]
# whole words only ("heatstroke" is not "stroke"). A negation such as "no chest pain" still raises the alarm on purpose:
# a false alarm costs a nurse call, a missed emergency costs far more.
EMERGENCY_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in sorted(ALL_EMERGENCY_WORDS, key=len, reverse=True)) + r")(?!\w)")
PREFERENCE_WORDS = utils.config("language")["preference_words"]   # channel and time phrases, in config/language.json
REPLIES = utils.config("language")["family_replies"]              # the answers, in each family's language


def reply_language(ctx, person):
    """Answers go in the language the invitation used: the family's language here, English in usual care."""
    lang = ctx.guardian(person).row.language if ctx.mode == "new" else "English"
    return lang if lang in REPLIES else "English"


def say(ctx, person, key, **values):
    text = REPLIES[reply_language(ctx, person)][key]
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def may_contact(ctx, person):
    """One rule everywhere (simulation, API, Streamlit): the person and, for a child, the guardian allow contact."""
    return consent.has(ctx, person, "contact") and consent.has(ctx, ctx.guardian(person), "contact")


def reply_allowed(ctx, person, text):
    """Should the assistant answer this reply? Always for emergency words and START; otherwise not after an opt-out."""
    t = str(text).strip().lower()
    return person.state != "Opted out" or ctx.reply_words.get(t) == "START" or bool(EMERGENCY_RE.search(t))


def raise_emergency(ctx, person, day, text):
    """The care team sees it at once: an Urgent card at the top of the doctor's list and the outreach queue."""
    if any(c["check"] == "emergency_callback" and c["status"] != "done" for c in person.cards):
        return
    person.cards.insert(0, {"check": "emergency_callback", "pathway": "Emergency", "reason": "Patient reported emergency words",
                            "invite_by_day": day, "recall_months": None, "guideline": "Emergency protocol",
                            "decision": "Clinician decides", "status": "agreed", "raised_on": day})
    person.urgency, person.state, person.flagged_on, person.awaiting_reply = "Urgent", "Flagged", day, False
    person.priority += simulation.DESIGN["priority_boosts"]["emergency"]
    ctx.audit.append({"day": day, "agent": "chat", "person": person.pid, "data": "reply text",
                      "why": "emergency words: care team alerted for a same-day call"})


def handle_reply(ctx, person, text, day):
    """Returns the assistant's answer and applies the effect. Works for typed text and for simulated reply words."""
    t = text.strip().lower()
    rec = ctx.guardian(person)
    if EMERGENCY_RE.search(t):
        raise_emergency(ctx, person, day, text)
        ctx.log(day, person.pid, "emergency", "care team alerted: Urgent call-back card added")
        return say(ctx, person, "emergency", n=EMERGENCY["UAE"])
    keyword = ctx.reply_words.get(t)
    if keyword == "STOP":
        # STOP is about the phone that received the message: it covers the recipient and every child they look after
        for m in ctx.family(rec):
            consent.record(ctx, day, m.pid, "contact", False, rec.pid)
            if m is person or m.state != "Booked":        # a family member's visit already booked still happens, with no more messages
                m.state, m.awaiting_reply = "Opted out", False
            ctx.log(day, m.pid, "opt_out", "replied STOP" if m is person else f"family opt-out (STOP from {rec.pid})")
        return say(ctx, person, "stop")
    if keyword == "START":
        for m in ctx.family(rec):
            consent.record(ctx, day, m.pid, "contact", True, rec.pid)
            if m.state == "Opted out" or m is person:
                m.state = "Flagged" if m.cards else "None"
                m.flagged_on = day
            ctx.log(day, m.pid, "opt_in", "replied START" if m is person else f"family opt-in (START from {rec.pid})")
        return say(ctx, person, "start")
    if keyword == "INFO":
        ctx.log(day, person.pid, "info", "asked for information")
        return say(ctx, person, "info")
    if keyword == "LATER":
        pause = simulation.DESIGN["later_pause_days"]
        wait = pause["result"] if person.needs_results_review else pause["other"]    # a result to review cannot wait a month
        person.paused_until, person.state, person.awaiting_reply = day + wait, "Flagged", False
        person.flagged_on = day + wait                    # the contact deadline restarts when the pause ends
        ctx.log(day, person.pid, "later", f"paused {wait} days")
        return say(ctx, person, "later", n=wait)
    said = {k for k, words in PREFERENCE_WORDS.items() if any(w in t for w in words)}
    for ch in ("Voice call", "WhatsApp"):
        if ch in said:
            rec.preferred_channel = ch
    for tm in ("Evening", "Morning"):
        if tm in said:
            rec.preferred_time = tm
    if keyword == "BOOK" or t in ("book", "yes"):
        return book(ctx, person, day)
    if rec.preferred_channel or rec.preferred_time:
        return say(ctx, person, "preference")
    check = person.cards[0]["check"] if person.cards else "check-up"
    return summaries.general_answer(text, rec.row.language, check)


def first_free_day(ctx, day, urgent=False):
    """A random day in the booking window, moved later while that day's clinic slots are full (same limit both ways).
    Telehealth uses the same nurses and doctors, so it shares the slots. Urgent work (a results review, an emergency
    call-back, an Urgent case) uses the slots kept free for it each day, booked within a few days."""
    if urgent:
        u = simulation.DESIGN["urgent_slots"]
        d = day + int(ctx.rng.integers(u["lead_days"][0], u["lead_days"][1] + 1))
        while ctx.slots.get(("urgent", d), 0) >= u["per_day"]:
            d += 1
        ctx.slots[("urgent", d)] = ctx.slots.get(("urgent", d), 0) + 1
        return d
    lo, hi = simulation.B["booking_lead_days"]
    d = day + int(ctx.rng.integers(lo, hi + 1))
    while ctx.slots.get(("clinic", d), 0) >= ctx.params["clinic_slots_per_day"]:
        d += 1
    ctx.slots[("clinic", d)] = ctx.slots.get(("clinic", d), 0) + 1
    return d


def book(ctx, person, day):
    cards = [c for c in person.cards if c["status"] in ("ready", "agreed")]
    if not cards:
        return say(ctx, person, "nothing")
    option = person.booking_option or "Clinic visit"
    if option.startswith("Telehealth"):
        # telehealth books only the checks that can be done remotely; the rest stay open for an in-person invite
        remote = [c for c in cards if c["check"] in TELEHEALTH_OK]
        if remote:
            cards = remote
        else:
            option = in_person_option(ctx, person)
            person.booking_option = option
    place = ctx.guardian(person).row.home_place
    if option.startswith("Mobile clinic") and place in ctx.mobile_clinics \
            and ctx.slots.get(("mobile", ctx.mobile_clinics[place]), 0) < simulation.DESIGN["mobile_clinic_slots"]:
        bday = ctx.mobile_clinics[place]
        ctx.slots[("mobile", bday)] = ctx.slots.get(("mobile", bday), 0) + 1
    else:
        if option.startswith("Mobile clinic"):                 # the mobile clinic is full or has moved: book the clinic
            option = in_person_option(ctx, person, mobile=False)
            person.booking_option = option
        urgent = person.urgency == "Urgent" or any(c["check"] in ("results_review", "emergency_callback") for c in cards)
        bday = first_free_day(ctx, day, urgent=urgent)
    person.state, person.booked_day, person.booked_on, person.awaiting_reply = "Booked", max(bday, day + 1), day, False
    person.booking_checks = [c["check"] for c in cards]
    evening = ctx.mode == "new" and ctx.guardian(person).row.work_shift == "day"
    person.evening_slot = evening
    support = None
    if ctx.mode == "new":
        from agents.priority import barrier_for
        from models import noshow
        support = noshow.rule_support(barrier_for(person, ctx), person.past_no_shows)
        from agents.outreach import completion_lags
        if support == noshow.SUPPORT[0] and completion_lags(ctx, person):
            support = noshow.SUPPORT[1]       # reach floor: a group behind on completed care gets the extra reminder
    person.support = support
    ctx.log(day, person.pid, "booked", f"day {person.booked_day}, {option}{', evening slot' if evening else ''}"
            f"{'; support: ' + support if support else ''}")
    joined = book_family(ctx, person, day, option) if ctx.mode == "new" and not option.startswith("Telehealth") else 0
    also = say(ctx, person, "also", n=joined) if joined else ""
    return say(ctx, person, "booked", date=ctx.date(person.booked_day).date().isoformat(),
               option=option_text(option, reply_language(ctx, person)), also=also)


def book_family(ctx, person, day, option):
    """Family visit: when one person books, children and the parent with an in-person check due come to the same
    visit. Children's check-ups depend on the parent bringing them, so one trip for the family is easier."""
    rec, kind = ctx.guardian(person), ("mobile" if option.startswith("Mobile clinic") else "clinic")
    limit = simulation.DESIGN["mobile_clinic_slots"] if kind == "mobile" else ctx.params["clinic_slots_per_day"]
    joined = 0
    for m in ctx.family(rec):
        if m is person or m.state not in ("Flagged", "Contacted") or m.paused_until > day or not may_contact(ctx, m):
            continue
        cards = [c for c in m.cards if c["status"] in ("ready", "agreed") and c["check"] not in TELEHEALTH_OK]
        if not cards or ctx.slots.get((kind, person.booked_day), 0) >= limit:
            continue
        ctx.slots[(kind, person.booked_day)] = ctx.slots.get((kind, person.booked_day), 0) + 1
        m.state, m.booked_day, m.booked_on, m.awaiting_reply = "Booked", person.booked_day, day, False
        m.booking_checks, m.booking_option = [c["check"] for c in cards], option
        m.evening_slot, m.support = person.evening_slot, person.support
        m.contacted_on = day if m.contacted_on is None else m.contacted_on
        m.first_invite_day = day if m.first_invite_day is None else m.first_invite_day
        ctx.log(day, m.pid, "booked", f"day {m.booked_day}, {option}; family visit with {person.pid[:8]}")
        joined += 1
    return joined


class ChatAgent(Agent):
    name = "chat"

    def run(self, ctx, day):
        todo = [x for x in ctx.pending_replies if x[2] <= day]
        ctx.pending_replies = [x for x in ctx.pending_replies if x[2] > day]
        for pid, word, d in todo:
            p = ctx.people[pid]
            if not reply_allowed(ctx, p, word):               # emergencies are always answered
                continue
            answer = handle_reply(ctx, p, word, day)
            ctx.messages.append({"day": day, "person": pid, "recipient": ctx.guardian(p).pid, "channel": "reply",
                                 "language": "", "check": "", "window": "", "text": f"{word} -> {answer}"})
            if word == "INFO" and ctx.rng.random() < simulation.B["info_then_book"]:     # some go on to book after the explanation
                ctx.pending_replies.append((pid, "BOOK", day + 1))
        from agents.outreach import emergency_callbacks
        emergency_callbacks(ctx, day)                         # an emergency is called back the same day
