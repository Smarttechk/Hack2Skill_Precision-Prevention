import sys, types, warnings, json, ast
warnings.filterwarnings("ignore")
sys.path.insert(0, sys.argv[1])
from snips2 import S2
import numpy as np, pandas as pd
from datetime import date, timedelta

ok = []
def check(name, cond):
    ok.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)
row = types.SimpleNamespace
rng = np.random.default_rng(7)

# ---- population weights ----
n = 4000
patients = pd.DataFrame({"age": rng.integers(0, 90, n), "GENDER": rng.choice(["M", "F"], n), "x": rng.random(n) < 0.3})
ns = dict(np=np, pd=pd, patients=patients)
exec(S2["weights"], ns)
P = ns["patients"]
check("weights: TARGET sums to 1", abs(sum(ns["TARGET"].values()) - 1) < 1e-9)
est = P.groupby(["band", "GENDER"], observed=True).weight.sum() / P.weight.sum()
check("weights: weighted mix equals TARGET", all(abs(est[k] - v) < 1e-9 for k, v in ns["TARGET"].items()))
P["young_male"] = (P.band == "18-44") & (P.GENDER == "M")
check(f"weights: weighted share of young men = 0.38 (raw {P.young_male.mean():.2f})", abs(ns["weighted_rate"](P, "young_male") - 0.38) < 1e-9)

# ---- Synthea strata total ----
counts = [int(l.split("-p ")[1].split()[0]) for l in S2["strata"].splitlines()]
check(f"strata: 5000 people in total ({sum(counts)}) and young men 38%", sum(counts) == 5000 and counts[0] / 5000 == 0.38)

# ---- access barrier ----
ns = dict(np=np)
exec(S2["access"].split("priority =")[0], ns)
ab = ns["access_barrier"]
worker = row(language="Urdu", insurance="Basic", work_shift="day", phone_type="basic", travel_min=20)
local = row(language="Arabic", insurance="Thiqa", work_shift="none", phone_type="smartphone", travel_min=20)
far = row(language="Arabic", insurance="Thiqa", work_shift="none", phone_type="smartphone", travel_min=120)
check(f"access: worker {ab(worker)} > far {ab(far)} > local {ab(local)}", ab(worker) > ab(far) > ab(local))
check("access: max is 1.0", ab(row(language="Urdu", insurance="Basic", work_shift="day", phone_type="none", travel_min=200)) == 1.0)
pr = lambda p, risk=0.4, gaps=2: 0.5 * risk + 0.2 * min(gaps / 5, 1) + 0.3 * ab(p)
check("access: same risk -> worker ranked above local", pr(worker) > pr(local))
check("access: much higher risk still wins", pr(local, risk=0.9) > pr(worker, risk=0.3))
compile(S2["access"], "access", "exec"); check("access: snippet compiles", True)

