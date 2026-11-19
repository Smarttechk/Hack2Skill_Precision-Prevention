"""
Priority step (guide Step 5): urgency, priority score, contact deadlines and pathway cards.

A card is an outreach invite only when its check is in the approved guidelines and the case is not urgent;
everything else waits for a clinician ("Clinician decides"). The doctor's review is simulated here:
the doctor agrees with most flags and puts the rest on hold.
"""
from agents.base import Agent
import consent
import simulation
import utils
from agents.care_gap import RECALL
from models.risk_scores import cutoffs, risk_rank
from utils import access_barrier

DESIGN = simulation.DESIGN
W = DESIGN["priority_weights"]
DEADLINE_DAYS = DESIGN["deadline_days"]
NEEDS_CLINICIAN = set(DESIGN["clinician_decides_checks"])
CR = utils.config("clinical_rules")
GUIDELINE_ALIASES = CR["guideline_aliases"]
URGENT_SIGNS, HIGH_SIGNS = set(CR["urgent_signs"]), set(CR["high_signs"])


def urgency(p):
    signs = set(p.warning_signs)
    if signs & URGENT_SIGNS or p.needs_results_review or any(
            c["check"] == "emergency_callback" and c["status"] != "done" for c in p.cards):
        return "Urgent"
    if (p.risk is not None and p.risk >= cutoffs()["high"]) or signs & HIGH_SIGNS:
        return "High"
    if p.gaps or signs:
        return "Routine"
    return None


_GUIDE_CACHE = {}


def guideline_row(check, guidelines):
    key = id(guidelines)
    if key not in _GUIDE_CACHE:                     # one lookup table per guidelines table, built once
        _GUIDE_CACHE[key] = {r.check: r for r in guidelines.drop_duplicates("check").itertuples(index=False)}
    return _GUIDE_CACHE[key].get(GUIDELINE_ALIASES.get(check, check))


def pathway_card(p, gap, guidelines, day):
    g = guideline_row(gap["check"], guidelines)
    clinical = g is None or gap["check"] in NEEDS_CLINICIAN or p.urgency == "Urgent"
    return {
        "check": gap["check"], "pathway": gap["pathway"], "reason": gap["reason"],
        "invite_by_day": day + DEADLINE_DAYS.get(p.urgency, DEADLINE_DAYS["Routine"]),
        # the interval comes from config/clinical_rules.json (the rules that create the gap); guidelines.csv only
        # names the source, and its intervals are written from the same rules (scripts/get_all_data.py reference step)
        "recall_months": str(RECALL[gap["check"]]) if gap["check"] in RECALL else (None if g is None else str(g.every_months)),
        "guideline": "Not in approved guidelines" if g is None else f"{g.source} ({g.version})",
        "decision": "Clinician decides" if clinical else "Outreach invite (population priority)",
        "status": "awaiting_doctor" if clinical else "ready",
    }


NO_CONSENT = CR["no_consent_task"]


def no_consent_task(ctx, p, cards, day):
    """Urgent but no contact consent: messages are never sent, so the doctor gets a task to reach the person another
    way (at their next visit, or by the care team if the doctor judges it necessary). Closed when consent returns."""
    can_message = consent.has(ctx, p, "contact") and consent.has(ctx, ctx.guardian(p), "contact")
    task = next((c for c in cards if c["check"] == NO_CONSENT["check"] and c["status"] != "done"), None)
    if task and (can_message or p.urgency != "Urgent"):
        task["status"] = "done"
        ctx.log(day, p.pid, "no_consent_task_closed", "contact consent given again" if can_message else "no longer Urgent")
    elif not task and not can_message and p.urgency == "Urgent":
        cards.append({"check": NO_CONSENT["check"], "pathway": NO_CONSENT["pathway"],
                      "reason": NO_CONSENT["reason"] + ": " + ", ".join(c["check"] for c in cards if c["status"] != "done"),
                      "invite_by_day": day + DEADLINE_DAYS["Urgent"], "recall_months": None,
                      "guideline": NO_CONSENT["guideline"], "decision": "Clinician decides", "status": "awaiting_doctor"})
        ctx.log(day, p.pid, "no_consent_task", "Urgent, no contact consent: doctor decides how to reach")


def barrier_for(p, ctx):
    b = getattr(p, "_barrier", None)
    if b is None:                                   # the guardian's barrier for a child; computed once
        g = ctx.guardian(p).row
        b = p._barrier = access_barrier(g.language, g.insurance, g.work_shift, g.phone_type, g.travel_min)
    return b


