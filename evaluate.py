"""
Evaluation (guide Steps 10.3 to 10.7, 13.3): this system against usual care, fairness by group,
the worst case from the sensitivity analysis, and the business case.

    python evaluate.py                 reads outputs/new and outputs/old, writes outputs/results/
    python evaluate.py --seeds         re-runs the headline with every seed (a few minutes): the averages the app shows
    python evaluate.py --sensitivity   also re-runs both ways under 64 settings around the real values (over an hour)
"""
import argparse
import itertools
import json
import os

import numpy as np
import pandas as pd

import simulation
import utils

RESULTS = os.path.join(utils.OUTPUTS, "results")
_G = utils.config("clinical_rules")["check_groups"]
WELL_CHILD, ADULT_SCREEN, INTERVENTIONS = set(_G["well_child"]), set(_G["adult_screening"]), set(_G["programmes"])
GROUPS = ["nationality", "region", "language", "insurance", "sex", "age_band"]
REAL = simulation.REAL                       # config/real_values.json: every price with its source
FX = REAL["usd_to_aed"]["value"]
C = {k: v["value"] for k, v in REAL["cost"].items()}
D = simulation.DESIGN
WINDOW = D["outcome_window_days"]


def load(mode):
    d = os.path.join(utils.OUTPUTS, mode)
    p = pd.read_csv(os.path.join(d, "people_final.csv"), dtype={"Id": str})
    for c in ("cards", "done", "detected", "diagnosed"):
        p[c] = p[c].map(json.loads)
    bands = D["fairness_age_bands"]
    p["age_band"] = pd.cut(p.age, [-1] + [hi for lo, hi in bands.values()], labels=list(bands)).astype(str)
    ev = pd.read_csv(os.path.join(d, "events.csv"), dtype={"person": str})
    msg = pd.read_csv(os.path.join(d, "messages.csv"), dtype={"person": str})
    info = utils.load_json(os.path.join(d, "run_info.json"))
    return p, ev, msg, info


def completion(p, checks):
    due = p.cards.map(lambda cs: {c["check"] for c in cs} & checks)
    done = p.done.map(lambda d: set(d) & checks)
    n_due = int(due.map(len).sum())
    n_done = int(sum(len(a & b) for a, b in zip(due, done)))
    people_due = int((due.map(len) > 0).sum())
    people_done = int(sum(bool(a & b) for a, b in zip(due, done)))
    return {"checks_due": n_due, "checks_done": n_done, "check_rate": round(n_done / max(n_due, 1), 3),
            "people_due": people_due, "people_completed": people_done, "people_rate": round(people_done / max(people_due, 1), 3),
            "people_rate_range": utils.wilson(people_done, people_due)}


def completion_same_basis(p, p_old, checks, children):
    """Completion on the SAME people and checks for both ways: what usual care's own rules say is due. This system
    also offers extra checks (development, lipids, wellbeing...); counting those would make the two rates divide by
    different groups, so they are reported separately."""
    due_old = p_old.set_index("Id").cards.map(lambda cs: {c["check"] for c in cs} & checks)
    who = p_old.set_index("Id").is_child == (1 if children else 0)
    due_old = due_old[who & (due_old.map(len) > 0)]
    done = p.set_index("Id").done.reindex(due_old.index).map(lambda d: set(d) & checks if isinstance(d, dict) else set())
    people_done = int(sum(bool(a & b) for a, b in zip(due_old, done)))
    n_due, n_done = int(due_old.map(len).sum()), int(sum(len(a & b) for a, b in zip(due_old, done)))
    return {"checks_due": n_due, "checks_done": n_done, "check_rate": round(n_done / max(n_due, 1), 3),
            "people_due": int(len(due_old)), "people_completed": people_done,
            "people_rate": round(people_done / max(len(due_old), 1), 3), "people_rate_range": utils.wilson(people_done, len(due_old)),
            "basis": "people and checks due under usual-care rules (same for both ways)"}


