import sys, os, types, warnings, tempfile, urllib.request
warnings.filterwarnings("ignore")
sys.path.insert(0, sys.argv[1])
from snips import S
from snips3 import S3
import numpy as np, pandas as pd

ok = []
def check(name, cond):
    ok.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)
rng = np.random.default_rng(3)

# helpers the guide defines earlier (Step 10.6 suppress, Step 10.7 calibration_summary)
base = dict(np=np, pd=pd, rng=rng, patients=pd.DataFrame({"x": range(10)}))
exec(S["fair"], base); exec(S["outcomes"], base)

# ---- fake NHANES files with the real column names ----
N = 6000
seqn = np.arange(93703, 93703 + N).astype(float)
age = rng.integers(0, 81, N).astype(float)
male = rng.random(N) < 0.5
bmi = np.clip(rng.normal(29, 6, N), 15, 60)
waist = bmi * 3.3 + rng.normal(0, 5, N)
sbp = 110 + 0.5 * age + rng.normal(0, 12, N)
fam = rng.choice([1, 2, 9], N, p=[.4, .55, .05]).astype(float)
pre = rng.choice([1, 2], N, p=[.1, .9]).astype(float)
z = -9 + 0.06 * age + 0.11 * bmi + 0.8 * (fam == 1) + 1.0 * (pre == 1) + 0.3 * male
truly = rng.random(N) < 1 / (1 + np.exp(-z))
diq = np.where(rng.random(N) < 0.4, np.where(truly, 1, 2), 2).astype(float)   # some already diagnosed
diq[rng.random(N) < 0.03] = 3
ghb = np.where(truly, rng.normal(7.4, 0.8, N), rng.normal(5.4, 0.35, N))
ghb[rng.random(N) < 0.1] = np.nan
fake = {
    "DEMO_J": pd.DataFrame({"SEQN": seqn, "RIDAGEYR": age, "RIAGENDR": np.where(male, 1., 2.),
                            "RIDRETH3": rng.choice([1., 2., 3., 4., 6., 7.], N), "WTMEC2YR": rng.uniform(5e3, 9e4, N)}),
    "BMX_J": pd.DataFrame({"SEQN": seqn, "BMXBMI": bmi, "BMXWAIST": waist}),
    "BPX_J": pd.DataFrame({"SEQN": seqn, "BPXSY1": sbp}),
    "GHB_J": pd.DataFrame({"SEQN": seqn, "LBXGH": ghb}),
    "DIQ_J": pd.DataFrame({"SEQN": seqn, "DIQ010": diq, "DIQ160": pre}),
    "MCQ_J": pd.DataFrame({"SEQN": seqn[: N - 500], "MCQ300c": fam[: N - 500]}),   # lower-case c, fewer rows
}
calls = []
def fake_retrieve(url, path):
    calls.append(url); open(path, "wb").write(b"x")
def fake_read_sas(path, format):
    return fake[os.path.basename(path)[:-4]].copy()
urllib.request.urlretrieve = fake_retrieve
pd.read_sas = fake_read_sas

ns = dict(base)
exec(S3["nhanes_load"], ns)
tmp = tempfile.mkdtemp()
raw = ns["load_nhanes"](tmp)
check("load: six files fetched from the CDC 2017 folder", len(calls) == 6 and all(c.startswith("https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/") and c.endswith(".xpt") for c in calls))
raw2 = ns["load_nhanes"](tmp)
check("load: second run reads from disk (no new downloads)", len(calls) == 6)
check("load: joined on SEQN, one row per person, MCQ300C upper-cased", len(raw) == N and "MCQ300C" in raw.columns and raw.index.name == "SEQN")
check("load: person missing from MCQ file kept with unknown", raw.MCQ300C.isna().sum() == 500)

exec(S3["nhanes_prepare"], ns)
X, y, w, g = ns["nhanes_table"](raw)
check("prepare: only adults 20+", (X.age >= 20).all())
check("prepare: people already diagnosed (DIQ010 = 1) excluded", not raw.loc[X.index].DIQ010.eq(1).any())
check("prepare: HbA1c is not a feature", not any("GH" in c.upper() or "HBA1C" in c.upper() for c in X.columns))
check("prepare: label = HbA1c >= 6.5", (y == (raw.loc[X.index].LBXGH >= 6.5)).all())
check("prepare: don't-know family history becomes unknown", X.family_history.isna().sum() == ((raw.loc[X.index].MCQ300C == 9) | raw.loc[X.index].MCQ300C.isna()).sum())


# ---- attendance simulation + no-show model ----
exec(S3["attend"], ns)
ap = ns["attend_probability"]
easy = types.SimpleNamespace(access_barrier=0.04, past_no_shows=0, days_notice=14, is_ramadan=False, evening_slot=False)
hard = types.SimpleNamespace(access_barrier=0.84, past_no_shows=2, days_notice=2, is_ramadan=True, evening_slot=False)
pe, ph = ap(easy, "Clinic visit"), ap(hard, "Clinic visit")
check(f"attend: easy {pe:.2f} > hard {ph:.2f}, both in sensible range", 0.75 < pe < 0.9 and 0.05 < ph < 0.35)
check("attend: telehealth raises attendance", ap(hard, "Telehealth") > ph)


print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
