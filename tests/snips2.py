# Second round of code added to the Implementation Guide (v3). Executed by test_snips2.py.
S2 = {}

S2["strata"] = '''java -jar synthea-with-dependencies.jar -s 1 -p 1900 -g M -a 18-44 --exporter.baseDirectory=./out_m_18_44 --exporter.csv.export=true
java -jar synthea-with-dependencies.jar -s 2 -p 850  -g F -a 18-44 --exporter.baseDirectory=./out_f_18_44 --exporter.csv.export=true
java -jar synthea-with-dependencies.jar -s 3 -p 1050 -a 0-17  --exporter.baseDirectory=./out_child --exporter.csv.export=true
java -jar synthea-with-dependencies.jar -s 4 -p 1050 -a 45-64 --exporter.baseDirectory=./out_45_64 --exporter.csv.export=true
java -jar synthea-with-dependencies.jar -s 5 -p 150  -a 65-90 --exporter.baseDirectory=./out_65plus --exporter.csv.export=true'''

S2["weights"] = '''TARGET = {   # share of the population by age band and sex
    # EXAMPLE VALUES ONLY: replace with the latest Statistics Centre Abu Dhabi figures and cite them
    ("0-17", "M"): .11, ("0-17", "F"): .10, ("18-44", "M"): .38, ("18-44", "F"): .17,
    ("45-64", "M"): .14, ("45-64", "F"): .07, ("65+", "M"): .02, ("65+", "F"): .01,
}
patients["band"] = pd.cut(patients.age, [-1, 17, 44, 64, 200], labels=["0-17", "18-44", "45-64", "65+"])
share = patients.groupby(["band", "GENDER"], observed=True).size() / len(patients)
patients["weight"] = [TARGET[(b, g)] / share[(b, g)] for b, g in zip(patients.band, patients.GENDER)]
def weighted_rate(d, col):
    """Population estimate: each person counts as many people as they represent in Abu Dhabi."""
    return float((d[col] * d.weight).sum() / d.weight.sum())'''

S2["access"] = '''def access_barrier(p):
    """0 to 1: how hard it is for this person to reach care today. For a child, pass the guardian's row."""
    score = 0.25 if p.language not in ("Arabic", "English") else 0.0
    score += 0.20 if p.insurance == "Basic" else 0.0
    score += 0.20 if p.work_shift == "day" else 0.0           # cannot attend in normal clinic hours
    score += 0.15 if p.phone_type != "smartphone" else 0.0
    score += 0.20 * min(p.travel_min / 90, 1)
    return round(score, 2)
priority = 0.5 * risk + 0.2 * min(len(gaps) / 5, 1) + 0.3 * access_barrier(recipient(p, people))'''

S2["prs"] = '''patients["prs"] = rng.normal(0, 1, n)                        # simulated polygenic score, mean 0, SD 1
kids = patients.guardian_id.notna()
parent_prs = patients.guardian_id.map(patients.set_index("id").prs)
patients.loc[kids, "prs"] = 0.5 * parent_prs[kids] + rng.normal(0, np.sqrt(0.75), kids.sum())
PRS_BETA = 0.3      # assumption: change in log-odds per 1 SD of score; check against published studies
def combined_risk(clinical_risk, p):
    """Adds the genetic score to the clinical risk, only with genetic consent."""
    if not p.consent_genetic or np.isnan(p.prs):
        return clinical_risk, None
    c = min(max(clinical_risk, 1e-4), 1 - 1e-4)
    r = 1 / (1 + np.exp(-(np.log(c / (1 - c)) + PRS_BETA * p.prs)))
    return r, ("Genetic score in the top 10%" if p.prs >= 1.28 else None)'''

S2["vus"] = '''ACTIONABLE = {"pathogenic", "likely_pathogenic"}
def genetic_action(p, marker):
    status = p.gene_status.get(marker)       # "pathogenic", "likely_pathogenic" or "VUS"
    if status in ACTIONABLE:
        return "Use in screening plan"
    if status == "VUS":                      # variant of uncertain significance
        return "Do not use; list for the genetic counsellor only"
    return None
def premarital_flag(p, partner=None):
    if not p.consent_genetic or not p.gene_HBB_CARRIER or p.married or not 18 <= p.age <= 40:
        return None
    if partner is not None and partner.consent_genetic and partner.gene_HBB_CARRIER:
        return ("High", "Both partners carry a blood disorder trait: genetic counselling before marriage")
    return ("Routine", "Premarital screening and carrier counselling")'''