def metrics(p, ev, msg, info):
    days = info["days"]
    invited = p[p.first_invite_day.notna()]
    first_done = p.done.map(lambda d: min(d.values()) if d else None)
    timely = [(fd - fi) <= WINDOW for fd, fi in zip(first_done[invited.index], invited.first_invite_day) if fd is not None]
    detected = p.detected.map(len).sum()
    # follow-up completion counts only results found at least WINDOW days before the end (each had the full window)
    eligible = [(c, d, dia) for det, dia in zip(p.detected, p.diagnosed) for c, d in det.items() if d <= days - WINDOW]
    diagnosed_in_30 = sum(1 for c, d, dia in eligible if c in dia and dia[c] - d <= WINDOW)
    chw = int((msg.channel == "Community health worker visit").sum())
    n_msg = int((msg.channel != "reply").sum())
    agree = int((ev.event == "doctor_agree").sum())
    disagree = int((ev.event == "doctor_disagree").sum())
    m = {
        "days": days, "people": int(len(p)),
        "recommended": int((p.cards.map(len) > 0).sum()),
        "contacted": int(len(invited)), "messages_sent": n_msg,
        "well_child": completion(p[p.is_child == 1], WELL_CHILD),
        "adult_screening": completion(p[p.is_child == 0], ADULT_SCREEN),
        "preventive_actions": completion(p, INTERVENTIONS),
        "timely_attendance": round(float(np.mean(timely)), 3) if timely else 0.0,
        "abnormal_found": int(detected),
        "abnormal_found_by_condition": pd.Series([c for d in p.detected for c in d]).value_counts().to_dict(),
        "followup_within_30_days": round(diagnosed_in_30 / max(len(eligible), 1), 3),
        "followup_eligible_results": len(eligible),
        "followup_followed_up": int(diagnosed_in_30),
        "followup_range": utils.wilson(int(diagnosed_in_30), len(eligible)),   # 95% range: few results per run
        "diagnosed": int(p.diagnosed.map(len).sum()),
        "contacts_per_case_found": round(n_msg / max(detected, 1), 1),
        "opt_out_rate": round(float((p.state == "Opted out").sum() / max(len(invited), 1)), 3),
        "doctor_agreement": round(agree / max(agree + disagree, 1), 3) if agree + disagree else None,
        "community_health_worker_visits": chw,
        "messages_by_channel": {k: int(v) for k, v in msg[msg.channel != "reply"].channel.value_counts().items()},
        "visits_attended": int((ev.event == "attended").sum()),
        "attendance_of_booked_visits": round(int((ev.event == "attended").sum())
                                             / max(int(ev.event.isin(["attended", "missed_visit"]).sum()), 1), 3),
    }
    return m


def costs(m, mode, p):
    """Running cost of the period in AED, from published prices. Calls and home visits are staff time,
    paid through the nurses' salaries."""
    share = m["days"] / 365
    ch = m["messages_by_channel"]
    parts = {
        "WhatsApp messages": ch.get("WhatsApp", 0) * C["whatsapp_per_message_usd"] * FX,
        "SMS messages": ch.get("SMS", 0) * C["sms_per_message_usd"] * FX,
        "Nurse time": C["outreach_nurses_new" if mode == "new" else "outreach_nurses_usual_care"] * C["nurse_salary_per_year_aed"] * share,
    }
    if mode == "new":
        parts["Cloud server"] = C["hosting_per_hour_usd"] * 24 * m["days"] * FX
    outreach = sum(parts.values())
    # the visits themselves: doctor and nurse time at the clinic, telehealth or mobile clinic
    parts["Clinic visits"] = m["visits_attended"] * C["primary_care_visit_aed"]
    total = sum(parts.values())
    wc, ad = m.get("well_child_all_checks", m["well_child"]), m.get("adult_screening_all_checks", m["adult_screening"])
    done = wc["checks_done"] + ad["checks_done"]                  # every visit is paid for, so every check done counts
    people = wc["people_completed"] + ad["people_completed"]
    return {"total_cost_aed": round(total), "outreach_cost_aed": round(outreach), "visit_cost_aed": round(parts["Clinic visits"]),
            "parts_aed": {k: round(v, 1) for k, v in parts.items()}, "completed_checks": done, "people_completed": people,
            "cost_per_completed_check_aed": round(total / max(done, 1), 1),
            "cost_per_person_completed_aed": round(total / max(people, 1), 1),
            # the visit price is an estimate: the same figure at the low and high end of the published range
            "cost_per_person_completed_range_aed": [round((outreach + m["visits_attended"] * v) / max(people, 1), 1)
                                                    for v in C["primary_care_visit_aed_range"]],
            "outreach_cost_per_person_completed_aed": round(outreach / max(people, 1), 1)}


