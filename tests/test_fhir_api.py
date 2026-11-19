"""
The FHIR R4 API (/api/fhir): patient records from the hospital records database as standard FHIR, only for the
accounts allowed to see them.
    python tests/test_fhir_api.py
"""
import json
import os
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
tmp = tempfile.mkdtemp()
os.environ["PP_USERS_FILE"] = os.path.join(tmp, "users.json")
os.environ["PP_SIGNUP_FILE"] = os.path.join(tmp, "signup_requests.json")
os.environ.pop("PP_PASSWORD", None)
os.environ.pop("PP_DEMO", None)      # production mode: demo tools hidden
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import fhir_import as fi  # noqa: E402
import records  # noqa: E402
import server  # noqa: E402
from api import app as appmod, auth  # noqa: E402

auth.ACCESS_LOG = os.path.join(tmp, "access_log.csv")
auth.LIMITS_FILE = os.path.join(tmp, "limits.json")
ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


def acct(role, pw, **extra):
    s, h = auth.hash_password(pw)
    return {"role": role, "salt": s, "hash": h, **extra}


p = appmod.people()
parent = p[(p.is_child == 1) & p.guardian_id.notna()].guardian_id.value_counts().index[0]
child = p[p.guardian_id == parent].Id.iloc[0]
region = p.region.value_counts().index[0]
inside = p[(p.region == region) & (p.Id != parent)].Id.iloc[0]
outside = p[(p.region != region) & (p.guardian_id != parent) & (p.Id != parent)].Id.iloc[0]
json.dump({"users": {"boss": acct("admin", "admin-pass-123"), "dr_r": acct("doctor", "doctor-pass-123", region=region),
                     "fam": acct("patient", "family-pass-123", person=parent), "plan": acct("manager", "manager-pass-123"),
                     "nurse": acct("outreach", "outreach-pass-123")}}, open(os.environ["PP_USERS_FILE"], "w"))
c = TestClient(server.app)
A, D, F = ("boss", "admin-pass-123"), ("dr_r", "doctor-pass-123"), ("fam", "family-pass-123")
get = lambda path, who: c.get(path, auth=who)  # noqa: E731

# ---- what the server offers
m = get("/api/fhir/metadata", A)
check("metadata is a FHIR CapabilityStatement in application/fhir+json",
      m.status_code == 200 and m.headers["content-type"].startswith("application/fhir+json")
      and m.json()["resourceType"] == "CapabilityStatement" and m.json()["fhirVersion"] == "4.0.1")

# ---- one patient
row = records.read("patient", patient=inside).iloc[0]
pt = get(f"/api/fhir/Patient/{inside}", A).json()
check("Patient: record number, name, birth date, sex, language and nationality from the database",
      pt["resourceType"] == "Patient" and pt["identifier"][0]["value"] == row.MRN and pt["name"][0]["family"] == row.FAMILY_NAME
      and pt["name"][0]["given"][0] == row.GIVEN_NAME and pt["birthDate"] == row.BIRTHDATE
      and pt["gender"] == {"M": "male", "F": "female"}[row.GENDER] and pt["communication"][0]["language"]["text"] == row.language
      and pt["extension"][0]["extension"][0]["valueCodeableConcept"]["text"] == row.nationality)
s = get(f"/api/fhir/Patient?identifier={row.MRN}", A).json()
check("search by hospital record number finds exactly that patient",
      s["resourceType"] == "Bundle" and s["type"] == "searchset" and s["total"] == 1 and s["entry"][0]["resource"]["id"] == inside)

# ---- the whole record, checked by importing it back
ev = get(f"/api/fhir/Patient/{inside}/$everything", A).json()
kinds = pd.Series([e["resource"]["resourceType"] for e in ev["entry"]]).value_counts().to_dict()
obs = records.read("observations", patient=inside)
bp = obs[obs.CODE.isin(["8480-6", "8462-4"])]
want = {"Patient": 1, "Encounter": len(records.read("encounters", ["Id"], patient=inside)),
        "Condition": len(records.read("conditions", ["CODE"], patient=inside)),
        "Observation": int((~obs.CODE.isin(["8480-6", "8462-4"])).sum()) + bp.groupby(["DATE", "ENCOUNTER"], dropna=False).ngroups,
        "Immunization": len(records.read("immunizations", ["CODE"], patient=inside)),
        "Procedure": len(records.read("procedures", ["CODE"], patient=inside)),
        "MedicationRequest": len(records.read("medications", ["CODE"], patient=inside)),
        "AllergyIntolerance": len(records.read("allergies", ["CODE"], patient=inside)),
        "CarePlan": len(records.read("careplans", ["CODE"], patient=inside)),
        "FamilyMemberHistory": len(records.read("family_history", ["CODE"], patient=inside)),
        "Consent": len(records.read("consents", ["PURPOSE"], patient=inside))}
