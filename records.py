"""
The hospital records database: every patient's record in one SQLite file, data/records/hospital.db, with the six tables
a hospital record system keeps, shaped like the FHIR R4 resources they come from:

    patient       one row per person: record number (MRN), name, date of birth, sex, nationality, language, insurance,
                  home area, guardian and household
    encounter     visits (wellness, ambulatory, emergency, inpatient ...)
    condition     diagnoses (SNOMED CT)
    observation   measurements and lab results (LOINC): HbA1c, blood pressure, BMI, cholesterol ...
    immunization  vaccines (CVX)
    procedure     screenings and other procedures (SNOMED CT)

and three more that the prevention rules need (scripts/clinical_records.py):

    family_history  what a person reported about relatives' conditions (FHIR FamilyMemberHistory)
    genetic_result  variants found, with gene and clinical significance (FHIR Observation, LOINC 69548-6)
    consent         consent decisions: purpose, permit or deny, who gave it (FHIR Consent)

and three that complete the clinical record (scripts/build_records_db.py), served as FHIR but not used by the rules:

    medication      medicines prescribed, RxNorm (FHIR MedicationRequest); no prices or payers
    allergy         allergies and intolerances with their reactions (FHIR AllergyIntolerance)
    careplan        care plans (FHIR CarePlan)

It is built by scripts/build_records_db.py (the "records" step of get_all_data.py), from the generated test patients or
from a hospital's FHIR export (scripts/fhir_import.py). The feature step and the app read patient records from here.
For a pilot the same tables move to the hospital's PostgreSQL server in the UAE; only connect() changes.
"""
import io
import os
import sqlite3

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
TABLES = {"patients": "patient", "encounters": "encounter", "conditions": "condition", "observations": "observation",
          "immunizations": "immunization", "procedures": "procedure",
          # family history, genetic results and consent decisions (scripts/clinical_records.py)
          "family_history": "family_history", "genetic_results": "genetic_result", "consents": "consent",
          # medicines, allergies and care plans (scripts/build_records_db.py)
          "medications": "medication", "allergies": "allergy", "careplans": "careplan"}


def path():
    return os.environ.get("PP_RECORDS_DB") or os.path.join(ROOT, "data", "records", "hospital.db")


def exists():
    return os.path.exists(path())


def connect():
    return sqlite3.connect(path())


def has(table):
    """True when the database holds this table (one built before a table was added does not)."""
    with connect() as con:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                           [TABLES.get(table, table)]).fetchone() is not None


def read(table, columns=None, codes=None, where=None, patient=None):
    """One table as text (like the CSV files it replaces): only these columns, only rows with these CODE values,
    only one person's rows (patient=Id), in the order they were stored. Values are always passed as parameters."""
    name = TABLES.get(table, table)
    cols = ", ".join(f'"{c}"' for c in columns) if columns else "*"
    sql, args, conds = f'SELECT {cols} FROM "{name}"', [], []
    if codes is not None:
        codes = list(codes)
        conds.append(f'"CODE" IN ({", ".join("?" * len(codes))})')
        args += codes
    if patient is not None:
        conds.append('"Id" = ?' if name == "patient" else '"PATIENT" = ?')
        args.append(patient)
    if where:
        conds.append(where)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY rowid"
    with connect() as con:
        df = pd.read_sql_query(sql, con, params=args)
    with pd.option_context("future.no_silent_downcasting", True):
        return df.replace({None: np.nan}).infer_objects(copy=False)


def read_like_csv(table, **read_csv_args):
    """The patient table with exactly the column types pd.read_csv would give the CSV file (numbers, true/false)."""
    df = read(table)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return pd.read_csv(buf, **read_csv_args)


def meta():
    if not exists():
        return {}
    with connect() as con:
        return dict(con.execute('SELECT "key", "value" FROM meta').fetchall())