def sources_table():
    """Every number behind the results, for the app's 'Where the numbers come from' table."""
    rows = [{"group": "Exchange rate", "name": "usd_to_aed", **{k: REAL["usd_to_aed"].get(k) for k in ("value", "status", "source", "url")}}]
    for group, key in (("Costs", "cost"), ("Behaviour", "behaviour"), ("Design", "design")):
        for name, v in REAL[key].items():
            rows.append({"group": group, "name": name, "value": v["value"], "status": v["status"], "source": v["source"], "url": v.get("url")})
    for name, group in (("clinical_rules", "Clinical rules"), ("synthetic_data", "Synthetic data"), ("models", "Models")):
        rows += utils.config_rows(name, group)
    for name, v in simulation.CALIBRATED.items():
        rows.append({"group": "Calibrated", "name": name, "value": v["value"], "status": "calibrated",
                     "source": f"{v['target']} (achieved {v['achieved']}); {v['source']['text']}", "url": v["source"].get("url")})
    return rows


def found_by_day(p, truth, start, days):
    """Share of all hidden cases present at the start that were found by day 30 and by the end."""
    hidden = []
    for cond in ("diabetes", "hypertension"):
        t = truth[truth[f"hidden_{cond}"] & (truth[f"{cond}_onset"] <= start)]
        hidden += [(pid, cond) for pid in t.index if pid in set(p.Id)]
    det = p.set_index("Id").detected
    days_found = [det[pid].get(cond) for pid, cond in hidden]
    n = max(len(hidden), 1)
    return {"hidden_cases_at_start": len(hidden),
            "found_by_day_30": round(sum(d is not None and d <= 30 for d in days_found) / n, 3),
            f"found_by_day_{days}": round(sum(d is not None for d in days_found) / n, 3)}


def clinical_value(p_new, p_old):
    n = p_new.set_index("Id").detected
    o = p_old.set_index("Id").detected
    rows, earlier = [], []
    for pid in n.index:
        for cond, d in n[pid].items():
            od = o.get(pid, {}).get(cond)
            rows.append({"person": pid, "condition": cond, "day_new": d, "day_old": od})
            if od is not None:
                earlier.append(od - d)
    t = pd.DataFrame(rows)
    only_new = int(t.day_old.isna().sum()) if len(t) else 0
    return {"found_new": int(sum(map(len, n))), "found_old": int(sum(map(len, o))),
            "extra_found": int(sum(map(len, n)) - sum(map(len, o))),
            "found_only_by_new_system": only_new,
            "avg_days_earlier_when_both_found": round(float(np.mean(earlier)), 1) if earlier else None}


