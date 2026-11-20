"""
The trained second opinion on undiagnosed diabetes (models/ml_risk.py, models/ml_comparison.py): it must be honest, safe and
information only.
    python tests/test_ml_risk.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import utils  # noqa: E402
from models import ml_comparison, ml_risk, risk_scores  # noqa: E402

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


card = ml_risk.load()
cmp_ = utils.load_json(os.path.join(utils.MODELS, "ml_comparison.json"), {})
app_row = next((r for r in cmp_.get("scores", []) if r.get("used_in_app")), {})
pub_row = next((r for r in cmp_.get("scores", []) if r.get("kind") == "published"), {})

# ---- what it is
check("the model is a short list of plain numbers saved as JSON (no pickled object), marked information only",
      card is not None and card["status"] == "information only" and set(card["params"]["coef"]) == set(ml_risk.INPUTS)
      and all(isinstance(v, float) for v in card["params"]["coef"].values()))
banned = {"nationality", "language", "insurance", "region", "race", "ethnicity", "income", "education", "hba1c"}
check("it never uses nationality, language, insurance, region, race, income, education, and never the HbA1c that defines the label",
      not banned & {i.lower() for i in ml_risk.INPUTS} and banned >= {"nationality", "race"} and "nationality" in card["not_used"])
check("the card lists what it must never be used for, and its limits (US survey data, no UAE validation)",
      "changing anyone's priority" in card["not_allowed_uses"] and any("UAE" in x for x in card["limits"]))
w = card["params"]["coef"]
check("the weights make clinical sense: older age, higher BMI, prediabetes, family history and higher blood sugar risk push the chance up, higher HDL pulls it down",
      w["age"] > 0 and w["bmi"] > 0 and w["has_prediabetes"] > 0 and w["family_hist_diabetes"] > 0 and w["hdl"] < 0)

# ---- the honest comparison it rests on
check("it was compared with the published score on the same real adults the Results page reports (12,925 with 456 cases)",
      cmp_.get("adults") == 12925 and cmp_.get("cases") == 456 and pub_row.get("auc") is not None)
check("on people it was not trained on it ranks better than FINDRISC, and the 95% range of the gain is above zero",
      app_row.get("auc", 0) > pub_row.get("auc", 1) and app_row["diff_vs_findrisc"]["range"][0] > 0 and app_row["diff_vs_findrisc"]["clearly_better"])
check("the gain holds in every group checked (sex, age band, ethnic group) and on every survey cycle left out of training",
      cmp_["by_group"] and all(g["model_auc"] > g["findrisc_auc"] for g in cmp_["by_group"])
      and all(c["Logistic regression, as used in the app"] > c["FINDRISC (not trained)"] for c in cmp_["train_on_two_cycles_test_on_third"]))
five = next(r for r in cmp_["scores"] if r["name"] == "Logistic regression, same 5 inputs")
check("the table shows where the gain comes from: with only the five questionnaire inputs it is small, the measurements add the rest",
      five["diff_vs_findrisc"]["auc_gain"] < app_row["diff_vs_findrisc"]["auc_gain"])

# ---- no leakage, and no learning from which values were missing
t = ml_comparison.load()
rng = np.random.default_rng(3)
idx = rng.permutation(len(t))
a, b = t.iloc[idx[:len(t) // 2]], t.iloc[idx[len(t) // 2:]]
from sklearn.metrics import roc_auc_score  # noqa: E402
y_shuf = rng.permutation(t.y.values)                      # the label no longer has anything to do with the inputs
p_shuf = ml_risk.fit(a, y_shuf[idx[:len(t) // 2]])
auc_shuf = roc_auc_score(y_shuf[idx[len(t) // 2:]], ml_risk.chance(p_shuf, b))
check(f"with the labels shuffled it scores at chance on new people (AUC {auc_shuf:.2f}): training and scoring leak nothing into each other",
      0.43 < auc_shuf < 0.57)
p_real = ml_risk.fit(a, a.y.values)
row = b.iloc[[0]].copy()
full = ml_risk.chance(p_real, row.assign(hdl=ml_risk.frame(a)["hdl"].median(), total_chol=ml_risk.frame(a)["total_chol"].median())).iloc[0]
gap = ml_risk.chance(p_real, row.assign(hdl=np.nan, total_chol=np.nan)).iloc[0]
check("a missing cholesterol or HDL is replaced by the typical value and is not a signal of its own",
      abs(full - gap) < 1e-9 and ml_risk.explain(p_real, row.assign(hdl=np.nan, total_chol=np.nan))[0]["measured"]
      == ml_risk.explain(p_real, row)[0]["measured"] - (0 if pd.isna(row.hdl.iloc[0]) and pd.isna(row.total_chol.iloc[0]) else 2))

# ---- on the hospital's own records
f = utils.load_features()
adults = f[(f.age >= risk_scores.CR["adult_age"]) & (f.data_sufficient == 1) & (f.has_diabetes.fillna(0) == 0)]
target = card["local_calibration"]["undiagnosed_rate_target"]
mean = float(ml_risk.chance(card["params"], adults, card["local_shift"]).mean())
check(f"scaled to the UAE level by one intercept shift: the average chance of local adults without a diagnosis is {mean:.3f} (target {target:.3f})",
      abs(mean - target) < 0.003)
share_high = float(np.mean([ml_risk.band(x) == "High" for x in ml_risk.chance(card["params"], adults, card["local_shift"])]))
check("High is the top 15% of local scores, as for the published score", abs(share_high - risk_scores.HIGH_SHARE) < 0.01)
corr = pd.Series(ml_risk.chance(card["params"], adults).values).corr(pd.Series(risk_scores.score(risk_scores.load(f), adults).risk.values), method="spearman")
check(f"it agrees broadly with the published score but is not a copy of it (rank correlation {corr:.2f})", 0.4 < corr < 0.95)
child = f[f.age < 18].head(1)
diagnosed = f[(f.age >= 20) & (f.has_diabetes.fillna(0) == 1)].head(1)
one = adults.head(1)
check("it is given only to adults without a diabetes diagnosis, and says how many inputs were recorded",
      ml_risk.second_opinion(child) is None and ml_risk.second_opinion(diagnosed) is None
      and ml_risk.second_opinion(one)["of"] == 8 and 5 <= ml_risk.second_opinion(one)["measured"] <= 8)

# ---- information only
import api.app as appmod  # noqa: E402
pid = one.Id.iloc[0]
r = appmod.person_row(pid)
rows = appmod.condition_status(r)
names = [c["condition"] for c in rows]
second = next((c for c in rows if c["condition"] == "diabetes_ml"), None)
check("the Doctor view gets it as an extra row after the published diabetes score, never instead of it",
      second is not None and names.index("diabetes") < names.index("diabetes_ml") and "Information only" in second["note"]
      and rows[names.index("diabetes")]["risk"] != second["risk"])
ml_risk.M["ml_second_opinion"]["enabled"] = False
off = [c["condition"] for c in appmod.condition_status(r)]
ml_risk.M["ml_second_opinion"]["enabled"] = True
check("the switch in config/models.json removes it and leaves the published score as it was", "diabetes_ml" not in off and "diabetes" in off)
sources = ["orchestrator.py", "simulation.py", "evaluate.py", "agents/priority.py", "agents/risk.py", "agents/outreach.py", "agents/care_gap.py",
           "agents/chat.py", "agents/followup.py", "agents/evaluation.py", "models/risk_scores.py"]
import ast  # noqa: E402


def reads_ml_risk(path):
    """True when the file really imports or uses ml_risk (not when a comment or docstring merely names it)."""
    tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "ml_risk":
            return True
        if isinstance(node, ast.ImportFrom) and any(a.name == "ml_risk" for a in node.names):
            return True
        if isinstance(node, ast.Import) and any(a.name.split(".")[-1] == "ml_risk" for a in node.names):
            return True
        if isinstance(node, ast.Name) and node.id == "ml_risk":
            return True
    return False


used = [s for s in sources if reads_ml_risk(s)]
check("nothing that decides who is contacted, or in what order, reads it: priority and every result are the same with it on or off", not used)
check("the switch is a documented setting, on by default", utils.config("models")["ml_second_opinion"]["enabled"] is True)

# ---- the shadow check on the simulated residents
from models import ml_shadow  # noqa: E402
sh = utils.load_json(os.path.join(utils.MODELS, "ml_shadow.json"), {})
pop, _ = ml_shadow.population()
check("the shadow check scores exactly the adults the published score covers, with the hidden truth as the answer key (never an input)",
      sh["adults"] == len(pop) == len(adults) and sh["cases"] == int(pop.hidden.sum()) and "hidden" not in ml_risk.INPUTS
      and not any(c in ml_risk.INPUTS for c in ("hidden_diabetes", "diabetes_onset", "hba1c_if_tested_now")))
check("its numbers can be recomputed: the published and the trained AUC match the file",
      abs(roc_auc_score(pop.hidden, pop.published) - sh["published"]["auc"]) < 0.002 and abs(roc_auc_score(pop.hidden, pop.trained) - sh["trained"]["auc"]) < 0.002)
check("on the simulated people FINDRISC reproduces the README's figure (AUC about 0.69), so the answer key is the right one", abs(sh["published"]["auc"] - 0.69) < 0.03)
check("it reports what it found, whichever way: both AUCs with ranges, the gain with its range, the overlap of the High band, and why (inputs alone, real against simulated)",
      all(k in sh for k in ("published", "trained", "auc_gain", "high_band_overlap", "trained_without_measurements", "single_input_auc"))
      and len(sh["single_input_auc"]) == 5 and "not a measure of real-world accuracy" in sh["note"] and "only watches" in sh["note"])
def opens_truth(path):
    """True when the code (not a docstring or comment) calls load_truth or names the truth file."""
    tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
    doc = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
           and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr == "load_truth" or isinstance(n, ast.Name) and n.id == "load_truth":
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "truth_hidden" in n.value and id(n) not in doc:
            return True
    return False


truth_users = [m for m in ("models/ml_risk.py", "models/risk_scores.py", "models/ml_comparison.py") if opens_truth(m)]
check("the file check really catches the truth: it finds it in the shadow check, which uses it as the answer key", opens_truth("models/ml_shadow.py"))
check("the models that score people never open the hidden truth (only the shadow check and the evaluation do, as the answer key)", not truth_users)
used_shadow = [s for s in sources if "ml_shadow" in open(os.path.join(ROOT, s), encoding="utf-8").read()]
check("nothing that decides who is contacted reads the shadow check", not used_shadow)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
