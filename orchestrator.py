"""
Orchestrator (guide Step 9): runs the seven agents in order, every simulated day.

    python orchestrator.py                 60 days, this system and usual care, writes outputs/new and outputs/old
    python orchestrator.py --days 30 --seed 3

Both runs use the same people, the same simulated world and the same daily capacity; only the way of
choosing, contacting and following up people differs.
"""
import argparse
import json
import os
import time

import numpy as np
import pandas as pd

import consent
import fhir
import simulation
import utils
from agents.base import Context, Person
from agents.care_gap import CareGapAgent
from agents.chat import ChatAgent
from agents.evaluation import EvaluationAgent
from agents.followup import Bandit, FollowUpAgent
from agents.outreach import ChannelLearner, OutreachAgent
from agents.priority import PriorityAgent
from agents.risk import RiskAgent

_CACHE = {}


def shared_inputs():
    """Loaded once and reused by every run (the sensitivity analysis runs the simulation many times)."""
    if not _CACHE:
        _CACHE.update(features=utils.load_features(), guidelines=utils.load_guidelines(), templates=utils.load_templates(),
                      reply_words=utils.load_reply_words(), calendar=utils.load_calendar(), truth=utils.load_truth(),
                      risk=RiskAgent())
    return _CACHE


def build_context(mode, seed, params, start):
    s = shared_inputs()
    f = s["features"]
    people = {r.Id: Person(pid=r.Id, row=r) for r in f.itertuples(index=False)}
    rng = np.random.default_rng(seed)
    ctx = Context(mode=mode, start=start, features=f, people=people, guidelines=s["guidelines"], templates=s["templates"],
                  reply_words=s["reply_words"], calendar=s["calendar"], truth=s["truth"], rng=rng, params=params)
    ctx.bandit = Bandit(np.random.default_rng(seed + SIM["learner_seed_offset"])) if mode == "new" else None
    if mode == "new" and params.get("ai_learn_first_channel", simulation.DESIGN["ai_features"]["learn_first_channel"]):
        ctx.channel_learner = ChannelLearner(np.random.default_rng(seed + 2 * SIM["learner_seed_offset"]))
    return ctx


SIM = simulation.DESIGN["simulation"]


def run(mode="new", seed=SIM["seed"], days=SIM["days"], params=None, start=None, write=False, quiet=True):
    params = {**simulation.DEFAULT_PARAMS, **(params or {})}
    start = pd.Timestamp(start or utils.run_date())
    ctx = build_context(mode, seed, params, start)
    risk = shared_inputs()["risk"]
    agents = [CareGapAgent(), risk, PriorityAgent(), OutreachAgent(), ChatAgent(), FollowUpAgent(), ChatAgent(), EvaluationAgent()]
    for day in range(days):
        for agent in agents:
            agent.run(ctx, day)
        if day == 0:
            ctx.start_gaps = {p.pid: len(p.gaps) for p in ctx.people.values()}
        if not quiet and day % 10 == 0:
            d = ctx.daily[-1]
            print(f"  [{mode}] day {day}: contacted {d['ever_contacted']}, completed {d['people_with_completed_care']}, "
                  f"abnormal found {d['abnormal_found']}")
    if write:
        write_outputs(ctx, mode)
    return ctx


