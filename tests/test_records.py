"""
The hospital records database (records.py) and the FHIR import (scripts/fhir_import.py).
    python tests/test_records.py
Needs the database (python scripts/get_all_data.py --only records) and data/raw for the round trip.
"""
import json
import os
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import pandas as pd  # noqa: E402

import fhir_import as fi  # noqa: E402
import records  # noqa: E402

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


# ---- the database
check("the hospital records database exists", records.exists())
p = records.read("patient")
check("every patient has a unique hospital record number (MRN)", p.MRN.notna().all() and p.MRN.is_unique)
check("every patient has a first and family name without generator digits",
      p.GIVEN_NAME.notna().all() and p.FAMILY_NAME.notna().all()
      and not (p.GIVEN_NAME + p.FAMILY_NAME).str.contains(r"\d").any())
kids = p[p.guardian_id.notna()]
fam = p.set_index("Id").FAMILY_NAME
check("children carry their guardian's family name", (kids.FAMILY_NAME.values == kids.guardian_id.map(fam).values).all())
check("no US identity or address columns are stored",
      not {"SSN", "DRIVERS", "PASSPORT", "ADDRESS", "CITY", "STATE", "ZIP", "FIRST", "LAST"} & set(p.columns))
check("the six record tables are there", all(len(records.read(t, ["PATIENT"]).head(1)) for t in
                                             ("encounters", "conditions", "observations", "immunizations", "procedures")))
check("the simulation's hidden truth is not in the hospital records",
      not any(c.startswith(("hidden_", "diagnosed_")) for c in p.columns))
pf = pd.read_csv(os.path.join(ROOT, "data", "processed", "features", "patient_features.csv"), nrows=5)
check("names and record numbers never become model features", not {"MRN", "GIVEN_NAME", "FAMILY_NAME"} & set(pf.columns))

# ---- FHIR round trip: data/raw -> FHIR bundle -> import -> the same rows
tmp = tempfile.mkdtemp()
bundle, people, n = fi.make_sample(25, fi.RAW, os.path.join(tmp, "fhir"))
rep = fi.import_fhir([bundle], os.path.join(tmp, "imported"))
ids = set(pd.read_csv(os.path.join(tmp, "imported", "patients.csv"), dtype=str).Id)
USED = {"patients": ["Id", "BIRTHDATE", "DEATHDATE", "GENDER"], "encounters": ["Id", "START", "PATIENT", "CODE", "REASONCODE"],
        "conditions": ["START", "STOP", "PATIENT", "CODE"], "observations": ["DATE", "PATIENT", "CODE", "VALUE"],
        "immunizations": ["DATE", "PATIENT", "CODE"], "procedures": ["START", "PATIENT", "CODE"]}
same = []
for table, cols in USED.items():
    a = pd.concat([ch[ch["Id" if table == "patients" else "PATIENT"].isin(ids)] for ch in
                   pd.read_csv(os.path.join(fi.RAW, f"{table}.csv"), dtype=str, chunksize=500_000)]).fillna("")
    b = pd.read_csv(os.path.join(tmp, "imported", f"{table}.csv"), dtype=str).fillna("")
    if table == "observations":
        for d in (a, b):
            num = pd.to_numeric(d.VALUE, errors="coerce")
            d["VALUE"] = num.round(6).astype(str).where(num.notna(), d.VALUE)
    same.append(a[cols].value_counts().sort_index().equals(b[cols].value_counts().sort_index()))
check(f"FHIR round trip ({people} people, {n:,} resources): all six tables come back identical", all(same))

# ---- a hospital-style patient: own MRN, name, nationality, language and Emirates ID; panels and other resources
hospital = {"resourceType": "Bundle", "type": "searchset", "entry": [
    {"resource": {"resourceType": "Patient", "id": "h1", "gender": "female", "birthDate": "1985-03-12",
                  "identifier": [{"type": {"coding": [{"code": "MR"}]}, "value": "SKMC-4471902"},
                                 {"system": "urn:oid:emirates-id", "value": "784-1985-7654321-9"}],
                  "name": [{"family": "Al Dhaheri", "given": ["Mariam"]}],
                  "extension": [{"url": "http://hl7.org/fhir/StructureDefinition/patient-nationality",
                                 "extension": [{"url": "code", "valueCodeableConcept": {"coding": [{"code": "ARE"}]}}]}],
                  "communication": [{"language": {"coding": [{"code": "ar"}]}, "preferred": True}]}},
    {"resource": {"resourceType": "Observation", "id": "o1", "subject": {"reference": "urn:uuid:h1"},
                  "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9"}]}, "effectiveDateTime": "2026-06-01",
                  "component": [{"code": {"coding": [{"code": "8480-6"}]}, "valueQuantity": {"value": 148}},
                                {"code": {"coding": [{"code": "8462-4"}]}, "valueQuantity": {"value": 92}}]}},
    {"resource": {"resourceType": "Encounter", "id": "e1", "subject": {"reference": "Patient/h1/_history/3"},
                  "class": {"code": "EMER"}, "period": {"start": "2026-05-01T10:00:00Z"}}},
    {"resource": {"resourceType": "Condition", "id": "c1", "subject": {"reference": "Patient/nobody"},
                  "code": {"coding": [{"code": "44054006"}]}}},
    {"resource": {"resourceType": "MedicationRequest", "id": "m1"}}]}
