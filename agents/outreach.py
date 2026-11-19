"""
Outreach agent (guide Step 6): the right recipient, channel, approved message, time and care option.

New way: guardian routing, own-language approved templates with a safety check, the person's own channel
choice, Ramadan / Friday / shift-aware timing, no routine messages on public holidays (Urgent still goes), telehealth, mobile clinics
and transport help. Usual care: an English SMS to everyone due by age and sex, in no particular order.
"""
import re

import pandas as pd

import consent
import simulation
import utils
from agents.base import Agent
from agents.evaluation import FAIRNESS_GROUPS, reach_rates
from agents.priority import barrier_for

BLOCKED = simulation.DESIGN["blocked_message_words"]
TELEHEALTH_OK = set(simulation.DESIGN["telehealth_checks"])
CARE_OPTIONS = utils.config("language")["care_options"]    # the care option written in each language


def option_text(option, language):
    """The care option as it appears inside a message in that language (English and unknown languages: as it is)."""
    words = CARE_OPTIONS.get(language)
    if not words or not option:
        return option
    if option.startswith("Mobile clinic on "):
        return words["Mobile clinic on {date}"].replace("{date}", option[len("Mobile clinic on "):])
    return words.get(option, option)


def channel_for(recipient):
    r = recipient.row
    if recipient.preferred_channel:
        return recipient.preferred_channel
    if r.phone_type == "smartphone":
        return "WhatsApp"
    if r.phone_type == "basic":
        return "SMS" if r.can_read else "Voice call"
    return "Community health worker visit"


class ChannelLearner:
    """Learns the channel of the first message from replies (Thompson sampling, like the follow-up learner).

    Safe by construction: it chooses only among channels that need no staff time and that the person can use
    (design.first_channel_learner.choices, by phone type); everyone else keeps the rule's channel, and a person's own
    stated channel always wins. The rule's channel starts with assumed replies, so the learner moves away from the
    rule only when real replies say another channel works better for similar people."""

    def __init__(self, rng):
        self.rng = rng
        self.cfg = simulation.DESIGN["first_channel_learner"]
        self.wins, self.losses = {}, {}

    @staticmethod
    def key(recipient):
        r = recipient.row
        return (r.phone_type, bool(r.can_read), r.area_type)

    def choices(self, recipient):
        return self.cfg["choices"].get(recipient.row.phone_type, [])

    def choose(self, recipient):
        """(channel, learned): the rule's channel unless this person has a choice of self-service channels."""
        rule = channel_for(recipient)
        options = self.choices(recipient)
        if recipient.preferred_channel or rule not in options or len(options) < 2:
            return rule, False
        key = self.key(recipient)
        for ch in options:
            self.wins.setdefault((key, ch), 1 + (self.cfg["rule_prior_replies"] if ch == rule else 0))
            self.losses.setdefault((key, ch), 1)
        draws = {ch: self.rng.beta(self.wins[(key, ch)], self.losses[(key, ch)]) for ch in options}
        return max(draws, key=draws.get), True

    def learn(self, recipient, channel, replied):
        k = (self.key(recipient), channel)
        if k in self.wins:
            (self.wins if replied else self.losses)[k] += 1

    def summary(self):
        rows = []
        prior = self.cfg["rule_prior_replies"]
        for (key, ch), w in sorted(self.wins.items(), key=str):
            l = self.losses[(key, ch)]
            start = 1 + (prior if ch == self.cfg["choices"].get(key[0], [None])[0] else 0)
            sent, replies = int(w - start + l - 1), int(w - start)
            rows.append({"phone_type": key[0], "can_read": key[1], "area_type": key[2], "channel": ch, "messages": sent,
                         "replies": replies, "reply_rate": round(replies / sent, 3) if sent else None})
        return rows


def safe_message(ctx, check, audience, language, option):
    """(text, in_own_language) from the approved templates, or (None, False) when staff must handle it."""
    t = ctx.templates.get((check, audience, language))
    own = t is not None
    t = t or ctx.templates.get((check, audience, "English"))
    if t is None:
        return None, False
    text = t.format(child="your child", option=option_text(option, language if own else "English"))
    text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)   # capital after a full stop
    if any(w in text.lower() for w in BLOCKED) or len(text) > simulation.DESIGN["max_message_characters"]:
        return None, False
    return text, own or language == "English"


