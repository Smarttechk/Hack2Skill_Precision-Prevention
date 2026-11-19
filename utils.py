"""Shared helpers used by the agents, models, evaluation and dashboard."""
import json
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
FEATURES = os.path.join(DATA, "processed", "features")
ADJUSTED = os.path.join(DATA, "processed", "uae_synthea")
REFERENCE = os.path.join(DATA, "reference")
REAL = os.path.join(DATA, "real")
OUTPUTS = os.path.join(ROOT, "outputs")
MODELS = os.path.join(OUTPUTS, "models")

MONTH = 30.4375


def ensure_dirs():
    for d in (OUTPUTS, MODELS):
        os.makedirs(d, exist_ok=True)


def months_between(earlier, later):
    """Whole months between two dates; None if the earlier date is missing."""
    if earlier is None or pd.isna(earlier):
        return None
    return (pd.Timestamp(later) - pd.Timestamp(earlier)).days / MONTH


def wilson(k, n, z=1.96):
    """95% range for a rate (Step 10.6). A wide range warns that a group is small."""
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def suppress(t, min_size=None, count_col="count"):
    """Hide groups under min_size, plus the next smallest when only one would be hidden (Step 10.6)."""
    t = t.copy()
    sg = _small_groups()
    min_size = sg["hide_under"] if min_size is None else min_size
    small = t[count_col] < min_size
    if small.sum() == 1 and len(t) > 1:
        small[t.loc[~small, count_col].idxmin()] = True
    out = t.astype(object)
    out.loc[small, :] = "Too few to report"
    n = sg["not_reliable_under"]
    out["reliable"] = np.where(small, "", np.where(t[count_col] < n, f"Not reliable: under {n} people", "OK"))
    return out


def calibration_summary(y, p):
    """Brier score, calibration slope (near 1 is good) and intercept (near 0 is good)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss
    y = np.asarray(y)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    logit = np.log(p / (1 - p)).reshape(-1, 1)
    lr = LogisticRegression(C=1e6).fit(logit, y)
    return {"brier": round(float(brier_score_loss(y, p)), 4), "slope": round(float(lr.coef_[0][0]), 3),
            "intercept": round(float(lr.intercept_[0]), 3)}


_BW = {}


def _barrier_weights():
    """Barrier points from config/real_values.json (design values), read once."""
    if not _BW:
        import json
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "config", "real_values.json"), encoding="utf-8") as fh:
            _BW.update(json.load(fh)["design"]["access_barrier_weights"]["value"])
    return _BW


def access_barrier(language, insurance, work_shift, phone_type, travel_min):
    """0 to 1: how hard it is to reach care (Step 5.2). For a child, pass the guardian's values."""
    w = _barrier_weights()
    s = w["language_not_arabic_or_english"] if language not in ("Arabic", "English") else 0.0
    s += w["basic_insurance"] if insurance == "Basic" else 0.0
    s += w["day_shift"] if work_shift == "day" else 0.0
    s += w["no_smartphone"] if phone_type != "smartphone" else 0.0
    s += w["travel_90_min_or_more"] * min(float(travel_min) / w["travel_full_minutes"], 1)
    return round(s, 2)


