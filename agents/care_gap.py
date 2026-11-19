"""
Care-gap agent (guide Steps 3.1 to 3.6): overdue checks, warning signs, developmental and genetic gaps,
and preventive interventions, from transparent rules.

Every age, interval and threshold comes from config/clinical_rules.json, with the guideline it comes from.
Checks done during the simulation (person.done) count as the latest test, so a completed check
closes its gap and the next one comes back when its interval passes (recall).
"""
import pandas as pd

import utils
from agents.base import Agent
from utils import MONTH

R = utils.config("clinical_rules")
ADULT = R["adult_age"]
REVIEW_EVERY = utils.design("weekly_review_every_days")
GENETIC_RULES = {m: (g["check"], g["from_age"], g["every_months"], g["extra"]) for m, g in R["genetic_rules"].items()}
ACTIONABLE = set(R["actionable_variants"])
# record date that counts as the last test for each genetic-pathway check (so a recent test is not repeated)
RECORD_DATE = R["record_date_for_check"]
_PRE_LO, _PRE_HI = R["prediabetes_hba1c"]
INTERVENTIONS = [
    ("prediabetes_programme", lambda r: r.age >= ADULT and pd.notna(r.hba1c) and _PRE_LO <= r.hba1c < _PRE_HI and not r.has_diabetes),
    ("weight_support", lambda r: r.age >= ADULT and pd.notna(r.bmi) and r.bmi >= R["weight_support_bmi"]),
    ("stop_smoking_service", lambda r: r.age >= ADULT and bool(r.smoker)),
    ("family_nutrition_programme", lambda r: r.age < ADULT and pd.notna(r.bmi_percentile)
     and r.bmi_percentile >= R["child_obesity_bmi_percentile"]),
]
OLD_WAY_CHECKS = set(R["usual_care_checks"])


# how often each check repeats, from the same rules the gaps use: the one source for pathway cards and guidelines.csv
RECALL = {"well_child_visit": R["well_child_interval_months_after_1"], "bp_check": R["bp_check_every_months"],
          "diabetes_screen": R["diabetes_screen"]["every_months"], "hba1c_test": R["hba1c_every_months_with_diabetes"],
          "lipid_test": R["lipid_test"]["every_months"], "cervical_screening": R["cervical_screen"]["every_months"],
          "breast_screening": R["breast_screen"]["every_months"], "bowel_screening": R["bowel_screen"]["every_months"],
          "depression_screen": R["depression_screen_every_months"]}


def between(x, lo_hi):
    return lo_hi[0] <= x <= lo_hi[1]


def months_since(row_date, done_day, today_day, start):
    """Months since the latest of: the record date, or the day it was done in the simulation."""
    last = row_date if pd.notna(row_date) else None
    if done_day is not None:
        d = start + pd.Timedelta(days=done_day)
        last = d if last is None or d > last else last
    if last is None:
        return None
    return (start + pd.Timedelta(days=today_day) - last).days / MONTH


def overdue(m, every):
    return m is None or m > every


