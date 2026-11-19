"""
Family history, genetic results and consent as hospital records.

The six record tables (visits, diagnoses, results, vaccines, procedures) do not hold these, so they have three tables of
their own, next to the others and in the hospital records database (records.py):

    family_history    one row per relative's condition a person reported (FHIR FamilyMemberHistory); a row without a
                      code means the history was taken and nothing relevant was reported
    genetic_results   one row per variant found (FHIR Observation, LOINC 69548-6): gene, the project's marker group
                      and the clinical significance (pathogenic, likely_pathogenic, VUS ...)
    consents          one row per consent decision (FHIR Consent): purpose (contact, use_genetic), permit or deny,
                      and who gave it (for a child, the guardian)

Where the rows come from:
  * a hospital's FHIR export (scripts/fhir_import.py): the rows are the hospital's. Nothing is generated; a person with
    no consent record gets the default in config/clinical_rules.json "consent_when_not_recorded".
  * the generated test patients: the rows are written here, from the same draws the feature step used before, so the
    test population and every result stay exactly the same.

    python scripts/clinical_records.py        (also runs at the end of the "adjust" step of get_all_data.py)

The feature step (scripts/preprocess.py) reads the three tables back with read_all() and derive(): what the care-gap,
risk and outreach agents know about family history, genetics and consent is what the records say.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import records  # noqa: E402
import utils  # noqa: E402

S, CR = utils.config("synthetic_data"), utils.config("clinical_rules")
SEED = S["features_seed"]
ADULT = CR["adult_age"]
GENETIC_MARKERS = S["genetic_markers"]
STATUS = (list(S["variant_status_mix"]), list(S["variant_status_mix"].values()))
CONSENT_CONTACT, CONSENT_GENETIC = S["consent_contact"], S["consent_genetic"]
DEV_CONCERN, BEHAVIOUR_CONCERN, ASSESSED = (S["child_concerns"][k] for k in ("development_under_6", "behaviour", "assessed"))

COLUMNS = {"family_history": ["DATE", "PATIENT", "RELATIONSHIP", "SYSTEM", "CODE", "DESCRIPTION"],
           "genetic_results": ["DATE", "PATIENT", "GENE", "MARKER", "SIGNIFICANCE", "DESCRIPTION"],
           "consents": ["DATE", "PATIENT", "PURPOSE", "DECISION", "GIVEN_BY"]}
MARKER_GENES = CR["genetic_marker_genes"]                       # marker group -> the genes that count for it
GENE_MARKER = {g: m for m, genes in MARKER_GENES.items() for g in genes}
FOUND = ["pathogenic", "likely_pathogenic", "VUS"]              # most serious first; benign findings are not markers
FAMILY_DIABETES = CR["family_history_diabetes_codes"]
PURPOSE_COLUMN = {"contact": "consent", "use_genetic": "consent_genetic"}
NO_HISTORY = "No relevant family history reported"
MARKER_NAME = {"FH": "Familial hypercholesterolaemia", "BRCA": "Hereditary breast and ovarian cancer",
               "LYNCH": "Lynch syndrome", "HBB_CARRIER": "Beta-globin (thalassaemia / sickle cell) carrier",
               "MODY": "Maturity-onset diabetes of the young"}


def marker_of(gene_or_marker):
    """The project's marker group for a gene symbol ('BRCA1' -> 'BRCA') or a marker name; '' when there is no rule."""
    v = str(gene_or_marker or "").strip().upper()
    return v if v in MARKER_GENES else GENE_MARKER.get(v, "")