check(f"$everything holds the whole record ({sum(want.values())} resources; blood pressure as panels)",
      {k: v for k, v in want.items() if v} == kinds)
carrier = records.read("genetic_results", ["PATIENT"]).PATIENT.iloc[0]
cev = get(f"/api/fhir/Patient/{carrier}/$everything", A).json()
cons = get(f"/api/fhir/Consent?patient={carrier}", A).json()
check("consent decisions are served (purpose, permit or deny, who gave it); genetic results never are",
      {e["resource"]["category"][0]["coding"][0]["code"] for e in cons["entry"]} == {"contact", "use_genetic"}
      and all(e["resource"]["provision"]["type"] in ("permit", "deny") and e["resource"]["performer"][0]["reference"].startswith("Patient/")
              for e in cons["entry"])
      and not any(e["resource"].get("code", {}).get("coding", [{}])[0].get("code") == "69548-6" for e in cev["entry"])
      and "48018-6" not in json.dumps(cev) and "Genetic variant" not in json.dumps(cev)
      and get(f"/api/fhir/Observation?patient={carrier}&code=69548-6", A).json()["total"] == 0)
ef = os.path.join(tmp, "everything.json")
json.dump(ev, open(ef, "w"))
fi.import_fhir([ef], os.path.join(tmp, "back"))
same = []
for table, cols in {"encounters": ["Id", "START", "CODE"], "conditions": ["START", "STOP", "CODE"],
                    "observations": ["DATE", "CODE", "VALUE"], "immunizations": ["DATE", "CODE"], "procedures": ["START", "CODE"],
                    "family_history": ["DATE", "RELATIONSHIP", "CODE"], "consents": ["DATE", "PURPOSE", "DECISION", "GIVEN_BY"]}.items():
    a = records.read(table, cols, patient=inside).fillna("").astype(str)
    b = pd.read_csv(os.path.join(tmp, "back", f"{table}.csv"), dtype=str).fillna("")[cols]
    if table == "observations":
        for d in (a, b):
            num = pd.to_numeric(d.VALUE, errors="coerce")
            d["VALUE"] = num.round(6).astype(str).where(num.notna(), d.VALUE)
    same.append(a.value_counts().sort_index().equals(b.value_counts().sort_index()))
check("importing the FHIR output back gives the same rows as the database (every table)", all(same))

hb = get(f"/api/fhir/Observation?patient=Patient/{inside}&code=4548-4", A).json()
panel = get(f"/api/fhir/Observation?patient={inside}&code=http://loinc.org|85354-9", A).json()
check("Observation search by code: HbA1c only; the blood pressure panel code returns panels with two readings",
      all(e["resource"]["code"]["coding"][0]["code"] == "4548-4" for e in hb["entry"])
      and all(len(e["resource"]["component"]) == 2 for e in panel["entry"]))

# ---- medicines, allergies and care plans
alive = set(p.Id)                               # the database also holds people who have died: the app opens only living people
many = next(x for x in records.read("medications", ["PATIENT"]).PATIENT.value_counts().index if x in alive)
med_rows = records.read("medications", patient=many)
meds = get(f"/api/fhir/MedicationRequest?patient={many}", A).json()
one_code = med_rows.CODE.iloc[0]
by_code = get(f"/api/fhir/MedicationRequest?patient={many}&code={one_code}", A).json()
check("MedicationRequest: every medicine of the person with an RxNorm code and dates, none of the prices",
      meds["total"] == len(med_rows) and all(e["resource"]["resourceType"] == "MedicationRequest" and e["resource"]["intent"] == "order"
      and e["resource"]["medicationCodeableConcept"]["coding"][0]["system"].endswith("rxnorm")
      and e["resource"]["status"] in ("active", "completed") and e["resource"]["authoredOn"] and e["resource"]["subject"]["reference"] == f"Patient/{many}"
      for e in meds["entry"])
      and not any(w in json.dumps(meds).lower() for w in ("base_cost", "totalcost", "payer", "payer_coverage"))
      and by_code["total"] == int((med_rows.CODE == one_code).sum()))