hf = os.path.join(tmp, "hospital.json")
json.dump(hospital, open(hf, "w"))
rep = fi.import_fhir([hf], os.path.join(tmp, "hosp"))
hp = pd.read_csv(os.path.join(tmp, "hosp", "patients.csv"), dtype=str).iloc[0]
check("a hospital's own record number, name, nationality group and language are kept",
      hp.MRN == "SKMC-4471902" and hp.FIRST == "Mariam" and hp.LAST == "Al Dhaheri" and hp.NATIONALITY == "Emirati"
      and hp.LANGUAGE == "Arabic")
check("only the last 4 digits of an Emirates ID are kept",
      hp.EMIRATES_ID_LAST4 == "3219" and "7654321" not in open(os.path.join(tmp, "hosp", "patients.csv")).read())
ho = pd.read_csv(os.path.join(tmp, "hosp", "observations.csv"), dtype=str)
check("a blood pressure panel becomes a systolic and a diastolic reading",
      set(zip(ho.CODE, ho.VALUE)) == {("8480-6", "148"), ("8462-4", "92")})
he = pd.read_csv(os.path.join(tmp, "hosp", "encounters.csv"), dtype=str)
check("every reference form is understood and an EMER visit is an emergency", he.PATIENT.tolist() == ["h1"]
      and he.ENCOUNTERCLASS.tolist() == ["emergency"])
check("records of people not in the import and unsupported resources are skipped and counted",
      rep["skipped"].get("conditions for a patient not in the import") == 1 and rep["skipped"].get("MedicationRequest") == 1)

# ---- family history, genetic results and consent are records too (scripts/clinical_records.py)
import clinical_records as cr  # noqa: E402

fam, gen, con = (records.read(t).fillna("") for t in ("family_history", "genetic_results", "consents"))
alive = p[p.DEATHDATE.isna()]
both = con.groupby("PATIENT").PURPOSE.agg(set)
check("the database holds family history, genetic results and a consent decision per purpose for every living patient",
      len(fam) and len(gen) and set(both.index) == set(alive.Id) and (both == {"contact", "use_genetic"}).all())
kc = con[con.PATIENT.isin(kids.Id)]
check("a child's consent decisions are given by the guardian",
      (kc.GIVEN_BY.values == kc.PATIENT.map(kids.set_index("Id").guardian_id).values).all())
full = pd.read_csv(os.path.join(ROOT, "data", "processed", "features", "patient_features.csv"), dtype={"Id": str}).set_index("Id")
permit = {pu: con[con.PURPOSE == pu].set_index("PATIENT").DECISION.eq("permit") for pu in ("contact", "use_genetic")}
check("the feature table says what the records say: consent, family history of diabetes, genetic markers",
      (full.consent == permit["contact"].reindex(full.index)).all()
      and (full.consent_genetic == permit["use_genetic"].reindex(full.index)).all()
      and set(full.index[full.family_history_recorded]) == set(fam.PATIENT)
      and set(full.index[full.family_hist_diabetes]) == set(fam[fam.CODE == "73211009"].PATIENT)
      and all(set(full.index[full[f"gene_{m}"]]) == set(gen[gen.MARKER == m].PATIENT) for m in cr.GENETIC_MARKERS))

# the same people's three tables survive the FHIR round trip (the sample bundle carries them)
adj = os.path.join(ROOT, "data", "processed", "uae_synthea")
same = []
for table, cols in cr.COLUMNS.items():
    a = pd.read_csv(os.path.join(adj, f"{table}.csv"), dtype=str).fillna("")
    b = pd.read_csv(os.path.join(tmp, "imported", f"{table}.csv"), dtype=str).fillna("")
    a = a[a.PATIENT.isin(ids)]
    same.append(len(a) == len(b) and a[cols].value_counts().sort_index().equals(b[cols].value_counts().sort_index()))
check("FHIR round trip: family history, genetic results and consent decisions come back identical", all(same))

# a hospital's own way of writing them: ICD-10 family history, a gene symbol, words for the significance
obs = lambda i, who, gene, sig, **extra: {"resource": {  # noqa: E731
    "resourceType": "Observation", "id": i, "subject": {"reference": f"Patient/{who}"}, "effectiveDateTime": "2025-02-03T09:00:00Z",
    "code": {"coding": [{"system": "http://loinc.org", "code": "69548-6"}]},
    "component": [{"code": {"coding": [{"code": "48018-6"}]}, "valueCodeableConcept": {"coding": [{"code": "HGNC:1", "display": gene}]}},
                  {"code": {"coding": [{"code": "53037-8"}]}, "valueCodeableConcept": {"text": sig}}], **extra}}