def safeguards(p, truth, start, mode_label="new"):
    """Challenge requirement 4: false-positive and false-negative patterns by group, checked against the hidden truth
    (never seen by the system).
      Risk flags (people the models score): a "true case" has diabetes or high blood pressure present at the start
      and not diagnosed, the question the two models answer. Overall ("either"): flagged when the combined chance is
      High; per condition: flagged when that condition's chance is High.
      False positive = flagged High without being a case; false negative = a case not flagged High.
      Local calibration: each condition's average predicted chance against the observed rate, by group.
      Whole system: hidden cases present at the start that the visits found (or missed) by the end.
    Groups under the small-group limit are hidden; rates from too few cases are not shown, to avoid misleading results."""
    import json as _json
    from models.risk_scores import CONDITIONS, cutoffs
    sg = utils._small_groups()
    t = truth.reindex(p.Id)
    hidden_now = {}
    for cond in CONDITIONS:
        onset = pd.to_datetime(t[f"{cond}_onset"]).values
        hidden_now[cond] = t[f"hidden_{cond}"].fillna(False).astype(bool).values & pd.notna(onset) & (onset <= np.datetime64(start))
    case = np.zeros(len(p), dtype=bool)
    for cond in CONDITIONS:
        case |= hidden_now[cond]
    scored = p.risk.notna().values
    flag = scored & (p.risk.fillna(-1).values >= cutoffs()["high"])
    risks = p["risks"].map(lambda s: _json.loads(s) if isinstance(s, str) else (s if isinstance(s, dict) else {})) \
        if "risks" in p.columns else pd.Series([{}] * len(p), index=p.index)
    by_c = cutoffs().get("by_condition", {})
    cond_risk = {c: np.array([(r.get(c) or {}).get("risk", np.nan) for r in risks], dtype=float) for c in CONDITIONS}
    found = {c: np.array([c in d for d in p.detected]) for c in CONDITIONS}
    cols = {"_case": case, "_scored": scored, "_flag": flag,
            "_hidden": sum(hidden_now[c].astype(int) for c in CONDITIONS),
            "_found": sum((hidden_now[c] & found[c]).astype(int) for c in CONDITIONS)}
    for c in CONDITIONS:
        cols[f"_r_{c}"], cols[f"_y_{c}"] = cond_risk[c], hidden_now[c]
        cols[f"_f_{c}"] = ~np.isnan(cond_risk[c]) & (np.nan_to_num(cond_risk[c], nan=-1) >= by_c.get(c, {}).get("high", cutoffs()["high"]))
    d = p.assign(**cols)

    def rate(k, n):
        return round(k / n, 3) if n >= sg["hide_under"] else None      # too few to give a rate

    def errors(s, case_col, flag_col):
        cases, non = int(s[case_col].sum()), int((~s[case_col]).sum())
        fp, fn, flagged = int((s[flag_col] & ~s[case_col]).sum()), int((~s[flag_col] & s[case_col]).sum()), int(s[flag_col].sum())
        return cases, non, fp, fn, flagged

    def row(name, x):
        s = x[x._scored]
        cases, non, fp, fn, flagged = errors(s, "_case", "_flag")
        hidden, got = int(x._hidden.sum()), int(x._found.sum())
        out = {"group": str(name), "count": len(x), "scored": len(s), "true_cases": cases, "flagged_high": flagged,
               "flag_rate": rate(flagged, len(s)),
               "false_positive_rate": rate(fp, non), "false_negative_rate": rate(fn, cases),
               "fnr_range": utils.wilson(fn, cases) if cases >= sg["hide_under"] else None,
               "cases_per_100_flagged": round(100 * (flagged - fp) / flagged, 1) if flagged >= sg["hide_under"] else None,
               "cases_not_scored": int((x._case & ~x._scored).sum()),
               "hidden_cases_at_start": hidden, "hidden_found_share": rate(got, hidden),
               "hidden_missed": hidden - got}
        for c in CONDITIONS:
            sc = x[~np.isnan(x[f"_r_{c}"].values)]
            out[f"{c}_predicted"] = round(float(sc[f"_r_{c}"].mean()), 3) if len(sc) >= sg["hide_under"] else None
            out[f"{c}_observed"] = rate(int(sc[f"_y_{c}"].sum()), len(sc))
        return out

    def condition_overall(c):
        s = d[~np.isnan(d[f"_r_{c}"].values)]
        cases, non, fp, fn, flagged = errors(s, f"_y_{c}", f"_f_{c}")
        return {"condition": c, "scored": len(s), "true_cases": cases, "flag_rate": rate(flagged, len(s)),
                "false_positive_rate": rate(fp, non), "false_negative_rate": rate(fn, cases),
                "cases_per_100_flagged": round(100 * (flagged - fp) / flagged, 1) if flagged >= sg["hide_under"] else None,
                "predicted": round(float(s[f"_r_{c}"].mean()), 3) if len(s) else None,
                "observed": rate(cases, len(s))}

    out = {"all": [row("Everyone", d)], "by_condition": [condition_overall(c) for c in CONDITIONS], "definitions": {
        "true_case": "diabetes or high blood pressure present at the start and not diagnosed (what the two models predict)",
        "flag": "chance at or above the High cut-off (top share of local scores), combined or per condition",
        "false_positive_rate": "flagged High, but not a true case (share of people who are not cases)",
        "false_negative_rate": "a true case, but not flagged High (share of cases); children and people with too little data are not scored and are counted under cases_not_scored",
        "hidden_found_share": "hidden cases present at the start that a visit found by the end of the run",
        "calibration": "each condition's average predicted chance against the observed rate in the same people",
        "small_groups": f"groups under {sg['hide_under']} people hidden; a rate needs at least {sg['hide_under']} people or cases"}}
    for g in GROUPS:
        rows = [row(val, x) for val, x in d.groupby(g)]
        tt = utils.suppress(pd.DataFrame(rows).set_index("group")).reset_index().astype(object)
        out[g] = tt.where(tt.notna(), None).to_dict("records")       # "no rate" stays empty, never NaN
    return out


