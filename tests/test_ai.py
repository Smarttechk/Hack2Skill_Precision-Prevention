"""
The risk score and the AI steps that act inside the rules: the published diabetes score (nothing is trained), the first-contact
channel learner, and the fixed doctor summary and chat reply (no AI service is called).
    python tests/test_ai.py
Needs the features, the risk scores (python -m models.validate_scores; python -m models.risk_scores) and a simulation run
(python orchestrator.py, then python evaluate.py).
"""
import os
import sys
import tempfile
import warnings
from types import SimpleNamespace

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
tmp = tempfile.mkdtemp()
os.environ["PP_USERS_FILE"] = os.path.join(tmp, "none.json")     # open mode: the API answers as the admin
os.environ["PP_ANSWERS_FILE"] = os.path.join(tmp, "answers.json")   # clinic answers saved below go to a temporary file
os.environ.pop("PP_PASSWORD", None)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import summaries  # noqa: E402
import orchestrator  # noqa: E402
import simulation  # noqa: E402
import utils  # noqa: E402
from agents.outreach import ChannelLearner, channel_for  # noqa: E402
from models import risk_scores as rs, validate_scores as vs  # noqa: E402

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


# ---- the diabetes score is the published FINDRISC questionnaire; nothing is trained
one = pd.DataFrame({"age": [50, 30], "male": [1, 0], "bmi": [31.0, 22.0], "waist": [103.0, 70.0], "has_hypertension": [1, 0],
                    "has_prediabetes": [0, 0], "family_hist_diabetes": [1, 0], "family_history_recorded": [1, 1]})
# worked by hand: man of 50, BMI 31, waist 103, treated for blood pressure, close relative with diabetes
#   FINDRISC age 50 = 2, BMI over 30 = 3, waist over 102 = 4, blood pressure treatment = 2, family = 5, high sugar 0 -> 16
#   ADA age 50 = 2, BMI 30-39 = 2, male = 1, family = 1, high blood pressure = 1 -> 7
pts, unknown = rs.items(one)
check("FINDRISC gives the published points for a worked example, and zero for a young, slim, healthy woman",
      list(pts.sum(axis=1)) == [16.0, 0.0] and list(vs.ada(one)) == [7.0, 0.0])
check("the yes/no rule is age 45 or more, or BMI 25 or more", list(vs.broad_rule(one)) == [1.0, 0.0])
lifestyle = one.assign(exercise_daily=[0, 1], veg_daily=[0, 1])
check("the two lifestyle answers add their published points (no daily exercise 2, no daily vegetables 1) and leave nothing unknown",
      list(rs.items(lifestyle)[0].sum(axis=1)) == [19.0, 0.0] and list(rs.items(lifestyle)[1]) == [0.0, 0.0] and list(rs.items(one)[1]) == [3.0, 3.0])
check("the published bands: under 7 points about 1%, 7 to 11 about 4%, 12 to 14 about 17%, 15 to 20 about 33%, over 20 about 50%",
      [round(float(x), 2) for x in rs.chance([3, 9, 13, 17.5, 23])] == [0.01, 0.04, 0.17, 0.33, 0.5]
      and (np.diff(rs.chance(np.arange(0, 27))) >= 0).all())
f = utils.load_features()
adults = f[(f.age >= 18) & (f.data_sufficient == 1)]
bundle = rs.load(f)
s = rs.score(bundle, adults)
known = adults.has_diabetes.fillna(0).astype(bool).values
sc = s[~known]
card = utils.load_json(os.path.join(utils.MODELS, "risk_model_card.json"), {})
check("the card says the score is published and nothing is trained, with its source and the items the records lack",
      "FINDRISC" in card["conditions"]["diabetes"]["score"] and "Nothing is trained" in card["method"] and "Lindstrom" in card["source"]
      and "Waist" in card["conditions"]["diabetes"]["items_not_in_the_records"])
check(f"every adult without a diabetes diagnosis gets a chance between 0 and 1 inside its range ({len(sc)} adults); known diabetes gets none",
      sc.risk.between(0, 1).all() and (sc.risk_low <= sc.risk).all() and (sc.risk <= sc.risk_high).all() and s[known].risk.isna().all())
check("each score comes with up to three reasons that name the items which gave the points",
      sc.risk_reasons.map(lambda r: 1 <= len(r) <= 3 and all(x in set(rs.items(one)[0].columns) | {"No single strong factor"} for x in r)).all())
