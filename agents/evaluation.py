"""Evaluation agent (guide Step 10.1): a daily snapshot of where everyone stands, plus alerts for the team when
something goes wrong: contact deadlines missed, Urgent people who cannot be messaged, a spike in opt-outs, or one
language, nationality, insurance or region group falling behind."""
import re
from collections import Counter

import consent
import simulation
import utils
from agents.base import Agent

A = simulation.DESIGN["alerts"]
def _group_value(spec):
    """'guardian.language' or 'person.region' from config/real_values.json (design.fairness_groups) as a function."""
    who, field = spec.split(".")
    if field == "age_group":
        return lambda ctx, p: next(g for g, (lo, hi) in simulation.DESIGN["age_groups"].items() if lo <= p.row.age <= hi)
    if field == "sex":
        return lambda ctx, p: "female" if p.row.GENDER == "F" else "male"
    if who == "guardian":
        return lambda ctx, p: getattr(ctx.guardian(p).row, field)
    return lambda ctx, p: getattr(p.row, field)


FAIRNESS_GROUPS = {label: _group_value(spec) for label, spec in simulation.DESIGN["fairness_groups"].items()}


def _first_number(text):
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


CASE_FINDING = set(utils.config("clinical_rules")["case_finding_checks"])


SCREEN_FROM_AGE = utils.config("clinical_rules")["diabetes_screen"]["from_age"]


def _due_case_finding(p):
    """Adults of screening age (where hidden diabetes and high blood pressure sit) with a case-finding check due or done."""
    return p.row.age >= SCREEN_FROM_AGE and (any(c["check"] in CASE_FINDING for c in p.cards) or any(c in CASE_FINDING for c in p.done))


def reach_rates(ctx, people, outcome="reached"):
    """{group type: {group: share}} among people with something due, for groups of a reportable size.
    outcome "reached" = invited at least once; "completed" = completed at least one check;
    "screened" = among people due a check that can find hidden diabetes or high blood pressure, the share who did it
    (the system cannot see hidden cases, so this is how it watches the gap in cases found)."""
    if outcome == "screened":
        hit, due = (lambda p: any(c in CASE_FINDING for c in p.done)), _due_case_finding
    else:
        hit = (lambda p: p.first_invite_day is not None) if outcome == "reached" else (lambda p: bool(p.done))
        due = lambda p: bool(p.gaps or p.cards or p.done)  # noqa: E731
    out = {}
    for label, value in FAIRNESS_GROUPS.items():
        reach = {}
        for p in people:
            if due(p):
                g = value(ctx, p)
                n, k = reach.get(g, (0, 0))
                reach[g] = (n + 1, k + hit(p))
        out[label] = {g: k / n for g, (n, k) in reach.items() if n >= A["fairness_min_group"]}
    return out


class EvaluationAgent(Agent):
    name = "evaluation"

    def run(self, ctx, day):
        people = list(ctx.people.values())
        states = Counter(p.state for p in people)
        ever_contacted = sum(p.first_invite_day is not None for p in people)
        completed = sum(bool(p.done) for p in people)
        deadlines = simulation.DESIGN["deadline_days"]
        late = [p for p in people if p.state == "Flagged" and p.urgency in deadlines and p.flagged_on is not None
                and day - p.flagged_on > deadlines[p.urgency] and p.paused_until <= day]
        # people with no contact consent can never be messaged: they are not an outreach delay but a clinician's task
        no_consent = [p for p in late if not (consent.has(ctx, p, "contact") and consent.has(ctx, ctx.guardian(p), "contact"))]  # same rule as chat.may_contact
        nc_ids = {p.pid for p in no_consent}
        missed = [p for p in late if p.pid not in nc_ids]
        opt_today = max(states["Opted out"] - (ctx.daily[-1]["opted_out"] if ctx.daily else 0), 0)
        ctx.daily.append({
            "day": day, "date": ctx.date(day).date().isoformat(), "flagged": states["Flagged"], "contacted": states["Contacted"],
            "booked": states["Booked"], "done": states["Done"], "on_hold": states["On hold"], "opted_out": states["Opted out"],
            "ever_contacted": ever_contacted, "people_with_completed_care": completed,
            "urgent_waiting": sum(p.urgency == "Urgent" and p.state not in ("Done",) for p in people),
            "missed_deadlines": len(missed), "urgent_no_consent": sum(p.urgency == "Urgent" for p in no_consent),
            "opt_outs_today": opt_today,
            "abnormal_found": sum(len(p.detected) for p in people),
            "diagnosed": sum(len(p.diagnosed) for p in people),
            "messages": len(ctx.messages)})
        if ctx.mode != "new":
            return
        for p in no_consent:
            if p.urgency == "Urgent" and not getattr(p, "_no_consent_logged", False):
                p._no_consent_logged = True
                ctx.log(day, p.pid, "urgent_no_consent", "no contact consent: the clinician decides how to reach this person")
        n_nc = sum(p.urgency == "Urgent" for p in no_consent)
        if n_nc:
            self._alert(ctx, day, "Urgent, no contact consent",
                        f"{n_nc} Urgent people cannot be messaged (no contact consent): the clinician decides how to "
                        "reach them, for example at their next visit", "high")
        urgent_late = sum(p.urgency == "Urgent" for p in missed)
        if urgent_late:
            self._alert(ctx, day, "Urgent deadline missed",
                        f"{urgent_late} Urgent people not contacted within {deadlines['Urgent']} days", "high")
        if len(missed) >= A["missed_deadlines"]:
            self._alert(ctx, day, "Deadlines missed", f"{len(missed)} people past their contact deadline", "medium")
        w = A["window_days"]
        before = [d["opt_outs_today"] for d in ctx.daily[-(w + 1):-1]]
        if len(before) == w:
            avg = sum(before) / w
            if opt_today >= A["opt_out_spike_min"] and opt_today > A["opt_out_spike_ratio"] * max(avg, 1):
                self._alert(ctx, day, "Opt-out spike", f"{opt_today} opt-outs today against {avg:.1f} a day last week: "
                            "check the message wording and timing", "high")
        if day % A["window_days"] == A["window_days"] - 1:  # weekly fairness check: who is reached, by group
            for outcome in ("reached", "completed", "screened"):
                for label, rates in reach_rates(ctx, people, outcome).items():
                    if len(rates) > 1 and max(rates.values()) - min(rates.values()) > A["fairness_gap"]:
                        lo, hi = min(rates, key=rates.get), max(rates, key=rates.get)
                        self._alert(ctx, day, f"Fairness gap ({label}, {outcome})", f"{label} {lo}: {rates[lo]:.0%} {outcome}, "
                                    f"against {rates[hi]:.0%} for {hi}", "medium")

    @staticmethod
    def _alert(ctx, day, kind, detail, level):
        last = next((a for a in reversed(ctx.alerts) if a["kind"] == kind), None)
        if last and day - last["day"] <= A["repeat_days"]:
            # the same kind of warning within a week: only repeat it if the number has grown by half or more
            n_new, n_old = _first_number(detail), _first_number(last["detail"])
            if n_new is None or n_old is None or n_new < n_old * A["repeat_growth"]:
                last["repeated"] = last.get("repeated", 0) + 1
                last["last_seen_day"] = day
                return
        ctx.alerts.append({"day": day, "date": ctx.date(day).date().isoformat(), "kind": kind, "detail": detail, "level": level})