def save_json(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ data loading
DATE_COLS = ["first_record", "last_visit", "last_chronic_test", "last_vaccine", "last_wellness_visit",
             "last_well_child_visit", "hba1c_date", "bmi_date", "bmi_percentile_date", "sbp_date", "dbp_date",
             "total_chol_date", "ldl_date", "hdl_date", "last_cervical_screen", "last_breast_screen",
             "last_bowel_screen", "last_depression_screen"]


def load_features():
    df = pd.read_csv(os.path.join(FEATURES, "patient_features.csv"), dtype={"Id": str, "guardian_id": str, "household_id": str})
    for c in DATE_COLS:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    df["gene_status"] = df.gene_status.map(json.loads)
    return df


def load_guidelines():
    return pd.read_csv(os.path.join(REFERENCE, "guidelines.csv"), encoding="utf-8-sig")


def load_templates():
    t = pd.read_csv(os.path.join(REFERENCE, "templates.csv"), encoding="utf-8-sig")
    return {(r.check, r.audience, r.language): r.text for r in t.itertuples()}


def load_reply_words():
    t = pd.read_csv(os.path.join(REFERENCE, "reply_keywords.csv"), encoding="utf-8-sig").fillna("")
    words = {}
    for r in t.itertuples():
        for col in ("English", "Arabic", "Urdu", "Hindi", "Tagalog"):
            for w in str(getattr(r, col)).split(";"):
                if w.strip():
                    words[w.strip().lower()] = r.keyword
    return words


def load_calendar():
    c = pd.read_csv(os.path.join(REAL, "calendar", "uae_calendar_2025_2027.csv"), encoding="utf-8-sig")
    c["date"] = pd.to_datetime(c.date)
    return c.set_index("date")


def load_facilities():
    return pd.read_csv(os.path.join(REAL, "osm", "abu_dhabi_health_facilities.csv"), encoding="utf-8-sig")


def load_truth():
    t = pd.read_csv(os.path.join(ADJUSTED, "truth_hidden.csv"), dtype={"PATIENT": str})
    for c in ("diabetes_onset", "hypertension_onset"):
        t[c] = pd.to_datetime(t[c])
    return t.set_index("PATIENT")

def run_date():
    """The date every step treats as "today", so a rerun gives the same data and results.
    Set by "run_date" in config/real_values.json (design section); PP_RUN_DATE=YYYY-MM-DD overrides it;
    with neither, the real date is used."""
    import json
    import pandas as pd
    if os.environ.get("PP_RUN_DATE"):
        return pd.Timestamp(os.environ["PP_RUN_DATE"]).normalize()
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "config", "real_values.json"), encoding="utf-8") as fh:
            v = json.load(fh).get("design", {}).get("run_date", {}).get("value")
        if v:
            return pd.Timestamp(v).normalize()
    except (OSError, ValueError):
        pass
    return pd.Timestamp.today().normalize()

_CONFIG = {}


def config(name):
    """{name: value} from config/<name>.json ("values" section), read once. Every number the code uses lives in a
    config file with its source and status; config_rows() lists them for the app's sources table."""
    if name not in _CONFIG:
        with open(os.path.join(ROOT, "config", f"{name}.json"), encoding="utf-8") as fh:
            _CONFIG[name] = {k: v["value"] for k, v in json.load(fh)["values"].items()}
    return _CONFIG[name]


def demo_mode():
    """Demo tools (config/roles.json "demo_pages", such as the patient phone simulator) show only with PP_DEMO=1."""
    return os.environ.get("PP_DEMO", "").strip().lower() in ("1", "true", "yes")


def role_pages():
    """Which pages each role can open (config/roles.json), without the demo tools unless demo_mode() is on.
    The server, the React app (via /api/roles) and the Streamlit dashboard all use this one list."""
    cfg = config("roles")
    hide = set() if demo_mode() else set(cfg.get("demo_pages", []))
    return {role: [p for p in pages if p not in hide] for role, pages in cfg["role_pages"].items()}


def design(key):
    """One design value from config/real_values.json ("design" section), for modules that cannot import simulation."""
    if "_design" not in _CONFIG:
        with open(os.path.join(ROOT, "config", "real_values.json"), encoding="utf-8") as fh:
            _CONFIG["_design"] = {k: v["value"] for k, v in json.load(fh)["design"].items()}
    return _CONFIG["_design"][key]


def config_rows(name, group):
    with open(os.path.join(ROOT, "config", f"{name}.json"), encoding="utf-8") as fh:
        vals = json.load(fh)["values"]
    return [{"group": group, "name": k, "value": v["value"], "status": v["status"], "source": v["source"], "url": v.get("url")}
            for k, v in vals.items()]

def _small_groups():
    with open(os.path.join(ROOT, "config", "real_values.json"), encoding="utf-8") as fh:
        return json.load(fh)["design"]["small_groups"]["value"]