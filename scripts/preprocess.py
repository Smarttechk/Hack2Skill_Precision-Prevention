"""
Preprocessing: turns the UAE-adjusted Synthea records into model-ready tables.

    python scripts/preprocess.py            (also runs as the "features" step of get_all_data.py)

Reads  the hospital records database (records.py, data/records/hospital.db; the CSV copies in
       data/processed/uae_synthea/ when the database has not been built) and data/reference/code_map.csv
Writes data/processed/features/
    patient_features.csv     one row per living person, as of today          (guide Steps 1.2, 1.2b, 1.3, 3.6)
    feature_dictionary.csv   every column: meaning, type, source
    preprocess_summary.json  counts and rates, for the report and the tests

Rules kept here:
  * Features for a date D use only records dated on or before D (no future information).
  * The hidden truth file never becomes a feature.
  * Synthea's US identity and address columns are dropped.
"""
import json, os, sys
from datetime import date

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "processed", "uae_synthea")
REF = os.path.join(ROOT, "data", "reference")
OUT = os.path.join(ROOT, "data", "processed", "features")
# every assumption lives in config/synthetic_data.json, every clinical threshold in config/clinical_rules.json
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clinical_records  # noqa: E402
import records  # noqa: E402
import utils  # noqa: E402
S, CR = utils.config("synthetic_data"), utils.config("clinical_rules")
DAY = pd.Timedelta(days=1)
ADULT = CR["adult_age"]
GENETIC_MARKERS = S["genetic_markers"]
WCV_AGES = S["well_child_ages_months"]
US_COLUMNS = ["SSN", "DRIVERS", "PASSPORT", "PREFIX", "FIRST", "MIDDLE", "LAST", "SUFFIX", "MAIDEN", "MARITAL",
              "RACE", "ETHNICITY", "BIRTHPLACE", "ADDRESS", "CITY", "STATE", "COUNTY", "FIPS", "ZIP", "LAT", "LON",
              "HEALTHCARE_EXPENSES", "HEALTHCARE_COVERAGE", "INCOME"]


VALID = {k: tuple(v) for k, v in S["valid_ranges"].items()}
CLEANING = {}


def log(msg):
    print(f"[preprocess] {msg}", flush=True)


def ts(s):
    return pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601").dt.tz_localize(None)