t = rs.local_target("diabetes", f)
check(f"scaled to the UAE level: the average chance is the expected undiagnosed rate ({t['undiagnosed_rate_target']:.1%})",
      abs(float(sc.risk.mean()) - t["undiagnosed_rate_target"]) < 0.01)
central = rs.items(adults[~known])[0].sum(axis=1) + rs.items(adults[~known])[1] / 2
check("the UAE shift changes the level, never the order of people",
      pd.Series(sc.risk.values).corr(pd.Series(central.values), method="spearman") > 0.999)
check("nationality, language, insurance and region are never inputs",
      not {"nationality", "language", "insurance", "region"} & set(rs.items(adults)[0].columns) and "region" in card["not_used"])
check("blood pressure has no predicted score (a measured high reading is a finding); children have none either",
      "hypertension" not in rs.CONDITIONS and "hypertension_risk" not in s.columns and s.loc[f[f.age < 18].index.intersection(s.index)].empty)
check("diabetes is flagged High for the top 15% of local scores and Moderate for the top 40%",
      rs.band(card["threshold_high"] + 0.001, "diabetes") == "High" and rs.band(card["threshold_moderate"] + 1e-6, "diabetes") == "Moderate"
      and rs.band(0.0001, "diabetes") == "Low" and rs.band(None) is None)
val = utils.load_json(os.path.join(utils.MODELS, "nhanes_validation.json"), {})
names = [x["name"] for x in val.get("scores", [])]
check("on real NHANES adults the score ranks undiagnosed diabetes well above the broad rule, with ranges and sources",
      len(names) == 4 and val["adults"] > 5000 and val["cases"] > 200
      and next(x for x in val["scores"] if x["kind"] == "used here")["auc"] > 0.68 > next(x for x in val["scores"] if x["kind"] == "rule")["auc"]
      and all(x["auc_low"] <= x["auc"] <= x["auc_high"] for x in val["scores"] if x["auc_low"] is not None)
      and all(x["source"] for x in val["scores"] if x["kind"] in ("published", "used here")))
check("the check is run on each survey cycle separately and holds (AUC above 0.68 in each)",
      len(val["by_cycle"]) == 3 and all(c["auc"] > 0.68 for c in val["by_cycle"]))
check("no model-training code is left in the project: no learner is fitted anywhere in the package",
      not any(os.path.exists(os.path.join(ROOT, "models", n)) for n in ("risk_model.py", "child_model.py", "nhanes_validation.py"))
      and not any(("XGB" + "Classifier") in open(os.path.join(d, n), encoding="utf-8").read() for d, _, fs in os.walk(ROOT)
                  for n in fs if n.endswith(".py") and not any(x in d for x in ("node_modules", ".git", "venv"))))

# ---- heart disease: the published pooled cohort equations (information for the doctor)
ex = pd.DataFrame({"age": [55, 55, 30, 85, 60], "male": [1, 0, 1, 1, 1], "total_chol": [213, 213, 200, 200, 200], "hdl": [50, 50, 50, 50, 45],
                   "sbp": [120, 120, 120, 120, 140], "smoker": [0, 0, 0, 0, 1], "has_diabetes": [0, 0, 0, 0, 1], "has_hypertension": [0, 0, 0, 0, 1]})
h = rs.ascvd(ex)
check("the pooled cohort equations reproduce the guideline's worked example (55 y, TC 213, HDL 50, SBP 120, no risk factors): "
      f"man {h.risk[0]:.1%} (published 5.3%), woman {h.risk[1]:.1%} (published 2.1%)",
      0.052 <= h.risk[0] <= 0.056 and 0.019 <= h.risk[1] <= 0.022)
check("outside ages 40 to 79 there is no score; smoking, diabetes and treated high blood pressure raise it a lot",
      pd.isna(h.risk[2]) and pd.isna(h.risk[3]) and h.risk[4] > 5 * h.risk[0] and h.band[4] == "High" and h.band[1] == "Low")
check("bands follow the guideline: under 5% Low, 5% to under 7.5% Moderate, 7.5% or more High", list(h.band[[0, 1, 4]]) == ["Moderate", "Low", "High"])
check("the factors named are the ones raised, from the allow-listed names",
      set(h.reasons[4]) <= {"Smoking", "Diabetes diagnosis", "Systolic blood pressure", "Total cholesterol", "HDL cholesterol", "Age"} and "Smoking" in h.reasons[4])