all_allergies = records.read("allergies")
allergic = next(x for x in all_allergies.PATIENT if x in alive)
al = get(f"/api/fhir/AllergyIntolerance?patient={allergic}", A).json()
al_rows = records.read("allergies", patient=allergic)
reactive = next(x for x in all_allergies[all_allergies.REACTION1.notna()].PATIENT if x in alive)
check("AllergyIntolerance: type, category, status and the reactions with their severity",
      al["total"] == len(al_rows) and all(e["resource"]["clinicalStatus"]["coding"][0]["code"] in ("active", "resolved")
      and e["resource"]["type"] in ("allergy", "intolerance") and e["resource"]["category"][0] in ("food", "medication", "environment", "biologic")
      for e in al["entry"])
      and any("reaction" in e["resource"] and e["resource"]["reaction"][0]["manifestation"][0]["coding"][0]["code"]
              for e in get(f"/api/fhir/AllergyIntolerance?patient={reactive}", A).json()["entry"]))
planned = next(x for x in records.read("careplans", ["PATIENT"]).PATIENT if x in alive)
cp = get(f"/api/fhir/CarePlan?patient={planned}", A).json()
check("CarePlan: title, SNOMED category, intent plan and its period",
      cp["total"] == len(records.read("careplans", patient=planned)) and all(e["resource"]["intent"] == "plan" and e["resource"]["title"]
      and e["resource"]["category"][0]["coding"][0]["system"].endswith("sct") and e["resource"]["period"]["start"] for e in cp["entry"]))
check("the new record types are in the capability statement, and a patient account reads only its own",
      {"MedicationRequest", "AllergyIntolerance", "CarePlan"} <= {r["type"] for r in m.json()["rest"][0]["resource"]}
      and get(f"/api/fhir/MedicationRequest?patient={many}", F).status_code in (200, 404)
      and get(f"/api/fhir/MedicationRequest?patient={inside}", F).status_code == 404)

# ---- the bulk export: NDJSON files, one resource per line, and a manifest that agrees with them
import fhir_bulk_export as bulk  # noqa: E402
out_dir = os.path.join(tmp, "bulk")
counts = bulk.export(out_dir, limit=300)
manifest = json.load(open(os.path.join(out_dir, "manifest.json")))
files = {t: [json.loads(x) for x in open(os.path.join(out_dir, f"{t}.ndjson"), encoding="utf-8")] for t in counts}
exported = {r["id"] for r in files["Patient"]}
subject = lambda r: (r.get("subject") or r.get("patient") or r.get("for") or {}).get("reference", "")  # noqa: E731
check("bulk export: a manifest and one NDJSON file per type; every line a resource of that type, counts agree, ids unique",
      len(files["Patient"]) == 300 and all(o["count"] == len(files[o["type"]]) == counts[o["type"]] for o in manifest["output"])
      and all(r["resourceType"] == t and r.get("id") for t, rs in files.items() for r in rs)
      and all(len({r["id"] for r in rs}) == len(rs) for rs in files.values()))
check("bulk export: the medicines, allergies, care plans, Tasks and Communications are in it, each pointing at an exported patient",
      {"MedicationRequest", "AllergyIntolerance", "CarePlan", "Task", "Communication"} <= set(files)
      and all(subject(r).split("/")[-1] in exported for t in ("MedicationRequest", "AllergyIntolerance", "CarePlan", "Task", "Communication", "Encounter")
              for r in files[t]))
sent = [r for r in files["Communication"] if r["sender"].get("display") == "Clinic"]
replies = [r for r in files["Communication"] if r["sender"].get("reference", "").startswith("Patient/")]
check("Communication: outreach messages go from the clinic to the family with the date, text and how they were sent; replies come from the person",
      sent and replies and all(r["recipient"][0]["reference"].startswith("Patient/") for r in sent)
      and all(r["recipient"][0].get("display") == "Clinic" for r in replies)
      and all(r["payload"][0]["contentString"] and len(r["sent"]) == 10 and r["subject"]["reference"].startswith("Patient/")
              and r["medium"][0]["coding"][0]["code"] in ("SMSWRIT", "WRITTEN", "PHONE", "FACE") for r in files["Communication"]))