def fairness(p):
    out = {}
    for g in GROUPS:
        rows = []
        for val, d in p.groupby(g):
            rec = d[d.cards.map(len) > 0]
            reached = rec.first_invite_day.notna().sum()
            completed = (rec.done.map(len) > 0).sum()
            rows.append({"group": str(val), "count": len(d), "recommendation_rate": round(len(rec) / len(d), 3),
                         "reach": round(reached / max(len(rec), 1), 3), "reach_range": utils.wilson(int(reached), len(rec)),
                         "uptake": round(completed / max(len(rec), 1), 3), "uptake_range": utils.wilson(int(completed), len(rec))})
        out[g] = utils.suppress(pd.DataFrame(rows).set_index("group")).reset_index().to_dict("records")
    return out


def gap_between_groups(fair, measure="uptake"):
    vals = [r[measure] for r in fair if isinstance(r.get(measure), (int, float)) and pd.notna(r[measure])]
    return round(max(vals) - min(vals), 3) if vals else None


def sensitivity(days=D["simulation"]["days"], seeds=tuple(D["sensitivity"]["seeds"])):
    import orchestrator
    base = simulation.DEFAULT_PARAMS["base_attendance_logit"]
    best_usual_care = round(1 - simulation.B["results_not_followed_up_low"], 3)   # the best study in the published range
    grid = {"telehealth_boost": [0.0, simulation.B["telehealth_boost"]],          # assumption: none at all, or as assumed
            "base_attendance_logit": [round(base - D["sensitivity"]["attendance_logit_drop"], 3), base],   # calibrated, and much worse
            "reply_mult": D["sensitivity"]["reply_multipliers"],                  # published reply rates, and 30% fewer
            "old_followup_rate": [simulation.DEFAULT_PARAMS["old_followup_rate"], best_usual_care],
            "doctor_agree": [D["sensitivity"]["doctor_agree_low"], simulation.B["doctor_agree"]],   # doctors agree far less often
            "clinic_slots_per_day": [D["sensitivity"]["clinic_slots_low"], simulation.DEFAULT_PARAMS["clinic_slots_per_day"]]}
    rows = []
    for combo in itertools.product(*grid.values()):
        params = dict(zip(grid, combo))
        for s in seeds:
            res = {}
            for mode in ("old", "new"):
                ctx = orchestrator.run(mode, s, days, params=params)
                eligible = [(c, d, p) for p in ctx.people.values() for c, d in p.detected.items() if d <= days - WINDOW]
                followed = sum(1 for c, d, p in eligible if c in p.diagnosed and p.diagnosed[c] - d <= WINDOW)
                res[mode] = (sum(len(p.detected) for p in ctx.people.values()),
                             sum(bool(p.done) for p in ctx.people.values()), followed / max(len(eligible), 1))
            rows.append({**params, "seed": s, "extra_found": res["new"][0] - res["old"][0],
                         "extra_people_completed": res["new"][1] - res["old"][1],
                         "followup_new": res["new"][2], "followup_old": res["old"][2],
                         "followup_gap": res["new"][2] - res["old"][2]})
        print(f"  sensitivity {params}: extra found {np.mean([r['extra_found'] for r in rows[-len(seeds):]]):.1f}")
    t = pd.DataFrame(rows).groupby(list(grid)).mean(numeric_only=True).drop(columns="seed").reset_index()
    worst = t.loc[t.extra_found.idxmin()].to_dict()
    return t, worst


