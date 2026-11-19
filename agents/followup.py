"""
Follow-up agent (guide Steps 8.1 to 8.5): reminders, a learning choice of the next action, attendance,
results, the results-review loop that confirms a diagnosis, rebooking and recall.
"""
from collections import defaultdict

import numpy as np

import simulation
import utils
from models import noshow
from agents.base import Agent
from agents.outreach import TELEHEALTH_OK, channel_for, in_person_option, is_holiday
from agents.priority import barrier_for

ACTIONS = list(simulation.DESIGN["followup_actions"])
DESIGN = simulation.DESIGN
ACTION_CHANNEL = {a: ch for a, ch in simulation.DESIGN["followup_actions"].items() if ch}


class Bandit:
    """Thompson sampling: picks the follow-up that has worked best for similar people, still trying the others."""

    def __init__(self, rng):
        self.rng = rng
        self.wins = defaultdict(lambda: np.ones(len(ACTIONS)))
        self.losses = defaultdict(lambda: np.ones(len(ACTIONS)))

    @staticmethod
    def key(ctx, p):
        g = ctx.guardian(p).row
        return (g.language, g.area_type, bool(p.is_child))

    def choose(self, key, allowed):
        draws = self.rng.beta(self.wins[key], self.losses[key])
        for i in np.argsort(-draws):
            if ACTIONS[i] in allowed:
                return ACTIONS[i]
        return None

    def learn(self, key, action, success):
        (self.wins if success else self.losses)[key][ACTIONS.index(action)] += 1

    def summary(self):
        rows = []
        for key in set(self.wins) | set(self.losses):
            w, l = self.wins[key], self.losses[key]
            rate = (w - 1) / np.maximum(w + l - 2, 1)
            rows.append({"language": key[0], "area_type": key[1], "child": key[2], "tries": int((w + l - 2).sum()),
                         "best_action": ACTIONS[int(np.argmax(w / (w + l)))],
                         **{f"success_{a}": round(float(r), 2) for a, r in zip(ACTIONS, rate)}})
        return rows


def attend(ctx, p, day):
    g = ctx.guardian(p).row
    when = ctx.date(day)
    ramadan = when in ctx.calendar.index and bool(ctx.calendar.loc[when].is_ramadan)
    evening = getattr(p, "evening_slot", False)
    notice = day - (p.booked_on if p.booked_on is not None else day)      # lead time: days from booking to the visit
    prob = simulation.attend_probability(ctx, barrier_for(p, ctx), p.past_no_shows, notice, ramadan, evening,
                                         p.booking_option, p.support)
    came = bool(ctx.rng.random() < prob)
    ctx.invites.append({"day": day, "person": p.pid, "age": int(p.row.age), "is_child": int(p.is_child),
                        "access_barrier": barrier_for(p, ctx), "past_no_shows": p.past_no_shows, "days_notice": notice,
                        "is_ramadan": int(ramadan), "evening_slot": int(evening), **noshow.option_features(p.booking_option, p.support),
                        "true_chance_of_attending": round(prob, 4), "attended": int(came)})
    return came