def find_gaps(p, ctx, day, mode="new"):
    r, start, done = p.row, ctx.start, p.done
    gaps = []

    def add(check, reason, pathway):
        if not any(g["check"] == check for g in gaps):
            gaps.append({"check": check, "reason": reason, "pathway": pathway})

    age_m = r.age_months + day / MONTH
    if r.age < ADULT:
        interval = R["well_child_interval_months_under_1"] if age_m < 12 else R["well_child_interval_months_after_1"]
        m = months_since(r.last_well_child_visit, done.get("well_child_visit"), day, start)
        if (r.missed_well_child > 0 and "well_child_visit" not in done) or overdue(m, interval + R["well_child_grace_months"]):
            add("well_child_visit", "Missed or overdue well-child visit", "Well-Child Visit")
        if mode == "new":
            for a in R["developmental_screen_ages_months"]:
                if between(age_m - a, R["screen_window_months"]) and (m is None or m > age_m - a) and "developmental_screen" not in done:
                    add("developmental_screen", f"No development check since {a} months", "Well-Child Visit")
            for a in R["autism_screen_ages_months"]:
                if between(age_m - a, R["screen_window_months"]) and (m is None or m > age_m - a) and "autism_screen" not in done:
                    add("autism_screen", f"No autism screen since {a} months", "Well-Child Visit")
            # UAE schedule: vaccines at 2, 4, 6, 12 and 18 months, so no vaccine for over 6 months before age 2 is a gap
            vc = R["vaccine_catch_up"]
            if vc["from_age_months"] <= age_m < vc["under_age_months"] \
                    and overdue(months_since(r.last_vaccine, done.get("vaccine_catch_up"), day, start), vc["gap_months"]):
                add("vaccine_catch_up", f"No vaccine recorded in over {vc['gap_months']} months (under {vc['under_age_months'] // 12})",
                    "Well-Child Visit")
            if (between(age_m, R["mmr_first_dose_window_months"]) and r.mmr_doses == 0) \
                    or (between(age_m, R["mmr_second_dose_window_months"]) and r.mmr_doses < 2):
                if "mmr_vaccine" not in done:
                    add("mmr_vaccine", "Measles vaccine dose missing", "Well-Child Visit")
    else:
        female = r.GENDER == "F"
        bp_every = R["bp_check_every_months_with_hypertension"] if r.has_hypertension else R["bp_check_every_months"]
        if overdue(months_since(r.sbp_date, done.get("bp_check"), day, start), bp_every):
            add("bp_check", f"High blood pressure with no check in {bp_every} months" if r.has_hypertension
                else f"Blood pressure not checked in {bp_every} months", "Adult comprehensive screening")
        hba1c_m = months_since(r.hba1c_date, done.get("diabetes_screen") or done.get("hba1c_test"), day, start)
        ds, h_every = R["diabetes_screen"], R["hba1c_every_months_with_diabetes"]
        if r.has_diabetes and overdue(hba1c_m, h_every):
            add("hba1c_test", f"Diabetes with no HbA1c in {h_every} months", "Adult comprehensive screening")
        elif not r.has_diabetes and (r.age >= ds["from_age"] or (mode == "new" and pd.notna(r.bmi) and r.bmi >= ds["or_bmi_at_least"])) \
                and overdue(hba1c_m, ds["every_months"]):
            add("diabetes_screen", f"No diabetes screen in {ds['every_months'] // 12} years", "Adult comprehensive screening")
        cs, bs, ws = R["cervical_screen"], R["breast_screen"], R["bowel_screen"]
        if female and between(r.age, cs["ages"]) and overdue(months_since(r.last_cervical_screen, done.get("cervical_screening"), day, start), cs["every_months"]):
            add("cervical_screening", "Cervical screening overdue", "Adult comprehensive screening")
        if female and between(r.age, bs["ages"]) and overdue(months_since(r.last_breast_screen, done.get("breast_screening"), day, start), bs["every_months"]):
            add("breast_screening", "Breast screening overdue", "Adult comprehensive screening")
        if between(r.age, ws["ages"]) and overdue(months_since(r.last_bowel_screen, done.get("bowel_screening"), day, start), ws["every_months"]):
            add("bowel_screening", "Bowel screening overdue", "Adult comprehensive screening")
        if mode == "new":
            lt = R["lipid_test"]
            if (r.age >= lt["from_age"] or r.gene_FH) and overdue(months_since(r.ldl_date, done.get("lipid_test"), day, start), lt["every_months"]):
                add("lipid_test", f"Cholesterol not checked in {lt['every_months'] // 12} years", "Adult comprehensive screening")
            if overdue(months_since(r.last_depression_screen, done.get("depression_screen"), day, start), R["depression_screen_every_months"]):
                add("depression_screen", "Wellbeing check overdue", "Adult comprehensive screening")
            lung = R["lung_screen_ages_smokers"]
            if between(r.age, lung) and bool(r.smoker) and "lung_screening" not in done:
                add("lung_screening", f"Smoker aged {lung[0]}-{lung[1]}", "Adult comprehensive screening")
            if not r.data_sufficient and "basic_health_check" not in done:
                add("basic_health_check", "Not enough data to judge", "Adult comprehensive screening")

    if mode == "new":
        # genetic rules: only with genetic consent, only for clear (not uncertain) findings
        if r.consent_genetic:
            for marker, (check, start_age, every, extra) in GENETIC_RULES.items():
                status = r.gene_status.get(marker)
                if not getattr(r, f"gene_{marker}") or status not in ACTIONABLE or r.age < start_age:
                    continue
                rec_date = getattr(r, RECORD_DATE[check]) if check in RECORD_DATE else None
                last = months_since(rec_date, done.get(check), day, start)
                if (every and overdue(last, every)) or (not every and last is None):
                    add(check, f"Genetic marker: {marker}", "Genetic pathway" if check.endswith("counselling") else gaps_pathway(check))
                if extra and extra not in done:
                    add(extra, f"Genetic marker: {marker}", "Genetic pathway")
            if r.gene_HBB_CARRIER and r.gene_status.get("HBB_CARRIER") in ACTIONABLE and not r.married \
                    and between(r.age, R["premarital_ages"]) and "premarital_screening" not in done:
                add("premarital_screening", "Carrier: premarital counselling", "Genetic pathway")
        for name, offer in INTERVENTIONS:
            if name not in done and offer(r):
                add(name, "Lifestyle or lab finding", "Preventive intervention")
    else:
        gaps = [g for g in gaps if g["check"] in OLD_WAY_CHECKS]
    return gaps