# ------------------------------------------------------------------ test patients: the draws (guide Steps 1.2, 1.2b, 3.6)
def draw(pts, truth):
    """Every attribute the generated test patients need beyond the six record tables, one row per person. Consent,
    family history and genetic markers are then written out as records (test_records); child development and
    behaviour concerns, the polygenic score and marital status are used by the feature step as they are."""
    rng = np.random.default_rng(SEED)
    n = len(pts)
    p = pts[["Id", "age", "guardian_id"]].copy()
    adult = p.guardian_id.isna()
    # consent: an adult decides for themselves; a guardian decides for the child
    p["consent"] = rng.random(n) < CONSENT_CONTACT
    p["consent_genetic"] = rng.random(n) < CONSENT_GENETIC
    g = p.set_index("Id")
    for col in ("consent", "consent_genetic"):
        p.loc[~adult, col] = p.loc[~adult, "guardian_id"].map(g[col]).values
    p["consent_given_by"] = np.where(adult, p.Id, p.guardian_id)
    # family history: more likely when the person truly has diabetes, diagnosed or not, as in real life (it is a
    # screening criterion because it signals undiagnosed cases). Strength checked against real data: 45% vs 15% is an
    # odds ratio of about 4.6; NHANES 1999-2002 found diabetes in 14.3% with a first-degree relative with diabetes
    # vs 3.2% without, an odds ratio of about 5 (CDC, Preventing Chronic Disease 2005;2(2):A19).
    t = truth.set_index("PATIENT")
    true_dm = p.Id.map(t.diagnosed_diabetes | t.hidden_diabetes).fillna(False).astype(bool).values
    fh = S["family_history_diabetes"]
    p["family_history_recorded"] = rng.random(n) < S["family_history_recorded"]
    p["family_hist_diabetes"] = p.family_history_recorded & (rng.random(n) < np.where(true_dm, fh["with_diabetes"], fh["without_diabetes"]))
    # child development and behaviour concerns (Synthea has none)
    child = p.age < ADULT
    p["dev_concern"] = child & (p.age < CR["warning_missed_check_under_age"]) & (rng.random(n) < DEV_CONCERN)
    p["behaviour_concern"] = child & (rng.random(n) < BEHAVIOUR_CONCERN)
    p["dev_assessed"] = p.dev_concern & (rng.random(n) < ASSESSED)
    p["behaviour_assessed"] = p.behaviour_concern & (rng.random(n) < ASSESSED)
    # genetic markers, a child of a carrier inherits with 50% chance
    for m, rate in GENETIC_MARKERS.items():
        p[f"gene_{m}"] = rng.random(n) < rate
    gm = p.set_index("Id")
    for m in GENETIC_MARKERS:
        col = f"gene_{m}"
        par = p.guardian_id.map(gm[col]).eq(True)
        p.loc[~adult & par & (rng.random(n) < S["genetic_inheritance"]), col] = True
    statuses = []
    for _, r in p.iterrows():
        statuses.append({m: str(rng.choice(STATUS[0], p=STATUS[1])) for m in GENETIC_MARKERS if r[f"gene_{m}"]})
    p["gene_status"] = [json.dumps(s) for s in statuses]
    p["genetic_risk_flag"] = [any(v in CR["actionable_variants"] for v in s.values()) for s in statuses]
    p["has_vus_only"] = [bool(s) and all(v == "VUS" for v in s.values()) for s in statuses]
    # polygenic score: mean 0, SD 1, half passed from guardian to child
    p["prs"] = rng.normal(0, 1, n)
    prs_g = p.set_index("Id").prs
    k = ~adult
    h = S["polygenic_heritability"]
    p.loc[k, "prs"] = h * p.loc[k, "guardian_id"].map(prs_g).values + rng.normal(0, np.sqrt(1 - h * h), k.sum())
    p["married"] = (p.age >= S["married"]["from_age"]) & (rng.random(n) < S["married"]["share"])
    return p.drop(columns=["age", "guardian_id"])


def test_records(d, alive, date):
    """The draws as the three record tables, for the living test patients, dated the day the data was made."""
    d = d[alive.values].reset_index(drop=True)
    consents = pd.concat([pd.DataFrame({"DATE": date, "PATIENT": d.Id, "PURPOSE": purpose,
                                        "DECISION": np.where(d[col].astype(bool), "permit", "deny"), "GIVEN_BY": d.consent_given_by})
                          for purpose, col in PURPOSE_COLUMN.items()], ignore_index=True)
    asked = d[d.family_history_recorded.astype(bool)]
    dm = asked.family_hist_diabetes.astype(bool).values
    family = pd.DataFrame({"DATE": date, "PATIENT": asked.Id.values, "RELATIONSHIP": np.where(dm, "PRN", "FAMMEMB"),
                           "SYSTEM": np.where(dm, "SNOMED-CT", ""), "CODE": np.where(dm, "73211009", ""),
                           "DESCRIPTION": np.where(dm, "Diabetes mellitus", NO_HISTORY)})
    rows = []
    for pid, status in zip(d.Id, d.gene_status.map(json.loads)):
        for m, s in status.items():
            rows.append({"DATE": date, "PATIENT": pid, "GENE": MARKER_GENES[m][0], "MARKER": m, "SIGNIFICANCE": s,
                         "DESCRIPTION": MARKER_NAME.get(m, m)})
    return {"family_history": family[COLUMNS["family_history"]],
            "genetic_results": pd.DataFrame(rows, columns=COLUMNS["genetic_results"]),
            "consents": consents[COLUMNS["consents"]]}


def write(raw, out, log=print):
    """Put the three tables next to the adjusted records in `out`. From a FHIR import (`raw` holds
    import_report.json) they are the hospital's rows, or empty: nothing is made up. Otherwise they are written for
    the generated test patients."""
    pts = pd.read_csv(os.path.join(out, "patients.csv"), dtype={"Id": str, "guardian_id": str})
    if os.path.exists(os.path.join(raw, "import_report.json")):
        for name, cols in COLUMNS.items():
            f = os.path.join(raw, f"{name}.csv")
            df = pd.read_csv(f, dtype=str).fillna("") if os.path.exists(f) else pd.DataFrame(columns=cols)
            df = df.reindex(columns=cols).fillna("")
            df[df.PATIENT.isin(pts.Id)].to_csv(os.path.join(out, f"{name}.csv"), index=False)
            log(f"{name}: {len(df):,} rows from the import")
        return
    truth = pd.read_csv(os.path.join(out, "truth_hidden.csv"), dtype={"PATIENT": str})
    tables = test_records(draw(pts, truth), pts.DEATHDATE.isna(), utils.run_date().date().isoformat())
    for name, df in tables.items():
        df.to_csv(os.path.join(out, f"{name}.csv"), index=False)
        log(f"{name}: {len(df):,} rows for the test patients")


