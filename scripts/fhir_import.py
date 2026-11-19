"""
Import patient records sent in HL7 FHIR R4 (a hospital record system, Abu Dhabi's Malaffi exchange, or a Synthea FHIR
run) into the six tables the pipeline reads: patients, encounters, conditions, observations, immunizations and
procedures (the same columns as data/raw). Nothing after this step changes.

    python scripts/fhir_import.py hospital_export/ --out data/imported       folders, .json files or .ndjson files
    python scripts/fhir_import.py --sample 300                              a sample FHIR bundle made from data/raw
                                                                            (data/fhir_sample/bundle.json), for demos

Then run the pipeline on the imported tables (PowerShell):
    $env:PP_RAW_DIR = "data/imported"
    python scripts/get_all_data.py --only adjust features
    python orchestrator.py
    python evaluate.py

Also read, into three more tables (scripts/clinical_records.py): FamilyMemberHistory (family_history), genetic variant
Observations, LOINC 69548-6 (genetic_results), and active Consent resources about contact or genetic use (consents).
From an import these are never generated: what the hospital did not send is simply not there.

Reads: a Bundle (collection, transaction, searchset or batch), a single resource, or a FHIR bulk export (.ndjson, one
resource per line). References may be "Patient/123" or "urn:uuid:123". Other resources are counted and
skipped. A blood pressure panel (LOINC 85354-9) becomes one row per reading (systolic 8480-6, diastolic 8462-4).
An Emirates ID is kept only as its last 4 digits. A report of what was read and which codes the project recognises
is written next to the tables (import_report.json): a hospital that codes diagnoses in ICD-10 instead of SNOMED CT
needs those codes added to data/reference/code_map.csv before the care-gap rules can see them.
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
sys.path.insert(0, ROOT)

# the columns of each table, exactly as data/raw has them (Synthea's CSV layout), plus what a hospital adds
COLUMNS = {
    "patients": ["Id", "BIRTHDATE", "DEATHDATE", "SSN", "DRIVERS", "PASSPORT", "PREFIX", "FIRST", "MIDDLE", "LAST", "SUFFIX",
                 "MAIDEN", "MARITAL", "RACE", "ETHNICITY", "GENDER", "BIRTHPLACE", "ADDRESS", "CITY", "STATE", "COUNTY", "FIPS",
                 "ZIP", "LAT", "LON", "HEALTHCARE_EXPENSES", "HEALTHCARE_COVERAGE", "INCOME", "SYNTHEA_GROUP",
                 "MRN", "EMIRATES_ID_LAST4", "NATIONALITY", "LANGUAGE"],
    "encounters": ["Id", "START", "STOP", "PATIENT", "ORGANIZATION", "PROVIDER", "PAYER", "ENCOUNTERCLASS", "CODE", "DESCRIPTION",
                   "BASE_ENCOUNTER_COST", "TOTAL_CLAIM_COST", "PAYER_COVERAGE", "REASONCODE", "REASONDESCRIPTION"],
    "conditions": ["START", "STOP", "PATIENT", "ENCOUNTER", "SYSTEM", "CODE", "DESCRIPTION"],
    "observations": ["DATE", "PATIENT", "ENCOUNTER", "CATEGORY", "CODE", "DESCRIPTION", "VALUE", "UNITS", "TYPE"],
    "immunizations": ["DATE", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "BASE_COST"],
    "procedures": ["START", "STOP", "PATIENT", "ENCOUNTER", "SYSTEM", "CODE", "DESCRIPTION", "BASE_COST", "REASONCODE",
                   "REASONDESCRIPTION"],
}
RESOURCE_TABLE = {"Patient": "patients", "Encounter": "encounters", "Condition": "conditions", "Observation": "observations",
                  "Immunization": "immunizations", "Procedure": "procedures"}

SYSTEM_NAME = {"http://snomed.info/sct": "SNOMED-CT", "http://hl7.org/fhir/sid/icd-10": "ICD-10",
               "http://hl7.org/fhir/sid/icd-10-cm": "ICD-10-CM", "http://loinc.org": "LOINC",
               "http://hl7.org/fhir/sid/cvx": "CVX"}
SYSTEM_URL = {v: k for k, v in SYSTEM_NAME.items()}

# FHIR visit class (v3 ActCode) -> the words the pipeline uses; an ambulatory visit with a check-up code is "wellness"
CLASS_WORD = {"EMER": "emergency", "IMP": "inpatient", "ACUTE": "inpatient", "NONAC": "inpatient", "SS": "inpatient",
              "HH": "home", "VR": "virtual", "AMB": "ambulatory", "OBSENC": "ambulatory", "PRENC": "ambulatory"}
WELLNESS_CODES = {"162673000", "410620009"}   # SNOMED: general examination, well child visit

BP_PANEL, BP_PARTS = "85354-9", ("8480-6", "8462-4")

# family history, genetic results and consent decisions: three more tables (scripts/clinical_records.py)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clinical_records as cr  # noqa: E402
import fhir  # noqa: E402
GENETIC_CODE, GENE_STUDIED, SIGNIFICANCE_CODE = fhir.GENETIC_CODE, fhir.GENE_STUDIED, fhir.SIGNIFICANCE_CODE
SIGNIFICANCE_BY_CODE = {code: name for name, (code, _) in fhir.SIGNIFICANCE.items()}
EXTRA_TABLE = {"FamilyMemberHistory": "family_history", "Consent": "consents"}

# nationality: a country code (ISO 3166 alpha-3 or alpha-2) or the group name -> the project's nationality groups
ARAB = {"SAU", "SA", "KWT", "KW", "BHR", "BH", "QAT", "QA", "OMN", "OM", "YEM", "YE", "IRQ", "IQ", "SYR", "SY", "JOR", "JO",
        "LBN", "LB", "PSE", "PS", "EGY", "EG", "SDN", "SD", "LBY", "LY", "TUN", "TN", "DZA", "DZ", "MAR", "MA", "MRT", "MR",
        "SOM", "SO", "DJI", "DJ", "COM", "KM"}
SOUTH_ASIAN = {"IND", "IN", "PAK", "PK", "BGD", "BD", "LKA", "LK", "NPL", "NP", "BTN", "BT", "MDV", "MV", "AFG", "AF"}
WESTERN = {"GBR", "GB", "USA", "US", "CAN", "CA", "AUS", "AU", "NZL", "NZ", "IRL", "IE", "FRA", "FR", "DEU", "DE", "ITA", "IT",
           "ESP", "ES", "NLD", "NL", "BEL", "BE", "CHE", "CH", "AUT", "AT", "SWE", "SE", "NOR", "NO", "DNK", "DK", "FIN", "FI",
           "PRT", "PT", "ZAF", "ZA"}
GROUPS = {"Emirati", "Other Arab", "South Asian", "Filipino", "Western", "Other"}
LANGUAGE_NAME = {"ar": "Arabic", "en": "English", "ur": "Urdu", "hi": "Hindi", "tl": "Tagalog", "fil": "Tagalog"}


def nationality_group(value):
    v = (value or "").strip()
    if not v:
        return ""
    if v in GROUPS:
        return v
    c = v.upper()
    if c in ("ARE", "AE", "UAE"):
        return "Emirati"
    if c in ARAB:
        return "Other Arab"
    if c in SOUTH_ASIAN:
        return "South Asian"
    if c in ("PHL", "PH"):
        return "Filipino"
    if c in WESTERN:
        return "Western"
    return "Other"


def language_name(value):
    v = (value or "").strip()
    if not v:
        return ""
    if v in LANGUAGE_NAME.values():
        return v
    return LANGUAGE_NAME.get(v.lower().split("-")[0], "")      # other languages: left empty (the pipeline picks one)


# ------------------------------------------------------------------ small helpers
def ref_id(ref):
    """'Patient/123', 'Patient/123/_history/2', 'urn:uuid:123' or a full URL -> '123'."""
    if isinstance(ref, dict):
        ref = ref.get("reference", "")
    ref = str(ref or "")
    if ref.startswith("urn:uuid:"):
        return ref[9:]
    parts = ref.split("/")
    if "_history" in parts:
        parts = parts[:parts.index("_history")]
    return parts[-1] if parts else ""


def coding(cc):
    """(system name, code, display) of the first coding of a CodeableConcept."""
    cc = cc or {}
    c = (cc.get("coding") or [{}])[0]
    system = SYSTEM_NAME.get(c.get("system", ""), c.get("system", ""))
    return system, str(c.get("code", "") or ""), cc.get("text") or c.get("display") or ""


def number(v):
    """A JSON number as text, written the way it came (120 stays 120, 80.0 stays 80.0)."""
    if isinstance(v, bool) or v is None:
        return ""
    return str(v)


def first(*vals):
    for v in vals:
        if v:
            return v
    return ""


# ------------------------------------------------------------------ one resource -> rows
def patient_row(r, report):
    row = {"Id": r.get("id", ""), "BIRTHDATE": r.get("birthDate", ""),
           "GENDER": {"male": "M", "female": "F"}.get(r.get("gender", ""), "")}
    if r.get("deceasedDateTime"):
        row["DEATHDATE"] = r["deceasedDateTime"][:10]
    elif r.get("deceasedBoolean"):
        report["skipped"]["Patient deceased, no date"] += 1
        return None
    if not row["GENDER"]:
        report["warnings"]["patients without male/female sex"] += 1
    name = (r.get("name") or [{}])[0]
    given = name.get("given") or []
    row.update({"PREFIX": " ".join(name.get("prefix") or []), "FIRST": given[0] if given else "",
                "MIDDLE": " ".join(given[1:]), "LAST": name.get("family", ""), "SUFFIX": " ".join(name.get("suffix") or []),
                "MARITAL": coding(r.get("maritalStatus"))[1]})
    addr = (r.get("address") or [{}])[0]
    row.update({"ADDRESS": " ".join(addr.get("line") or []), "CITY": addr.get("city", ""), "STATE": addr.get("state", ""),
                "ZIP": addr.get("postalCode", "")})
    for ext in addr.get("extension") or []:
        if ext.get("url", "").endswith("geolocation"):
            for e in ext.get("extension") or []:
                row["LAT" if e.get("url") == "latitude" else "LON"] = number(e.get("valueDecimal"))
    for ident in r.get("identifier") or []:
        kind = coding(ident.get("type"))[1].upper()
        system, value = ident.get("system", "").lower(), str(ident.get("value", ""))
        if "emirates" in system or kind in ("NNARE", "NI"):
            digits = "".join(ch for ch in value if ch.isdigit())
            row["EMIRATES_ID_LAST4"] = digits[-4:]          # never the whole number
        elif kind == "MR":
            row["MRN"] = value
        elif kind == "SS":
            row["SSN"] = value
        elif kind == "DL":
            row["DRIVERS"] = value
        elif kind == "PPN":
            row["PASSPORT"] = value
    for ext in r.get("extension") or []:
        url = ext.get("url", "")
        if "nationality" in url:
            parts = ext.get("extension") or [ext]
            for e in parts:
                cc = e.get("valueCodeableConcept")
                if cc:
                    _, code, text = coding(cc)
                    row["NATIONALITY"] = nationality_group(first(code, text))
        elif url.endswith("us-core-race") or url.endswith("us-core-ethnicity"):
            text = next((e.get("valueString") for e in ext.get("extension") or [] if e.get("url") == "text"), "")
            row["RACE" if url.endswith("race") else "ETHNICITY"] = (text or "").lower()
    comms = r.get("communication") or []
    comm = next((c for c in comms if c.get("preferred")), comms[0] if comms else None)
    if comm:
        _, code, text = coding(comm.get("language"))
        row["LANGUAGE"] = first(language_name(code), language_name(text))
        if not row["LANGUAGE"]:
            report["warnings"]["languages without templates (left for the pipeline to choose)"] += 1
    return row


def encounter_row(r):
    system, code, desc = coding((r.get("type") or [{}])[0])
    cls = r.get("class") or {}
    if isinstance(cls, list):                                 # FHIR R5 allows a list
        cls = (cls[0].get("coding") or [{}])[0] if cls else {}
    c = str(cls.get("code", ""))
    word = CLASS_WORD.get(c.upper(), c.lower())
    if word == "ambulatory" and code in WELLNESS_CODES:
        word = "wellness"
    _, rcode, rdesc = coding((r.get("reasonCode") or [{}])[0])
    period = r.get("period") or {}
    participant = (r.get("participant") or [{}])[0]
    return {"Id": r.get("id", ""), "START": period.get("start", ""), "STOP": period.get("end", ""),
            "PATIENT": ref_id(r.get("subject")), "ORGANIZATION": ref_id(r.get("serviceProvider")),
            "PROVIDER": ref_id(participant.get("individual")), "ENCOUNTERCLASS": word, "CODE": code, "DESCRIPTION": desc,
            "REASONCODE": rcode, "REASONDESCRIPTION": rdesc}


def condition_row(r):
    system, code, desc = coding(r.get("code"))
    return {"START": (r.get("onsetDateTime") or (r.get("onsetPeriod") or {}).get("start") or r.get("recordedDate") or "")[:10],
            "STOP": (r.get("abatementDateTime") or "")[:10], "PATIENT": ref_id(r.get("subject")),
            "ENCOUNTER": ref_id(r.get("encounter")), "SYSTEM": system, "CODE": code, "DESCRIPTION": desc}


def observation_rows(r):
    base = {"DATE": r.get("effectiveDateTime") or (r.get("effectivePeriod") or {}).get("start") or r.get("issued") or "",
            "PATIENT": ref_id(r.get("subject")), "ENCOUNTER": ref_id(r.get("encounter")),
            "CATEGORY": coding((r.get("category") or [{}])[0])[1]}

    def value_of(x):
        if "valueQuantity" in x:
            q = x["valueQuantity"]
            return number(q.get("value")), q.get("unit") or q.get("code") or "", "numeric"
        if "valueCodeableConcept" in x:
            return coding(x["valueCodeableConcept"])[2], "", "text"
        for k in ("valueString", "valueBoolean", "valueInteger"):
            if k in x:
                return str(x[k]), "", "text" if k != "valueInteger" else "numeric"
        return None

    rows = []
    for comp in r.get("component") or []:                    # a panel: one row per reading (blood pressure)
        v = value_of(comp)
        if v:
            _, code, desc = coding(comp.get("code"))
            rows.append({**base, "CODE": code, "DESCRIPTION": desc, "VALUE": v[0], "UNITS": v[1], "TYPE": v[2]})
    v = value_of(r)
    if v:
        _, code, desc = coding(r.get("code"))
        rows.append({**base, "CODE": code, "DESCRIPTION": desc, "VALUE": v[0], "UNITS": v[1], "TYPE": v[2]})
    return rows


def immunization_row(r):
    _, code, desc = coding(r.get("vaccineCode"))
    return {"DATE": r.get("occurrenceDateTime", ""), "PATIENT": ref_id(r.get("patient")), "ENCOUNTER": ref_id(r.get("encounter")),
            "CODE": code, "DESCRIPTION": desc}


def procedure_row(r):
    system, code, desc = coding(r.get("code"))
    period = r.get("performedPeriod") or {}
    _, rcode, rdesc = coding((r.get("reasonCode") or [{}])[0])
    return {"START": r.get("performedDateTime") or period.get("start", ""), "STOP": period.get("end", ""),
            "PATIENT": ref_id(r.get("subject")), "ENCOUNTER": ref_id(r.get("encounter")), "SYSTEM": system, "CODE": code,
            "DESCRIPTION": desc, "REASONCODE": rcode, "REASONDESCRIPTION": rdesc}


# ------------------------------------------------------------------ family history, genetic results, consent
def family_history_rows(r, report):
    if r.get("status") in ("entered-in-error", "not-done", "health-unknown"):      # the history was not taken
        report["skipped"][f"FamilyMemberHistory {r['status']}"] += 1
        return []
    rel = r.get("relationship") or {}
    base = {"DATE": (r.get("date") or "")[:10], "PATIENT": ref_id(r.get("patient")),
            "RELATIONSHIP": coding(rel)[1] or rel.get("text", "")}
    conds = [c for c in r.get("condition") or [] if c.get("code")]
    if not conds:
        note = ((r.get("note") or [{}])[0]).get("text", "")
        return [{**base, "SYSTEM": "", "CODE": "", "DESCRIPTION": note or cr.NO_HISTORY}]
    rows = []
    for c in conds:
        system, code, desc = coding(c["code"])
        rows.append({**base, "SYSTEM": system, "CODE": code, "DESCRIPTION": desc})
    return rows


def is_genetic(r):
    """A genetic variant result: LOINC 69548-6, or any Observation that names the gene studied (48018-6)."""
    return coding(r.get("code"))[1] == GENETIC_CODE or any(
        coding(c.get("code"))[1] == GENE_STUDIED for c in r.get("component") or [])


def significance(code, text):
    """A laboratory's clinical significance (LOINC answer code or words) as the project writes it."""
    if code in SIGNIFICANCE_BY_CODE:
        return SIGNIFICANCE_BY_CODE[code]
    t = " ".join(str(text or "").lower().replace("_", " ").replace("-", " ").split())
    if "benign" in t:
        return "likely_benign" if "likely" in t else "benign"
    if "pathogenic" in t:
        return "likely_pathogenic" if "likely" in t else "pathogenic"
    if "uncertain" in t or "unknown" in t or t == "vus":
        return "VUS"
    return ""