def contact_window(ctx, recipient, day):
    r, when = recipient.row, ctx.date(day)
    cal = ctx.calendar.loc[when] if when in ctx.calendar.index else None
    if recipient.preferred_time:
        return recipient.preferred_time
    w = simulation.DESIGN["contact_windows"]
    if cal is not None and bool(cal.is_ramadan):
        return w["ramadan"]
    if when.weekday() == simulation.DESIGN["short_contact_day_weekday"]:   # Friday in the UAE
        return w["friday"]
    if r.work_shift == "day":
        return w["after_day_shift"]
    return w["default"]


def is_holiday(ctx, day):
    when = ctx.date(day)
    return when in ctx.calendar.index and str(ctx.calendar.loc[when].holiday) not in ("", "nan")


def care_option(ctx, person, check):
    g = ctx.guardian(person).row
    if check in TELEHEALTH_OK and g.phone_type != "none":
        return "Telehealth"
    return in_person_option(ctx, person)


def in_person_option(ctx, person, mobile=True):
    """Where an in-person check happens: a nearby mobile clinic, a clinic visit with transport help, or the clinic."""
    g = ctx.guardian(person).row
    if mobile and g.home_place in ctx.mobile_clinics:
        return f"Mobile clinic on {ctx.date(ctx.mobile_clinics[g.home_place]).date().isoformat()}"
    if g.travel_min > simulation.DESIGN["telehealth_travel_min"]:
        return "Clinic visit with transport help offered"
    return "Clinic visit"


def plan_mobile_clinics(ctx, day):
    """Weekly: a mobile clinic for the 3 rural or remote places with the most people waiting."""
    waiting = [ctx.guardian(p).row.home_place for p in ctx.people.values()
               if p.state in ("Flagged", "Contacted") and ctx.guardian(p).row.area_type in ("rural", "remote")]
    top = pd.Series(waiting).value_counts().head(simulation.DESIGN["mobile_clinic_places"]).index if waiting else []
    ctx.mobile_clinics = {place: day + simulation.DESIGN["mobile_clinic_first_day"] + i for i, place in enumerate(top)}
    for place, d in ctx.mobile_clinics.items():
        ctx.log(day, "-", "mobile_clinic_planned", f"{place} on day {d}")


def lagging_groups(ctx):
    """Reach floor: groups whose share reached is more than the allowed gap below the best-reached group of the
    same kind (language, nationality, insurance, region). Their people move up within each urgency level, so the
    barrier weighting cannot leave any group far behind."""
    floor = simulation.DESIGN["reach_floor_gap"]
    # (case-finding checks by group are watched by the evaluation agent's weekly alert, not acted on here: moving those
    # groups up was tested over 4 seeds and found no fewer missed cases, see README "Safeguards by group")
    out, ctx.completion_behind = {}, {}
    people = list(ctx.people.values())
    for outcome, target in (("reached", out), ("completed", ctx.completion_behind)):
        for label, rates in reach_rates(ctx, people, outcome).items():
            if len(rates) > 1 and max(rates.values()) > 0:
                best = max(rates.values())
                low = {g for g, r in rates.items() if r < best - floor}
                if low:
                    target[label] = low
    # a group behind on completed care also goes first: being invited is not enough if its visits do not happen
    for label, low in ctx.completion_behind.items():
        out.setdefault(label, set()).update(low)
    return out


def completion_lags(ctx, person):
    """True when the person belongs to a group behind on completed care (set daily by lagging_groups)."""
    behind = getattr(ctx, "completion_behind", None) or {}
    return any(fn(ctx, person) in behind.get(label, ()) for label, fn in FAIRNESS_GROUPS.items())


def contact_ok(ctx, p):
    return consent.has(ctx, p, "contact") and consent.has(ctx, ctx.guardian(p), "contact")


def contactable_cards(p):
    return [c for c in p.cards if c["status"] in ("ready", "agreed")]


