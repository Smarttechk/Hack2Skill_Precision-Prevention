"""
Shadow check: how do the trained second opinion and the published FINDRISC score rank the hidden diabetes cases among the app's own
simulated residents?

    python -m models.ml_shadow        writes outputs/models/ml_shadow.json (the Results page shows it)

"Shadow" means the model only watches: it changes nobody's priority and no result of the simulation. The hidden truth
(data/processed/uae_synthea/truth_hidden.csv) is used here only as the answer key, exactly as evaluate.py uses it, and is never an
input of either score.

Who is scored: adults the score covers (enough records, no diabetes diagnosis on record). A hidden case is a person whose
undiagnosed diabetes (HbA1c of 6.5% or more) began on or before the run date.

Read the result with care. The simulation creates its hidden cases by its own rules (age, BMI, family history and so on), not from the
cholesterol, HDL or blood-pressure values the trained model also reads, so this shows how the model behaves on this population. It is not
a measure of real-world accuracy: that is the real-data check on NHANES (models/ml_comparison.py).
"""
import os
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils  # noqa: E402
from models import ml_risk, risk_scores, validate_scores as vs  # noqa: E402
from utils import MODELS, ensure_dirs, save_json  # noqa: E402

SEED, N_BOOT = 11, 400
TOP = vs.TOP_SHARE


def population():
    """The adults the scores cover, with the answer key and both scores."""
    f = utils.load_features()
    truth = utils.load_truth()
    today = utils.run_date()
    adults = f[(f.age >= risk_scores.CR["adult_age"]) & (f.data_sufficient == 1) & (f[risk_scores.KNOWN["diabetes"]].fillna(0) == 0)].copy()
    t = truth.reindex(adults.Id.values)
    adults["hidden"] = (t.hidden_diabetes.fillna(False).astype(bool) & (t.diabetes_onset <= today)).values.astype(int)
    model = ml_risk.load()
    adults["published"] = risk_scores.score(risk_scores.load(f), adults).risk.values
    adults["trained"] = ml_risk.chance(model["params"], adults, model["local_shift"]).values
    return adults, model


def top_share(y, s, share):
    k = max(int(round(share * len(s))), 1)
    order = np.argsort(-np.asarray(s, dtype=float), kind="stable")[:k]
    return float(y[order].sum() / max(y.sum(), 1))