check("bulk export: genetic results are left out unless asked for",
      "GeneticObservation" not in files and not any("69548-6" in json.dumps(r) for r in files["Observation"])
      and not os.path.exists(os.path.join(out_dir, "GeneticObservation.ndjson")))

# ---- who may see what
check("a doctor limited to a region reads a patient there, and anyone outside looks like no one",
      get(f"/api/fhir/Patient/{inside}", D).status_code == 200 and get(f"/api/fhir/Patient/{outside}", D).status_code == 404
      and get(f"/api/fhir/Patient/{outside}/$everything", D).json()["resourceType"] == "OperationOutcome")
check("a patient reads their own record and their child's, and nobody else's",
      get(f"/api/fhir/Patient/{parent}", F).status_code == 200 and get(f"/api/fhir/Patient/{child}/$everything", F).status_code == 200
      and get(f"/api/fhir/Patient/{inside}", F).status_code == 404
      and get(f"/api/fhir/Observation?patient={inside}", F).status_code == 404)
check("programme managers and outreach staff cannot use the FHIR API",
      get(f"/api/fhir/Patient/{inside}", ("plan", "manager-pass-123")).status_code == 403
      and get(f"/api/fhir/Patient/{inside}", ("nurse", "outreach-pass-123")).status_code == 403
      and c.get(f"/api/fhir/Patient/{inside}").status_code == 401)
check("unknown resource types and searches without a patient get a FHIR OperationOutcome",
      get("/api/fhir/Device?patient=x", A).status_code == 404 and get("/api/fhir/Observation", A).status_code == 400
      and get("/api/fhir/Observation", A).json()["resourceType"] == "OperationOutcome")
check("production mode: no role has the patient phone simulator and its API is refused, even for the admin",
      not any("phone" in pages for pages in auth.ROLE_PAGES.values()) and get("/api/phone/people", A).status_code == 403
      and "phone" not in get("/api/me", A).json()["pages"])
hd = get(f"/api/fhir/Patient/{inside}", A).headers
page = c.get("/")
check("security headers: API answers are never stored, nothing may frame the app, only its own scripts run",
      hd["cache-control"] == "no-store" and hd["x-content-type-options"] == "nosniff" and hd["x-frame-options"] == "DENY"
      and "script-src 'self'" in page.headers["content-security-policy"] and "frame-ancestors 'none'" in page.headers["content-security-policy"]
      and page.headers["referrer-policy"] == "no-referrer")
check("the security headers still let the map load its background pictures",
      "tile.openstreetmap.org" in page.headers["content-security-policy"].split("img-src")[1].split(";")[0])
big = c.post("/api/login", content=b"x" * 1_100_000, headers={"content-type": "application/json"})
hl = c.get("/api/health")
check("an oversized request is refused, and the health check answers without sign-in and without patient data",
      big.status_code == 413 and hl.status_code == 200 and hl.json()["status"] == "ok"
      and set(hl.json()) == {"status", "checks"} and hl.json()["checks"]["sign_in_required"] is True)
import time  # noqa: E402
t0 = time.perf_counter(); c.post("/api/login", json={"user": "boss", "password": "wrong-password-1"}); known = time.perf_counter() - t0
t0 = time.perf_counter(); c.post("/api/login", json={"user": "nobody-here", "password": "wrong-password-1"}); unknown = time.perf_counter() - t0
check(f"a wrong password takes as long for an unknown user name as for a real one ({known * 1000:.0f} ms vs {unknown * 1000:.0f} ms)",
      unknown > 0.5 * known)
log = open(auth.ACCESS_LOG, encoding="utf-8").read()
check("every FHIR record opened is in the access log (who and which record)",
      f"/api/fhir/Patient/{inside}/$everything" in log and "dr_r" in log and "fam" in log)
check("opening someone's medicines, allergies or care plans is in the access log too",
      all(f"/api/fhir/{t}?patient=" in log for t in ("MedicationRequest", "AllergyIntolerance", "CarePlan")))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