def emergency_callbacks(ctx, day):
    """A nurse calls back everyone who reported emergency words, outside the daily queue and its limit."""
    for p in ctx.people.values():
        for c in p.cards:
            if c["check"] == "emergency_callback" and c["status"] == "agreed":
                rec = ctx.guardian(p)
                ctx.messages.append({"day": day, "person": p.pid, "recipient": rec.pid, "channel": "Staff call",
                                     "language": rec.row.language, "check": c["check"], "window": "now",
                                     "text": "[nurse call-back after emergency words]"})
                ctx.log(day, p.pid, "emergency_callback", f"nurse called {'guardian' if rec is not p else 'person'} back")
                c["status"] = "done"
                if p.state == "Flagged":
                    p.state = "Done"                      # the priority step re-flags any other open cards


class OutreachAgent(Agent):
    name = "outreach"

    def run(self, ctx, day):
        new = ctx.mode == "new"
        emergency_callbacks(ctx, day)                     # before anything else, holidays included, both ways
        if new and day % simulation.DESIGN["weekly_review_every_days"] == 0:
            plan_mobile_clinics(ctx, day)
        holiday = new and is_holiday(ctx, day)
        if holiday:
            ctx.log(day, "-", "no_outreach", "public holiday: Urgent cases only")
        queue = [p for p in ctx.people.values()
                 if p.state == "Flagged" and p.paused_until <= day and contactable_cards(p)
                 and contact_ok(ctx, p)]
        if holiday:                                        # no routine messages on a holiday, but Urgent cannot wait
            queue = [p for p in queue if p.urgency == "Urgent"]
        rank = {"Urgent": 0, "High": 1, "Routine": 2}
        behind = lagging_groups(ctx) if new else {}
        if behind:
            ctx.log(day, "-", "reach_floor", "; ".join(f"{k} {v}" for k, vs in behind.items() for v in sorted(vs)))

        def lags(p):
            return any(fn(ctx, p) in behind.get(label, ()) for label, fn in FAIRNESS_GROUPS.items())
        # urgency first (Step 5.2); within the same urgency, people from a group that has fallen behind go first
        queue.sort(key=lambda p: (rank.get(p.urgency, 3), not lags(p), -p.priority) if new else (0, -p.priority))
        sent_to = set()
        for p in queue:
            if len(sent_to) >= ctx.params["capacity"]:    # the limit counts messages sent, not people skipped
                break
            rec = ctx.guardian(p)
            if new and rec.pid in sent_to:                # one message per family per day
                continue
            card = sorted(contactable_cards(p), key=lambda c: c["invite_by_day"])[0]
            audience = "guardian" if p.is_child else "adult"
            option = care_option(ctx, p, card["check"]) if new else "Clinic visit"
            learned = False
            if new:
                language, channel = rec.row.language, channel_for(rec)
                if ctx.channel_learner is not None:          # the first message's channel, learned from replies
                    channel, learned = ctx.channel_learner.choose(rec)
            else:
                language, channel = "English", "SMS"
            text, own = safe_message(ctx, card["check"], audience, language, option)
            if new and card["check"] == "results_review":     # results are discussed by a nurse on the phone
                text = None
            if text is None:
                if not new:
                    continue
                # the log line never names the check: a genetic check name must not appear in any message record
                channel, text, own, learned = "Staff call", "[staff call: a nurse discusses the open pathway card]", True, False
            barrier = barrier_for(p, ctx)
            if not new and language != rec.row.language and rec.row.language != "English":
                own = False
            p.state, p.contacted_on, p.channel, p.awaiting_reply = "Contacted", day, channel, True
            p.first_invite_day = p.first_invite_day if p.first_invite_day is not None else day
            p.booking_option = option
            window = contact_window(ctx, rec, day) if new else "any time"
            ctx.messages.append({"day": day, "person": p.pid, "recipient": rec.pid, "channel": channel, "language": language,
                                 "check": card["check"], "window": window, "text": text})
            ctx.audit.append({"day": day, "agent": self.name, "person": p.pid, "data": "contact details of " + rec.pid,
                              "why": f"invite for {card['check']}"})
            ctx.log(day, p.pid, "message", f"{channel} to {'guardian' if rec is not p else 'person'} in {language}: {card['check']}")
            sent_to.add(rec.pid)
            kind = "results" if card["check"] == "results_review" else "invite"
            reply = simulation.respond(ctx, rec, channel, own, barrier, kind)
            if learned:                                    # a reply that is not STOP counts as the channel working
                ctx.channel_learner.learn(rec, channel, reply is not None and reply != "STOP")
            if reply:
                ctx.pending_replies.append((p.pid, reply, day))