# ------------------------------------------------------------------ load
def load():
    cm = pd.read_csv(os.path.join(REF, "code_map.csv"), dtype=str).fillna("")
    codes = {r.field: set(c for c in r.codes.split(";") if c) for r in cm.itertuples()}
    use_db = records.exists()                 # patient records come from the hospital records database
    log("reading patient records from " + (os.path.relpath(records.path(), ROOT) if use_db else os.path.relpath(SRC, ROOT)))

    def table(name, usecols):
        return records.read(name, usecols) if use_db else pd.read_csv(os.path.join(SRC, f"{name}.csv"), dtype=str, usecols=usecols)
    pdt = {"Id": str, "guardian_id": str, "household_id": str}
    pts = records.read_like_csv("patients", dtype=pdt) if use_db else pd.read_csv(os.path.join(SRC, "patients.csv"), dtype=pdt)
    pts = pts.drop(columns=[c for c in ("MRN", "GIVEN_NAME", "FAMILY_NAME") if c in pts.columns])   # identity: never a feature
    pts["BIRTHDATE"] = pd.to_datetime(pts.BIRTHDATE)
    pts["DEATHDATE"] = pd.to_datetime(pts.DEATHDATE)
    enc = table("encounters", ["Id", "START", "PATIENT", "ENCOUNTERCLASS", "CODE", "DESCRIPTION", "REASONCODE"])
    enc["START"] = ts(enc.START)
    cond = table("conditions", ["START", "STOP", "PATIENT", "CODE", "DESCRIPTION"])
    cond["START"], cond["STOP"] = ts(cond.START), ts(cond.STOP)
    proc = table("procedures", ["START", "PATIENT", "CODE"])
    proc["START"] = ts(proc.START)
    imm = table("immunizations", ["DATE", "PATIENT", "CODE"])
    imm["DATE"] = ts(imm.DATE)
    obs_fields = ["hba1c", "bmi", "bmi_percentile", "systolic_bp", "diastolic_bp", "total_cholesterol", "ldl", "hdl"]
    want = {c: f for f in obs_fields for c in codes[f]}
    if use_db:                                # only the measurements the project uses
        obs = records.read("observations", ["DATE", "PATIENT", "CODE", "VALUE"], codes=want)
        obs = obs.assign(field=obs.CODE.map(want))
    else:
        parts = []
        for ch in pd.read_csv(os.path.join(SRC, "observations.csv"), dtype=str, usecols=["DATE", "PATIENT", "CODE", "VALUE"],
                              chunksize=1_000_000):
            ch = ch[ch.CODE.isin(want)]
            parts.append(ch.assign(field=ch.CODE.map(want)))
        obs = pd.concat(parts, ignore_index=True)
    obs["DATE"] = ts(obs.DATE)
    obs["VALUE"] = pd.to_numeric(obs.VALUE, errors="coerce")
    obs = obs.dropna(subset=["VALUE"])
    # cleaning: physiologically impossible values (a Synthea quirk, e.g. HbA1c below 3.5% repeated for 88 people).
    # The test did happen, so its date is kept; only the value is set to missing.
    lo = obs.field.map({f: r[0] for f, r in VALID.items()})
    hi = obs.field.map({f: r[1] for f, r in VALID.items()})
    bad = (obs.VALUE < lo) | (obs.VALUE > hi)
    CLEANING["implausible_values_set_missing"] = int(bad.sum())
    CLEANING["by_field"] = obs[bad].field.value_counts().to_dict()
    CLEANING["people_affected"] = int(obs[bad].PATIENT.nunique())
    obs.loc[bad, "VALUE"] = np.nan
    truth = pd.read_csv(os.path.join(SRC, "truth_hidden.csv"), dtype={"PATIENT": str})
    for c in ("diabetes_onset", "hypertension_onset"):
        truth[c] = pd.to_datetime(truth[c])
    return codes, pts, enc, cond, proc, imm, obs, truth


# ------------------------------------------------------------------ consent, family history, genetics (guide Steps 1.2, 1.2b, 3.6)
def person_attributes(pts, truth):
    """Consent, family history and genetic markers come from the records (family_history, genetic_result and
    consent tables: a hospital's own rows after a FHIR import, scripts/clinical_records.py). Child development and
    behaviour concerns, the polygenic score and marital status have no record table yet and are still generated."""
    family, genetic, consents = clinical_records.read_all(SRC)
    log(f"records: {len(family):,} family history rows, {len(genetic):,} genetic results, {len(consents):,} consent decisions")
    return clinical_records.derive(clinical_records.draw(pts, truth), pts, family, genetic, consents)


# ------------------------------------------------------------------ record-based features as of a date
def latest(df, date_col, by="PATIENT"):
    return df.sort_values(date_col).groupby(by).tail(1).set_index(by)


def missed_well_child(kids, wcv, D):
    """Count scheduled well-child visits (Bright Futures ages) with no visit near the due date."""
    if kids.empty:
        return pd.Series(dtype=float)
    rows = kids[["Id", "BIRTHDATE"]].merge(pd.DataFrame({"m": WCV_AGES}), how="cross")
    rows["due"] = rows.BIRTHDATE + pd.to_timedelta(rows.m * 30.4375, unit="D")
    hist_start = D - pd.Timedelta(days=S["history_years"] * 365)      # Synthea keeps about 10 years
    win = S["well_child_windows_days"]
    rows = rows[(rows.due >= hist_start) & (rows.due <= D - pd.Timedelta(days=win["under_48_months"][0]))].copy()
    rows["before"] = np.select([rows.m < 24, rows.m < 48], [win["under_24_months"][0], win["under_48_months"][0]], win["older"][0])
    rows["after"] = np.select([rows.m < 24, rows.m < 48], [win["under_24_months"][1], win["under_48_months"][1]], win["older"][1])
    rows = rows.reset_index(drop=True).reset_index().rename(columns={"index": "slot"})
    pairs = rows.merge(wcv[["PATIENT", "START"]], left_on="Id", right_on="PATIENT", how="left")
    ok = (pairs.START >= pairs.due - pd.to_timedelta(pairs.before, unit="D")) & \
         (pairs.START <= pairs.due + pd.to_timedelta(pairs.after, unit="D"))
    hit = ok.groupby(pairs.slot).any()
    rows["missed"] = ~rows.slot.map(hit).fillna(False).astype(bool)
    return rows.groupby("Id").missed.sum()