S2["vus_data"] = '''patients["married"] = (patients.age >= 22) & (rng.random(n) < 0.7)          # simulated
patients["gene_status"] = [
    {m: rng.choice(["pathogenic", "likely_pathogenic", "VUS"], p=[.60, .25, .15])
     for m in GENETIC_MARKERS if row[f"gene_{m}"]}
    for _, row in patients.iterrows()]'''

S2["bandit"] = '''from collections import defaultdict
ACTIONS = ["reminder_same_channel", "switch_channel", "community_health_worker"]
wins = defaultdict(lambda: np.ones(len(ACTIONS)))      # every group starts with no preference
losses = defaultdict(lambda: np.ones(len(ACTIONS)))
def choose_followup(p, allowed):
    """Pick the follow-up that has worked best for similar people, while still trying the others."""
    key = (p.language, p.area_type, p.age < 18)
    draws = rng.beta(wins[key], losses[key])
    for i in np.argsort(-draws):
        if ACTIONS[i] in allowed:            # never break consent or the person's own channel choice
            return ACTIONS[i], key
    return None, key
def learn(key, action, booked):
    (wins if booked else losses)[key][ACTIONS.index(action)] += 1'''

S2["sensitivity"] = '''import itertools
GRID = {"telehealth_boost": [0.0, 0.05, 0.15], "base_attendance": [0.60, 0.75], "reply_rate": [0.3, 0.5]}
def sensitivity(run_simulation, grid=GRID, seeds=range(5)):
    """Re-run old and new ways under every combination of assumptions."""
    rows = []
    for combo in itertools.product(*grid.values()):
        settings = dict(zip(grid, combo))
        for s in seeds:
            old = run_simulation(mode="old", seed=s, **settings)
            new = run_simulation(mode="new", seed=s, **settings)
            rows.append({**settings, "extra_found": new["found"] - old["found"],
                         "extra_completed": new["completed"] - old["completed"]})
    t = pd.DataFrame(rows).groupby(list(grid)).mean().reset_index()
    return t, t.loc[t.extra_found.idxmin()]          # full table and the worst case'''

S2["fhir"] = '''def to_fhir_task(p, card, today):
    """Turn a pathway card into a FHIR Task that a clinic system can show in its work list."""
    return {
        "resourceType": "Task",
        "status": "requested",
        "intent": "proposal" if card["decision"] == "Clinician decides" else "plan",
        "description": f'{card["pathway"]}: {card["check"]}',
        "for": {"reference": f"Patient/{p.id}"},
        "authoredOn": today.isoformat(),
        "restriction": {"period": {"end": card["invite_by"].isoformat()}},
        "reasonCode": {"text": card["reason"]},
        "note": [{"text": f'Guideline: {card["guideline"]}. Outreach priority, not a diagnosis.'}],
    }'''

S2["rtl"] = '''lang = st.sidebar.radio("Language / اللغة", ["English", "العربية"])
if lang == "العربية":
    st.markdown("<style>.stMarkdown, .stText {direction: rtl; text-align: right;}</style>",
                unsafe_allow_html=True)
    st.markdown("**قائمة أولويات للتواصل. ليست تشخيصاً. الطبيب هو من يقرر.**")'''

S2["business"] = '''COST = {   # AED per year. ALL VALUES ARE PLACEHOLDERS: replace with real quotes and cite them
    "per_message": 0.10, "extra_per_person": 0.05, "hosting": 60000,
    "outreach_staff": 2 * 180000, "per_chw_visit": 60,
}
def total_cost(run, people):
    return (run["messages"] * COST["per_message"] + people * COST["extra_per_person"]
            + COST["hosting"] + COST["outreach_staff"] + run["chw_visits"] * COST["per_chw_visit"])
def cost_per_completed(run, people):
    return total_cost(run, people) / max(run["completed"], 1)
def return_on_investment(old, new, people, saving_per_early_case):
    """Money saved by finding cases early, for each extra dirham spent. Above 1 means it pays for itself."""
    extra_cost = total_cost(new, people) - total_cost(old, people)
    saving = (new["found_early"] - old["found_early"]) * saving_per_early_case
    return saving / extra_cost if extra_cost > 0 else float("inf")'''