def seed_spread(seeds=None):
    """The headline results re-run with several random seeds, both ways (same seed for both), reported as the average
    and the range: one run's luck should not be read as an effect. Follow-up rests on few results per run, so its
    counts are kept too."""
    import orchestrator
    seeds = seeds or D["spread_seeds"]
    rows = []
    for seed in seeds:
        tabs = {}
        for mode in ("new", "old"):
            p = orchestrator.people_table(orchestrator.run(mode, seed=seed))
            for c in ("cards", "done", "detected", "diagnosed"):
                p[c] = p[c].map(json.loads)
            tabs[mode] = p
        row = {"seed": seed}
        days = D["simulation"]["days"]
        for mode, p in tabs.items():
            row[f"well_child_{mode}"] = completion_same_basis(p, tabs["old"], WELL_CHILD, children=True)["people_rate"]
            row[f"adult_screening_{mode}"] = completion_same_basis(p, tabs["old"], ADULT_SCREEN, children=False)["people_rate"]
            row[f"found_{mode}"] = int(sum(map(len, p.detected)))
            el = [(c, d, x) for det, x in zip(p.detected, p.diagnosed) for c, d in det.items() if d <= days - WINDOW]
            row[f"followup_{mode}"] = round(sum(1 for c, d, x in el if c in x and x[c] - d <= WINDOW) / max(len(el), 1), 3)
            row[f"followup_results_{mode}"] = len(el)
        rows.append(row)
        print(f"  seed {seed}: {row}")
    t = pd.DataFrame(rows).set_index("seed")
    return {"seeds": list(seeds), "runs": rows,
            "summary": {c: {"mean": round(float(t[c].mean()), 3), "min": round(float(t[c].min()), 3), "max": round(float(t[c].max()), 3)}
                        for c in t.columns}}


AI_SETTINGS = {"rules_only": {"ai_learn_first_channel": False},
               "channel_learner": {"ai_learn_first_channel": True}}


