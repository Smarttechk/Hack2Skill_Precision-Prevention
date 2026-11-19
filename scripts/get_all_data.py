"""
Get every dataset the project needs, with one command.

    python scripts/get_all_data.py              # everything (Java, Synthea, NHANES, reference files)
    python scripts/get_all_data.py --only nhanes reference
    python scripts/get_all_data.py --with-kaggle   # also the optional real no-show dataset

Steps (each one is skipped if its output already exists, so the script can be re-run safely):
  java       portable Java 17 runtime in tools/jre (only if Java is not already installed)
  synthea    download synthea-with-dependencies.jar to tools/
  generate   run Synthea six times (age and sex groups matching the UAE, Step 1.2d)
  combine    stack the six runs into data/raw/*.csv
  codes      list every code Synthea produced and map the ones the project uses
  nhanes     download real NHANES 2017-2018 files and join them (Step 4.10)
  reference  write data/reference/guidelines.csv, templates.csv, reply_keywords.csv
  worldbank  real UAE population by age and sex (World Bank API)
  who        real UAE health indicators (WHO Global Health Observatory API)
  nhanes2    NHANES 2015-2016 and 2021-2023 (real)
  growth     WHO child growth tables (real)
  geo        boundaries of the 7 emirates (real map file)
  clinics    hospitals, clinics and doctors in Abu Dhabi emirate (OpenStreetMap, real)
  places / calendar / adjust   Abu Dhabi places, UAE calendar, and the UAE adjustment of the synthetic data
  records    the hospital records database data/records/hospital.db (scripts/build_records_db.py)
  features   preprocessing: patient feature table and risk training set (scripts/preprocess.py)
  kaggle     optional real no-show dataset (needs a Kaggle account), only with --with-kaggle
  verify     check everything and write data/DATA_MANIFEST.csv and data/DATA_REPORT.md
"""
import argparse, csv, glob, hashlib, json, os, re, shutil, subprocess, sys, time, urllib.request, zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
DATA = os.path.join(ROOT, "data")
# the six record tables; PP_RAW_DIR points the pipeline at an import instead (scripts/fhir_import.py)
RAW = os.path.join(ROOT, os.environ["PP_RAW_DIR"]) if os.environ.get("PP_RAW_DIR") else os.path.join(DATA, "raw")
RUNS = os.path.join(RAW, "synthea_runs")
REAL = os.path.join(DATA, "real")
REF = os.path.join(DATA, "reference")
UA = {"User-Agent": "hack2skill-data-setup/1.0"}

SYNTHEA_URL = "https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar"
JRE_URL = "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jre/hotspot/normal/eclipse"
NHANES_BASE = "https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/"
NHANES_FILES = {   # file: what it holds
    "DEMO_J": "age, sex, ethnicity, survey weights",
    "BMX_J": "body measurements (BMI, waist)",
    "BPX_J": "blood pressure readings",
    "GHB_J": "HbA1c (glycohemoglobin)",
    "DIQ_J": "diabetes questions (told diabetes, prediabetes, insulin)",
    "MCQ_J": "medical conditions incl. family history of diabetes",
    "SMQ_J": "smoking",
    "PAQ_J": "physical activity",
    "TCHOL_J": "total cholesterol",
    "HDL_J": "HDL cholesterol",
    "BPQ_J": "blood pressure and cholesterol questions",
    "HIQ_J": "health insurance",
}