# ------------------------------------------------------------------ the feature step reads the records back
def read_all(src):
    """The three tables as text: from the hospital records database when it holds them, else the CSV copies in
    `src`, else empty."""
    out = []
    for name, cols in COLUMNS.items():
        f = os.path.join(src, f"{name}.csv")
        if records.exists() and records.has(name):
            df = records.read(name)
        elif os.path.exists(f):
            df = pd.read_csv(f, dtype=str)
        else:
            df = pd.DataFrame(columns=cols)
        out.append(df.reindex(columns=cols).fillna("").astype(str))
    return out


def is_family_diabetes(code):
    c = str(code or "").strip().upper()
    return c in FAMILY_DIABETES["codes"] or any(c.startswith(x) for x in FAMILY_DIABETES["icd10_prefixes"])


def derive(sim, pts, family, genetic, consents):
    """Consent, family history and genetic columns of the feature table, taken from the records (the columns of
    the same name in `sim` are replaced; its other columns are kept).
      consent: the newest decision per purpose; for a child only a decision given by the guardian counts; with no
               record, the default in config/clinical_rules.json "consent_when_not_recorded"
      family history: recorded = any row; diabetes = a row with a diabetes code
      genetics: per marker group the most serious finding among pathogenic, likely pathogenic and uncertain"""
    assert (sim.Id.values == pts.Id.values).all()
    p = sim.copy()
    ids = p.Id
    guardian = pd.Series(pts.guardian_id.values, index=pts.Id.values)
    # ---- consent
    default = CR["consent_when_not_recorded"]
    c = consents[consents.DECISION.isin(["permit", "deny"]) & consents.PATIENT.isin(guardian.index)].copy()
    g = c.PATIENT.map(guardian)
    c = c[g.isna().values | (c.GIVEN_BY.values == g.values)]
    c = c.assign(_order=np.arange(len(c))).sort_values(["DATE", "_order"], kind="stable")
    last = c.drop_duplicates(["PATIENT", "PURPOSE"], keep="last")
    for purpose, col in PURPOSE_COLUMN.items():
        permit = last[last.PURPOSE == purpose].set_index("PATIENT").DECISION.eq("permit")
        got = ids.map(permit)
        p[col] = got.where(got.notna(), bool(default[purpose])).astype(bool).values
    by = ids.map(last[last.PURPOSE == "contact"].set_index("PATIENT").GIVEN_BY)
    own = np.where(guardian.reindex(ids).isna().values, ids.values, guardian.reindex(ids).values)
    p["consent_given_by"] = np.where(by.notna().values, by.values, own)
    # ---- family history
    p["family_history_recorded"] = ids.isin(family.PATIENT).values
    p["family_hist_diabetes"] = ids.isin(family[family.CODE.map(is_family_diabetes).astype(bool)].PATIENT).values
    # ---- genetic results
    found = genetic[genetic.SIGNIFICANCE.isin(FOUND)].copy()
    found["marker"] = [marker_of(m) or marker_of(gene) for m, gene in zip(found.MARKER, found.GENE)]
    found = found[found.marker != ""]
    found["rank"] = found.SIGNIFICANCE.map(FOUND.index)
    worst = found.sort_values("rank", kind="stable").drop_duplicates(["PATIENT", "marker"])
    per = {}
    for pid, m, s in zip(worst.PATIENT, worst.marker, worst.SIGNIFICANCE):
        per.setdefault(pid, {})[m] = s
    statuses = [{m: per[i][m] for m in GENETIC_MARKERS if m in per[i]} if i in per else {} for i in ids]
    for m in GENETIC_MARKERS:
        p[f"gene_{m}"] = [m in s for s in statuses]
    p["gene_status"] = [json.dumps(s) for s in statuses]
    p["genetic_risk_flag"] = [any(v in CR["actionable_variants"] for v in s.values()) for s in statuses]
    p["has_vus_only"] = [bool(s) and all(v == "VUS" for v in s.values()) for s in statuses]
    return p


if __name__ == "__main__":
    raw = os.path.join(ROOT, os.environ["PP_RAW_DIR"]) if os.environ.get("PP_RAW_DIR") else os.path.join(ROOT, "data", "raw")
    write(raw, utils.ADJUSTED, log=lambda m: print(f"[clinical] {m}", flush=True))