def genetic_row(r, report):
    _, vcode, vtext = coding(r.get("valueCodeableConcept"))
    if vcode == "LA9634-2" or str(vtext).strip().lower() in ("absent", "negative", "not detected"):
        report["skipped"]["genetic results with no variant found"] += 1
        return None
    gene = sig = ""
    for comp in r.get("component") or []:
        code = coding(comp.get("code"))[1]
        _, ccode, ctext = coding(comp.get("valueCodeableConcept"))
        if code == GENE_STUDIED:
            gene = str(ctext or ccode).strip()
        elif code == SIGNIFICANCE_CODE:
            sig = significance(ccode, ctext)
    if not sig:
        report["skipped"]["genetic results without a clinical significance"] += 1
        return None
    marker = cr.marker_of(gene)
    if not marker:
        report["warnings"]["genetic results for a gene the project has no rule for (stored, not used)"] += 1
    note = ((r.get("note") or [{}])[0]).get("text", "")
    return {"DATE": (r.get("effectiveDateTime") or r.get("issued") or "")[:10], "PATIENT": ref_id(r.get("subject")),
            "GENE": gene, "MARKER": marker, "SIGNIFICANCE": sig, "DESCRIPTION": note or cr.MARKER_NAME.get(marker, "")}


def consent_purpose(r):
    """Which of the project's two purposes a hospital's Consent is about: contact, use_genetic, or '' (another)."""
    words = []
    for cat in (r.get("category") or []) + ((r.get("provision") or {}).get("purpose") or []):
        codings = cat.get("coding") or ([cat] if cat.get("code") or cat.get("display") else [])
        for c in codings:
            if c.get("code") in cr.PURPOSE_COLUMN:
                return c["code"]
            words += [str(c.get("code", "")), str(c.get("display", ""))]
        words.append(str(cat.get("text", "")))
    text = " ".join(words).lower()
    if "genet" in text or "genom" in text:
        return "use_genetic"
    if any(w in text for w in ("contact", "outreach", "remind", "communicat")):
        return "contact"
    return ""