def ablation(seeds=None):
    """What the one AI step that acts inside the rules adds (the first-message channel learner): the headline results with it
    off and on, same seeds, same people, same capacity. Reported whatever it shows: a step that does not help is said not to help."""
    import orchestrator
    seeds = seeds or D["spread_seeds"]
    days, rows = D["simulation"]["days"], []
    for seed in seeds:
        old = orchestrator.people_table(orchestrator.run("old", seed=seed))
        for c in ("cards", "done", "detected", "diagnosed"):
            old[c] = old[c].map(json.loads)
        for name, params in AI_SETTINGS.items():
            ctx = orchestrator.run("new", seed=seed, params=params)
            p = orchestrator.people_table(ctx)
            for c in ("cards", "done", "detected", "diagnosed"):
                p[c] = p[c].map(json.loads)
            row = {"setting": name, "seed": seed,
                   "well_child": completion_same_basis(p, old, WELL_CHILD, children=True)["people_rate"],
                   "adult_screening": completion_same_basis(p, old, ADULT_SCREEN, children=False)["people_rate"],
                   "found": int(sum(map(len, p.detected))),
                   "people_with_completed_care": int(p.done.map(bool).sum()),
                   "people_contacted": int(p.first_invite_day.notna().sum())}
            if ctx.channel_learner is not None:           # what the learner saw: replies per 100 first messages, by channel
                s = pd.DataFrame(ctx.channel_learner.summary())
                for ch, g in (s.groupby("channel") if len(s) else []):
                    row[f"first_messages_{ch}"] = int(g.messages.sum())
                    row[f"replies_per_100_{ch}"] = round(100 * g.replies.sum() / max(g.messages.sum(), 1), 1)
            rows.append(row)
        print(f"  ablation seed {seed} done")
    t = pd.DataFrame(rows)
    num = [c for c in t.columns if c not in ("setting", "seed")]
    summary = {name: {c: round(float(g[c].mean()), 3) for c in num if g[c].notna().any()} for name, g in t.groupby("setting", sort=False)}
    return {"seeds": list(seeds), "settings": {k: v for k, v in AI_SETTINGS.items()}, "summary": summary, "runs": rows,
            "note": "Averages over the seeds. rules_only is the system without the channel learner."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ablation", action="store_true", help="re-run with each AI step off and on (design.ai_features)")
    ap.add_argument("--sensitivity", action="store_true")
    ap.add_argument("--seeds", action="store_true", help="also re-run the headline results with every seed in design.spread_seeds")
    a = ap.parse_args()
    os.makedirs(RESULTS, exist_ok=True)
    pn, en, mn, info_n = load("new")
    po, eo, mo, info_o = load("old")
    mn_, mo_ = metrics(pn, en, mn, info_n), metrics(po, eo, mo, info_o)
    for m, p in ((mn_, pn), (mo_, po)):          # headline completion: same people and checks for both ways
        m["well_child_all_checks"], m["adult_screening_all_checks"] = m["well_child"], m["adult_screening"]
        m["well_child"] = completion_same_basis(p, po, WELL_CHILD, children=True)
        m["adult_screening"] = completion_same_basis(p, po, ADULT_SCREEN, children=False)
    cn, co = costs(mn_, "new", pn), costs(mo_, "old", po)
    cv = clinical_value(pn, po)
    truth, start = utils.load_truth(), pd.Timestamp(info_n["start"])
    cv["share_of_hidden_cases_found_new"] = found_by_day(pn, truth, start, info_n["days"])
    cv["share_of_hidden_cases_found_old"] = found_by_day(po, truth, start, info_o["days"])
    extra_cost = cn["total_cost_aed"] - co["total_cost_aed"]
    fair_new, fair_old = fairness(pn), fairness(po)
    value = {   # no published UAE figure for the saving from finding a case early, so no return-on-investment is claimed
        "extra_cost_aed": extra_cost, "extra_cases_found": cv["extra_found"],
        "extra_cost_per_extra_case_found_aed": round(extra_cost / cv["extra_found"], 1) if cv["extra_found"] > 0 else None,
        "diabetes_cost_per_person_year_aed": round(C["diabetes_cost_per_person_year_usd"] * FX),
    }
    results = {
        "run": info_n, "new": mn_, "old": mo_, "cost_new": cn, "cost_old": co, "clinical_value": cv,
        "value": value, "sources": sources_table(),
        "prices": {"visit_aed": C["primary_care_visit_aed"], "visit_range_aed": C["primary_care_visit_aed_range"],
                   "best_published_followup_rate": round(1 - simulation.B["results_not_followed_up_low"], 3)},
        "calibration_check": {"usual_care_attendance": mo_["attendance_of_booked_visits"],
                              "usual_care_attendance_target": round(1 - simulation.B["no_show_rate"], 3),
                              "usual_care_opt_out": mo_["opt_out_rate"], "usual_care_opt_out_target": simulation.B["text_opt_out_rate"],
                              # the calibration itself averages 6 runs; one run of about 450 visits can swing several points
                              "usual_care_attendance_6_runs": simulation.CALIBRATED.get("base_attendance_logit", {}).get("achieved"),
                              "usual_care_opt_out_6_runs": simulation.CALIBRATED.get("stop_share", {}).get("achieved")},
        "fairness_new": fair_new, "fairness_old": fair_old,
        # false positives, false negatives and missed hidden cases by group, against the hidden truth (requirement 4)
        "safeguards_new": safeguards(pn, truth, start), "safeguards_old": safeguards(po, truth, start),
        "missed_case_gap_between_groups": {g: gap_between_groups(safeguards(pn, truth, start)[g], "hidden_found_share") for g in GROUPS},
        "uptake_gap_between_groups": {g: {"new": gap_between_groups(fair_new[g]), "old": gap_between_groups(fair_old[g])} for g in GROUPS},
        "risk_model": utils.load_json(os.path.join(utils.MODELS, "risk_model_card.json")),
        "nhanes": utils.load_json(os.path.join(utils.MODELS, "nhanes_validation.json")),
        "noshow_model": utils.load_json(os.path.join(utils.OUTPUTS, "new", "noshow_model.json")),
    }
    if a.sensitivity:
        table, worst = sensitivity(days=info_n["days"])
        table.to_csv(os.path.join(RESULTS, "sensitivity.csv"), index=False)
        results["sensitivity_worst_case"] = worst
    elif os.path.exists(os.path.join(RESULTS, "sensitivity.csv")):
        t = pd.read_csv(os.path.join(RESULTS, "sensitivity.csv"))
        results["sensitivity_worst_case"] = t.loc[t.extra_found.idxmin()].to_dict()
    sens = os.path.join(RESULTS, "sensitivity.csv")
    if os.path.exists(sens):
        t = pd.read_csv(sens)
        if "followup_gap" in t:
            results["sensitivity_worst_followup"] = t.loc[t.followup_gap.idxmin()].to_dict()
    results["ai_features"] = D["ai_features"]
    ablation_file = os.path.join(RESULTS, "ai_ablation.json")
    if a.ablation:
        utils.save_json(ablation(), ablation_file)
    if os.path.exists(ablation_file):
        results["ai_ablation"] = utils.load_json(ablation_file)
    spread_file = os.path.join(RESULTS, "seed_spread.json")
    if a.seeds:
        utils.save_json(seed_spread(), spread_file)
    if os.path.exists(spread_file):
        results["seed_spread"] = utils.load_json(spread_file)
    utils.save_json(results, os.path.join(RESULTS, "results.json"))
    for g in GROUPS:
        pd.DataFrame(fair_new[g]).to_csv(os.path.join(RESULTS, f"fairness_{g}.csv"), index=False)
    print_summary(results)


def print_summary(r):
    n, o, cv = r["new"], r["old"], r["clinical_value"]
    print(f"Completed Well-Child care (people): new {n['well_child']['people_rate']:.0%} vs usual {o['well_child']['people_rate']:.0%}")
    print(f"Completed adult screening (people): new {n['adult_screening']['people_rate']:.0%} vs usual {o['adult_screening']['people_rate']:.0%}")
    print(f"Hidden cases found: new {cv['found_new']} vs usual {cv['found_old']} (extra {cv['extra_found']}); "
          f"days earlier when both found: {cv['avg_days_earlier_when_both_found']}")
    sn, so = cv["share_of_hidden_cases_found_new"], cv["share_of_hidden_cases_found_old"]
    print(f"Share of all {sn['hidden_cases_at_start']} hidden cases found by day 30: new {sn['found_by_day_30']:.0%} vs usual {so['found_by_day_30']:.0%}; "
          f"by the end: new {list(sn.values())[-1]:.0%} vs usual {list(so.values())[-1]:.0%}")
    print(f"Follow-up within 30 days: new {n['followup_within_30_days']:.0%} vs usual {o['followup_within_30_days']:.0%}")
    print(f"Cost per person with completed care (outreach + visits): new {r['cost_new']['cost_per_person_completed_aed']} AED vs usual "
          f"{r['cost_old']['cost_per_person_completed_aed']} AED; outreach only {r['cost_new']['outreach_cost_per_person_completed_aed']} vs "
          f"{r['cost_old']['outreach_cost_per_person_completed_aed']} AED; "
          f"extra cost per extra case found {r['value']['extra_cost_per_extra_case_found_aed']} AED "
          f"(a year of diabetes care costs {r['value']['diabetes_cost_per_person_year_aed']} AED)")
    c = r["calibration_check"]
    print(f"Calibration check (usual care): attendance {c['usual_care_attendance']} in this run, {c.get('usual_care_attendance_6_runs')} over the 6 calibration runs, vs {c['usual_care_attendance_target']}, "
          f"opt-out {c['usual_care_opt_out']} vs {c['usual_care_opt_out_target']}")
    if "sensitivity_worst_case" in r:
        w = r["sensitivity_worst_case"]
        print(f"Worst case: telehealth boost {w['telehealth_boost']}, base {w['base_attendance_logit']}, reply x{w['reply_mult']}, "
              f"doctors agree {w.get('doctor_agree')}, slots {w.get('clinic_slots_per_day')}: "
              f"extra found {w['extra_found']:.1f}, extra people completed {w['extra_people_completed']:.1f}")
    print("Results written to outputs/results/")


if __name__ == "__main__":
    main()
