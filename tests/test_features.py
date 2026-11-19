"""
Checks on the preprocessed tables. Run after scripts/preprocess.py:
    python tests/test_features.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = os.path.join(ROOT, "data", "processed", "features")
P = os.path.join(ROOT, "data", "processed", "uae_synthea")
ok = []


def check(name, cond):
    ok.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)


pf = pd.read_csv(os.path.join(F, "patient_features.csv"), dtype={"Id": str, "guardian_id": str})
dic = pd.read_csv(os.path.join(F, "feature_dictionary.csv"))
pts = pd.read_csv(os.path.join(P, "patients.csv"), dtype={"Id": str})
s = json.load(open(os.path.join(F, "preprocess_summary.json")))

# ---- shape and privacy
check(f"one row per living person ({len(pf)})", len(pf) == pts.DEATHDATE.isna().sum() and pf.Id.is_unique)
us = {"SSN", "DRIVERS", "PASSPORT", "FIRST", "LAST", "ADDRESS", "CITY", "STATE", "ZIP", "LAT", "LON", "RACE", "INCOME"}
check("no US identity or address columns", not us & set(pf.columns))
check("hidden truth never a feature", not any(c.startswith(("hidden_", "diagnosed_")) or c.endswith("_onset") or "if_tested" in c
                                             for c in pf.columns))
check("every column is in the feature dictionary", set(pf.columns) <= set(dic.column))

# ---- values make sense
check("ages 0 to 90", pf.age.between(0, 90).all())
check("HbA1c, BMI and blood pressure in human ranges",
      pf.hba1c.dropna().between(3.5, 20).all() and pf.bmi.dropna().between(10, 80).all() and pf.sbp.dropna().between(60, 260).all())
kids = pf[pf.age < 18]
check(f"about a third of children missed a scheduled visit ({s['children_missing_any_scheduled_visit']:.0%})",
      0.25 <= s["children_missing_any_scheduled_visit"] <= 0.45)
check("adults have no missed well-child visits", (pf[pf.age >= 18].missed_well_child == 0).all())
check("growth concern only for ages 2-17", (pf[pf.child_growth_concern == 1].age.between(2, 17)).all())
check("follow-up done and no follow-up never both", not ((pf.followup_done == 1) & (pf.abnormal_no_followup == 1)).any())
ds = pf.groupby(pf.age >= 18).data_sufficient.mean()
check(f"'enough data' works for children and adults ({ds.to_dict()})", ds.min() > 0.3)

# ---- consent and genetics
g = pf.set_index("Id")
kc = kids[kids.guardian_id.notna()]
check("a child's consents are the guardian's", (kc.consent.values == g.loc[kc.guardian_id].consent.values).all()
      and (kc.consent_genetic.values == g.loc[kc.guardian_id].consent_genetic.values).all())
check("consent given by the guardian for every child", (kc.consent_given_by == kc.guardian_id).all())
check(f"contact consent about 92% ({pf.consent.mean():.0%})", 0.88 < pf.consent.mean() < 0.96)
st = pf.gene_status.map(json.loads)
check("genetic status only for markers the person carries",
      all(set(d) == {m for m in ["FH", "BRCA", "LYNCH", "HBB_CARRIER", "MODY"] if r[f"gene_{m}"]} for d, (_, r) in zip(st, pf.iterrows())))
check("actionable flag ignores uncertain variants",
      (pf.genetic_risk_flag == st.map(lambda d: any(v in ("pathogenic", "likely_pathogenic") for v in d.values()))).all())
par = kc.guardian_id.map(g.prs)
check(f"polygenic score passed from guardian to child (r = {np.corrcoef(par, kc.prs)[0, 1]:.2f})", 0.3 < np.corrcoef(par, kc.prs)[0, 1] < 0.7)


print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
