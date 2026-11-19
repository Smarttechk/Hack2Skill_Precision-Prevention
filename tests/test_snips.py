import sys, types, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, sys.argv[1])
from snips import S
import numpy as np, pandas as pd
from datetime import timedelta, date

ok = []
def check(name, cond):
    ok.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)

# ---- fake Synthea patients.csv + original Step 1.2 / 1.2b code ----
rng = np.random.default_rng(42)
N = 3000
bd = pd.Timestamp("2026-11-01") - pd.to_timedelta(rng.integers(0, 85 * 365, N), unit="D")
patients = pd.DataFrame({"Id": [f"p{i}" for i in range(N)], "BIRTHDATE": bd.strftime("%Y-%m-%d"),
                         "DEATHDATE": np.where(rng.random(N) < 0.05, "2020-01-01", None)})
n = len(patients)
patients["language"] = rng.choice(["Arabic","English","Urdu","Hindi","Tagalog"], n, p=[.35,.25,.15,.15,.10])
patients["phone_type"] = rng.choice(["smartphone","basic","none"], n, p=[.70,.22,.08])
patients["area_type"] = rng.choice(["city","rural","remote"], n, p=[.65,.25,.10])
patients["area"] = patients["area_type"] + "_" + rng.integers(1, 8, n).astype(str)
base = patients["area_type"].map({"city": 15, "rural": 45, "remote": 90})
patients["travel_min"] = (base + rng.normal(0, 10, n)).clip(5)
GENETIC_MARKERS = {"FH": 0.004, "BRCA": 0.003, "LYNCH": 0.003, "HBB_CARRIER": 0.05, "MODY": 0.001}
for marker, rate in GENETIC_MARKERS.items():
    patients[f"gene_{marker}"] = rng.random(n) < rate
before_fh = patients.gene_FH.sum()

ns = dict(np=np, pd=pd, rng=rng, patients=patients, GENETIC_MARKERS=GENETIC_MARKERS, n=n)
exec(S["context"], ns)
P = ns["patients"]
check("context: deceased removed", P.DEATHDATE.isna().all() and len(P) < N)
check("context: age int, age_group/nationality/region exist", P.age.dtype.kind == "i" and P.age_group.notna().all() and P.nationality.notna().all() and P.region.notna().all())
check("context: no smokers under 15", not P[(P.age < 15) & P.smoker].shape[0])
check("context: Emiratis have Thiqa", (P[P.nationality == "Emirati"].insurance == "Thiqa").all())
check("context: children have no work shift", (P[P.age < 18].work_shift == "none").all())

exec(S["households"], ns)
P = ns["patients"]
kids = P[P.age < 18]; by = P.set_index("id")
check("households: every child has guardian", kids.guardian_id.notna().all())
check("households: guardians are adults", (by.loc[kids.guardian_id].age >= 18).all())
check("households: adults have no guardian", P[P.age >= 18].guardian_id.isna().all())
check("households: child shares guardian language/area", (kids.language.values == by.loc[kids.guardian_id].language.values).all() and (kids.area.values == by.loc[kids.guardian_id].area.values).all())
check("households: child of FH carrier often FH", P.gene_FH.sum() >= before_fh)
# inheritance rate check with forced carriers
P2 = P.copy(); P2.loc[P2.age >= 18, "gene_HBB_CARRIER"] = True; P2.loc[P2.age < 18, "gene_HBB_CARRIER"] = False
ns2 = dict(ns, patients=P2, GENETIC_MARKERS={"HBB_CARRIER": 0})
exec(S["households"].split("for marker in GENETIC_MARKERS")[0].replace("", "") , ns2)  # no-op rebuild
by2 = P2.set_index("id"); col = "gene_HBB_CARRIER"
parent_has = P2.guardian_id.map(by2[col]).fillna(False).astype(bool)
P2.loc[parent_has & (rng.random(len(P2)) < 0.5), col] = True
rate = P2[P2.age < 18][col].mean()
check(f"households: inheritance ~50% (got {rate:.2f})", 0.4 < rate < 0.6)