# ---- high blood pressure: the measured reading and its guideline category (no predicted score)
check("blood-pressure categories follow the 2017 ACC/AHA guideline at every boundary",
      [rs.bp_category(*x) for x in [(119, 79), (120, 79), (129, 79), (129, 80), (130, 70), (139, 89), (139, 90), (140, 70), (110, 90)]]
      == ["Normal", "Elevated", "Elevated", "Stage 1 high blood pressure", "Stage 1 high blood pressure", "Stage 1 high blood pressure",
          "Stage 2 high blood pressure", "Stage 2 high blood pressure", "Stage 2 high blood pressure"] and rs.bp_category(None, 80) is None)

# ---- the AI steps that act inside the rules
check("the one remaining AI step is off by default, because the comparison found no gain from it",
      simulation.DESIGN["ai_features"]["learn_first_channel"] is False and orchestrator.run("new", days=1).channel_learner is None)
ab = utils.load_json(os.path.join(utils.OUTPUTS, "results", "ai_ablation.json"), {})
check("the comparison covers both settings over the same seeds as the headline results",
      set(ab.get("summary", {})) == {"rules_only", "channel_learner"} and ab.get("seeds") == sorted(simulation.DESIGN["spread_seeds"]))
# ---- the first-contact channel learner
def person(phone, can_read=True, preferred=None, area="city"):
    return SimpleNamespace(row=SimpleNamespace(phone_type=phone, can_read=can_read, area_type=area), preferred_channel=preferred)


L = ChannelLearner(np.random.default_rng(0))
allowed = {c for cs in simulation.DESIGN["first_channel_learner"]["choices"].values() for c in cs}
check("the learner chooses only among self-service channels (never a staff call or a visit)",
      allowed <= {"WhatsApp", "SMS"})
check("a person's own stated channel always wins", L.choose(person("smartphone", preferred="Voice call")) == ("Voice call", False))
check("people without a smartphone keep the rule's channel",
      all(L.choose(p) == (channel_for(p), False) for p in (person("basic"), person("basic", can_read=False), person("none"))))
first = [L.choose(person("smartphone"))[0] for _ in range(200)]
check(f"it starts from the rule: WhatsApp for {first.count('WhatsApp')} of the first 200 smartphone owners",
      first.count("WhatsApp") >= 150)
p = person("smartphone")
for _ in range(300):                                   # replies say SMS works and WhatsApp does not, for these people
    L.learn(p, "SMS", True)
    L.learn(p, "WhatsApp", False)
later = [L.choose(p)[0] for _ in range(200)]
check(f"and moves when replies say so: SMS for {later.count('SMS')} of the next 200", later.count("SMS") >= 190)
row = next(r for r in L.summary() if r["channel"] == "SMS")
check("its table counts real messages and replies only (not the assumed starting replies)", row["messages"] == 300 and row["replies"] == 300)

# ---- no AI service: the doctor summary and the chat reply are fixed text, and nothing in the project needs an AI package
s1 = summaries.clinician_summary("Low", ["Age", "BMI"], ["Abnormal hypertension result awaiting review"], "Urgent")
check("the doctor summary puts the urgency and its cause first, then the risk band and the main factors",
      s1.startswith("Urgent because of abnormal hypertension") and "low band" in s1 and "Age, BMI" in s1 and s1.endswith("not a diagnosis."))
check("a person with no warning sign and no score still gets a plain sentence",
      "routine check is due" in summaries.clinician_summary(None, [], [], None) and "no risk score" in summaries.clinician_summary(None, [], [], None))
check("a free question in the chat gets the approved answer that points to the care team",
      summaries.general_answer("what is this?", "English", "bp_check").startswith("Thank you for your question"))
import re  # noqa: E402
skip = {"node_modules", "dist", ".git", "outputs", "demo_pack", "tests"}
found = []
for d_, dirs, files in os.walk(ROOT):
    dirs[:] = [x for x in dirs if x not in skip]
    for n in files:
        if n.endswith((".py", ".txt", ".json", ".jsx", ".js")) and n != "package-lock.json":
            txt = open(os.path.join(d_, n), encoding="utf-8", errors="ignore").read()
            if re.search(r"import anthropic|anthropic[>=]|ANTHROPIC_API_KEY", txt):
                found.append(n)