def consent_row(r, report):
    if r.get("status", "active") != "active":                 # drafts, withdrawn forms and errors decide nothing
        report["skipped"][f"Consent {r.get('status')}"] += 1
        return None
    purpose = consent_purpose(r)
    if not purpose:
        report["skipped"]["Consent for a purpose the project does not use"] += 1
        return None
    patient = ref_id(r.get("patient"))
    performer = ref_id((r.get("performer") or [{}])[0])
    return {"DATE": (r.get("dateTime") or "")[:10], "PATIENT": patient, "PURPOSE": purpose,
            "DECISION": "deny" if (r.get("provision") or {}).get("type") == "deny" else "permit", "GIVEN_BY": performer or patient}


# ------------------------------------------------------------------ read files
def resources_in(path):
    """Every resource in a .json (Bundle or one resource) or .ndjson file."""
    with open(path, encoding="utf-8-sig") as fh:
        if path.lower().endswith(".ndjson"):
            for line in fh:
                if line.strip():
                    yield json.loads(line)
            return
        doc = json.load(fh)
    stack = [doc]
    while stack:
        d = stack.pop()
        if d.get("resourceType") == "Bundle":
            stack.extend(e["resource"] for e in reversed(d.get("entry") or []) if e.get("resource"))
        else:
            yield d


