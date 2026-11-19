"""
Export the whole simulated cohort as FHIR R4 files, in the NDJSON layout hospital systems use for bulk data (one
resource per line, one file per resource type) with a manifest listing the files.

    python scripts/fhir_bulk_export.py                    everything, into outputs/fhir_export/
    python scripts/fhir_bulk_export.py --patients 200     only the first 200 people (a quick sample)
    python scripts/fhir_bulk_export.py --out some/folder  another folder
    python scripts/fhir_bulk_export.py --with-genetic     also the genetic results (never part of the API)

Records come from the hospital records database (records.py): Patient, Encounter, Condition, Observation,
Immunization, Procedure, MedicationRequest, AllergyIntolerance, CarePlan, FamilyMemberHistory and Consent.
After a 60-day run (orchestrator.py) the work lists (Task) and the outreach messages (Communication) are added from
outputs/new. Every resource is simulated: no real person is in this system.
"""
import argparse
import json
import os
import sys
import time

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import fhir  # noqa: E402
import records  # noqa: E402

BATCH = 250                                   # people read from the database at a time (SQLite allows 999 parameters)
# resource type: (table, how one row becomes a resource)
PER_ROW = {
    "Encounter": ("encounters", lambda r, pid, i: fhir.encounter(r)),
    "Condition": ("conditions", lambda r, pid, i: fhir.condition(r, f"cond-{pid}-{i}")),
    "Immunization": ("immunizations", lambda r, pid, i: fhir.immunization(r, f"imm-{pid}-{i}")),
    "Procedure": ("procedures", lambda r, pid, i: fhir.procedure(r, f"proc-{pid}-{i}")),
    "MedicationRequest": ("medications", lambda r, pid, i: fhir.medication_request(r, f"med-{pid}-{i}")),
    "AllergyIntolerance": ("allergies", lambda r, pid, i: fhir.allergy(r, f"allergy-{pid}-{i}")),
    "CarePlan": ("careplans", lambda r, pid, i: fhir.care_plan(r, f"plan-{pid}-{i}")),
    "FamilyMemberHistory": ("family_history", lambda r, pid, i: fhir.family_history(r, f"fmh-{pid}-{i}")),
    "Consent": ("consents", lambda r, pid, i: fhir.consent(r, f"consent-{pid}-{i}")),
}


def line(resource):
    return json.dumps(resource, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def rows_for(con, table, ids):
    """All rows of one table for these people, grouped by person, in stored order."""
    name = records.TABLES.get(table, table)
    marks = ", ".join("?" * len(ids))
    df = pd.read_sql_query(f'SELECT * FROM "{name}" WHERE "PATIENT" IN ({marks}) ORDER BY rowid', con, params=list(ids))
    return {pid: list(g.itertuples(index=False)) for pid, g in df.groupby("PATIENT", sort=False)}


def export(out, limit=None, with_genetic=False):
    if not records.exists():
        sys.exit("The hospital records database has not been built (python scripts/get_all_data.py --only records)")
    os.makedirs(out, exist_ok=True)
    start = time.time()
    counts, files = {}, {}

    def open_type(rtype):
        files[rtype] = open(os.path.join(out, f"{rtype}.ndjson"), "w", encoding="utf-8", newline="\n")
        counts[rtype] = 0
        return files[rtype]

    def write(rtype, resource):
        (files.get(rtype) or open_type(rtype)).write(line(resource))
        counts[rtype] += 1

    with records.connect() as con:
        people = pd.read_sql_query('SELECT * FROM "patient" ORDER BY rowid', con)
        if limit:
            people = people.head(limit)
        types = {t: spec for t, spec in PER_ROW.items() if records.has(spec[0])}
        for t in types:
            open_type(t)
        open_type("Patient")
        open_type("Observation")
        if with_genetic and records.has("genetic_results"):
            open_type("GeneticObservation")
        for b in range(0, len(people), BATCH):
            chunk = people.iloc[b:b + BATCH]
            ids = list(chunk.Id)                      # always passed to SQLite as parameters
            tables = {t: rows_for(con, spec[0], ids) for t, spec in types.items()}
            obs = rows_for(con, "observations", ids)
            gen = rows_for(con, "genetic_results", ids) if "GeneticObservation" in files else {}
            for row in chunk.to_dict("records"):
                pid = row["Id"]
                write("Patient", fhir.patient(row))
                for t, (table, build) in types.items():
                    for i, r in enumerate(tables[t].get(pid, [])):
                        write(t, build(r, pid, i))
                for res in fhir.observations(obs.get(pid, []), prefix=f"obs-{pid}"):
                    write("Observation", res)
                for i, r in enumerate(gen.get(pid, [])):
                    write("GeneticObservation", fhir.genetic_result(r, f"genetic-{pid}-{i}"))
            print(f"  {min(b + BATCH, len(people)):,} of {len(people):,} people", flush=True)
        keep = set(people.Id)
        run = os.path.join(ROOT, "outputs", "new")
        tasks = os.path.join(run, "fhir_tasks.json")
        if os.path.exists(tasks):
            n = 0
            for entry in json.load(open(tasks, encoding="utf-8")).get("entry", []):
                t = entry["resource"]
                if t["for"]["reference"].split("/")[-1] in keep:
                    write("Task", {**t, "id": f"task-{n}"})
                    n += 1
        msgs, info = os.path.join(run, "messages.csv"), os.path.join(run, "run_info.json")
        if os.path.exists(msgs) and os.path.exists(info):
            began = pd.Timestamp(json.load(open(info, encoding="utf-8"))["start"])
            for i, r in enumerate(pd.read_csv(msgs, dtype={"person": str, "recipient": str}).itertuples(index=False)):
                if r.person in keep:
                    write("Communication", fhir.communication(r, f"comm-{i}", (began + pd.Timedelta(days=int(r.day))).date().isoformat()))
    for f in files.values():
        f.close()
    for t in [t for t in counts if counts[t] == 0]:
        os.remove(os.path.join(out, f"{t}.ndjson"))
        del counts[t]
    manifest = {
        "transactionTime": time.strftime("%Y-%m-%dT%H:%M:%S"), "requiresAccessToken": False,
        "request": "scripts/fhir_bulk_export.py" + (f" --patients {limit}" if limit else ""),
        "output": [{"type": "Observation" if t == "GeneticObservation" else t, "count": n, "url": f"{t}.ndjson"}
                   for t, n in counts.items()],
        "extension": {"note": "Simulated people only (generated test patients adjusted to Abu Dhabi). "
                              + ("Includes genetic results: handle with the person's genetic consent. " if with_genetic else
                                 "Genetic results are not included. ") + "Codes: SNOMED CT, LOINC, CVX, RxNorm."}}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    shown = os.path.relpath(out, ROOT) if os.path.splitdrive(out)[0].lower() == os.path.splitdrive(ROOT)[0].lower() else out
    print(f"Wrote {sum(counts.values()):,} resources in {len(counts)} files to {shown} ({time.time() - start:.0f}s)")
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "outputs", "fhir_export"))
    ap.add_argument("--patients", type=int, help="only the first N people")
    ap.add_argument("--with-genetic", action="store_true", help="also the genetic results")
    a = ap.parse_args()
    export(a.out, a.patients, a.with_genetic)
