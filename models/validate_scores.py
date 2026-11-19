"""
Check on real people how well the published diabetes scores rank undiagnosed diabetes (no model is trained here).

    python -m models.validate_scores

Real NHANES adults from three survey cycles (2015-2016, 2017-2018, 2021-2023) who were never told they have diabetes; undiagnosed
diabetes = a measured HbA1c of 6.5% or more. Four ways of choosing who to invite, on the same people:

* FINDRISC with every item NHANES has (age, BMI, waist, treated for high blood pressure, ever told of high blood sugar, close
  relative with diabetes; exercise and vegetables are not in the survey);
* FINDRISC as this prototype can use it (no waist), scored exactly as models/risk_scores.py scores the records;
* the ADA diabetes risk test (Bang et al., Ann Intern Med 2009), the five of its seven items NHANES has;
* the broad rule used today: age 45 or more, or BMI 25 or more.

None of the scores was built for the UAE and the points are the published ones, filled in only where NHANES has the answer.
Writes outputs/models/nhanes_validation.json (the Results page shows it).
"""
import os
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import risk_scores  # noqa: E402
from utils import MODELS, REAL, config, ensure_dirs, save_json  # noqa: E402

M = config("models")
CYCLES = {"2015-2016": os.path.join(REAL, "nhanes_2015", "nhanes_2015_joined.csv"),
          "2017-2018": os.path.join(REAL, "nhanes", "nhanes_2017_2018_joined.csv"),
          "2021-2023": os.path.join(REAL, "nhanes_2021", "nhanes_2021_joined.csv")}
N_BOOT = 300
SEED = 11
TOP_SHARE = M["nhanes_top_share"]
ADA_SOURCE = "Bang H et al. Development and validation of a patient self-assessment score for diabetes risk. Ann Intern Med 2009;151:775-783"


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def nhanes():
    """Adults who were never told they have diabetes, with the answers the scores need and the measured HbA1c label."""
    parts = []
    yesno = {1: 1, 2: 0}
    for cycle, path in CYCLES.items():
        d = pd.read_csv(path, low_memory=False)
        parts.append(pd.DataFrame({
            "cycle": cycle, "age": _num(d.RIDAGEYR), "male": (_num(d.RIAGENDR) == 1).astype(int), "bmi": _num(d.BMXBMI),
            "waist": _num(d.get("BMXWAIST")),
            "family_hist_diabetes": _num(d["MCQ300C"]).map(yesno) if "MCQ300C" in d.columns else np.nan,
            "has_prediabetes": _num(d.get("DIQ160")).map(yesno), "has_hypertension": _num(d.get("BPQ020")).map(yesno),
            "has_diabetes": _num(d.get("DIQ010")).map({1: 1, 2: 0, 3: 0}), "hba1c": _num(d.get("LBXGH"))}))
    a = pd.concat(parts, ignore_index=True)
    a = a[(a.age >= M["nhanes_adult_age"]) & (a.has_diabetes == 0) & a.hba1c.notna()].copy()
    a["y"] = (a.hba1c >= config("synthetic_data")["abnormal_results"]["hba1c"]).astype(int)
    return a


def ada(d):
    """ADA diabetes risk test points for the five items NHANES has (0 to 9; the full test is 0 to 11)."""
    age = np.select([d.age < 40, d.age < 50, d.age < 60], [0, 1, 2], 3)
    bmi = np.select([d.bmi < 25, d.bmi < 30, d.bmi < 40], [0, 1, 2], 3)
    return (age + bmi + d.male + d.family_hist_diabetes.fillna(0) + d.has_hypertension.fillna(0)).astype(float)


def findrisc_full(d):
    pts, _ = risk_scores.items(d)
    return pts.sum(axis=1)


def findrisc_here(d):
    """As the prototype scores the records: no waist, and the missing items counted as the central value."""
    pts, unknown = risk_scores.items(d.assign(waist=np.nan))
    return pts.sum(axis=1) + unknown / 2


