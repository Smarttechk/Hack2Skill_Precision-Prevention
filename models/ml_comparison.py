"""
Does a trained machine-learning model rank undiagnosed diabetes better than the published FINDRISC score?

    python -m models.ml_comparison

Same real people and same label as models/validate_scores.py: NHANES adults (2015-2016, 2017-2018, 2021-2023) who were never told
they have diabetes; undiagnosed diabetes = a measured HbA1c of 6.5% or more. Nothing here changes the app: the app keeps using the
published score unless this check shows a clear gain.

Fair-comparison rules
* Same people for every score (the 12,925 adults the Results page reports).
* Only inputs a hospital record can hold. Race, nationality, income and education are NOT used (the project never lets them
  into a risk score).
* Every learned model is scored on people it was not trained on: repeated 5-fold cross-validation, and "train on two survey
  cycles, test on the third".
* The published score is not trained at all and is scored on the same people.

Missing values are replaced by the median and not flagged, for every model: a survey's pattern of who was measured is not a clinical
signal and would not exist in hospital records.
Models: logistic regression and gradient boosting (small, regularised trees), each on
  A. the same five inputs the prototype's FINDRISC uses (age, BMI, told of high blood sugar, high blood pressure diagnosis,
     family history of diabetes);
  B. those plus what a record can also hold: sex, systolic blood pressure, total and HDL cholesterol, ever smoked.
Writes outputs/models/ml_comparison.json.
"""
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import ml_risk, validate_scores as vs  # noqa: E402
from utils import MODELS, config, ensure_dirs, save_json  # noqa: E402

SEED, N_BOOT, TOP = 11, 400, vs.TOP_SHARE
INPUTS_A = ["age", "bmi", "has_prediabetes", "has_hypertension", "family_hist_diabetes"]
INPUTS_B = INPUTS_A + ["male", "sbp", "tchol", "hdl", "ever_smoked"]


def load():
    """The people and label of models/validate_scores.py, plus the extra record items."""
    yesno = {1: 1, 2: 0}
    parts = []
    num = vs._num
    for cycle, path in vs.CYCLES.items():
        d = pd.read_csv(path, low_memory=False)
        sbp = num(d.get("BPXSY1"))
        if "BPXOSY1" in d.columns:
            sbp = sbp.fillna(num(d["BPXOSY1"]))
        parts.append(pd.DataFrame({
            "cycle": cycle, "age": num(d.RIDAGEYR), "male": (num(d.RIAGENDR) == 1).astype(int), "bmi": num(d.BMXBMI),
            "waist": num(d.get("BMXWAIST")),
            "family_hist_diabetes": num(d["MCQ300C"]).map(yesno) if "MCQ300C" in d.columns else np.nan,
            "has_prediabetes": num(d.get("DIQ160")).map(yesno), "has_hypertension": num(d.get("BPQ020")).map(yesno),
            "has_diabetes": num(d.get("DIQ010")).map({1: 1, 2: 0, 3: 0}), "hba1c": num(d.get("LBXGH")),
            "sbp": sbp, "tchol": num(d.get("LBXTC")), "total_chol": num(d.get("LBXTC")), "hdl": num(d.get("LBDHDD")), "ever_smoked": num(d.get("SMQ020")).map(yesno),
            "ethnicity": num(d.get("RIDRETH3"))}))   # only for the group check below, never an input
    a = pd.concat(parts, ignore_index=True)
    a = a[(a.age >= vs.M["nhanes_adult_age"]) & (a.has_diabetes == 0) & a.hba1c.notna()].copy()
    a["y"] = (a.hba1c >= config("synthetic_data")["abnormal_results"]["hba1c"]).astype(int)
    return a[a[["age", "bmi", "waist"]].notna().all(axis=1)].reset_index(drop=True)      # the same people as the Results page


def logit():
    # a missing value is replaced by the median and NOT flagged: which values a survey happened to collect (who had blood drawn, which
    # question a cycle asked) says nothing about diabetes and would not exist in hospital records, so a model must not learn from it
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))


def boost():
    return make_pipeline(SimpleImputer(strategy="median"),
                         HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=60,
                                                        l2_regularization=2.0, random_state=SEED))