def main():
    ensure_dirs()
    a, model = population()
    y = a.hidden.values
    pub, tr = a.published.values.astype(float), a.trained.values.astype(float)
    rng = np.random.default_rng(SEED)
    boots = {"published": [], "trained": [], "gain": []}
    for _ in range(N_BOOT):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() == y[i].max():
            continue
        p, t = roc_auc_score(y[i], pub[i]), roc_auc_score(y[i], tr[i])
        boots["published"].append(p); boots["trained"].append(t); boots["gain"].append(t - p)
    q = lambda v: [round(float(x), 3) for x in np.percentile(v, [2.5, 97.5])]
    auc_p, auc_t = round(float(roc_auc_score(y, pub)), 3), round(float(roc_auc_score(y, tr)), 3)
    hi_p = pub >= np.quantile(pub, 1 - risk_scores.HIGH_SHARE)
    hi_t = tr >= np.quantile(tr, 1 - risk_scores.HIGH_SHARE)
    cases = y == 1
    # why: does the model still rank well when it cannot see the measurements the simulation never linked to diabetes?
    plain = a.copy()
    for c in ("sbp", "total_chol", "hdl"):
        plain[c] = np.nan
    auc_plain = round(float(roc_auc_score(y, ml_risk.chance(model["params"], plain).values)), 3)
    # and how much does each input say about diabetes in real adults (NHANES) and in the simulated residents?
    from models import ml_comparison
    real = ml_comparison.load()
    rows_in = []
    for c, label in (("age", "Age"), ("bmi", "BMI"), ("hdl", "HDL cholesterol"), ("total_chol", "Total cholesterol"), ("sbp", "Systolic blood pressure")):
        sign = 1 if model["params"]["coef"][c] > 0 else -1             # read each input in the direction the model uses it
        r_ok, s_ok = real[c].notna(), a[c].notna()
        rows_in.append({"input": label, "real_adults_auc": round(float(roc_auc_score(real.y[r_ok], sign * real[c][r_ok])), 3),
                        "simulated_auc": round(float(roc_auc_score(y[s_ok.values], sign * a[c][s_ok].values)), 3)})
    groups = {"Sex": a.male.map({1: "Men", 0: "Women"}), "Age": pd.cut(a.age, [0, 44, 64, 200], labels=["under 45", "45 to 64", "65 and over"]),
              "Nationality": a.nationality}
    by_group = []
    for gname, col in groups.items():
        for lab in col.dropna().unique():
            m = (col == lab).values
            if y[m].sum() >= 15 and y[m].min() != y[m].max():
                by_group.append({"group": gname, "value": str(lab), "adults": int(m.sum()), "cases": int(y[m].sum()),
                                 "published_auc": round(float(roc_auc_score(y[m], pub[m])), 3), "trained_auc": round(float(roc_auc_score(y[m], tr[m])), 3)})
    res = {
        "population": "the app's own simulated residents: adults with enough records and no diabetes diagnosis on record",
        "label": "hidden (undiagnosed) diabetes that began on or before the run date, from the simulation's hidden truth",
        "adults": int(len(a)), "cases": int(y.sum()), "top_share": TOP, "high_share": risk_scores.HIGH_SHARE,
        "published": {"name": "FINDRISC (published, sets the priority)", "auc": auc_p, "auc_low": q(boots["published"])[0], "auc_high": q(boots["published"])[1],
                      "cases_found_top30": round(top_found := top_share(y, pub, TOP), 3), "cases_in_high_band": round(float(cases[hi_p].sum() / max(cases.sum(), 1)), 3)},
        "trained": {"name": "Trained second opinion (information only)", "auc": auc_t, "auc_low": q(boots["trained"])[0], "auc_high": q(boots["trained"])[1],
                    "cases_found_top30": round(top_share(y, tr, TOP), 3), "cases_in_high_band": round(float(cases[hi_t].sum() / max(cases.sum(), 1)), 3)},
        "auc_gain": {"value": round(auc_t - auc_p, 3), "range": q(boots["gain"])},
        "high_band_overlap": {"found_by_both": int((cases & hi_p & hi_t).sum()), "only_published": int((cases & hi_p & ~hi_t).sum()),
                              "only_trained": int((cases & ~hi_p & hi_t).sum()), "neither": int((cases & ~hi_p & ~hi_t).sum())},
        "trained_without_measurements": {"auc": auc_plain, "what": "the same model with HDL, total cholesterol and blood pressure left out (the typical value used)"},
        "single_input_auc": rows_in,
        "rank_agreement": round(float(pd.Series(pub).corr(pd.Series(tr), method="spearman")), 3),
        "by_group": by_group,
        "note": "The model only watches: it changes nobody's priority and no result of the simulation. The simulation makes its hidden cases by its own "
                "rules, not from the cholesterol, HDL or blood-pressure values the trained model also reads, so this shows how the model behaves on "
                "this population. It is not a measure of real-world accuracy: that is the real-data check on NHANES.",
    }
    save_json(res, os.path.join(MODELS, "ml_shadow.json"))
    ov = res["high_band_overlap"]
    print(f"{len(a)} simulated adults, {int(y.sum())} hidden diabetes cases")
    print(f"  published FINDRISC : AUC {auc_p} ({res['published']['auc_low']} to {res['published']['auc_high']}), top 30% finds {res['published']['cases_found_top30']:.0%}, High band holds {res['published']['cases_in_high_band']:.0%}")
    print(f"  trained model      : AUC {auc_t} ({res['trained']['auc_low']} to {res['trained']['auc_high']}), top 30% finds {res['trained']['cases_found_top30']:.0%}, High band holds {res['trained']['cases_in_high_band']:.0%}")
    print(f"  gain {res['auc_gain']['value']:+.3f} {res['auc_gain']['range']};  High band: both {ov['found_by_both']}, only published {ov['only_published']}, only trained {ov['only_trained']}, neither {ov['neither']}")
    print(f"  rank agreement (Spearman) {res['rank_agreement']}")
    print(f"  trained model without HDL, cholesterol and blood pressure: AUC {auc_plain}")
    for r in rows_in:
        print(f"   one input alone, {r['input']:24} real adults AUC {r['real_adults_auc']}   simulated AUC {r['simulated_auc']}")
    for g in by_group:
        print(f"   {g['group']:12} {g['value']:16} adults {g['adults']:4d} cases {g['cases']:3d}  published {g['published_auc']:.3f}  trained {g['trained_auc']:.3f}")
    return res


if __name__ == "__main__":
    main()
