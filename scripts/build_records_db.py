"""
Build the hospital records database (records.py, data/records/hospital.db) from the adjusted records in
data/processed/uae_synthea (generated test patients, or a hospital's FHIR export after scripts/fhir_import.py).

    python scripts/build_records_db.py            (also the "records" step of get_all_data.py)

Every patient gets a hospital record number (MRN) and a name that fits their nationality group, unless the import
already brought them (a hospital's own MRN and names are kept). The generator's US identity and address columns are
not stored. The simulation's hidden truth (truth_hidden.csv) is not a hospital record and stays out of the database.
"""
import json
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clinical_records  # noqa: E402
import records  # noqa: E402
import utils  # noqa: E402

SRC = utils.ADJUSTED
NAMES = os.path.join(utils.ROOT, "config", "patient_names.json")
NAME_SEED = 2027                          # its own random stream: naming never changes anything else
US_COLUMNS = ["SSN", "DRIVERS", "PASSPORT", "PREFIX", "SUFFIX", "MAIDEN", "MARITAL", "RACE", "ETHNICITY", "BIRTHPLACE",
              "ADDRESS", "CITY", "STATE", "COUNTY", "FIPS", "ZIP", "LAT", "LON", "HEALTHCARE_EXPENSES", "HEALTHCARE_COVERAGE",
              "INCOME", "FIRST", "MIDDLE", "LAST"]
INDEXES = {"patient": ["Id", "MRN", "BIRTHDATE"], "encounter": ["PATIENT", "Id"], "condition": ["PATIENT", "CODE"],
           "observation": ["PATIENT", "CODE"], "immunization": ["PATIENT"], "procedure": ["PATIENT", "CODE"],
           "family_history": ["PATIENT"], "genetic_result": ["PATIENT"], "consent": ["PATIENT"],
           "medication": ["PATIENT", "CODE"], "allergy": ["PATIENT", "CODE"], "careplan": ["PATIENT"]}