def features_asof(D, ids, pts, codes, enc, cond, proc, imm, obs):
    f = pd.DataFrame(index=pd.Index(ids, name="Id"))
    b = pts.set_index("Id").loc[ids]
    f["age"] = ((D - b.BIRTHDATE).dt.days // 365.25).astype(int)
    f["age_months"] = ((D - b.BIRTHDATE).dt.days / 30.4375).astype(int)
    f["male"] = (b.GENDER == "M").astype(int)
    e = enc[enc.START <= D]
    ge = e.groupby("PATIENT")
    f["first_record"] = ge.START.min()
    f["last_visit"] = ge.START.max()
    f["days_since_last_visit"] = (D - f.last_visit).dt.days
    f["years_of_records"] = ((D - f.first_record).dt.days / 365.25).round(2)
    e2 = e[e.START > D - S["feature_lookback_days"] * DAY]
    f["visits_2y"] = e2.groupby("PATIENT").size()
    f["wellness_visits_2y"] = e2[e2.ENCOUNTERCLASS == "wellness"].groupby("PATIENT").size()
    f["last_wellness_visit"] = e[e.ENCOUNTERCLASS == "wellness"].groupby("PATIENT").START.max()
    f["last_well_child_visit"] = e[e.CODE.isin(codes["well_child_visit"])].groupby("PATIENT").START.max()
    er = e[(e.ENCOUNTERCLASS == "emergency") & (e.START > D - S["er_lookback_days"] * DAY)]
    f["er_visits_12m"] = er.groupby("PATIENT").size()
    f["er_visits_12m_same_problem"] = er.assign(r=er.REASONCODE.fillna("none")).groupby(["PATIENT", "r"]).size().groupby(level=0).max()
    # long-term conditions active on D
    c = cond[(cond.START <= D) & (cond.STOP.isna() | (cond.STOP > D))]
    for name in ["diabetes", "prediabetes", "hypertension", "obesity", "asthma", "hyperlipidemia", "developmental_delay"]:
        f[f"has_{name}"] = f.index.isin(c[c.CODE.isin(codes[name])].PATIENT).astype(int)
    f["has_chronic"] = f[["has_diabetes", "has_hypertension", "has_asthma", "has_hyperlipidemia"]].max(axis=1)
    f["pregnant"] = f.index.isin(c[c.CODE == "72892002"].PATIENT).astype(int)
    # latest measurements on or before D
    o = obs[obs.DATE <= D]
    names = {"hba1c": "hba1c", "bmi": "bmi", "bmi_percentile": "bmi_percentile", "systolic_bp": "sbp",
             "diastolic_bp": "dbp", "total_cholesterol": "total_chol", "ldl": "ldl", "hdl": "hdl"}
    for fld, col in names.items():
        last_test = latest(o[o.field == fld], "DATE")                      # any test, even if its value was unusable
        last_value = latest(o[(o.field == fld) & o.VALUE.notna()], "DATE")  # latest usable value
        f[col] = last_value.VALUE
        f[f"{col}_date"] = last_test.DATE
    labs = o[o.field.isin(["hba1c", "total_cholesterol", "ldl", "hdl"]) & (o.DATE > D - S["feature_lookback_days"] * DAY)]
    f["lab_results_count"] = labs.groupby("PATIENT").size()
    chronic_tests = o[o.field.isin(["hba1c", "systolic_bp"])].groupby("PATIENT").DATE.max()
    f["last_chronic_test"] = chronic_tests
    f["months_since_chronic_test"] = ((D - f.last_chronic_test).dt.days / 30.4375).round(1)
    # abnormal result in someone not yet diagnosed for it, with no visit in the 30 days after
    A = S["abnormal_results"]
    ab = o[(o.DATE > D - S["er_lookback_days"] * DAY) & (o.DATE <= D - A["followup_days"] * DAY)].copy()
    dm = set(f.index[f.has_diabetes == 1]); htn = set(f.index[f.has_hypertension == 1])
    ab = ab[((ab.field == "hba1c") & (ab.VALUE >= A["hba1c"]) & ~ab.PATIENT.isin(dm)) |
            ((ab.field == "systolic_bp") & (ab.VALUE >= A["systolic_bp"]) & ~ab.PATIENT.isin(htn)) |
            ((ab.field == "ldl") & (ab.VALUE >= A["ldl"]))]
    if len(ab):
        nxt = pd.merge_asof(ab.sort_values("DATE"), e[["PATIENT", "START"]].sort_values("START"),
                            left_on="DATE", right_on="START", by="PATIENT", direction="forward", allow_exact_matches=False)
        nxt["followed"] = (nxt.START - nxt.DATE) <= A["followup_days"] * DAY
        f["abnormal_result"] = f.index.isin(nxt.PATIENT).astype(int)
        f["abnormal_no_followup"] = f.index.isin(nxt[~nxt.followed].PATIENT).astype(int)
    else:
        f["abnormal_result"] = 0
        f["abnormal_no_followup"] = 0
    f["followup_done"] = ((f.abnormal_result == 1) & (f.abnormal_no_followup == 0)).astype(int)
    # screenings and vaccines on or before D
    pr = proc[proc.START <= D]
    for name, col in [("cervical_screening", "cervical"), ("breast_screening", "breast"),
                      ("bowel_screening", "bowel"), ("depression_screen", "depression")]:
        last = pr[pr.CODE.isin(codes[name])].groupby("PATIENT").START.max()
        f[f"last_{col}_screen"] = last
        f[f"months_since_{col}_screen"] = ((D - last).dt.days / 30.4375).round(1)
    for col in ("hba1c", "ldl", "sbp", "bmi"):
        f[f"months_since_{col}"] = ((D - f[f"{col}_date"]).dt.days / 30.4375).round(1)
    f["months_since_wellness"] = ((D - f.last_wellness_visit).dt.days / 30.4375).round(1)
    f["months_since_well_child"] = ((D - f.last_well_child_visit).dt.days / 30.4375).round(1)
    im = imm[imm.DATE <= D]
    f["mmr_doses"] = im[im.CODE.isin(codes["mmr_vaccine"])].groupby("PATIENT").size()
    f["last_vaccine"] = im.groupby("PATIENT").DATE.max()
    # children: missed scheduled well-child visits and growth concern
    kids = b[f.age < ADULT].reset_index()
    wcv = e[e.CODE.isin(codes["well_child_visit"])]
    f["missed_well_child"] = missed_well_child(kids, wcv, D)
    G = S["child_growth_concern"]
    f["child_growth_concern"] = ((f.age.between(*G["ages"])) & ((f.bmi_percentile < G["below_percentile"])
                                 | (f.bmi_percentile >= G["at_or_above_percentile"]))).astype(int)
    prenatal = e[e.CODE.isin(codes["prenatal_visit"]) & (e.START > D - S["prenatal_check_days"] * DAY)].PATIENT
    f["pregnant_missed_check"] = ((f.pregnant == 1) & ~f.index.isin(prenatal)).astype(int)
    f["screened"] = (f.months_since_hba1c <= S["screened_months"]).astype(int)          # tested for the family / genetic risk
    # fill counts
    for col in ["visits_2y", "wellness_visits_2y", "er_visits_12m", "er_visits_12m_same_problem", "lab_results_count",
                "mmr_doses", "missed_well_child"]:
        f[col] = f[col].fillna(0).astype(int)
    ds = S["data_sufficient"]
    adult_ok = (f.visits_2y >= ds["adult_visits_2y"]) & (f.lab_results_count >= ds["adult_labs"]) & (f.years_of_records >= ds["years_of_records"])
    child_ok = (f.visits_2y >= ds["child_visits_2y"]) & (f.years_of_records >= ds["years_of_records"])   # children rarely have lab tests
    f["data_sufficient"] = np.where(f.age >= ADULT, adult_ok, child_ok).astype(int)
    return f


# ------------------------------------------------------------------ main
CONTEXT = ["GENDER", "SYNTHEA_GROUP", "nationality", "language", "work_shift", "insurance", "phone_type", "can_read",
           "smoker", "guardian_id", "household_id", "home_place", "region", "area_type", "home_lat", "home_lon",
           "nearest_facility", "nearest_facility_km", "facilities_within_10km", "travel_min", "access_barrier"]


def main():
    sys.path.insert(0, ROOT)
    import utils
    today = utils.run_date()                  # fixed in config/real_values.json, so a rerun gives the same tables
    os.makedirs(OUT, exist_ok=True)
    codes, pts, enc, cond, proc, imm, obs, truth = load()
    log(f"loaded {len(pts)} people, {len(enc):,} visits, {len(cond):,} conditions, {len(obs):,} measurements")
    sim = person_attributes(pts, truth)

    # ---- 1. patient_features.csv (today)
    alive_ids = pts[pts.DEATHDATE.isna()].Id.tolist()
    feats = features_asof(today, alive_ids, pts, codes, enc, cond, proc, imm, obs)
    ctx = pts.set_index("Id")[CONTEXT]
    pf = feats.join(ctx).join(sim.set_index("Id")).reset_index()
    assert not set(US_COLUMNS + ["MRN", "GIVEN_NAME", "FAMILY_NAME"]) & set(pf.columns)
    assert not any(c.startswith(("hidden_", "diagnosed_")) or c.endswith("_onset") for c in pf.columns)
    pf.to_csv(os.path.join(OUT, "patient_features.csv"), index=False)
    log(f"patient_features.csv: {pf.shape[0]} people x {pf.shape[1]} columns")


    # ---- 3. dictionary + summary
    write_dictionary(pf.columns)
    a = pf
    kids = a[a.age < 18]
    summary = {
        "date": today.date().isoformat(),
        "people": len(a), "columns": a.shape[1],
        "children": len(kids), "children_missing_any_scheduled_visit": round(float((kids.missed_well_child > 0).mean()), 3),
        "mean_missed_well_child_per_child": round(float(kids.missed_well_child.mean()), 2),
        "data_sufficient_share": round(float(a.data_sufficient.mean()), 3),
        "has_chronic_share_adults": round(float(a[a.age >= 18].has_chronic.mean()), 3),
        "abnormal_no_followup": int(a.abnormal_no_followup.sum()),
        "pregnant_missed_check": int(a.pregnant_missed_check.sum()),
        "child_growth_concern": int(a.child_growth_concern.sum()),
        "dev_concern_unassessed": int((a.dev_concern & ~a.dev_assessed).sum()),
        "consent_contact_share": round(float(a.consent.mean()), 3),
        "consent_genetic_share": round(float(a.consent_genetic.mean()), 3),
        "any_genetic_marker": int(a[[f"gene_{m}" for m in GENETIC_MARKERS]].any(axis=1).sum()),
        "actionable_genetic_flag": int(a.genetic_risk_flag.sum()),
        "cleaning": CLEANING,
    }
    json.dump(summary, open(os.path.join(OUT, "preprocess_summary.json"), "w"), indent=2)
    for k, v in summary.items():
        log(f"{k}: {v}")


DICT = {
    "Id": ("person id", "Synthea"), "age": ("age in years", "birth date"), "age_months": ("age in months", "birth date"),
    "male": ("1 = male", "Synthea"), "first_record": ("first visit on record", "encounters"),
    "last_visit": ("most recent visit", "encounters"), "days_since_last_visit": ("days since the last visit", "encounters"),
    "years_of_records": ("years between first visit and the reference date", "encounters"),
    "visits_2y": ("visits in the last 2 years", "encounters"), "wellness_visits_2y": ("wellness visits in 2 years", "encounters"),
    "last_wellness_visit": ("date of the latest wellness visit", "encounters"),
    "last_well_child_visit": ("date of the latest well-child visit", "encounters"),
    "months_since_wellness": ("months since the latest wellness visit", "encounters"),
    "months_since_well_child": ("months since the latest well-child visit", "encounters"),
    "er_visits_12m": ("emergency visits in 12 months", "encounters"),
    "er_visits_12m_same_problem": ("most emergency visits for one reason in 12 months", "encounters"),
    "has_diabetes": ("diabetes diagnosis active", "conditions"), "has_prediabetes": ("prediabetes active", "conditions"),
    "has_hypertension": ("hypertension active", "conditions"), "has_obesity": ("obesity finding active", "conditions"),
    "has_asthma": ("asthma active", "conditions"), "has_hyperlipidemia": ("hyperlipidemia active", "conditions"),
    "has_developmental_delay": ("developmental or attention disorder recorded", "conditions"),
    "has_chronic": ("any of diabetes, hypertension, asthma, hyperlipidemia", "conditions"),
    "pregnant": ("active normal pregnancy", "conditions"),
    "hba1c": ("latest HbA1c %", "observations 4548-4"), "bmi": ("latest BMI", "observations 39156-5"),
    "bmi_percentile": ("latest child BMI percentile", "observations 59576-9"), "sbp": ("latest systolic BP mmHg", "observations 8480-6"),
    "dbp": ("latest diastolic BP mmHg", "observations 8462-4"), "total_chol": ("latest total cholesterol mg/dL", "observations 2093-3"),
    "ldl": ("latest LDL mg/dL", "observations 18262-6"), "hdl": ("latest HDL mg/dL", "observations 2085-9"),
    "lab_results_count": ("lab results (HbA1c, cholesterol) in 2 years", "observations"),
    "last_chronic_test": ("date of the latest HbA1c or blood pressure", "observations"),
    "months_since_chronic_test": ("months since that test", "observations"),
    "abnormal_result": ("HbA1c >= 6.5 or SBP >= 160 without that diagnosis, or LDL >= 190, in the last year", "observations"),
    "abnormal_no_followup": ("such a result with no visit in the next 30 days", "observations + encounters"),
    "followup_done": ("abnormal result that was followed up", "derived"),
    "missed_well_child": ("scheduled Bright Futures visits with no visit near the due date (last 10 years)", "encounters"),
    "child_growth_concern": ("child BMI percentile under 5 or 97 and over", "observations"),
    "pregnant_missed_check": ("pregnant with no prenatal visit in 6 weeks", "conditions + encounters"),
    "screened": ("HbA1c test in the last 3 years", "observations"), "mmr_doses": ("MMR doses received", "immunizations"),
    "last_vaccine": ("latest vaccine date", "immunizations"),
    "data_sufficient": ("adults: 2+ visits in 2 years, 1+ lab result, 1+ year of records; children: 1+ visit in 2 years, 1+ year of records", "derived"),
    "consent": ("may be contacted (child: guardian's decision)", "consent records"),
    "consent_genetic": ("genetic data may change the plan (child: guardian's decision)", "consent records"),
    "consent_given_by": ("who gave consent", "consent records"),
    "family_history_recorded": ("family history was asked", "family history records"),
    "family_hist_diabetes": ("close relative with diabetes", "family history records"), "dev_concern": ("development concern noted", "simulated"),
    "behaviour_concern": ("behaviour concern noted", "simulated"), "dev_assessed": ("development concern assessed", "simulated"),
    "behaviour_assessed": ("behaviour concern assessed", "simulated"), "gene_status": ("status per marker (JSON)", "genetic results"),
    "genetic_risk_flag": ("any pathogenic or likely pathogenic marker", "genetic results"),
    "has_vus_only": ("only uncertain variants", "genetic results"), "prs": ("polygenic score, SD units", "simulated"),
    "married": ("married (premarital rule)", "simulated"),
}


def write_dictionary(columns):
    rows = []
    for c in columns:
        if c in DICT:
            meaning, src = DICT[c]
        elif c.startswith("gene_"):
            meaning, src = (f"carries {c[5:]} marker", "genetic results")
        elif c.endswith("_date"):
            meaning, src = (f"date of the latest {c[:-5]} test (the value column holds the latest plausible value)", "observations")
        elif c.startswith(("last_", "months_since_")):
            meaning, src = (c.replace("_", " "), "procedures / observations")
        else:
            meaning, src = (c.replace("_", " "), "uae_adjust.py (context)")
        rows.append({"column": c, "meaning": meaning, "source": src})
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "feature_dictionary.csv"), index=False)


if __name__ == "__main__":
    main()