class FollowUpAgent(Agent):
    name = "followup"

    def run(self, ctx, day):
        new = ctx.mode == "new"
        holiday = new and is_holiday(ctx, day)
        held_yesterday = new and day > 0 and is_holiday(ctx, day - 1)

        def due(waited, days):
            """A reminder day, or the day after one that fell on a public holiday (no messages on holidays)."""
            return not holiday and (waited in days or (held_yesterday and waited - 1 in days))

        for p in list(ctx.people.values()):
            if p.state == "Contacted" and p.awaiting_reply:
                waited = day - p.contacted_on
                rec = ctx.guardian(p)
                if new and due(waited, DESIGN["reminder_days"]):
                    self._nudge(ctx, p, day, "reminder_same_channel", f"reminder day {waited}")
                elif new and due(waited, DESIGN["followup_choice_days"]):
                    allowed = {"reminder_same_channel", "switch_channel"}
                    if rec.row.area_type != "city" or waited >= DESIGN["followup_choice_days"][-1]:
                        allowed.add("community_health_worker")
                    if rec.preferred_channel:
                        allowed.discard("switch_channel")
                    action = ctx.bandit.choose(Bandit.key(ctx, p), allowed)
                    self._nudge(ctx, p, day, action, f"follow-up: {action}", learn=True)
                elif not new and waited == DESIGN["usual_care_reminder_day"]:
                    self._nudge(ctx, p, day, "reminder_same_channel", "usual-care reminder")
                elif waited == DESIGN["no_reply_give_up_day"] and p.followup_action_key is not None:
                    ctx.bandit.learn(*p.followup_action_key, False)
                    p.followup_action_key = None
                if waited >= DESIGN["no_reply_give_up_day"]:
                    # no reply after every try: back on the list, invited again after a pause (both ways alike)
                    p.state, p.awaiting_reply = "Flagged", False
                    p.paused_until = p.flagged_on = day + DESIGN["no_reply_retry_after_days"]
                    ctx.log(day, p.pid, "no_reply", f"invite again from day {p.paused_until}")
            if p.state == "Booked" and p.booked_day == day:
                came = attend(ctx, p, day)
                if p.followup_action_key is not None and ctx.bandit is not None:
                    ctx.bandit.learn(*p.followup_action_key, came)   # success = the person came, not just booked
                    p.followup_action_key = None
                if came:
                    self._attended(ctx, p, day)
                else:
                    p.past_no_shows += 1
                    ctx.log(day, p.pid, "missed_visit", p.booking_option or "")
                    if new:
                        g = ctx.guardian(p).row
                        remote_ok = all(c in TELEHEALTH_OK for c in p.booking_checks)
                        if remote_ok and g.travel_min > DESIGN["telehealth_travel_min"] and g.phone_type != "none":
                            p.booking_option = "Telehealth"
                        elif not remote_ok:
                            p.booking_option = in_person_option(ctx, p)   # a check that needs a visit is never moved to telehealth
                        p.rebooks += 1
                        p.state, p.awaiting_reply, p.flagged_on = "Flagged", False, day
                        if p.rebooks > DESIGN["max_rebooks"]:
                            # missed again after every rebooking: rest, then invite again (no endless rebooking)
                            p.paused_until, p.rebooks = day + DESIGN["no_reply_retry_after_days"], 0
                            p.flagged_on = p.paused_until
                            ctx.log(day, p.pid, "rebook_limit", f"invite again from day {p.paused_until}")
                        else:
                            p.priority += DESIGN["priority_boosts"]["missed_visit"]
                            ctx.log(day, p.pid, "rebook", p.booking_option)
                    else:
                        p.state = "Done"                     # usual care: no rebooking
            # the clinic's own result handling (published rate, Callen 2011) runs in both ways: this system adds the
            # nurse call on top of it and never switches it off
            for cond, d in list(p.detected.items()):
                if cond not in p.diagnosed and day == d + DESIGN["clinic_result_handling_day"] and ctx.rng.random() < ctx.params["old_followup_rate"]:
                    p.diagnosed[cond] = day
                    ctx.log(day, p.pid, "diagnosed", f"{cond} (clinic's usual result handling)")
                    if new and cond in p.needs_results_review:
                        p.needs_results_review.remove(cond)
                        if not p.needs_results_review:
                            for c in p.cards:
                                if c["check"] == "results_review" and c["status"] not in ("done",):
                                    c["status"] = "done"

    def _nudge(self, ctx, p, day, action, label, learn=False):
        rec = ctx.guardian(p)
        channel = ACTION_CHANNEL.get(action) or p.channel or channel_for(rec)
        p.reminders += 1
        if learn:
            p.followup_action_key = (Bandit.key(ctx, p), action)
        ctx.log(day, p.pid, "follow_up", f"{label} via {channel}")
        own = ctx.mode == "new" or rec.row.language == "English"
        reply = simulation.respond(ctx, rec, channel, own, barrier_for(p, ctx))
        if reply:
            ctx.pending_replies.append((p.pid, reply, day))

    def _attended(self, ctx, p, day):
        checks = list(p.booking_checks)
        for c in p.cards:
            if c["check"] in checks:                     # only what was booked: an open results review stays open
                c["status"] = "done"
        for chk in checks:
            p.done[chk] = day
        ctx.log(day, p.pid, "attended", ", ".join(checks))
        p.rebooks = 0
        # a results-review visit confirms what an earlier visit found
        if "results_review" in checks:
            for cond in list(p.needs_results_review):
                p.diagnosed[cond] = day
                ctx.log(day, p.pid, "diagnosed", cond)
            p.needs_results_review = []
        found = simulation.screening_findings(ctx, p, checks, day)
        # a normal result is information too: blood pressure is measured at every adult visit, blood sugar when
        # a diabetes check was booked; with both normal, the risk prediction no longer applies for now
        if not found and not p.is_child and set(checks) & simulation.DM_CHECKS:
            p.risk_normal_day = day
        for cond in found:
            p.detected[cond] = day
            ctx.log(day, p.pid, "abnormal_result", cond)
            if ctx.mode == "new":
                p.needs_results_review.append(cond)
                p.cards.append({"check": "results_review", "pathway": "Result follow-up", "reason": f"Abnormal {cond} result",
                                "invite_by_day": day + utils.config("clinical_rules")["results_review_invite_days"], "recall_months": None, "guideline": "Clinician review",
                                "decision": "Clinician decides", "status": "awaiting_doctor"})
        p.state = "Done"
        p.last_action = "records_changed"
        if any(c["status"] in ("awaiting_doctor", "agreed") for c in p.cards):
            p.state, p.flagged_on = "Flagged", day