# ---- PRS ----
n = 20000
pts = pd.DataFrame({"id": [f"p{i}" for i in range(n)]})
pts["guardian_id"] = None
pts.loc[n // 2:, "guardian_id"] = [f"p{i}" for i in range(n // 2)]
ns = dict(np=np, pd=pd, rng=rng, patients=pts, n=n)
exec(S2["prs"], ns)
P = ns["patients"]; kids = P.guardian_id.notna()
par = P.set_index("id").prs.loc[P.guardian_id[kids]].values
check(f"prs: child SD ~1 ({P.prs[kids].std():.2f})", abs(P.prs[kids].std() - 1) < 0.05)
check(f"prs: parent-child correlation ~0.5 ({np.corrcoef(par, P.prs[kids])[0,1]:.2f})", abs(np.corrcoef(par, P.prs[kids])[0, 1] - 0.5) < 0.05)
cr = ns["combined_risk"]
r, why = cr(0.2, row(consent_genetic=True, prs=2.0)); check(f"prs: high score raises risk 0.2 -> {r:.2f} with reason", r > 0.2 and why)
r, why = cr(0.2, row(consent_genetic=False, prs=2.0)); check("prs: no consent -> unchanged", r == 0.2 and why is None)
r, why = cr(0.2, row(consent_genetic=True, prs=0.0)); check("prs: average score -> unchanged", abs(r - 0.2) < 1e-9 and why is None)
r, _ = cr(1.0, row(consent_genetic=True, prs=1.0)); check("prs: risk of 1.0 does not crash", 0 < r <= 1)

# ---- VUS + premarital ----
ns = {}
exec(S2["vus"], ns)
ga, pm = ns["genetic_action"], ns["premarital_flag"]
check("vus: pathogenic used", ga(row(gene_status={"BRCA": "pathogenic"}), "BRCA") == "Use in screening plan")
check("vus: VUS not used", ga(row(gene_status={"BRCA": "VUS"}), "BRCA").startswith("Do not use"))
check("vus: unknown -> None", ga(row(gene_status={}), "BRCA") is None)
a = row(consent_genetic=True, gene_HBB_CARRIER=True, married=False, age=24)
b = row(consent_genetic=True, gene_HBB_CARRIER=True, married=False, age=26)
check("premarital: single carrier -> routine", pm(a)[0] == "Routine")
check("premarital: both carriers -> High", pm(a, b)[0] == "High")
check("premarital: married -> none", pm(row(consent_genetic=True, gene_HBB_CARRIER=True, married=True, age=30)) is None)
check("premarital: no consent -> none", pm(row(consent_genetic=False, gene_HBB_CARRIER=True, married=False, age=24)) is None)
check("premarital: partner without consent not used", pm(a, row(consent_genetic=False, gene_HBB_CARRIER=True))[0] == "Routine")

pts = pd.DataFrame({"age": rng.integers(0, 80, 3000), "gene_FH": rng.random(3000) < 0.3, "gene_BRCA": rng.random(3000) < 0.3})
ns = dict(np=np, pd=pd, rng=rng, patients=pts, n=3000, GENETIC_MARKERS={"FH": .004, "BRCA": .003})
exec(S2["vus_data"], ns)
P = ns["patients"]
check("vus data: status only for markers the person has", all(set(s) == {m for m in ("FH", "BRCA") if r[f"gene_{m}"]} for s, (_, r) in zip(P.gene_status, P.iterrows())))
allst = [v for s in P.gene_status for v in s.values()]
check(f"vus data: about 15% VUS ({allst.count('VUS') / len(allst):.2f})", 0.11 < allst.count("VUS") / len(allst) < 0.19)
check("vus data: nobody under 22 married", not (P.married & (P.age < 22)).any())

# ---- bandit ----
ns = dict(np=np, rng=np.random.default_rng(1))
exec(S2["bandit"], ns)
true_rate = {"reminder_same_channel": 0.10, "switch_channel": 0.35, "community_health_worker": 0.20}
p = row(language="Urdu", area_type="city", age=40)
picks = []
for t in range(600):
    a, key = ns["choose_followup"](p, set(ns["ACTIONS"]))
    ns["learn"](key, a, np.random.default_rng(t).random() < true_rate[a])
    picks.append(a)
late = pd.Series(picks[-200:]).value_counts(normalize=True)
check(f"bandit: learns best action ({late.idxmax()} {late.max():.0%} of last 200)", late.idxmax() == "switch_channel" and late.max() > 0.7)
a, _ = ns["choose_followup"](p, {"reminder_same_channel", "community_health_worker"})
check("bandit: respects allowed actions", a in {"reminder_same_channel", "community_health_worker"})
check("bandit: nothing allowed -> None", ns["choose_followup"](p, set())[0] is None)
k2 = ns["choose_followup"](row(language="Arabic", area_type="remote", age=5), set(ns["ACTIONS"]))[1]
check("bandit: separate learning per group", k2 != key and k2[2] is True)

# ---- sensitivity ----
ns = dict(pd=pd)
exec(S2["sensitivity"], ns)
def run_sim(mode, seed, telehealth_boost, base_attendance, reply_rate):
    r = np.random.default_rng(seed)
    invited = 1000 if mode == "old" else 1000
    hit = 0.05 if mode == "old" else 0.09                  # new way invites people more likely to need care
    att = base_attendance + (telehealth_boost if mode == "new" else 0)
    completed = r.binomial(invited, att * reply_rate)
    return {"completed": completed, "found": r.binomial(completed, hit)}
table, worst = ns["sensitivity"](run_sim)
check(f"sensitivity: 12 combinations ({len(table)})", len(table) == 12)
check(f"sensitivity: worst case is boost 0 ({worst.telehealth_boost}), still positive ({worst.extra_found:.1f})", worst.telehealth_boost == 0.0 and worst.extra_found > 0)

# ---- FHIR ----
ns = {}
exec(S2["fhir"], ns)
card = {"decision": "Clinician decides", "pathway": "Genetic pathway", "check": "genetic_counselling",
        "invite_by": date(2026, 11, 11), "reason": "Genetic marker: BRCA", "guideline": "Not in approved guidelines"}
t = ns["to_fhir_task"](row(id="p1"), card, date(2026, 11, 9))
json.dumps(t)
check("fhir: valid JSON, R4 Task fields, proposal for clinician cases",
      t["resourceType"] == "Task" and t["intent"] == "proposal" and t["status"] == "requested"
      and t["for"]["reference"] == "Patient/p1" and t["restriction"]["period"]["end"] == "2026-11-11")
card["decision"] = "Outreach invite (population priority)"
check("fhir: plan for outreach cases", ns["to_fhir_task"](row(id="p1"), card, date(2026, 11, 9))["intent"] == "plan")
check("fhir: no genetic result in the task text", "BRCA" not in t["description"])

# ---- RTL + business ----
compile(S2["rtl"], "rtl", "exec"); check("rtl: snippet compiles and has Arabic", any("؀" <= c <= "ۿ" for c in S2["rtl"]))
ns = {}
exec(S2["business"], ns)
old = {"messages": 5000, "chw_visits": 0, "completed": 1500, "found_early": 40}
new = {"messages": 9000, "chw_visits": 300, "completed": 2600, "found_early": 75}
cpo, cpn = ns["cost_per_completed"](old, 5000), ns["cost_per_completed"](new, 5000)
check(f"business: total cost adds up ({ns['total_cost'](old, 5000):.0f})", abs(ns["total_cost"](old, 5000) - (500 + 250 + 60000 + 360000)) < 1e-6)
check(f"business: cost per completed old {cpo:.0f} vs new {cpn:.0f}", cpn < cpo)
roi = ns["return_on_investment"](old, new, 5000, 2000)
check(f"business: ROI computed ({roi:.1f})", abs(roi - (35 * 2000) / (400 + 18000)) < 1e-9)
check("business: no extra cost -> inf", ns["return_on_investment"](old, old, 5000, 2000) == float("inf"))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
