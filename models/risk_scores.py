"""
Risk scores from published, validated questionnaires. Nothing is trained here.

    python -m models.risk_scores        scales the score to the local level, sets the High/Moderate cut-offs, writes the model card

* Diabetes: FINDRISC (Lindstrom and Tuomilehto, Diabetes Care 2003), a points score from age, BMI, waist, treatment for high blood
  pressure, a history of high blood sugar, family history, daily exercise and daily vegetables. The published bands give the chance
  of developing diabetes (1%, 4%, 17%, 33%, 50%); they are read as a smooth curve and moved, on the log-odds scale, so the average
  equals the expected undiagnosed rate among UAE adults (WHO prevalence minus the diagnosed share in the records). That one shift
  changes the level, never the order.
* The records have no waist, exercise or vegetable answers (and sometimes no family history). The score uses what is known, adds half
  of the points the missing items could add as its central value, and gives the full possible spread as the range. A person is
  never scored as if those answers were known.
* High blood pressure: no predicted score. Blood pressure is measured, so a reading at or above the threshold is a finding
  (an abnormal-result card), and treated or diagnosed blood pressure counts as an input to the diabetes score.
* Reasons are the items that gave the points, so a doctor sees exactly why. No nationality, language, insurance or region is used.

How well the score ranks real people is checked in models/validate_scores.py on real NHANES adults.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import simulation  # noqa: E402
import utils  # noqa: E402
from utils import FEATURES, MODELS, REAL, ensure_dirs, save_json  # noqa: E402

M = utils.config("models")
CR = utils.config("clinical_rules")
SD = utils.config("synthetic_data")
CONDITIONS = ["diabetes"]                                # the conditions that get a predicted score
SHOWN = ["diabetes", "hypertension"]                     # the conditions a doctor sees a row for
KNOWN = {"diabetes": "has_diabetes", "hypertension": "has_hypertension"}
NAMES = {"diabetes": "Diabetes", "hypertension": "High blood pressure"}
HIGH_SHARE = simulation.DESIGN["risk_high_share"]        # High = top 15% of scores (config/real_values.json)
MODERATE_SHARE = simulation.DESIGN["risk_moderate_share"]
FALLBACK_CUTOFFS = M["fallback_cutoffs"]
SOURCE = "Lindstrom J, Tuomilehto J. The diabetes risk score. Diabetes Care 2003;26:725-731"
# published FINDRISC bands (points -> chance of developing diabetes), at the middle of each band: <7 1%, 7-11 4%, 12-14 17%, 15-20 33%, >20 50%
ANCHORS = [(0.0, 0.01), (3.0, 0.01), (9.0, 0.04), (13.0, 0.17), (17.5, 0.33), (23.0, 0.50), (26.0, 0.50)]
EXERCISE_VEG_MAX = 3                                      # daily exercise (2) and daily vegetables (1): not in the records
WAIST_MAX = 4                                             # waist points when waist is unknown and BMI is 25 or more
FAMILY_MAX = 5                                            # close relative with diabetes, when family history was not recorded
_CUTOFFS = {}


# ------------------------------------------------------------------ cut-offs, bands, ranks (read by the agents)
def cutoffs():
    """High and Moderate cut-offs of the score, saved in the model card (read once); fallbacks if missing."""
    if not _CUTOFFS:
        try:
            with open(os.path.join(MODELS, "risk_model_card.json"), encoding="utf-8") as fh:
                c = json.load(fh)
            _CUTOFFS.update({"high": float(c["threshold_high"]), "moderate": float(c["threshold_moderate"]),
                             "base_rate": float(c["base_rate_per_100"]) / 100, "percentiles": c.get("score_percentiles"),
                             "by_condition": c.get("thresholds_by_condition", {})})
        except (OSError, KeyError, ValueError):
            _CUTOFFS.update(FALLBACK_CUTOFFS)
    return _CUTOFFS


def base_rate():
    return cutoffs()["base_rate"]


def risk_rank(risk):
    """0 to 1: the share of local scores below this one, used in the priority score. No score counts as the middle."""
    q = cutoffs().get("percentiles")
    if risk is None or pd.isna(risk):
        return M["no_score_rank"]
    if not q:
        return float(risk)
    return float(np.searchsorted(q, risk, side="right") - 1) / 100


def band(risk, condition=None):
    """Low / Moderate / High for the score, None when there is no score."""
    if risk is None or pd.isna(risk):
        return None
    c = cutoffs()
    t = c.get("by_condition", {}).get(condition) if condition else None
    hi, mo = (t["high"], t["moderate"]) if t else (c["high"], c["moderate"])
    return "High" if risk >= hi else "Moderate" if risk >= mo else "Low"


# ------------------------------------------------------------------ the FINDRISC points
def _col(d, name, default=np.nan):
    return d[name] if name in d.columns else pd.Series(default, index=d.index)


def items(d):
    """FINDRISC points per item for each row, and how many more points the unknown items could add.
    Returns (points DataFrame, unknown_max Series)."""
    age = _col(d, "age").astype(float)
    bmi = _col(d, "bmi").astype(float)
    man = _col(d, "male", 0).fillna(0) == 1
    waist = _col(d, "waist")
    pts = pd.DataFrame(index=d.index)
    pts["Age"] = np.select([age < 45, age < 55, age < 65], [0, 2, 3], 4)
    pts["BMI"] = np.where(bmi.isna(), 0, np.select([bmi < 25, bmi <= 30], [0, 1], 3))
    wp = np.where(man, np.select([waist < 94, waist <= 102], [0, 3], 4), np.select([waist < 80, waist <= 88], [0, 3], 4))
    pts["Waist"] = np.where(waist.isna(), 0, wp)
    pts["High blood pressure diagnosis"] = 2 * _col(d, "has_hypertension", 0).fillna(0)
    pts["Prediabetes"] = 5 * _col(d, "has_prediabetes", 0).fillna(0)
    fam = _col(d, "family_hist_diabetes")
    recorded = _col(d, "family_history_recorded", 1).fillna(1).astype(bool) if "family_history_recorded" in d.columns else fam.notna()
    pts["Family history of diabetes"] = 5 * fam.fillna(0)
    # the two lifestyle answers a clinic can add: daily exercise (no = 2 points), daily vegetables (no = 1 point); unknown until asked
    ex, veg = _col(d, "exercise_daily"), _col(d, "veg_daily")
    pts["Daily exercise"] = np.where(ex == 0, 2, 0)
    pts["Daily vegetables"] = np.where(veg == 0, 1, 0)
    unknown = (np.where(waist.isna(), np.where(bmi.fillna(30) >= 25, WAIST_MAX, 0), 0) + np.where(ex.isna(), 2, 0) + np.where(veg.isna(), 1, 0)
               + np.where(recorded & fam.notna(), 0, FAMILY_MAX))
    return pts, pd.Series(unknown, index=d.index, dtype=float)


def chance(points):
    """The published risk curve (chance of developing diabetes) for fractional points."""
    x, y = zip(*ANCHORS)
    return np.interp(points, x, y)


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _scaled(p, shift):
    return 1 / (1 + np.exp(-(_logit(p) + shift)))


def _shift_to(p, target):
    """Intercept shift so the mean chance equals the target (bisection on the log-odds scale; order unchanged)."""
    lo, hi = -10.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if float(np.mean(_scaled(p, mid))) < target else (lo, mid)
    return (lo + hi) / 2


def local_target(condition, local):
    """Expected undiagnosed rate among local adults without the diagnosis: WHO UAE prevalence minus the diagnosed
    share in the records, capped at the published undiagnosed share. Only public figures and visible records."""
    lc = M["local_calibration"]
    who = pd.read_csv(os.path.join(REAL, lc["who_file"]))
    who = who[(who.sex.isna() | who.sex.astype(str).str.contains("BTSX"))]
    code, age = lc["indicators"][condition]
    x = who[(who.indicator == code) & (who.age_group.astype(str).str.contains(age) if age else True)].sort_values("year")
    prev = float(x.value.iloc[-1]) / 100
    a0, a1 = SD["prevalence_ages"][condition]
    elig = local[local.age.between(a0, a1)]
    known = elig[KNOWN[condition]].fillna(0).astype(bool)
    diag_share = float(known.mean())
    hidden_share = min(max(prev - diag_share, 0.0), SD["undiagnosed_share"][condition] * prev)
    return {"who_prevalence": round(prev, 4), "diagnosed_share_in_records": round(diag_share, 4),
            "undiagnosed_rate_target": round(hidden_share / max(1 - diag_share, 1e-6), 4)}


# ------------------------------------------------------------------ high blood pressure: the measured reading, by guideline category
BP_SOURCE = "2017 ACC/AHA guideline for the prevention, detection, evaluation and management of high blood pressure in adults"


def bp_category(sbp, dbp):
    """The adult blood-pressure category of one reading (2017 ACC/AHA): normal under 120 and under 80; elevated 120 to 129 and
    under 80; stage 1 130 to 139 or 80 to 89; stage 2 140 or more, or 90 or more. A category of a measurement, not a prediction."""
    if sbp is None or dbp is None or pd.isna(sbp) or pd.isna(dbp):
        return None
    if sbp >= 140 or dbp >= 90:
        return "Stage 2 high blood pressure"
    if sbp >= 130 or dbp >= 80:
        return "Stage 1 high blood pressure"
    if sbp >= 120:
        return "Elevated"
    return "Normal"


# ------------------------------------------------------------------ heart disease: the 2013 ACC/AHA pooled cohort equations
# Published equations (Goff et al., 2013 ACC/AHA guideline on the assessment of cardiovascular risk, Circulation 2014;129:S49-S73):
# the chance of a first heart attack, death from heart disease or stroke in the next 10 years, for ages 40 to 79. The guideline
# says to use the white-adult equations for people of other ethnic groups when no better equation exists, as here. They are
# shown to the doctor as information only: they are not scaled to the UAE (these equations can overestimate risk in some
# populations) and they do not change anyone's priority.
PCE = {
    1: {"age": 12.344, "tc": 11.853, "age_tc": -2.664, "hdl": -7.990, "age_hdl": 1.769, "treated_sbp": 1.797, "untreated_sbp": 1.764,
        "smoker": 7.837, "age_smoker": -1.795, "diabetes": 0.658, "age2": 0.0, "mean": 61.18, "s0": 0.9144},       # men
    0: {"age": -29.799, "age2": 4.884, "tc": 13.540, "age_tc": -3.114, "hdl": -13.578, "age_hdl": 3.149, "treated_sbp": 2.019,
        "untreated_sbp": 1.957, "smoker": 7.574, "age_smoker": -1.665, "diabetes": 0.661, "mean": -29.18, "s0": 0.9665},  # women
}
PCE_AGES = (40, 79)
PCE_SOURCE = "Goff DC et al. 2013 ACC/AHA guideline on the assessment of cardiovascular risk. Circulation 2014;129(25 Suppl 2):S49-S73"


def ascvd(d):
    """10-year heart-disease risk per row (NaN when age is outside 40 to 79 or an input is missing), with a band and the factors
    that raise it. Treated blood pressure is read from a recorded high-blood-pressure diagnosis."""
    n = len(d)
    out = pd.DataFrame(index=d.index, columns=["risk", "band", "reasons"], dtype=object)
    age, tc, hdl, sbp = (_col(d, c).astype(float) for c in ("age", "total_chol", "hdl", "sbp"))
    man = _col(d, "male", 0).fillna(0).astype(int)
    smoker = _col(d, "smoker", 0).fillna(0).astype(float)
    dm = _col(d, "has_diabetes", 0).fillna(0).astype(float)
    treated = _col(d, "has_hypertension", 0).fillna(0).astype(int)
    ok = age.between(*PCE_AGES) & tc.notna() & hdl.notna() & sbp.notna() & (tc > 0) & (hdl > 0) & (sbp > 0)
    risk = np.full(n, np.nan)
    for i in np.flatnonzero(ok.values):
        c = PCE[int(man.iloc[i])]
        la, lt, lh, ls = np.log(age.iloc[i]), np.log(tc.iloc[i]), np.log(hdl.iloc[i]), np.log(sbp.iloc[i])
        s = (c["age"] * la + c["age2"] * la * la + c["tc"] * lt + c["age_tc"] * la * lt + c["hdl"] * lh + c["age_hdl"] * la * lh
             + (c["treated_sbp"] if treated.iloc[i] else c["untreated_sbp"]) * ls + (c["smoker"] + c["age_smoker"] * la) * smoker.iloc[i]
             + c["diabetes"] * dm.iloc[i])
        risk[i] = 1 - c["s0"] ** np.exp(s - c["mean"])
    out["risk"] = risk.round(4)
    out["band"] = [None if np.isnan(r) else "High" if r >= 0.075 else "Moderate" if r >= 0.05 else "Low" for r in risk]
    why = []
    for i in range(n):
        got = []
        if smoker.iloc[i]:
            got.append("Smoking")
        if dm.iloc[i]:
            got.append("Diabetes diagnosis")
        if sbp.iloc[i] >= 130:
            got.append("Systolic blood pressure")
        if tc.iloc[i] >= 200:
            got.append("Total cholesterol")
        if hdl.iloc[i] < (40 if man.iloc[i] else 50):
            got.append("HDL cholesterol")
        if age.iloc[i] >= 55:
            got.append("Age")
        why.append(got[:M["top_reasons"]] or ["No single strong factor"])
    out["reasons"] = why
    return out


# ------------------------------------------------------------------ calibrate and score
def _scored(local):
    return local[(local.age >= CR["adult_age"]) & (local.data_sufficient == 1)]


def calibrate(local):
    """The one number that moves the published curve to the UAE level, and where it comes from."""
    sc = _scored(local)
    rows = sc[sc[KNOWN["diabetes"]].fillna(0) == 0]
    pts, unknown = items(rows)
    central = pts.sum(axis=1) + unknown / 2
    t = local_target("diabetes", local)
    shift = _shift_to(chance(central.values), t["undiagnosed_rate_target"])
    return {"conditions": {"diabetes": {"shift": shift}}, "local_calibration": {**t, "intercept_shift": round(shift, 3)}}


def load(local=None):
    """The scoring bundle: the intercept shift saved by main(); worked out from the records when no card exists yet."""
    path = os.path.join(MODELS, "risk_model_card.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            card = json.load(fh)
        shift = card.get("conditions", {}).get("diabetes", {}).get("local_calibration", {}).get("intercept_shift")
        if shift is not None:
            return {"conditions": {"diabetes": {"shift": float(shift)}}}
    return calibrate(local if local is not None else utils.load_features())


def score(bundle, df):
    """For every row: the chance of undiagnosed diabetes (None when already diagnosed), its range and the items that gave
    the points. The combined columns (risk, risk_low, risk_high, risk_reasons) are the same numbers, as diabetes is the one
    predicted condition."""
    shift = bundle["conditions"]["diabetes"]["shift"]
    pts, unknown = items(df)
    own = pts.sum(axis=1)
    mid, hi = own + unknown / 2, own + unknown
    known = df[KNOWN["diabetes"]].fillna(0).astype(bool).values
    r, lo, up = (_scaled(chance(v.values), shift) for v in (mid, own, hi))
    out = pd.DataFrame(index=df.index)
    top = M["top_reasons"]
    reasons = []
    for i in range(len(df)):
        row = pts.iloc[i]
        got = [k for k in row.sort_values(ascending=False).index[:top] if row[k] > 0]
        reasons.append(got or ["No single strong factor"])
    out["diabetes_risk"] = np.where(known, np.nan, r.round(4))
    out["diabetes_low"] = np.where(known, np.nan, np.minimum(lo, r).round(4))
    out["diabetes_high"] = np.where(known, np.nan, np.maximum(up, r).round(4))
    out["diabetes_reasons"] = [[] if k else x for k, x in zip(known, reasons)]
    out["risk"], out["risk_low"], out["risk_high"] = out.diabetes_risk, out.diabetes_low, out.diabetes_high
    out["risk_reasons"] = out.diabetes_reasons
    return out


# ------------------------------------------------------------------ main
def main():
    ensure_dirs()
    local = pd.read_csv(os.path.join(FEATURES, "patient_features.csv"))
    bundle = calibrate(local)
    s = score(bundle, _scored(local))
    v = s.diabetes_risk.dropna().values
    thr = {"high": round(float(np.quantile(v, 1 - HIGH_SHARE)), 4), "moderate": round(float(np.quantile(v, 1 - MODERATE_SHARE)), 4)}
    val = utils.load_json(os.path.join(MODELS, "nhanes_validation.json"), {})
    card = {
        "target": "chance of undiagnosed diabetes now, from a published questionnaire score",
        "method": "FINDRISC points scaled to the UAE undiagnosed level. Nothing is trained: the points and bands are published",
        "source": SOURCE,
        "conditions": {
            "diabetes": {
                "label": "Diabetes (HbA1c 6.5% or more in an adult never told they have diabetes)",
                "score": "FINDRISC (Lindstrom and Tuomilehto 2003)",
                "items_used": ["Age", "BMI", "High blood pressure diagnosis", "Prediabetes", "Family history of diabetes"],
                "items_not_in_the_records": ["Waist", "Daily exercise", "Daily vegetables"],
                "range": "from the points the missing items could add; a person is never scored as if they were known",
                "local_calibration": bundle["local_calibration"],
                "validation": val.get("headline"),
            },
            "hypertension": {
                "label": "High blood pressure",
                "score": "none: blood pressure is measured, so a high reading is a finding (an abnormal-result card), not a prediction",
            },
        },
        "threshold_high": thr["high"], "threshold_moderate": thr["moderate"], "thresholds_by_condition": {"diabetes": thr},
        "cutoff_rule": f"High = top {HIGH_SHARE:.0%} of local scores, Moderate = top {MODERATE_SHARE:.0%}",
        "score_percentiles": [round(float(x), 4) for x in np.quantile(v, np.linspace(0, 1, 101))],
        "base_rate_per_100": round(float(100 * v.mean()), 2), "people_scored": int(len(v)),
        "not_used": ["nationality", "language", "insurance", "region"],
        "not_allowed_uses": ["diagnosis", "insurance pricing", "reducing anyone's access to care"],
    }
    save_json(card, os.path.join(MODELS, "risk_model_card.json"))
    _CUTOFFS.clear()
    c = bundle["local_calibration"]
    print(f"Diabetes (FINDRISC): local target {c['undiagnosed_rate_target']:.1%} (WHO {c['who_prevalence']:.1%}, diagnosed {c['diagnosed_share_in_records']:.1%}), "
          f"intercept shift {c['intercept_shift']}; High cut-off {thr['high']}, Moderate {thr['moderate']}; {len(v)} adults scored")
    return card


if __name__ == "__main__":
    main()