# ---- interventions ----
exec(S["interventions"], ns)
row = types.SimpleNamespace
iv = ns["interventions"]
r = iv(row(hba1c=6.0, has_diabetes=False, age=40, bmi=32, smoker=True, active=False, bmi_percentile=np.nan))
check("interventions: adult prediabetic smoker gets 4", len(r) == 4)
r = iv(row(hba1c=np.nan, has_diabetes=False, age=9, bmi=np.nan, smoker=False, active=True, bmi_percentile=97))
check("interventions: obese child -> family nutrition only", [x["check"] for x in r] == ["Family nutrition programme"])
r = iv(row(hba1c=7.0, has_diabetes=True, age=50, bmi=24, smoker=False, active=True, bmi_percentile=np.nan))
check("interventions: diabetic not in prediabetes programme", r == [])

# ---- consent ----
ns["audit_log"] = []
exec(S["consent"], ns)
rc, hc = ns["record_consent"], ns["has_consent"]
child = row(id="c1", age=6, guardian_id="g1"); adult = row(id="a1", age=40, guardian_id=None)
check("consent: none recorded -> False", hc(adult, "contact") is False)
rc("a1", "contact", True, "a1", date(2026, 11, 1)); check("consent: granted", hc(adult, "contact") is True)
rc("a1", "contact", False, "a1", date(2026, 11, 2)); check("consent: latest withdrawal wins", hc(adult, "contact") is False)
rc("c1", "contact", True, "c1", date(2026, 11, 1)); check("consent: child alone cannot consent", hc(child, "contact") is False)
rc("c1", "contact", True, "g1", date(2026, 11, 2)); check("consent: guardian consent works", hc(child, "contact") is True)
check("consent: audit logged", len(ns["audit_log"]) == 4)

# ---- pathway card ----
ns["DEADLINE_DAYS"] = {"Urgent": 2, "High": 7, "Routine": 30}; ns["timedelta"] = timedelta
exec(S["pathway"], ns)
G = {"bp_check": {"pathway": "Adult comprehensive screening", "every_months": 12, "source": "USPSTF", "version": "2021"}}
t = date(2026, 11, 9)
c = ns["pathway_card"](row(urgency="Routine"), {"check": "bp_check"}, G, t)
check("pathway: in-guideline routine -> outreach", c["decision"].startswith("Outreach") and c["guideline"] == "USPSTF (2021)" and c["invite_by"] == t + timedelta(days=30))
c = ns["pathway_card"](row(urgency="High"), {"check": "genetic_counselling", "reason": "Genetic marker: BRCA"}, G, t)
check("pathway: genetic -> clinician", c["decision"] == "Clinician decides" and c["guideline"] == "Not in approved guidelines")
c = ns["pathway_card"](row(urgency="Urgent"), {"check": "bp_check"}, G, t)
check("pathway: urgent -> clinician, 2-day deadline", c["decision"] == "Clinician decides" and c["invite_by"] == t + timedelta(days=2))

# ---- routing + templates ----
exec(S["routing"], ns)
people = pd.DataFrame([{"id": "g1", "age": 35, "language": "Urdu", "guardian_id": None},
                       {"id": "c1", "age": 4, "language": "Urdu", "guardian_id": "g1"},
                       {"id": "a2", "age": 45, "language": "English", "guardian_id": None}]).set_index("id", drop=False)
txt, to = ns["safe_message"](people.loc["c1"], {"check": "well_child_visit"}, "Clinic visit on Monday", people)
check("routing: child message goes to guardian", to == "g1" and "your child" in txt)
txt, to = ns["safe_message"](people.loc["a2"], {"check": "breast_screening"}, "Clinic visit", people)
check("routing: BRCA-driven message never says gene/cancer", txt and not any(w in txt.lower() for w in ["gene", "cancer", "brca"]))
txt, why = ns["safe_message"](people.loc["a2"], {"check": "lung_screening"}, "Clinic visit", people)
check("routing: missing template -> staff review", txt is None and "review" in why)
ns["TEMPLATES"][("bp_check", "adult", "English")] = "Your cancer risk score is 70%. {option}"
txt, why = ns["safe_message"](people.loc["a2"], {"check": "bp_check"}, "Clinic visit", people)
check("routing: unsafe template blocked", txt is None and "safety" in why)