APP_MODEL = "Logistic regression, as used in the app"        # models/ml_risk.py: nine record inputs, no smoking
MODELS_ = {"Logistic regression, same 5 inputs": (logit, INPUTS_A), "Gradient boosting, same 5 inputs": (boost, INPUTS_A),
           "Logistic regression, + record items": (logit, INPUTS_B), "Gradient boosting, + record items": (boost, INPUTS_B)}


def top_found(y, s):
    k = int(TOP * len(s))
    order = np.argsort(-s, kind="stable")[:k]
    return float(y[order].sum() / max(y.sum(), 1))


def cv_scores(t, make, cols):
    """Out-of-fold score for every person, averaged over repeats: nobody is scored by a model that saw them."""
    X, y = t[cols].values.astype(float), t.y.values
    cv = RepeatedStratifiedKFold(n_splits=5, n_repeats=3, random_state=SEED)
    acc, cnt = np.zeros(len(y)), np.zeros(len(y))
    for tr, te in cv.split(X, y):
        m = make().fit(X[tr], y[tr])
        acc[te] += m.predict_proba(X[te])[:, 1]; cnt[te] += 1
    return acc / cnt


def cv_scores_app(t):
    """The same out-of-fold scoring for the model the app uses (models/ml_risk.py), so its number is a fair one."""
    y = t.y.values
    cv = RepeatedStratifiedKFold(n_splits=5, n_repeats=3, random_state=SEED)
    acc, cnt = np.zeros(len(y)), np.zeros(len(y))
    for tr, te in cv.split(t, y):
        p = ml_risk.fit(t.iloc[tr], y[tr])
        acc[te] += ml_risk.chance(p, t.iloc[te]).values; cnt[te] += 1
    return acc / cnt


def boot(y, s_ref, s_new, rng):
    """AUC with a 95% range for each score, and for the new score minus the reference (same resampled people)."""
    n = len(y); a_ref, a_new, diff = [], [], []
    for _ in range(N_BOOT):
        i = rng.integers(0, n, n)
        if y[i].min() == y[i].max():
            continue
        r, w = roc_auc_score(y[i], s_ref[i]), roc_auc_score(y[i], s_new[i])
        a_ref.append(r); a_new.append(w); diff.append(w - r)
    q = lambda v: [round(float(x), 3) for x in np.percentile(v, [2.5, 97.5])]
    return q(a_new), q(diff)


