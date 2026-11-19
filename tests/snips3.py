# Third round of code for the Implementation Guide (v4). Executed by test_snips3.py.
S3 = {}

S3["nhanes_load"] = '''import os, urllib.request
import numpy as np, pandas as pd
BASE = "https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/"
FILES = ["DEMO_J", "BMX_J", "BPX_J", "GHB_J", "DIQ_J", "MCQ_J"]   # 2017-2018 survey cycle
def load_nhanes(folder="data/real/nhanes"):
    """Downloads the six public files once (a few MB in total), then reads them from disk."""
    os.makedirs(folder, exist_ok=True)
    parts = []
    for f in FILES:
        path = os.path.join(folder, f + ".xpt")
        if not os.path.exists(path):
            urllib.request.urlretrieve(BASE + f + ".xpt", path)
        d = pd.read_sas(path, format="xport")
        d.columns = d.columns.str.upper()          # e.g. MCQ300c becomes MCQ300C
        parts.append(d.set_index("SEQN"))          # SEQN links the same person across files
    return parts[0].join(parts[1:], how="left")'''

S3["nhanes_prepare"] = '''def nhanes_table(raw):
    """Adults with an HbA1c test who have NOT been told they have diabetes."""
    d = raw[(raw.RIDAGEYR >= 20) & raw.LBXGH.notna() & raw.DIQ010.isin([2, 3])].copy()
    y = (d.LBXGH >= 6.5).astype(int)               # undiagnosed diabetes found by the HbA1c test
    X = pd.DataFrame({                             # HbA1c itself is never a feature: it is the answer
        "age": d.RIDAGEYR,
        "male": (d.RIAGENDR == 1).astype(int),
        "bmi": d.BMXBMI,
        "waist": d.BMXWAIST,
        "systolic_bp": d.BPXSY1,
        "family_history": d.MCQ300C.map({1: 1, 2: 0}),     # refused or don't know become unknown
        "told_prediabetes": d.DIQ160.map({1: 1, 2: 0}),
    }, index=d.index)
    return X, y, d.WTMEC2YR, d.RIDRETH3'''

S3["attend"] = '''def attend_probability(x, option):
    """Simulated chance of attending. Used instead of the simple formula in Step 8.3."""
    z = (1.1 - 1.6 * x.access_barrier - 0.5 * min(x.past_no_shows, 3)
         + 0.04 * min(x.days_notice, 14) - 0.5 * (x.is_ramadan and not x.evening_slot))
    if option.startswith(("Telehealth", "Mobile clinic")):
        z += 0.6
    return 1 / (1 + np.exp(-z))'''