def files_in(paths):
    for p in paths:
        if os.path.isdir(p):
            for ext in ("json", "ndjson"):
                yield from sorted(glob.glob(os.path.join(p, "**", f"*.{ext}"), recursive=True))
        else:
            yield p


def import_fhir(paths, out):
    import pandas as pd
    rows = defaultdict(list)
    report = {"files": 0, "read": Counter(), "skipped": Counter(), "warnings": Counter()}
    for f in files_in(paths):
        report["files"] += 1
        for r in resources_in(f):
            kind = r.get("resourceType", "?")
            table = RESOURCE_TABLE.get(kind) or EXTRA_TABLE.get(kind)
            if not table:
                report["skipped"][kind] += 1
                continue
            report["read"][kind] += 1
            if kind == "Patient":
                row = patient_row(r, report)
                if row:
                    rows[table].append(row)
            elif kind == "FamilyMemberHistory":
                rows[table].extend(family_history_rows(r, report))
            elif kind == "Consent":
                row = consent_row(r, report)
                if row:
                    rows[table].append(row)
            elif kind == "Observation" and is_genetic(r):       # a variant found: its own table, never a measurement
                row = genetic_row(r, report)
                if row:
                    rows["genetic_results"].append(row)
            elif kind == "Observation":
                rows[table].extend(observation_rows(r))
            else:
                rows[table].append({"Encounter": encounter_row, "Condition": condition_row, "Immunization": immunization_row,
                                    "Procedure": procedure_row}[kind](r))
    if not rows["patients"]:
        sys.exit("No Patient resources found: nothing to import.")
    os.makedirs(out, exist_ok=True)
    people = {r["Id"] for r in rows["patients"]}
    counts = {}
    for table, cols in {**COLUMNS, **cr.COLUMNS}.items():
        df = pd.DataFrame(rows[table], columns=cols).fillna("")
        if table != "patients":
            orphan = ~df.PATIENT.isin(people)              # records of someone who is not in the import
            if orphan.any():
                report["skipped"][f"{table} for a patient not in the import"] += int(orphan.sum())
                df = df[~orphan]
        df.to_csv(os.path.join(out, f"{table}.csv"), index=False)
        counts[table] = len(df)
    report["tables"] = counts
    report["recognised"] = recognised(out)
    report["systems"] = {t: dict(Counter(r.get("SYSTEM", "") for r in rows[t])) for t in ("conditions", "procedures")}
    with open(os.path.join(out, "import_report.json"), "w", encoding="utf-8") as fh:
        json.dump({k: dict(v) if isinstance(v, Counter) else v for k, v in report.items()}, fh, indent=2)
    return report