def broad_rule(d):
    return ((d.age >= 45) | (d.bmi >= 25)).astype(float)


def auc_ci(y, s, rng):
    base = roc_auc_score(y, s)
    out = []
    for _ in range(N_BOOT):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() != y[i].max():
            out.append(roc_auc_score(y[i], s[i]))
    lo, hi = np.percentile(out, [2.5, 97.5])
    return round(float(base), 3), round(float(lo), 3), round(float(hi), 3)


def found_share(y, s, rule=False):
    """(share of all cases found, share of people invited): a score invites its top 30%, the yes/no rule everyone it says yes to."""
    if rule:
        inv = s == 1
        return round(float(y[inv].sum() / max(y.sum(), 1)), 3), round(float(inv.mean()), 3)
    order = np.argsort(-s, kind="stable")[:int(TOP_SHARE * len(s))]
    return round(float(y[order].sum() / max(y.sum(), 1)), 3), TOP_SHARE


def main():
    ensure_dirs()
    a = nhanes()
    t = a[a[["age", "bmi", "waist"]].notna().all(axis=1)].copy()            # the same people for every score
    y = t.y.values
    rng = np.random.default_rng(SEED)
    rows = [("FINDRISC, every item NHANES has (6 of 8)", findrisc_full(t).values, "published", risk_scores.SOURCE,
             "age, BMI, waist, treated for high blood pressure, ever told of high blood sugar, close relative with diabetes"),
            ("FINDRISC as used in this prototype (no waist)", findrisc_here(t).values, "used here", risk_scores.SOURCE,
             "age, BMI, high blood pressure diagnosis, told of high blood sugar, family history; the missing items widen the range"),
            ("ADA diabetes risk test (5 of 7 items)", ada(t).values, "published", ADA_SOURCE, "age, sex, family history, high blood pressure, BMI"),
            ("Broad rule today (age 45+ or BMI 25+)", broad_rule(t).values, "rule", None, "age and BMI")]
    out = []
    for name, s, kind, source, its in rows:
        auc, lo, hi = auc_ci(y, s, rng) if kind != "rule" else (round(float(roc_auc_score(y, s)), 3), None, None)
        found, invited = found_share(y, s, kind == "rule")
        out.append({"name": name, "kind": kind, "auc": auc, "auc_low": lo, "auc_high": hi, "cases_found_share": found,
                    "invited_share": invited, "items": its, "source": source})
    used = next(r for r in out if r["kind"] == "used here")
    by_cycle = []
    for cyc, g in t.groupby("cycle"):
        yy = g.y.values
        if yy.sum() >= 20:
            by_cycle.append({"cycle": cyc, "adults": int(len(g)), "cases": int(yy.sum()), "auc": round(float(roc_auc_score(yy, findrisc_here(g).values)), 3)})
    res = {"test": "NHANES 2015-2023 adults never told they have diabetes (real survey data)", "label": "HbA1c 6.5% or more",
           "adults": int(len(t)), "cases": int(y.sum()), "top_share": TOP_SHARE, "scores": out, "by_cycle": by_cycle,
           "headline": {"score": used["name"], "auc": used["auc"], "auc_low": used["auc_low"], "auc_high": used["auc_high"],
                        "cases_found_share": used["cases_found_share"], "invited_share": used["invited_share"], "adults": int(len(t))},
           "note": "Nothing is trained: the points are the published ones, filled in only where NHANES has the answer, so a full "
                   "questionnaire would do a little better. None was built for the UAE. The check shows how well the score ranks real "
                   "people on data it was not tuned to."}
    save_json(res, os.path.join(MODELS, "nhanes_validation.json"))
    for r in out:
        print(f"{r['name']:50} AUC {r['auc']} ({r['auc_low']} to {r['auc_high']}), inviting {r['invited_share']:.0%} finds {r['cases_found_share']:.0%}")
    print(f"{len(t)} adults, {int(y.sum())} undiagnosed cases; by cycle: {by_cycle}")
    return res


if __name__ == "__main__":
    main()