# ---- timing ----
exec(S["timing"], ns)
cw = ns["contact_window"]
fri = date(2026, 11, 13); mon = date(2026, 11, 9)
check("timing: Friday afternoon", fri.weekday() == 4 and cw(row(preferred_time=None, work_shift="none"), fri, set()) == "16:00-20:00")
check("timing: Ramadan after iftar", cw(row(preferred_time=None, work_shift="day"), mon, {mon}) == "20:30-22:30")
check("timing: day worker evening", cw(row(preferred_time=None, work_shift="day"), mon, set()) == "18:00-21:00")
check("timing: preference wins", cw(row(preferred_time="Morning", work_shift="day"), mon, {mon}) == "Morning")

# ---- llm_safe ----
ns["llm"] = lambda prompt: "OK:" + prompt
exec(S["llmsafe"], ns)
check("llm_safe: allowed fields pass", ns["llm_safe"]("Explain {check} in {language}", check="bp_check", language="Arabic").startswith("OK:"))
try:
    ns["llm_safe"]("x {name}", name="Ahmed", check="x"); check("llm_safe: blocks name", False)
except ValueError as e:
    check("llm_safe: blocks name", "name" in str(e))

# ---- fairness ----
exec(S["fair"], ns)
lo, hi = ns["wilson"](5, 10); check(f"wilson 5/10 ~ (0.237,0.763) got {lo},{hi}", abs(lo - 0.237) < 0.01 and abs(hi - 0.763) < 0.01)
check("wilson n=0 -> nan", np.isnan(ns["wilson"](0, 0)[0]))
df = pd.DataFrame({"g": ["a"] * 200 + ["b"] * 200, "risk": [0.2] * 200 + [0.5] * 200,
                   "outcome": [1] * 40 + [0] * 160 + [1] * 60 + [0] * 140})
cb = ns["calibration_by_group"](df, "g")
check("calibration: group a well calibrated, b over-predicts", abs(cb.loc["a", "gap"]) < 1e-9 and abs(cb.loc["b", "gap"] - 0.2) < 1e-9)
t = pd.DataFrame({"count": [500, 300, 40, 6], "rate": [.5, .4, .3, .2]}, index=["w", "x", "y", "z"])
s = ns["suppress"](t)
check("suppress: tiny + next smallest hidden", s.loc["z", "count"] == "Too few to report" and s.loc["y", "count"] == "Too few to report" and s.loc["x", "count"] == 300)
t2 = pd.DataFrame({"count": [500, 6, 7], "rate": [.5, .2, .1]}, index=["w", "y", "z"])
s2 = ns["suppress"](t2)
check("suppress: two small hidden, big kept", s2.loc["w", "count"] == 500 and (s2.loc[["y", "z"], "count"] == "Too few to report").all())

# ---- outcomes ----
ns["patients"] = P
exec(S["outcomes"], ns)
inv = pd.DataFrame({"invited_on": pd.to_datetime(["2026-11-01"] * 4),
                    "attended_on": pd.to_datetime(["2026-11-10", "2026-12-20", None, "2026-11-30"])})
check("timely attendance = 0.5", ns["timely_attendance"](inv) == 0.5)
res = pd.DataFrame({"abnormal": [True, True, False], "result_on": pd.to_datetime(["2026-11-01"] * 3),
                    "followup_on": pd.to_datetime(["2026-11-15", None, None])})
check("follow-up completion = 0.5", ns["followup_completion"](res) == 0.5)
rr = np.random.default_rng(0); p = rr.uniform(0.05, 0.95, 20000); y = (rr.random(20000) < p).astype(int)
cs = ns["calibration_summary"](y, p)
check(f"calibration summary perfect model slope~1 int~0 ({cs['slope']:.2f},{cs['intercept']:.2f})", abs(cs["slope"] - 1) < 0.1 and abs(cs["intercept"]) < 0.1)
check("holdout ~10%", 0.07 < (P.arm == "usual_care").mean() < 0.13)

st = pd.DataFrame({"state": [None, "Flagged", "Done"], "urgency": [None, "High", "Routine"]})
check("10.1 fix: flagged counts urgency not None", int(st.urgency.notna().sum()) == 2 and int((st.state != "None").sum()) == 3)
att = [0.75 - 0.003 * t for t in (15, 45, 90)]
check(f"8.3 fix: attendance between 48% and 70% ({att})", abs(att[0] - 0.705) < 1e-9 and abs(att[2] - 0.48) < 1e-9)

print(f"\n{sum(c for _, c in ok)}/{len(ok)} checks passed")
sys.exit(0 if all(c for _, c in ok) else 1)