def people_table(ctx):
    rows = []
    for p in ctx.people.values():
        r = p.row
        rows.append({"Id": p.pid, "age": r.age, "sex": r.GENDER, "nationality": r.nationality, "language": r.language,
                     "insurance": r.insurance, "region": r.region, "area_type": r.area_type, "home_place": r.home_place,
                     "household_id": r.household_id, "guardian_id": r.guardian_id, "is_child": int(p.is_child),
                     "state": p.state, "urgency": p.urgency, "priority": p.priority,
                     "risk": p.risk, "risk_low": p.risk_low, "risk_high": p.risk_high, "risk_reasons": json.dumps(p.risk_reasons), "risk_found": int(p.risk_found), "risk_normal_day": p.risk_normal_day,
                     "risks": json.dumps(p.risks),
                     "child_risk": p.child_risk, "child_risk_low": p.child_risk_low, "child_risk_high": p.child_risk_high,
                     "child_reasons": json.dumps(p.child_reasons), "known_diabetes": int(bool(r.has_diabetes)), "known_hypertension": int(bool(r.has_hypertension)),
                     "data_sufficient": int(r.data_sufficient), "data_used": json.dumps(p.data_used),
                     "warning_signs": json.dumps(p.warning_signs), "cards": json.dumps(p.cards, default=str),
                     "n_gaps_start": None, "first_invite_day": p.first_invite_day, "reminders": p.reminders,
                     "done": json.dumps(p.done), "detected": json.dumps(p.detected), "diagnosed": json.dumps(p.diagnosed),
                     "doctor_feedback": p.doctor_feedback, "past_no_shows": p.past_no_shows, "support": p.support,
                     "consent": int(r.consent), "consent_genetic": int(r.consent_genetic),
                     "may_contact": int(consent.has(ctx, p, "contact") and consent.has(ctx, ctx.guardian(p), "contact")),
                     "home_lat": r.home_lat, "home_lon": r.home_lon, "travel_min": r.travel_min,
                     "nearest_facility": r.nearest_facility, "access_barrier": r.access_barrier})
    return pd.DataFrame(rows)


def write_outputs(ctx, mode):
    out = os.path.join(utils.OUTPUTS, mode)
    os.makedirs(out, exist_ok=True)
    pt = people_table(ctx)
    start_gaps = getattr(ctx, "start_gaps", {})
    pt["n_gaps_start"] = pt.Id.map(start_gaps)
    pt.to_csv(os.path.join(out, "people_final.csv"), index=False)
    pd.DataFrame(ctx.events).to_csv(os.path.join(out, "events.csv"), index=False)
    pd.DataFrame(ctx.messages).to_csv(os.path.join(out, "messages.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(ctx.daily).to_csv(os.path.join(out, "daily.csv"), index=False)
    pd.DataFrame(ctx.invites).to_csv(os.path.join(out, "invites.csv"), index=False)
    pd.DataFrame(ctx.audit).to_csv(os.path.join(out, "audit_log.csv"), index=False)
    pd.DataFrame(ctx.consents).to_csv(os.path.join(out, "consents.csv"), index=False)
    pd.DataFrame(ctx.alerts, columns=["day", "date", "kind", "detail", "level", "repeated", "last_seen_day"]).to_csv(os.path.join(out, "alerts.csv"), index=False)
    if ctx.bandit is not None:
        pd.DataFrame(ctx.bandit.summary()).to_csv(os.path.join(out, "followup_learning.csv"), index=False)
        utils.save_json({"support_planned_by": "rule: access barrier and past missed visits (no learned model)"}, os.path.join(out, "noshow_model.json"))
        fhir.write_bundle(ctx, os.path.join(out, "fhir_tasks.json"))
        pd.DataFrame(ctx.channel_learner.summary() if ctx.channel_learner else [],
                     columns=["phone_type", "can_read", "area_type", "channel", "messages", "replies", "reply_rate"]
                     ).to_csv(os.path.join(out, "channel_learning.csv"), index=False)
    utils.save_json({"mode": mode, "start": ctx.start.date().isoformat(), "days": len(ctx.daily), "params": ctx.params},
                    os.path.join(out, "run_info.json"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=SIM["days"])
    ap.add_argument("--seed", type=int, default=SIM["seed"])
    ap.add_argument("--start", default=None, help="simulation start date, e.g. 2027-01-20 to include Ramadan")
    a = ap.parse_args()
    utils.ensure_dirs()
    for mode in ("old", "new"):
        t0 = time.time()
        ctx = run(mode, a.seed, a.days, start=a.start, write=False, quiet=False)
        write_outputs(ctx, mode)
        d = ctx.daily[-1]
        print(f"{mode}: {d['ever_contacted']} contacted, {d['people_with_completed_care']} with completed care, "
              f"{d['abnormal_found']} abnormal results, {d['diagnosed']} diagnosed ({time.time() - t0:.0f} s)")
    print("outputs written to outputs/new and outputs/old; next: python evaluate.py")


if __name__ == "__main__":
    main()