def recognised(out):
    """How many imported rows match each field the project looks for (data/reference/code_map.csv)."""
    import pandas as pd
    cm = pd.read_csv(os.path.join(ROOT, "data", "reference", "code_map.csv"), dtype=str).fillna("")
    found = {}
    for r in cm.itertuples():
        f = os.path.join(out, f"{r.file}.csv")
        if os.path.exists(f):
            codes = set(c for c in r.codes.split(";") if c)
            found[r.field] = int(pd.read_csv(f, dtype=str, usecols=["CODE"]).CODE.isin(codes).sum())
    return found


# ------------------------------------------------------------------ sample: data/raw -> one FHIR bundle
def make_sample(n, src, out, seed=7, extras=cr.utils.ADJUSTED):
    """A FHIR R4 bundle for n people from the six tables, written the way a hospital FHIR API would send them (visit
    class as v3 ActCode, blood pressure as one panel), so the import can be shown and tested. Their family history,
    genetic results and consent decisions are added from the tables in `extras` (the adjusted records)."""
    import pandas as pd
    pts = pd.read_csv(os.path.join(src, "patients.csv"), dtype=str).fillna("")
    alive = pts[pts.DEATHDATE == ""]
    chosen = set(alive.sample(min(n, len(alive)), random_state=seed).Id)

    def read(table, keys):
        parts = [ch[ch.PATIENT.isin(chosen)] for ch in pd.read_csv(os.path.join(src, f"{table}.csv"), dtype=str,
                                                                    chunksize=500_000)]
        return pd.concat(parts).fillna("")

    resources =[fhir.patient(p) for p in pts[pts.Id.isin(chosen)].itertuples()]
    resources += [fhir.encounter(e) for e in read("encounters", None).itertuples()]
    resources += [fhir.condition(c, f"cond-{i}") for i, c in enumerate(read("conditions", None).itertuples())]
    resources += fhir.observations(list(read("observations", None).itertuples()))
    resources += [fhir.immunization(m, f"imm-{i}") for i, m in enumerate(read("immunizations", None).itertuples())]
    resources += [fhir.procedure(pr, f"proc-{i}") for i, pr in enumerate(read("procedures", None).itertuples())]
    # family history, genetic results and consent decisions, when the adjusted records hold them
    build = {"family_history": ("fmh", fhir.family_history), "genetic_results": ("gen", fhir.genetic_result),
             "consents": ("consent", fhir.consent)}
    for table, (tag, make) in build.items():
        f = os.path.join(extras or "", f"{table}.csv")
        if extras and os.path.exists(f):
            df = pd.read_csv(f, dtype=str).fillna("")
            resources += [make(r, f"{tag}-{i}") for i, r in enumerate(df[df.PATIENT.isin(chosen)].itertuples())]
    entries = fhir.bundle(resources, kind="collection")["entry"]
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "bundle.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"resourceType": "Bundle", "type": "collection", "total": len(entries), "entry": entries}, fh)
    return path, len(chosen), len(entries)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="FHIR files or folders (.json, .ndjson)")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "imported"), help="folder for the six tables")
    ap.add_argument("--sample", type=int, help="write a sample FHIR bundle of this many people from data/raw")
    ap.add_argument("--sample-out", default=os.path.join(ROOT, "data", "fhir_sample"))
    a = ap.parse_args(argv)
    if a.sample:
        path, people, n = make_sample(a.sample, RAW, a.sample_out)
        print(f"Wrote {path}: {people} people, {n} FHIR resources")
        return
    if not a.paths:
        ap.error("give FHIR files or folders to import (or --sample N)")
    rep = import_fhir(a.paths, a.out)
    print(f"Read {rep['files']} file(s): " + ", ".join(f"{v} {k}" for k, v in sorted(rep["read"].items())))
    if rep["skipped"]:
        print("Skipped: " + ", ".join(f"{v} {k}" for k, v in sorted(rep["skipped"].items())))
    for k, v in rep["warnings"].items():
        print(f"Note: {v} {k}")
    print("Tables: " + ", ".join(f"{k} {v}" for k, v in rep["tables"].items()) + f"  ->  {a.out}")
    missing = [f for f, n in rep["recognised"].items() if n == 0]
    print("Recognised codes: " + ", ".join(f"{f} {n}" for f, n in rep["recognised"].items()))
    if missing:
        print("Not found in this import (add the hospital's codes to data/reference/code_map.csv if they exist): "
              + ", ".join(missing))


if __name__ == "__main__":
    main()