def main():
    ensure_dirs()
    t = load()
    y = t.y.values
    print(f"{len(t)} adults, {int(y.sum())} undiagnosed cases (the Results page reports 12,925 and 456)")
    ref = vs.findrisc_here(t).values.astype(float)
    rng = np.random.default_rng(SEED)
    ref_auc = round(float(roc_auc_score(y, ref)), 3)
    ref_ci = vs.auc_ci(y, ref, rng)
    rows = [{"name": "FINDRISC as used in this prototype (not trained)", "kind": "published", "auc": ref_auc, "auc_low": ref_ci[1],
             "auc_high": ref_ci[2], "cases_found_share": round(top_found(y, ref), 3), "diff_vs_findrisc": None}]
    scores = {}
    for name, (make, cols) in MODELS_.items():
        s = cv_scores(t, make, cols)
        scores[name] = s
        ci, dci = boot(y, ref, s, rng)
        auc = round(float(roc_auc_score(y, s)), 3)
        rows.append({"name": name, "kind": "trained", "inputs": cols, "auc": auc, "auc_low": ci[0], "auc_high": ci[1],
                     "cases_found_share": round(top_found(y, s), 3), "diff_vs_findrisc": {"auc_gain": round(auc - ref_auc, 3), "range": dci,
                                                                                          "clearly_better": bool(dci[0] > 0)}})
    s_app = cv_scores_app(t)
    scores[APP_MODEL] = s_app
    ci, dci = boot(y, ref, s_app, rng)
    auc = round(float(roc_auc_score(y, s_app)), 3)
    rows.append({"name": APP_MODEL, "kind": "trained", "inputs": ml_risk.INPUTS, "auc": auc, "auc_low": ci[0], "auc_high": ci[1],
                 "cases_found_share": round(top_found(y, s_app), 3), "used_in_app": True,
                 "diff_vs_findrisc": {"auc_gain": round(auc - ref_auc, 3), "range": dci, "clearly_better": bool(dci[0] > 0)}})
    # train on two cycles, test on the third
    by_cycle = []
    for cyc in sorted(t.cycle.unique()):
        te, tr = t[t.cycle == cyc], t[t.cycle != cyc]
        if te.y.sum() < 20:
            continue
        item = {"test_cycle": cyc, "adults": int(len(te)), "cases": int(te.y.sum()),
                "FINDRISC (not trained)": round(float(roc_auc_score(te.y, vs.findrisc_here(te))), 3)}
        for name, (make, cols) in MODELS_.items():
            m = make().fit(tr[cols].values.astype(float), tr.y.values)
            item[name] = round(float(roc_auc_score(te.y, m.predict_proba(te[cols].values.astype(float))[:, 1])), 3)
        item[APP_MODEL] = round(float(roc_auc_score(te.y, ml_risk.chance(ml_risk.fit(tr, tr.y.values), te))), 3)
        by_cycle.append(item)
    # what drives the logistic model (standardised, so the sizes can be compared), and whether the gain holds in every group
    app_params = ml_risk.fit(t, y)
    drivers = sorted(({"input": ml_risk.LABELS[c], "weight": round(w, 2)} for c, w in app_params["coef"].items()), key=lambda r: -abs(r["weight"]))
    s_lr = s_app
    eth = {1: "Mexican American", 2: "Other Hispanic", 3: "White", 4: "Black", 6: "Asian", 7: "Other or mixed"}
    groups = {"Sex": t.male.map({1: "Men", 0: "Women"}), "Age": pd.cut(t.age, [0, 44, 64, 200], labels=["under 45", "45 to 64", "65 and over"]),
              "Ethnic group": t.ethnicity.map(eth)}
    by_group = []
    for gname, col in groups.items():
        for lab in col.dropna().unique():
            m = (col == lab).values
            if y[m].sum() >= 15 and y[m].min() != y[m].max():
                by_group.append({"group": gname, "value": str(lab), "adults": int(m.sum()), "cases": int(y[m].sum()),
                                 "findrisc_auc": round(float(roc_auc_score(y[m], ref[m])), 3),
                                 "model_auc": round(float(roc_auc_score(y[m], s_lr[m])), 3)})   # the model the app uses
    best = max((r for r in rows if r["kind"] == "trained"), key=lambda r: r["auc"])
    res = {"test": "NHANES 2015-2023 adults never told they have diabetes (real survey data)", "label": "HbA1c 6.5% or more",
           "adults": int(len(t)), "cases": int(y.sum()), "top_share": TOP, "scores": rows, "train_on_two_cycles_test_on_third": by_cycle,
           "best_trained": best["name"], "model_drivers": drivers, "by_group": by_group, "any_clearly_better": any(r.get("diff_vs_findrisc") and r["diff_vs_findrisc"]["clearly_better"] for r in rows),
           "note": "Models are scored only on people they were not trained on. Race, nationality, income and education are not used. "
                   "'Clearly better' means the 95% range of the AUC gain over FINDRISC is above zero."}
    save_json(res, os.path.join(MODELS, "ml_comparison.json"))
    print(f"\n{'':50} {'AUC (95% range)':24} cases found by inviting {int(TOP * 100)}%")
    for r in rows:
        extra = "" if not r["diff_vs_findrisc"] else f"  gain {r['diff_vs_findrisc']['auc_gain']:+.3f} {r['diff_vs_findrisc']['range']}"
        print(f"{r['name']:50} {r['auc']:.3f} ({r['auc_low']:.3f} to {r['auc_high']:.3f})   {r['cases_found_share']:.0%}{extra}")
    print("\nwhat drives the model the app uses (standardised weights):", drivers)
    print("\nAUC by group, FINDRISC vs the model the app uses:")
    for g in by_group:
        print(f"   {g['group']:13} {g['value']:18} adults {g['adults']:5d} cases {g['cases']:3d}  FINDRISC {g['findrisc_auc']:.3f}  model {g['model_auc']:.3f}")
    print("\ntrain on two cycles, test on the third (AUC):")
    for b in by_cycle:
        print("  ", b)
    return res


if __name__ == "__main__":
    main()