class PriorityAgent(Agent):
    name = "priority"

    def run(self, ctx, day):
        for p in ctx.people.values():
            if p.state == "Opted out":
                if ctx.mode == "new":                        # no messages, but an Urgent case still needs a doctor's decision
                    no_consent_task(ctx, p, p.cards, day)
                continue
            if ctx.mode == "old":
                p.urgency = "Routine" if p.gaps else None
                p.priority = float(ctx.rng.random())         # usual care: no ranking, invitations in any order
            else:
                p.urgency = urgency(p)
                # a visit result beats any prediction: a finding counts as the top rank, a normal result as the bottom
                risk = 1.0 if p.risk_found else 0.0 if p.risk_normal_day is not None else risk_rank(p.risk)
                p.priority = round(W["risk"] * risk + W["gaps"] * min(len(p.gaps) / DESIGN["gaps_for_full_score"], 1) + W["barrier"] * barrier_for(p, ctx), 3)
            # pathway cards: keep cards already decided, add new gaps
            existing = {c["check"]: c for c in p.cards}
            cards = []
            for gap in p.gaps:
                c = existing.get(gap["check"]) or pathway_card(p, gap, ctx.guidelines, day)
                if ctx.mode == "old":
                    c["decision"], c["status"] = "Outreach invite (population priority)", c.get("status") if c.get("status") == "done" else "ready"
                cards.append(c)
            in_cards = {id(x) for x in cards}
            gap_checks = {x["check"] for x in cards}
            for c in p.cards:                                  # keep results reviews and finished cards (every one of them:
                # a person can have a finished results review and a new open one for a later result)
                kept = ("done", "awaiting_doctor", "agreed") + (("on_hold",) if c["check"] == NO_CONSENT["check"] else ())
                if id(c) not in in_cards and c["status"] in kept \
                        and (c["check"] not in gap_checks or c["check"] in ("results_review", "emergency_callback")):
                    if c["check"] == "emergency_callback" and c["status"] != "done":
                        cards.insert(0, c)                      # an emergency call-back always comes first
                        continue
                    cards.append(c)
            # safety net: an abnormal result still waiting for review always has an open results-review card
            if p.needs_results_review and not any(c["check"] == "results_review" and c["status"] in ("awaiting_doctor", "agreed", "ready")
                                                   for c in cards):
                cards.append({"check": "results_review", "pathway": "Result follow-up",
                              "reason": "Abnormal " + ", ".join(p.needs_results_review) + " result", "invite_by_day": day + utils.config("clinical_rules")["results_review_invite_days"],
                              "recall_months": None, "guideline": "Clinician review", "decision": "Clinician decides",
                              "status": "awaiting_doctor"})
                ctx.log(day, p.pid, "results_review_reopened", ", ".join(p.needs_results_review))
            if ctx.mode == "new":
                no_consent_task(ctx, p, cards, day)
            p.cards = cards
            if p.urgency and p.state in ("None", "Done", "On hold") and any(c["status"] in ("ready", "awaiting_doctor", "agreed") for c in cards):
                p.state, p.flagged_on = "Flagged", day
                ctx.log(day, p.pid, "flagged", f"{p.urgency}; {len(cards)} card(s)")
            # simulated doctor review of clinician cards, the day after they appear
            if ctx.mode == "new":
                for c in p.cards:
                    if c["status"] == "on_hold" and day >= c.get("review_on_day", day + 1):
                        c["status"] = "awaiting_doctor"           # a hold is not forever: the doctor sees it again
                        c["seen_day"] = day
                        ctx.log(day, p.pid, "hold_review", c["check"])
                    if c["status"] == "awaiting_doctor" and day >= c.get("seen_day", day) + 1:
                        # an abnormal result is always reviewed; other clinician cards can be overruled
                        if c["check"] == "results_review" or ctx.rng.random() < ctx.params["doctor_agree"]:
                            c["status"] = "agreed"
                            p.doctor_feedback = "Agree"
                            ctx.log(day, p.pid, "doctor_agree", c["check"])
                        else:
                            c["status"] = "on_hold"
                            c["review_on_day"] = day + DESIGN["on_hold_review_after_days"]
                            p.doctor_feedback = "Disagree"
                            ctx.log(day, p.pid, "doctor_disagree", c["check"])
                    c.setdefault("seen_day", day)
                if p.cards and all(c["status"] in ("on_hold", "done") for c in p.cards) and any(c["status"] == "on_hold" for c in p.cards) \
                        and p.state == "Flagged":
                    p.state = "On hold"
                    ctx.log(day, p.pid, "on_hold", "doctor disagreed with every open card")
            # deadlines
            if p.state == "Flagged" and p.urgency and day - p.flagged_on >= DEADLINE_DAYS[p.urgency]:
                p.priority += DESIGN["priority_boosts"]["missed_deadline"]   # escalate to the top of its urgency group
