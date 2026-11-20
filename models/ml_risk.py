"""
A trained second opinion on undiagnosed diabetes, for the doctor. Information only.

    python -m models.ml_risk        trains on real NHANES adults, scales to the UAE level, writes outputs/models/ml_diabetes_model.json

The published FINDRISC score (models/risk_scores.py) still decides who is invited and in what order. This model is shown next to it in
the Doctor view as a second opinion; it never changes anyone's priority (config/models.json, ml_second_opinion). Why it is there:
on the same 12,925 real NHANES adults it ranks undiagnosed diabetes better than FINDRISC, on people it was not trained on
(models/ml_comparison.py, Results page "Trained models compared with the published score").

* Model: logistic regression on nine inputs a record can hold: age, BMI, told of high blood sugar, high-blood-pressure diagnosis,
  family history of diabetes, sex, systolic blood pressure, total cholesterol and HDL cholesterol. A short list of weights, saved as
  plain numbers in a JSON file (no pickled object), so a reviewer can read exactly what it does.
* Not used: nationality, language, insurance, region, race, income, education (the project never lets them into a risk score).
* Missing measurements: replaced by the training median and counted, so the doctor sees "n of 9 inputs measured". A missing yes/no
  answer counts as no, as in the published score.
* UAE level: one intercept shift (ranking unchanged) moves the average to the expected undiagnosed rate among local adults, the
  same way as for FINDRISC. The model was trained on US survey data and has not been validated on UAE patients.
* Reasons: the inputs that push this person's chance up most, from the model's own weights.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils  # noqa: E402
from utils import MODELS, ensure_dirs, save_json  # noqa: E402

M = utils.config("models")
PATH = os.path.join(MODELS, "ml_diabetes_model.json")
INPUTS = ["age", "bmi", "has_prediabetes", "has_hypertension", "family_hist_diabetes", "male", "sbp", "total_chol", "hdl"]
BINARY = ["has_prediabetes", "has_hypertension", "family_hist_diabetes", "male"]
MEASURED = ["age", "bmi", "sbp", "total_chol", "hdl"]
LABELS = {"age": "Age", "bmi": "BMI", "has_prediabetes": "Prediabetes", "has_hypertension": "High blood pressure diagnosis",
          "family_hist_diabetes": "Family history of diabetes", "male": "Sex", "sbp": "Systolic blood pressure",
          "total_chol": "Total cholesterol", "hdl": "HDL cholesterol"}
NOT_USED = ["nationality", "language", "insurance", "region", "race", "income", "education"]
NOT_ALLOWED = ["diagnosis", "insurance pricing", "reducing anyone's access to care", "changing anyone's priority"]
C = 0.5                                                                    # weight shrinkage (design): small, stable weights
_CACHE = {}


def enabled():
    return bool(M.get("ml_second_opinion", {}).get("enabled", False))


def frame(df):
    """The nine inputs as numbers: yes/no answers that are missing count as no; measurements are left missing."""
    x = pd.DataFrame(index=df.index)
    for c in INPUTS:
        s = df[c] if c in df.columns else pd.Series(np.nan, index=df.index)
        x[c] = pd.to_numeric(s.astype(float) if s.dtype == bool else s, errors="coerce")
    x[BINARY] = x[BINARY].fillna(0.0)
    return x


def fit(df, y):
    """Train on a table with the inputs and a 0/1 label. Returns plain numbers (standardised weights, medians, means, spread)."""
    from sklearn.linear_model import LogisticRegression
    x = frame(df)
    med = x.median()
    x = x.fillna(med)
    mean, scale = x.mean(), x.std(ddof=0).replace(0, 1.0)
    lr = LogisticRegression(C=C, max_iter=3000).fit(((x - mean) / scale).values, np.asarray(y))
    return {"coef": {c: float(w) for c, w in zip(INPUTS, lr.coef_[0])}, "intercept": float(lr.intercept_[0]),
            "median": {c: float(med[c]) for c in INPUTS}, "mean": {c: float(mean[c]) for c in INPUTS},
            "scale": {c: float(scale[c]) for c in INPUTS}}


def _z(p, df):
    x = frame(df)
    seen = x[MEASURED].notna()
    x = x.fillna(pd.Series(p["median"]))
    z = (x - pd.Series(p["mean"])) / pd.Series(p["scale"])
    return x, z, seen


def logit(p, df):
    _, z, _ = _z(p, df)
    return p["intercept"] + sum(z[c] * p["coef"][c] for c in INPUTS)


def chance(p, df, shift=0.0):
    """Chance of undiagnosed diabetes for every row (the training-data level plus the intercept shift)."""
    return 1 / (1 + np.exp(-(logit(p, df) + shift)))


def explain(p, df):
    """Per row: the main factors that push this person's chance up, and how many of the five measurements were really recorded."""
    x, z, seen = _z(p, df)
    out = []
    for i in range(len(df)):
        contrib = {c: z[c].iloc[i] * p["coef"][c] for c in INPUTS if c != "male" and (c in BINARY or seen[c].iloc[i])}
        top = [LABELS[c] for c, v in sorted(contrib.items(), key=lambda kv: -kv[1])[:M["top_reasons"]] if v > 0.05]
        out.append({"reasons": top or ["No single strong factor"], "measured": int(seen.iloc[i].sum()) + len(BINARY) - 1,
                    "of": len(INPUTS) - 1})
    return out