# the three tables that complete the record: the columns kept (prices and payers are left out)
EXTRA = {"medications": ["START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "DISPENSES", "REASONCODE", "REASONDESCRIPTION"],
         "allergies": ["START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "SYSTEM", "DESCRIPTION", "TYPE", "CATEGORY", "REACTION1",
                       "DESCRIPTION1", "SEVERITY1", "REACTION2", "DESCRIPTION2", "SEVERITY2"],
         "careplans": ["Id", "START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "REASONCODE", "REASONDESCRIPTION"]}


def log(msg):
    print(f"[records] {msg}", flush=True)


def generated_name(value):
    """A name from the test-data generator (it adds digits: 'Tracey100') or no name at all."""
    v = str(value or "")
    return v in ("", "nan") or any(ch.isdigit() for ch in v)


def name_people(pts):
    """Record number and name for everyone without one. Children take their guardian's family name."""
    rng = np.random.default_rng(NAME_SEED)
    names = {k: v for k, v in json.load(open(NAMES, encoding="utf-8")).items() if not k.startswith("_")}
    n = len(pts)
    given = pts.get("FIRST", pd.Series([""] * n, index=pts.index)).astype(str)
    family = pts.get("LAST", pd.Series([""] * n, index=pts.index)).astype(str)
    keep = ~(given.map(generated_name) | family.map(generated_name))
    out_given, out_family = given.where(keep, ""), family.where(keep, "")
    group = pts.nationality.where(pts.nationality.isin(names), "Other")
    sex = pts.GENDER.where(pts.GENDER.isin(["M", "F"]), "F")
    for i in pts.index[~keep]:
        pool = names[group[i]]
        if isinstance(pool, list):                    # "Other": first and family name from one origin
            pool = pool[rng.integers(len(pool))]
        out_given[i] = rng.choice(pool[sex[i]])
        out_family[i] = rng.choice(pool["family"])
    by_id = dict(zip(pts.Id, out_family))
    child = pts.guardian_id.notna() & ~keep
    out_family[child] = pts.loc[child, "guardian_id"].map(by_id).fillna(out_family[child])
    mrn = pts.get("MRN", pd.Series([np.nan] * n, index=pts.index))
    need = mrn.isna() | (mrn.astype(str).str.strip() == "")
    numbers = rng.choice(np.arange(1_000_000, 10_000_000), int(need.sum()), replace=False)
    mrn = mrn.astype(object)
    mrn[need] = [f"ADH-{x:07d}" for x in numbers]
    return out_given, out_family, mrn, int((~keep).sum())


def extra_rows(table, people, visits, today):
    """Medicines, allergies or care plans of the people kept: from a hospital's import when it brought them, else from the
    generated records. Nothing dated after the run date; a stop date after it means still going on; a visit outside the
    kept history is dropped from the row (the row stays)."""
    cols = EXTRA[table]
    src = next((f for f in (os.path.join(SRC, f"{table}.csv"), os.path.join(utils.DATA, "raw", f"{table}.csv")) if os.path.exists(f)), None)
    if src is None:
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(src, dtype=str).reindex(columns=cols)
    df = df[df.PATIENT.isin(people) & (df.START.str[:10] <= today)].copy()
    df.loc[df.STOP.notna() & (df.STOP.str[:10] > today), "STOP"] = np.nan
    df.loc[~df.ENCOUNTER.isin(visits), "ENCOUNTER"] = np.nan
    return df.astype(object)


def build():
    start = time.time()
    db = records.path()
    os.makedirs(os.path.dirname(db), exist_ok=True)
    tmp = db + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    counts = {}
    # patients: keep the types pd.read_csv gives (numbers, true/false); drop the generator's US identity columns
    pts = pd.read_csv(os.path.join(SRC, "patients.csv"), dtype=str)
    given, family, mrn, named = name_people(pts)
    # MRN is re-added first, below; an import's NATIONALITY and LANGUAGE are already in nationality and language
    # (uae_adjust.py), and SQLite column names ignore case, so the input columns go
    pts = pts.drop(columns=[c for c in US_COLUMNS + ["MRN", "NATIONALITY", "LANGUAGE"] if c in pts.columns])
    pts.insert(1, "MRN", mrn)
    pts.insert(2, "GIVEN_NAME", given)
    pts.insert(3, "FAMILY_NAME", family)
    pts.to_sql("patient", con, index=False)
    counts["patient"] = len(pts)
    for table, name in records.TABLES.items():
        if table == "patients" or table in EXTRA:
            continue
        n = 0
        if table in clinical_records.COLUMNS:         # family history, genetic results, consent: always there, even empty
            f = os.path.join(SRC, f"{table}.csv")
            cols = clinical_records.COLUMNS[table]
            df = pd.read_csv(f, dtype=str).reindex(columns=cols) if os.path.exists(f) else pd.DataFrame(columns=cols)
            df.astype(object).to_sql(name, con, index=False, dtype={c: "TEXT" for c in cols})
            counts[name] = len(df)
            log(f"{name}: {len(df):,} rows")
            continue
        for ch in pd.read_csv(os.path.join(SRC, f"{table}.csv"), dtype=str, chunksize=500_000):
            ch.to_sql(name, con, index=False, if_exists="append")
            n += len(ch)
        counts[name] = n
        log(f"{name}: {n:,} rows")
    visits = {r[0] for r in con.execute('SELECT "Id" FROM encounter')}
    today = utils.run_date().date().isoformat()
    for table, cols in EXTRA.items():
        df = extra_rows(table, set(pts.Id), visits, today)
        df.to_sql(records.TABLES[table], con, index=False, dtype={c: "TEXT" for c in cols})
        counts[records.TABLES[table]] = len(df)
        log(f"{records.TABLES[table]}: {len(df):,} rows")
    for table, cols in INDEXES.items():
        for c in cols:
            con.execute(f'CREATE INDEX "ix_{table}_{c}" ON "{table}" ("{c}")')
    summary = json.load(open(os.path.join(SRC, "adjust_summary.json"), encoding="utf-8")) if os.path.exists(
        os.path.join(SRC, "adjust_summary.json")) else {}
    meta = {"built": time.strftime("%Y-%m-%d %H:%M"), "source": summary.get("source", "test patients (Synthea), adjusted to Abu Dhabi"),
            "patients": str(counts["patient"]), "names_generated": str(named),
            "standard": "tables shaped like FHIR R4: Patient, Encounter, Condition, Observation, Immunization, Procedure, "
                        "MedicationRequest, AllergyIntolerance, CarePlan, FamilyMemberHistory, genetic Observation, Consent; "
                        "codes SNOMED CT, LOINC, CVX, RxNorm"}
    con.execute('CREATE TABLE meta ("key" TEXT PRIMARY KEY, "value" TEXT)')
    con.executemany('INSERT INTO meta VALUES (?, ?)', meta.items())
    con.commit()
    con.close()
    os.replace(tmp, db)
    log(f"built {os.path.relpath(db, ROOT)} ({os.path.getsize(db) / 1e6:.0f} MB) in {time.time() - start:.0f}s: "
        + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
    return counts


if __name__ == "__main__":
    build()