# Age and sex groups (Step 1.2d), 5,000 people in total.
# Shares are REAL UAE figures for 2024 from the World Bank API (data/real/worldbank/uae_target_bands.csv).
# For Abu Dhabi only, replace with Statistics Centre Abu Dhabi figures, delete data/raw/synthea_runs and re-run.
GROUPS = [  # name, seed, people, sex (None = both), ages
    ("m_18_44", 1, 2083, "M", "18-44"),     # 41.7%
    ("f_18_44", 2, 1027, "F", "18-44"),     # 20.5%
    ("child_0_17", 3, 943, None, "0-17"),   # 18.9% (M 9.6%, F 9.3%)
    ("m_45_64", 4, 582, "M", "45-64"),      # 11.7%
    ("f_45_64", 6, 276, "F", "45-64"),      # 5.5%
    ("older_65_90", 5, 89, None, "65-90"),  # 1.8% (M 1.0%, F 0.8%)
]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def download(url, path, label):
    """Download with a progress line. Writes to a .part file first so a failed download never looks complete."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        log(f"{label}: already present, skipping ({os.path.getsize(path) / 1e6:.1f} MB)")
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        done, last = 0, 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total and (done - last > 20e6 or done == total):
                log(f"{label}: {done / 1e6:.0f} / {total / 1e6:.0f} MB")
                last = done
    os.replace(tmp, path)
    log(f"{label}: saved to {os.path.relpath(path, ROOT)}")
    return path


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------ java
def find_java():
    exe = "java.exe" if os.name == "nt" else "java"
    local = sorted(glob.glob(os.path.join(TOOLS, "*", "bin", exe)))   # portable runtime unzipped into tools/
    if local:
        return local[-1]
    found = shutil.which("java")
    if found:
        out = subprocess.run([found, "-version"], capture_output=True, text=True)
        m = re.search(r'version "(\d+)', out.stderr + out.stdout)
        if m and int(m.group(1)) >= 17:
            return found
        log(f"Java on PATH is too old ({m.group(1) if m else 'unknown'}); Synthea needs 17 or newer")
    return None


def step_java():
    j = find_java()
    if j:
        log(f"java: using {j}")
        return j
    if os.name != "nt":
        sys.exit("Install Java 17 or newer (for example Eclipse Temurin) and run again.")
    z = download(JRE_URL, os.path.join(TOOLS, "temurin-jre17.zip"), "java runtime (Temurin 17)")
    with zipfile.ZipFile(z) as zf:
        zf.extractall(TOOLS)          # kept under its own folder name (renaming can be blocked by antivirus)
    j = find_java()
    if not j:
        sys.exit("Java runtime was unzipped but java.exe was not found under tools/")
    log(f"java: portable runtime ready at {os.path.relpath(j, ROOT)} (nothing installed system-wide)")
    return j


# ------------------------------------------------------------------ synthea
def jar_path():
    return os.path.join(TOOLS, "synthea-with-dependencies.jar")


def step_synthea():
    download(SYNTHEA_URL, jar_path(), "synthea jar")


UNUSED_SYNTHEA = {"claims_transactions.csv", "claims.csv", "payer_transitions.csv", "supplies.csv", "imaging_studies.csv"}


def already_combined():
    return os.path.exists(os.path.join(RAW, "patients.csv")) and not os.environ.get("FORCE_SYNTHEA")


def step_generate():
    if already_combined():
        log("generate: data/raw already has the combined patients, skipping (set FORCE_SYNTHEA=1 to rebuild)")
        return
    java = find_java() or step_java()
    if not os.path.exists(jar_path()):
        step_synthea()
    os.makedirs(RUNS, exist_ok=True)
    for name, seed, people, sex, ages in GROUPS:
        out = os.path.join(RUNS, name)
        if os.path.exists(os.path.join(out, "csv", "patients.csv")):
            log(f"generate {name}: already done, skipping")
            continue
        if os.path.exists(out):
            shutil.rmtree(out)                       # remove a half-finished run
        cmd = [java, "-Xmx4g", "-jar", jar_path(), "-s", str(seed), "-cs", str(seed), "-p", str(people), "-a", ages]
        if sex:
            cmd += ["-g", sex]
        cmd += [f"--exporter.baseDirectory={out}", "--exporter.csv.export=true",
                "--exporter.fhir.export=false", "--exporter.hospital.fhir.export=false",
                "--exporter.practitioner.fhir.export=false"]
        log(f"generate {name}: {people} people, ages {ages}, sex {sex or 'both'} (a few minutes)")
        t0 = time.time()
        with open(os.path.join(RUNS, f"{name}.log"), "w", encoding="utf-8") as lf:
            r = subprocess.run(cmd, cwd=TOOLS, stdout=lf, stderr=subprocess.STDOUT)
        if r.returncode != 0 or not os.path.exists(os.path.join(out, "csv", "patients.csv")):
            sys.exit(f"Synthea failed for {name}; see data/raw/synthea_runs/{name}.log")
        log(f"generate {name}: done in {time.time() - t0:.0f} s")


def step_combine():
    if already_combined():
        log("combine: data/raw already combined, skipping")
        return
    run_dirs = [os.path.join(RUNS, g[0], "csv") for g in GROUPS]
    missing = [d for d in run_dirs if not os.path.exists(os.path.join(d, "patients.csv"))]
    if missing:
        sys.exit(f"Run the generate step first; missing {missing}")
    names = sorted(set.intersection(*[{os.path.basename(p) for p in glob.glob(os.path.join(d, "*.csv"))} for d in run_dirs])
                   - UNUSED_SYNTHEA)                           # billing files the project never uses
    os.makedirs(RAW, exist_ok=True)
    for fname in names:
        dest = os.path.join(RAW, fname)
        header = None
        with open(dest, "w", encoding="utf-8", newline="") as out:
            for (gname, *_), d in zip(GROUPS, run_dirs):
                with open(os.path.join(d, fname), encoding="utf-8") as f:
                    h = f.readline()
                    if header is None:
                        header = h
                        out.write(h.rstrip("\r\n") + (",SYNTHEA_GROUP\n" if fname == "patients.csv" else "\n"))
                    elif h != header:
                        sys.exit(f"Column mismatch in {fname} for group {gname}")
                    if fname == "patients.csv":
                        for line in f:
                            out.write(line.rstrip("\r\n") + f",{gname}\n")
                    else:
                        shutil.copyfileobj(f, out, 1 << 20)
        log(f"combine: data/raw/{fname} ({os.path.getsize(dest) / 1e6:.1f} MB)")
    shutil.rmtree(RUNS, ignore_errors=True)                    # the six runs are now combined; free about 2 GB
    log("combine: removed data/raw/synthea_runs (duplicates of the combined files)")


# ------------------------------------------------------------------ codes
CODE_FILES = ["conditions", "observations", "encounters", "procedures", "immunizations", "medications", "careplans"]
# field used in the guide -> (file, regex on DESCRIPTION)
CODE_RULES = {
    "hba1c":               ("observations", r"Hemoglobin A1c"),
    "bmi":                 ("observations", r"^Body mass index \(BMI\) \[Ratio\]"),
    "bmi_percentile":      ("observations", r"Body mass index \(BMI\) \[Percentile\]"),
    "weight_percentile":   ("observations", r"Weight-for-length Per age and sex|Body weight.*Percentile"),
    "systolic_bp":         ("observations", r"^Systolic Blood Pressure"),
    "diastolic_bp":        ("observations", r"^Diastolic Blood Pressure"),
    "total_cholesterol":   ("observations", r"^Cholesterol \[Mass/volume\] in Serum or Plasma|Total Cholesterol"),
    "ldl":                 ("observations", r"Low Density Lipoprotein Cholesterol|Cholesterol in LDL"),
    "hdl":                 ("observations", r"High Density Lipoprotein Cholesterol|Cholesterol in HDL"),
    "glucose":             ("observations", r"^Glucose \[Mass/volume\]"),
    "smoking_status":      ("observations", r"Tobacco smoking status"),
    "depression_phq":      ("observations", r"PHQ-2|PHQ-9|Patient Health Questionnaire"),
    "well_child_visit":    ("encounters",   r"Well child visit"),
    "general_exam":        ("encounters",   r"General examination of patient|Check up"),
    "prenatal_visit":      ("encounters",   r"[Pp]renatal"),
    "diabetes":            ("conditions",   r"^Diabetes|Diabetes mellitus type 2"),
    "prediabetes":         ("conditions",   r"Prediabetes"),
    "hypertension":        ("conditions",   r"[Hh]ypertension"),
    "obesity":             ("conditions",   r"Body mass index 30\+|[Oo]besity"),
    "pregnancy":           ("conditions",   r"[Pp]regnancy"),
    "asthma":              ("conditions",   r"[Aa]sthma"),
    "hyperlipidemia":      ("conditions",   r"[Hh]yperlipidemia|[Hh]ypertriglyceridemia"),
    "developmental_delay": ("conditions",   r"[Dd]evelopmental|[Aa]utism|[Aa]ttention deficit"),
    "cervical_screening":  ("procedures",   r"[Cc]ervical|Papanicolaou|human papilloma|Cytopathology procedure.*genital"),
    "breast_screening":    ("procedures",   r"[Mm]ammogra"),
    "bowel_screening":     ("procedures",   r"[Cc]olonoscopy|occult blood|[Ss]creening for malignant neoplasm of colon"),
    "depression_screen":   ("procedures",   r"[Dd]epression screening|Assessment of depression"),
    "mmr_vaccine":         ("immunizations", r"MMR"),
    "hep_b_vaccine":       ("immunizations", r"Hep B"),
    "hpv_vaccine":         ("immunizations", r"HPV"),
}


def step_codes():
    import pandas as pd
    rows = []
    for name in CODE_FILES:
        path = os.path.join(RAW, f"{name}.csv")
        if not os.path.exists(path):
            continue
        cols = pd.read_csv(path, nrows=0).columns
        use = [c for c in ("CODE", "DESCRIPTION", "ENCOUNTERCLASS") if c in cols]
        counts = None
        for chunk in pd.read_csv(path, usecols=use, dtype=str, chunksize=500_000):
            c = chunk.fillna("").groupby(use).size()
            counts = c if counts is None else counts.add(c, fill_value=0)
        t = counts.astype(int).reset_index(name="count")
        t.insert(0, "file", name)
        rows.append(t)
    found = pd.concat(rows, ignore_index=True).sort_values(["file", "count"], ascending=[True, False])
    os.makedirs(REF, exist_ok=True)
    found.to_csv(os.path.join(REF, "synthea_codes_found.csv"), index=False)
    out = []
    for field, (fname, rx) in CODE_RULES.items():
        m = found[(found.file == fname) & found.DESCRIPTION.str.contains(rx, regex=True, na=False)]
        out.append({"field": field, "file": fname, "rule": rx, "codes": ";".join(m.CODE.unique()),
                    "descriptions": " | ".join(m.DESCRIPTION.unique()[:6]), "rows": int(m["count"].sum())})
    pd.DataFrame(out).to_csv(os.path.join(REF, "code_map.csv"), index=False)
    log(f"codes: {len(found)} distinct codes listed; {sum(1 for o in out if o['rows'])}/{len(out)} project fields matched "
        f"(see data/reference/code_map.csv)")


# ------------------------------------------------------------------ nhanes
def step_nhanes():
    import pandas as pd
    folder = os.path.join(REAL, "nhanes")
    for f, what in NHANES_FILES.items():
        download(NHANES_BASE + f + ".xpt", os.path.join(folder, f + ".xpt"), f"nhanes {f} ({what})")
    parts = []
    for f in NHANES_FILES:
        d = pd.read_sas(os.path.join(folder, f + ".xpt"), format="xport")
        d.columns = d.columns.str.upper()
        d = d.drop_duplicates("SEQN").set_index("SEQN")
        if parts:
            d = d.drop(columns=[c for c in d.columns if c in set().union(*[p.columns for p in parts])])
        parts.append(d)
    joined = parts[0].join(parts[1:], how="left")
    joined.to_csv(os.path.join(folder, "nhanes_2017_2018_joined.csv"))
    log(f"nhanes: joined table {joined.shape[0]} people x {joined.shape[1]} columns -> data/real/nhanes/nhanes_2017_2018_joined.csv")


# ------------------------------------------------------------------ reference files
BF = "AAP Bright Futures"
BF_V = "Bright Futures Guidelines, 4th edition (2017), with the AAP periodicity schedule"
GUIDELINES = [
    # check, pathway, sex, min_age_months, max_age_months, at_ages_months, condition, every_months, source, edition
    ("newborn_screen", "Well-Child Visit", "", 0, 1, "", "", "once", f"{BF}; UAE newborn screening programme",
     f"{BF_V}; MOHAP national newborn screening programme"),
    ("well_child_visit", "Well-Child Visit", "", 0, 11, "", "", "3", BF, BF_V),
    ("well_child_visit", "Well-Child Visit", "", 12, 71, "", "", "12", BF, BF_V),
    ("well_child_visit", "Well-Child Visit", "", 72, 215, "", "", "12", BF, BF_V),
    ("developmental_screen", "Well-Child Visit", "", 9, 35, "9;18;30", "", "once at each age", BF, BF_V),
    ("autism_screen", "Well-Child Visit", "", 18, 35, "18;24", "", "once at each age", BF, BF_V),
    ("mmr_vaccine", "Well-Child Visit", "", 12, 24, "", "not yet received", "once", "UAE national immunisation schedule",
     "MOHAP national immunisation programme schedule"),
    ("vision_hearing_screen", "Well-Child Visit", "", 36, 71, "", "", "12", BF, BF_V),
    ("growth_bmi_check", "Well-Child Visit", "", 24, 215, "", "", "12", BF, BF_V),
    ("adolescent_depression_screen", "Well-Child Visit", "", 144, 215, "", "", "12", f"{BF}; USPSTF",
     f"{BF_V}; USPSTF 2022 (depression and suicide risk in children and adolescents)"),
    ("family_nutrition_programme", "Preventive intervention", "", 72, 215, "", "BMI at or above the 95th percentile", "once",
     "USPSTF; AAP", "USPSTF 2024 (high BMI in children and adolescents: intensive behavioural intervention); "
     "AAP clinical practice guideline on obesity 2023"),
    ("bp_check", "Adult comprehensive screening", "", 216, 1440, "", "", "12", "USPSTF", "USPSTF 2021 (hypertension in adults: screening)"),
    ("diabetes_screen", "Adult comprehensive screening", "", 216, 1440, "", "age 35 or over, or BMI 25 or over", "36", "USPSTF; ADA",
     "USPSTF 2021 (prediabetes and type 2 diabetes: screening); ADA Standards of Care in Diabetes 2025"),
    ("hba1c_test", "Adult comprehensive screening", "", 0, 1440, "", "has diabetes", "6", "ADA", "ADA Standards of Care in Diabetes 2025"),
    ("lipid_test", "Adult comprehensive screening", "", 480, 1440, "", "or earlier with family or genetic risk", "60", "USPSTF",
     "USPSTF 2022 (statin use for primary prevention of cardiovascular disease)"),
    ("obesity_check", "Adult comprehensive screening", "", 216, 1440, "", "", "12", "USPSTF",
     "USPSTF 2018 (weight loss to prevent obesity-related illness in adults)"),
    ("cervical_screening", "Adult comprehensive screening", "F", 300, 780, "", "", "36 (smear) or 60 (HPV test)",
     "USPSTF; Department of Health Abu Dhabi", "USPSTF 2018 (cervical cancer: screening); DoH Abu Dhabi cancer screening standard"),
    ("breast_screening", "Adult comprehensive screening", "F", 480, 828, "", "", "24", "USPSTF; Department of Health Abu Dhabi",
     "USPSTF 2024 (breast cancer: screening, every 2 years from 40); DoH Abu Dhabi cancer screening standard"),
    ("bowel_screening", "Adult comprehensive screening", "", 540, 900, "", "", "12 (stool test)", "USPSTF; Department of Health Abu Dhabi",
     "USPSTF 2021 (colorectal cancer: screening from 45); DoH Abu Dhabi cancer screening standard"),
    ("depression_screen", "Adult comprehensive screening", "", 216, 1440, "", "", "12", "USPSTF", "USPSTF 2023 (depression in adults: screening)"),
    ("hepatitis_b_c_screen", "Adult comprehensive screening", "", 216, 1440, "", "not yet tested", "once", "USPSTF",
     "USPSTF 2020 (hepatitis B and hepatitis C: screening)"),
    ("smoking_check", "Adult comprehensive screening", "", 180, 1440, "", "", "12", "USPSTF",
     "USPSTF 2021 (tobacco smoking cessation in adults)"),
    ("lung_screening", "Adult comprehensive screening", "", 600, 960, "", "heavy smoker", "12", "USPSTF",
     "USPSTF 2021 (lung cancer: screening, 20 pack-years)"),
    ("osteoporosis_screen", "Adult comprehensive screening", "F", 780, 1440, "", "", "24", "USPSTF",
     "USPSTF 2025 (osteoporosis to prevent fractures: screening)"),
    ("prediabetes_programme", "Preventive intervention", "", 216, 1440, "", "HbA1c 5.7 to 6.4% (prediabetes)", "once", "ADA; CDC",
     "ADA Standards of Care in Diabetes 2025 (section 3: refer to a diabetes prevention programme); CDC National Diabetes Prevention Program"),
    ("weight_support", "Preventive intervention", "", 216, 1440, "", "BMI 30 or more", "once", "USPSTF",
     "USPSTF 2018 (intensive behavioural weight-loss interventions for adults with obesity)"),
    ("stop_smoking_service", "Preventive intervention", "", 216, 1440, "", "current smoker", "once", "USPSTF",
     "USPSTF 2021 (behavioural support and medication for tobacco cessation)"),
    ("premarital_screening", "Genetic pathway", "", 216, 600, "", "before marriage", "once", "UAE premarital screening programme",
     "UAE premarital screening programme (MOHAP)"),
    ("carrier_counselling", "Genetic pathway", "", 192, 1440, "", "carrier of a haemoglobin disorder (with genetic consent)", "once",
     "UAE premarital screening programme", "UAE premarital screening programme (MOHAP): counselling of carriers before marriage"),
    ("genetic_counselling", "Genetic pathway", "", 216, 1440, "", "pathogenic BRCA or Lynch variant (with genetic consent)", "once",
     "USPSTF; NCCN", "USPSTF 2019 (BRCA-related cancer: risk assessment and genetic counselling); "
     "NCCN guidelines on genetic/familial high-risk assessment"),
]

ADULT = "Hello. You are due for a routine {what}. {option}. Reply BOOK, INFO, LATER or STOP."
CHILD = ("Hello. {child} is due for a routine {what}. It helps make sure {child} is growing and developing well. "
         "{option}. Reply BOOK, INFO, LATER or STOP.")
PLAIN = {   # check -> plain words used in the message (never a disease, gene or risk score)
    "newborn_screen": "newborn check", "well_child_visit": "child health check",
    "developmental_screen": "development check", "autism_screen": "development check",
    "mmr_vaccine": "vaccination", "vision_hearing_screen": "eye and hearing check",
    "growth_bmi_check": "growth check", "adolescent_depression_screen": "wellbeing check",
    "bp_check": "blood pressure check", "diabetes_screen": "blood sugar check", "hba1c_test": "blood sugar test",
    "lipid_test": "blood test", "obesity_check": "health check", "cervical_screening": "cervical screening",
    "breast_screening": "breast screening", "bowel_screening": "bowel screening", "depression_screen": "wellbeing check",
    "hepatitis_b_c_screen": "blood test", "smoking_check": "health check", "lung_screening": "lung check",
    "osteoporosis_screen": "bone health check", "premarital_screening": "premarital health check",
    "cholesterol_test": "blood test", "genetic_counselling": "appointment with our specialist team",
    "carrier_counselling": "appointment with our specialist team", "blood_sugar_test": "blood sugar test",
    "basic_health_check": "health check", "vaccine_catch_up": "vaccination",
    "prediabetes_programme": "healthy lifestyle programme", "weight_support": "healthy weight support",
    "stop_smoking_service": "stop-smoking support", "family_nutrition_programme": "family nutrition programme",
    "results_review": "results appointment",
}
CHILD_CHECKS = {"newborn_screen", "well_child_visit", "developmental_screen", "autism_screen", "mmr_vaccine", "vaccine_catch_up",
                "vision_hearing_screen", "growth_bmi_check", "cholesterol_test", "adolescent_depression_screen",
                "basic_health_check", "blood_sugar_test", "family_nutrition_programme", "results_review"}
CHILD_ONLY = {"newborn_screen", "developmental_screen", "autism_screen", "mmr_vaccine", "vaccine_catch_up",
              "vision_hearing_screen", "adolescent_depression_screen", "family_nutrition_programme"}
# the same short message in the five languages of the population (drafts: native speakers and the clinical team must
# check them before use); none names a disease, gene or risk, and the reply words match REPLY_WORDS below
OTHER = {
    "Arabic": ("مرحباً. حان موعد فحصك الصحي الدوري. {option}. للرد: حجز، معلومات، لاحقاً، أو توقف.",
               "مرحباً. حان موعد الفحص الصحي الدوري لطفلك. يساعد هذا الفحص على التأكد من أن طفلك ينمو ويتطور بشكل جيد. "
               "{option}. للرد: حجز، معلومات، لاحقاً، أو توقف."),
    "Urdu": ("السلام علیکم۔ آپ کے باقاعدہ طبی معائنے کا وقت ہو گیا ہے۔ {option}۔ جواب دیں: بک، معلومات، بعد میں، یا بند۔",
             "السلام علیکم۔ آپ کے بچے کے باقاعدہ طبی معائنے کا وقت ہو گیا ہے۔ اس سے معلوم ہوتا ہے کہ بچہ ٹھیک سے بڑھ رہا ہے۔ "
             "{option}۔ جواب دیں: بک، معلومات، بعد میں، یا بند۔"),
    "Hindi": ("नमस्ते। आपकी नियमित स्वास्थ्य जाँच का समय हो गया है। {option}। जवाब दें: बुक, जानकारी, बाद में, या बंद।",
              "नमस्ते। आपके बच्चे की नियमित स्वास्थ्य जाँच का समय हो गया है। इससे पता चलता है कि बच्चा ठीक से बढ़ रहा है। "
              "{option}। जवाब दें: बुक, जानकारी, बाद में, या बंद।"),
    "Tagalog": ("Kumusta po. Oras na para sa inyong regular na health check. {option}. Sumagot ng BOOK, INFO, LATER o STOP.",
                "Kumusta po. Oras na para sa regular na health check ng inyong anak. Tinitiyak nito na maayos ang paglaki "
                "ng inyong anak. {option}. Sumagot ng BOOK, INFO, LATER o STOP."),
}
REPLY_WORDS = [  # keyword, English, Arabic, Urdu, Hindi, Tagalog (drafts; native speakers must check them)
    ("BOOK", "book;yes", "حجز;نعم", "بک;ہاں;جی ہاں", "बुक;हाँ;हां", "book;oo;sige"),
    ("INFO", "info", "معلومات", "معلومات", "जानकारी", "info;impormasyon"),
    ("LATER", "later", "لاحقاً;لاحقا", "بعد میں", "बाद में", "later;mamaya"),
    ("STOP", "stop;no", "توقف;إيقاف;لا", "بند;رکیں;نہیں", "बंद;रोकें;नहीं", "stop;tigil;huwag"),
    ("START", "start", "ابدأ;ابدأ من جديد", "شروع", "शुरू", "start;simula"),
]
BLOCKED = ["gene", "genetic", "brca", "cancer", "risk score", "%", "carrier"]


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:      # utf-8-sig so Excel shows Arabic correctly
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def step_reference():
    # intervals the rules use come from config/clinical_rules.json (one source); the rest of each row is reference text
    sys.path.insert(0, ROOT)
    from agents.care_gap import RECALL
    rows_g = []
    for g in GUIDELINES:
        every = str(RECALL[g[0]]) if g[0] in RECALL and not (g[0] == "well_child_visit" and g[4] < 12) else g[7]
        # verified = whether a clinician has checked this row against the named edition for local use
        rows_g.append((*g[:7], every, g[8], g[9], "edition named; local clinical sign-off pending"))
    write_csv(os.path.join(REF, "guidelines.csv"),
              ["check", "pathway", "sex", "min_age_months", "max_age_months", "at_ages_months", "condition",
               "every_months", "source", "version", "verified"], rows_g)
    rows = []
    for check, what in PLAIN.items():
        child_check, adult_check = check in CHILD_CHECKS, check not in CHILD_ONLY
        if child_check:
            rows.append((check, "guardian", "English", CHILD.replace("{what}", what), "draft: clinical team to approve"))
            rows += [(check, "guardian", lang, t[1], "draft: native speaker and clinical team to check") for lang, t in OTHER.items()]
        if adult_check:
            rows.append((check, "adult", "English", ADULT.replace("{what}", what), "draft: clinical team to approve"))
            rows += [(check, "adult", lang, t[0], "draft: native speaker and clinical team to check") for lang, t in OTHER.items()]
    bad = [r for r in rows if any(b in r[3].lower() for b in BLOCKED) or len(r[3]) > 320]
    assert not bad, f"templates fail the safety check: {bad[:2]}"
    write_csv(os.path.join(REF, "templates.csv"), ["check", "audience", "language", "text", "status"], rows)
    write_csv(os.path.join(REF, "reply_keywords.csv"),
              ["keyword", "English", "Arabic", "Urdu", "Hindi", "Tagalog", "status"],
              [(*w, "drafts: native speakers to check") for w in REPLY_WORDS])
    log(f"reference: guidelines.csv ({len(GUIDELINES)} rules), templates.csv ({len(rows)} templates, "
        f"{len(OTHER) + 1} languages), reply_keywords.csv")


# ------------------------------------------------------------------ kaggle (optional)
def step_kaggle():
    folder = os.path.join(REAL, "noshow")
    target = os.path.join(folder, "KaggleV2-May-2016.csv")
    if os.path.exists(target):
        log("kaggle: already present, skipping")
        return
    cred = os.path.join(os.path.expanduser("~"), ".kaggle", "kaggle.json")
    if not os.path.exists(cred) and not os.environ.get("KAGGLE_USERNAME"):
        log("kaggle: skipped. To add the real no-show dataset: create a free Kaggle account, download an API token "
            "(Settings > API > Create New Token) to ~/.kaggle/kaggle.json, run 'pip install kaggle', then run "
            "this script with --with-kaggle. Dataset: joniarroba/noshowappointments")
        return
    os.makedirs(folder, exist_ok=True)
    subprocess.run([sys.executable, "-m", "kaggle", "datasets", "download", "-d", "joniarroba/noshowappointments",
                    "-p", folder, "--unzip"], check=True)
    log(f"kaggle: saved to {os.path.relpath(folder, ROOT)}")


# ------------------------------------------------------------------ verify
def count_rows(path):
    with open(path, "rb") as f:
        return sum(1 for _ in f) - 1


def step_verify():
    import pandas as pd
    report, manifest, problems = [], [], []
    sources = {"raw": "Synthea (synthetic, generated locally)", "nhanes": "CDC NHANES 2017-2018 (real, public)",
               "nhanes_2015": "CDC NHANES 2015-2016 (real, public)", "nhanes_2021": "CDC NHANES 2021-2023 (real, public)",
               "worldbank": "World Bank API (real)", "who": "WHO Global Health Observatory API (real)",
               "growth": "WHO / CDC growth references (real)", "geo": "geoBoundaries / OpenStreetMap (ODbL)",
               "osm": "OpenStreetMap contributors via Overpass API (ODbL)",
               "calendar": "Aladhan API, Umm al-Qura calendar (real)",
               "processed": "Synthea adjusted to the UAE by scripts/uae_adjust.py (synthetic)",
               "reference": "Written by get_all_data.py (drafts to check)", "noshow": "Kaggle joniarroba/noshowappointments (real)"}
    for path in sorted(glob.glob(os.path.join(DATA, "**", "*.*"), recursive=True)):
        if "synthea_runs" in path or path.endswith((".log", ".part")) or os.path.basename(path).startswith("DATA_"):
            continue
        kind = next((v for k, v in sources.items() if os.sep + k + os.sep in path), "")
        rows = count_rows(path) if path.endswith(".csv") else ""
        manifest.append({"file": os.path.relpath(path, ROOT).replace("\\", "/"), "rows": rows,
                         "size_mb": round(os.path.getsize(path) / 1e6, 2), "source": kind,
                         "sha256": sha256(path) if os.path.getsize(path) < 3e9 else ""})
    for t in ["tools/synthea-with-dependencies.jar"]:
        p = os.path.join(ROOT, t)
        if os.path.exists(p):
            manifest.append({"file": t, "rows": "", "size_mb": round(os.path.getsize(p) / 1e6, 1),
                             "source": SYNTHEA_URL + f" (downloaded {datetime.fromtimestamp(os.path.getmtime(p)):%Y-%m-%d})",
                             "sha256": sha256(p)})
    pd.DataFrame(manifest).to_csv(os.path.join(DATA, "DATA_MANIFEST.csv"), index=False)

    pp = os.path.join(RAW, "patients.csv")
    if os.path.exists(pp):
        pts = pd.read_csv(pp, dtype=str)
        alive = pts[pts.DEATHDATE.isna()]
        sys.path.insert(0, ROOT)
        import utils
        age = ((utils.run_date() - pd.to_datetime(alive.BIRTHDATE)).dt.days / 365.25)
        report += ["## Synthea patients", f"- People: {len(pts)} ({len(alive)} alive, {len(pts) - len(alive)} deceased)",
                   "- Alive people by group:"]
        for g, d in alive.assign(age=age.values).groupby("SYNTHEA_GROUP"):
            report.append(f"  - {g}: {len(d)} people, ages {d.age.min():.0f} to {d.age.max():.0f}, "
                          f"{(d.GENDER == 'M').mean():.0%} male")
        ids = set(pts.Id)
        enc = pd.read_csv(os.path.join(RAW, "encounters.csv"), usecols=["PATIENT", "ENCOUNTERCLASS"], dtype=str)
        orphan = (~enc.PATIENT.isin(ids)).sum()
        if orphan:
            problems.append(f"{orphan} encounters point to unknown patients")
        report.append(f"- Encounters: {len(enc):,} ({', '.join(f'{k} {v:,}' for k, v in enc.ENCOUNTERCLASS.value_counts().head(5).items())})")
        if len(alive) < 4500:
            problems.append(f"only {len(alive)} living patients (expected about 5000)")
        cm = os.path.join(REF, "code_map.csv")
        if os.path.exists(cm):
            m = pd.read_csv(cm)
            missing = m[m.rows == 0].field.tolist()
            report.append(f"- Project fields found in Synthea codes: {(m.rows > 0).sum()} of {len(m)}"
                          + (f" (not produced by this Synthea version: {', '.join(missing)})" if missing else ""))
    nj = os.path.join(REAL, "nhanes", "nhanes_2017_2018_joined.csv")
    if os.path.exists(nj):
        n = pd.read_csv(nj, usecols=["SEQN", "RIDAGEYR", "LBXGH", "DIQ010"])
        adults = n[(n.RIDAGEYR >= 20) & n.LBXGH.notna() & n.DIQ010.isin([2, 3])]
        report += ["", "## NHANES (real data)", f"- People: {len(n):,}",
                   f"- Adults 20+ with HbA1c who were never told they have diabetes: {len(adults):,}",
                   f"- Of these, HbA1c 6.5% or more (undiagnosed diabetes): {(adults.LBXGH >= 6.5).sum():,} "
                   f"({(adults.LBXGH >= 6.5).mean():.1%}, unweighted)"]
        if len(n) < 9000:
            problems.append("NHANES has fewer people than expected (9,254)")
    report += extra_report(CTX)
    adj = os.path.join(DATA, "processed", "uae_synthea", "adjust_summary.json")
    if os.path.exists(adj):
        report += ["", "## Synthetic data adjusted to the UAE (data/processed/uae_synthea; use this, not data/raw)"]
        report += [f"- {k}: {v}" for k, v in json.load(open(adj, encoding="utf-8")).items()]
    else:
        problems.append("UAE adjustment not run yet: python scripts/get_all_data.py --only places calendar adjust")
    fs = os.path.join(DATA, "processed", "features", "preprocess_summary.json")
    if os.path.exists(fs):
        report += ["", "## Preprocessed features (data/processed/features)"]
        report += [f"- {k}: {v}" for k, v in json.load(open(fs, encoding="utf-8")).items()]
    else:
        problems.append("Features not built yet: python scripts/get_all_data.py --only features")
    report = ["# Data report", f"Created {datetime.now():%Y-%m-%d %H:%M}", ""] + report + \
             ["", "## Problems", *(["- " + p for p in problems] or ["- None found"])]
    open(os.path.join(DATA, "DATA_REPORT.md"), "w", encoding="utf-8").write("\n".join(report) + "\n")
    print("\n".join(report))
    return not problems


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extra_sources import EXTRA_STEPS, extra_report          # World Bank, WHO, more NHANES, growth, maps, clinics
from uae_adjust import ADJUST_STEPS                           # places, calendar, UAE adjustment of the synthetic data
CTX = {"REAL": REAL, "ROOT": ROOT, "RAW": RAW, "DATA": DATA, "log": log, "download": download}

STEPS = {"java": step_java, "synthea": step_synthea, "generate": step_generate, "combine": step_combine,
         "codes": step_codes, "nhanes": step_nhanes, "reference": step_reference,
         **{k: (lambda f=f: f(CTX)) for k, f in EXTRA_STEPS.items()},
         **{k: (lambda f=f: f(CTX)) for k, f in ADJUST_STEPS.items()},
         "records": lambda: __import__("build_records_db").build(),   # the hospital records database (records.py)
         "features": lambda: __import__("preprocess").main(),     # Step 1.3 feature table + risk training set
         "kaggle": step_kaggle, "verify": step_verify}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=STEPS, help="run only these steps")
    ap.add_argument("--with-kaggle", action="store_true", help="also get the optional real no-show dataset")
    a = ap.parse_args()
    order = a.only or [s for s in STEPS if s != "kaggle" or a.with_kaggle]
    for s in order:
        log(f"=== {s} ===")
        result = STEPS[s]()
        if s == "verify" and result is False:
            sys.exit(1)
    log("all done")