# the only place an outside AI service is named is the optional Care Assistant (api/assistant.py) and its own requirements file;
# the decisions, the summaries and the chat replies never use it, and the main requirements do not need it
check("only the optional Care Assistant refers to an AI service, and the main requirements do not need one",
      set(found) <= {"assistant.py", "requirements-assistant.txt", "models.json", "ar_more.js"}  # the last two only mention the key in text
      and "anthropic" not in open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read().lower())

# ---- the API
from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402
from api import app as appmod  # noqa: E402

c = TestClient(server.app)
ids = set(appmod.people().Id)
check("the results page no longer offers a written briefing", c.get("/api/results/briefing").status_code == 404)

pp_ = None
r = c.get("/api/results").json()
check("the results carry the real-data check of the published scores and the comparison of the remaining AI step",
      r["nhanes"]["headline"]["auc"] > 0.68 and "ai_features" in r and "child_model" not in r and set(r.get("ai_ablation", {}).get("summary", {})) >= {"rules_only"})
ff = appmod.feature_rows()
mid = ff[(ff.age.between(45, 70)) & ff.total_chol.notna() & ff.hdl.notna() & ff.sbp.notna()].index[0]
hr = appmod.heart_risk(mid)
check("a doctor sees the 10-year heart-disease chance (information only) for an adult aged 40 to 79 with the measurements, none for a child",
      hr and 0 < hr["risk"] < 1 and hr["band"] in ("Low", "Moderate", "High") and "information only" in hr["note"]
      and appmod.heart_risk(appmod.people()[lambda d: d.is_child == 1].Id.iloc[0]) is None
      and any(c["condition"] == "cardiovascular" for c in c.get(f"/api/person/{mid}").json()["conditions"]))
bpr = [i for i in ff[(ff.age >= 18) & ff.sbp.notna() & ff.dbp.notna()].index[:300] if appmod.bp_reading(i)]
cs_ = c.get(f"/api/person/{bpr[0]}").json()["conditions"]
check("the doctor sees an adult's last blood-pressure reading with its date and category, labelled as not a prediction; children get none",
      bpr and any(x["condition"] == "hypertension" and x["status"] in ("reading", "known", "found") for x in cs_)
      and "not a prediction" in appmod.bp_reading(bpr[0])["note"] and appmod.bp_reading(appmod.people()[lambda d: d.is_child == 1].Id.iloc[0]) is None)
# clinic answers (waist, exercise, vegetables) narrow the diabetes range for that person
dm_ = next(i for i in ff[(ff.age.between(30, 70))].index[:400]
           if any(x["condition"] == "diabetes" and x["status"] == "score" for x in c.get(f"/api/person/{i}").json()["conditions"]))
before = next(x for x in c.get(f"/api/person/{dm_}").json()["conditions"] if x["condition"] == "diabetes")
sv = c.post(f"/api/person/{dm_}/answers", json={"waist_cm": 84, "exercise_daily": True, "veg_daily": True})
after = next(x for x in c.get(f"/api/person/{dm_}").json()["conditions"] if x["condition"] == "diabetes")
check("with waist and lifestyle answers recorded the diabetes range narrows, the note says why, and the answers are kept",
      sv.status_code == 200 and (after["high"] - after["low"]) < (before["high"] - before["low"]) and "recorded" in after["note"]
      and after["low"] <= after["risk"] <= after["high"] and c.get(f"/api/person/{dm_}").json()["clinic_answers"]["waist_cm"] == 84)
check("a waist that is not plausible, an empty form and a child are refused",
      c.post(f"/api/person/{dm_}/answers", json={"waist_cm": 5}).status_code == 400 and c.post(f"/api/person/{dm_}/answers", json={}).status_code == 400
      and c.post(f"/api/person/{appmod.people()[lambda d: d.is_child == 1].Id.iloc[0]}/answers", json={"waist_cm": 60}).status_code == 400)
kid_ = appmod.people()[lambda d: d.is_child == 1].Id.iloc[0]
check("a child has no risk score for the doctor: invitations come from the rules", c.get(f"/api/person/{kid_}").json()["child_model"] is None
      and c.get(f"/api/person/{kid_}").json()["risk"] is None)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)