def load():
    """The saved model (read once); None when it has not been built yet."""
    if "m" not in _CACHE:
        _CACHE["m"] = utils.load_json(PATH, None)
    return _CACHE["m"]


def band(risk):
    m = load()
    if m is None or risk is None or pd.isna(risk):
        return None
    return "High" if risk >= m["threshold_high"] else "Moderate" if risk >= m["threshold_moderate"] else "Low"


def second_opinion(row_df):
    """The second opinion for one adult (a one-row table of the patient features), or None: switched off, no model yet, a child,
    or a diabetes diagnosis already on record."""
    m = load()
    if not enabled() or m is None or len(row_df) != 1:
        return None
    r = row_df.iloc[0]
    if pd.isna(r.get("age")) or r.age < utils.config("clinical_rules")["adult_age"] or int(r.get("has_diabetes", 0) or 0):
        return None
    risk = float(chance(m["params"], row_df, m["local_shift"]).iloc[0])
    e = explain(m["params"], row_df)[0]
    return {"risk": round(risk, 4), "band": band(risk), "reasons": e["reasons"], "measured": e["measured"], "of": e["of"],
            "auc": m["validation"].get("auc"), "findrisc_auc": m["validation"].get("findrisc_auc")}


def main():
    from models import ml_comparison, risk_scores
    ensure_dirs()
    t = ml_comparison.load()
    y = t.y.values
    params = fit(t, y)
    local = utils.load_features()
    sc = local[(local.age >= risk_scores.CR["adult_age"]) & (local.data_sufficient == 1) & (local[risk_scores.KNOWN["diabetes"]].fillna(0) == 0)]
    target = risk_scores.local_target("diabetes", local)
    p0 = chance(params, sc).values
    shift = float(risk_scores._shift_to(p0, target["undiagnosed_rate_target"]))
    p = chance(params, sc, shift).values
    thr = {"high": round(float(np.quantile(p, 1 - risk_scores.HIGH_SHARE)), 4), "moderate": round(float(np.quantile(p, 1 - risk_scores.MODERATE_SHARE)), 4)}
    cmp_ = utils.load_json(os.path.join(MODELS, "ml_comparison.json"), {})
    mine = next((r for r in cmp_.get("scores", []) if r.get("name") == ml_comparison.APP_MODEL), {})
    ref = next((r for r in cmp_.get("scores", []) if r.get("kind") == "published"), {})
    card = {
        "target": "chance of undiagnosed diabetes now (HbA1c 6.5% or more in an adult never told they have diabetes)",
        "method": "Logistic regression trained on real NHANES adults (2015-2023), then moved to the UAE level by one intercept shift. "
                  "Information only: shown next to the published FINDRISC score, never used for priority",
        "status": "information only", "inputs": INPUTS, "labels": LABELS, "params": params,
        "training": {"people": int(len(t)), "cases": int(y.sum()), "data": "NHANES 2015-2016, 2017-2018, 2021-2023 (US survey)"},
        "local_calibration": {**target, "intercept_shift": round(shift, 3)}, "local_shift": shift,
        "threshold_high": thr["high"], "threshold_moderate": thr["moderate"],
        "cutoff_rule": f"High = top {risk_scores.HIGH_SHARE:.0%} of local scores, Moderate = top {risk_scores.MODERATE_SHARE:.0%}",
        "validation": {"auc": mine.get("auc"), "auc_low": mine.get("auc_low"), "auc_high": mine.get("auc_high"), "findrisc_auc": ref.get("auc"),
                       "how": "5-fold cross-validation, repeated 3 times, on people the model was not trained on (models/ml_comparison.py)"},
        "not_used": NOT_USED, "not_allowed_uses": NOT_ALLOWED,
        "limits": ["Trained on US survey data, not UAE patients: it has not been validated on UAE patients",
                   "Missing cholesterol or HDL are replaced by the training median and counted"],
    }
    save_json(card, PATH)
    _CACHE.clear()
    print(f"ML second opinion: {len(t)} NHANES adults, {int(y.sum())} cases; local target {target['undiagnosed_rate_target']:.1%}, shift {shift:.3f}; "
          f"High cut-off {thr['high']}, Moderate {thr['moderate']}; CV AUC {mine.get('auc')} against {ref.get('auc')} for FINDRISC")
    return card


if __name__ == "__main__":
    main()