def gaps_pathway(check):
    return "Well-Child Visit" if check in ("cholesterol_test",) else "Adult comprehensive screening"


def warning_signs(p, ctx, day):
    r, signs = p.row, []
    if pd.isna(r.last_visit) or r.days_since_last_visit + day > R["warning_no_visit_days"]:
        signs.append(f"No clinic visit for over {R['warning_no_visit_days'] // 365} years")
    if r.age < R["warning_missed_check_under_age"] and r.missed_well_child >= 1 and "well_child_visit" not in p.done:
        signs.append("Missed child check-up or vaccine")
    if r.has_chronic and (pd.isna(r.months_since_chronic_test) or r.months_since_chronic_test + day / MONTH > R["warning_chronic_test_months"]) \
            and not ({"hba1c_test", "bp_check"} & set(p.done)):
        signs.append("Long-term illness with no recent test")
    if r.abnormal_no_followup and "results_review" not in p.done:
        signs.append("Abnormal result with no follow-up")
    if r.er_visits_12m_same_problem >= R["warning_er_visits_same_problem"]:
        signs.append("Many emergency visits for the same problem")
    if (r.family_hist_diabetes or r.genetic_risk_flag) and not r.screened and "diabetes_screen" not in p.done:
        signs.append("Family or genetic risk with no screening")
    if r.dev_concern and not r.dev_assessed:
        signs.append("Development concern with no assessment")
    if r.behaviour_concern and not r.behaviour_assessed:
        signs.append("Behaviour concern with no assessment")
    if r.child_growth_concern:
        signs.append("Child growth concern")
    if r.pregnant_missed_check:
        signs.append("Pregnancy with no recent check")
    for cond in p.needs_results_review:
        signs.append(f"Abnormal {cond} result awaiting review")
    return signs


class CareGapAgent(Agent):
    name = "care_gap"

    def run(self, ctx, day):
        for p in ctx.people.values():
            if p.state in ("Opted out",):
                continue
            # recompute on day 0 and for anyone whose records changed (a completed visit)
            if day == 0 or p.last_action == "records_changed":
                p.gaps = find_gaps(p, ctx, day, ctx.mode)
                p.warning_signs = warning_signs(p, ctx, day) if ctx.mode == "new" else []
                if p.last_action == "records_changed":
                    p.last_action = None
            elif day % REVIEW_EVERY == 0:                       # weekly: pick up recalls and warning signs that have come due
                p.gaps = find_gaps(p, ctx, day, ctx.mode)
                if ctx.mode == "new":
                    p.warning_signs = warning_signs(p, ctx, day)