pat = lambda i: {"resource": {"resourceType": "Patient", "id": i, "gender": "female", "birthDate": "1980-01-01"}}  # noqa: E731
hosp2 = {"resourceType": "Bundle", "type": "collection", "entry": [
    pat("a1"), pat("a2"), pat("a3"),
    {"resource": {"resourceType": "FamilyMemberHistory", "id": "f1", "status": "completed", "patient": {"reference": "Patient/a1"},
                  "date": "2025-01-10", "relationship": {"coding": [{"code": "FTH"}]},
                  "condition": [{"code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10", "code": "E11.9"}]}}]}},
    {"resource": {"resourceType": "FamilyMemberHistory", "id": "f2", "status": "completed", "patient": {"reference": "Patient/a2"},
                  "date": "2025-01-10", "relationship": {"coding": [{"code": "MTH"}]},
                  "condition": [{"code": {"coding": [{"system": "http://snomed.info/sct", "code": "38341003"}]}}]}},
    {"resource": {"resourceType": "FamilyMemberHistory", "id": "f3", "status": "health-unknown", "patient": {"reference": "Patient/a3"}}},
    obs("g1", "a1", "BRCA1", "Likely pathogenic"), obs("g2", "a1", "BRCA2", "Uncertain significance"),
    obs("g3", "a2", "MLH1", "Benign"), obs("g4", "a2", "TTN", "Pathogenic"),
    obs("g5", "a3", "LDLR", "Pathogenic", valueCodeableConcept={"coding": [{"code": "LA9634-2", "display": "Absent"}]}),
    {"resource": {"resourceType": "Consent", "id": "c1", "status": "active", "patient": {"reference": "Patient/a1"},
                  "dateTime": "2025-01-10", "category": [{"text": "Use of genomic data for care planning"}], "provision": {"type": "permit"}}},
    {"resource": {"resourceType": "Consent", "id": "c2", "status": "active", "patient": {"reference": "Patient/a1"},
                  "dateTime": "2025-06-01", "category": [{"text": "Outreach and reminders"}], "provision": {"type": "deny"}}},
    {"resource": {"resourceType": "Consent", "id": "c3", "status": "inactive", "patient": {"reference": "Patient/a2"},
                  "dateTime": "2025-06-01", "category": [{"text": "Genetic data"}], "provision": {"type": "permit"}}},
    {"resource": {"resourceType": "Consent", "id": "c4", "status": "active", "patient": {"reference": "Patient/a2"},
                  "dateTime": "2025-06-01", "category": [{"text": "Research registry"}], "provision": {"type": "permit"}}}]}
h2 = os.path.join(tmp, "hospital2.json")
json.dump(hosp2, open(h2, "w"))
rep2 = fi.import_fhir([h2], os.path.join(tmp, "hosp2"))
t3 = [pd.read_csv(os.path.join(tmp, "hosp2", f"{t}.csv"), dtype=str).fillna("") for t in cr.COLUMNS]
people3 = pd.DataFrame({"Id": ["a1", "a2", "a3"], "guardian_id": [None, None, None]})
blank = pd.DataFrame({"Id": people3.Id})
d = cr.derive(blank, people3, *t3).set_index("Id")
check("a hospital's family history counts when it is diabetes (ICD-10 E11.9), not for another condition, not when unknown",
      d.family_hist_diabetes.tolist() == [True, False, False] and d.family_history_recorded.tolist() == [True, True, False])
check("a gene symbol reaches its rule with the most serious finding; benign, absent and unknown genes change nothing",
      json.loads(d.gene_status["a1"]) == {"BRCA": "likely_pathogenic"} and d.gene_BRCA.tolist() == [True, False, False]
      and not d.gene_LYNCH.any() and not d.gene_FH.any() and d.genetic_risk_flag.tolist() == [True, False, False]
      and len(pd.read_csv(os.path.join(tmp, "hosp2", "observations.csv"))) == 0)
check("consent: a recorded no stops contact, genetic use needs a recorded yes, an inactive form decides nothing",
      d.consent.tolist() == [False, True, True] and d.consent_genetic.tolist() == [True, False, False]
      and rep2["skipped"].get("Consent inactive") == 1 and rep2["skipped"].get("Consent for a purpose the project does not use") == 1)
kid = pd.DataFrame({"Id": ["k1", "g1"], "guardian_id": ["g1", None]})
kc2 = pd.DataFrame([["2025-01-01", "k1", "use_genetic", "permit", "k1"], ["2025-01-02", "g1", "contact", "deny", "g1"],
                    ["2025-03-01", "k1", "contact", "deny", "g1"], ["2025-02-01", "k1", "contact", "permit", "g1"]],
                   columns=cr.COLUMNS["consents"])
empty = [pd.DataFrame(columns=cr.COLUMNS[t]) for t in ("family_history", "genetic_results")]
dk = cr.derive(pd.DataFrame({"Id": kid.Id}), kid, *empty, kc2).set_index("Id")
check("for a child only the guardian's decision counts, and the newest decision wins",
      not dk.consent["k1"] and not dk.consent_genetic["k1"] and dk.consent_given_by["k1"] == "g1")

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
