"""
Check the doctor view against figures worked out separately.

    python scripts/audit_doctor_view.py            200 people
    python scripts/audit_doctor_view.py --n 500

For each sampled person it asks the API what a doctor sees, then works the same figures out again from the features and the published
formulas and compares them: age and sex, the diabetes chance and its range, the heart-disease chance, the blood-pressure category, no
score for children, no prediction for someone with a known diagnosis, and a reason behind every Urgent. It changes nothing and exits with 1
if anything disagrees.
"""
import argparse
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
tmp = tempfile.mkdtemp()                                        # open mode on scratch files: nothing real is touched
for k, v in {"PP_USERS_FILE": "none.json", "PP_ACCESS_LOG": "a.csv", "PP_SESSIONS_FILE": "s.json", "PP_LIMITS_FILE": "l.json",
             "PP_ANSWERS_FILE": "ans.json", "PP_CARETEAM_FILE": "ct.json", "PP_FAMILY_FILE": "f.json"}.items():
    os.environ[k] = os.path.join(tmp, v)
os.environ["PP_OUTREACH_DIR"] = tmp
os.environ.pop("PP_PASSWORD", None)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
import utils  # noqa: E402
from api import app as appmod  # noqa: E402
from models import risk_scores as rs  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=200)
    a = ap.parse_args(argv)
    c = TestClient(server.app)
    feats = utils.load_features().set_index("Id")
    bundle = rs.load(utils.load_features())
    people = appmod.people()
    rng = np.random.default_rng(11)
    urgent = list(c.get("/api/doctor/list?urgency=Urgent,High,Routine&limit=100000").json()["items"])
    ids = [r["id"] for r in urgent]
    pool = list(dict.fromkeys(ids + list(people.Id)))
    pick = list(rng.choice(pool, size=min(a.n, len(pool)), replace=False))
    bad, checked = [], 0

    def flag(pid, msg):
        bad.append((appmod.mrn(pid), msg))

    adult_age = utils.config("clinical_rules")["adult_age"]
    for pid in pick:
        r = c.get(f"/api/person/{pid}")
        if r.status_code != 200:
            flag(pid, f"person endpoint answered {r.status_code}")
            continue
        d = r.json()
        checked += 1
        f = feats.loc[pid] if pid in feats.index else None
        if f is not None:
            if int(d["age"]) != int(f.age):
                flag(pid, f"age shown {d['age']} but features say {int(f.age)}")
            if "male" in f.index and not pd.isna(f.male) and (str(d["sex"])[:1].upper() == "M") != bool(f.male):
                flag(pid, f"sex shown {d['sex']} but features say male={f.male}")
        if d["is_child"]:
            if d.get("risk") is not None or any(x["status"] in ("score", "reading") for x in d["conditions"]) or any(x.get("condition") == "cardiovascular" for x in d["conditions"]):
                flag(pid, "a child has a risk score or a blood-pressure reading shown")
            continue
        conds = {x["condition"]: x for x in d["conditions"]}
        # known diagnosis is never predicted
        for cname in ("diabetes", "hypertension"):
            known = int(getattr(people.set_index("Id").loc[pid], f"known_{cname}", 0) or 0) if f is not None else 0
            if known and conds.get(cname, {}).get("status") == "score":
                flag(pid, f"{cname} is known but a prediction is shown")
        # diabetes chance recomputed from the features (no clinic answers in this scratch run)
        x = conds.get("diabetes")
        if x and x["status"] == "score" and f is not None:
            s = rs.score(bundle, feats.loc[[pid]]).iloc[0]
            if abs(float(s.risk) - x["risk"]) > 1e-6 or abs(float(s.risk_low) - x["low"]) > 1e-6 or abs(float(s.risk_high) - x["high"]) > 1e-6:
                flag(pid, f"diabetes chance shown {x['risk']:.3f} ({x['low']:.3f} to {x['high']:.3f}) but recomputed {float(s.risk):.3f}")
            if not (x["low"] - 1e-9 <= x["risk"] <= x["high"] + 1e-9):
                flag(pid, "diabetes chance outside its own range")
            if x["band"] not in ("Low", "Moderate", "High"):
                flag(pid, f"unknown band {x['band']}")
        # heart risk recomputed
        h = conds.get("cardiovascular")
        if f is not None and 40 <= f.age <= 79 and not any(pd.isna(f.get(k)) for k in ("total_chol", "hdl", "sbp")):
            want = rs.ascvd(feats.loc[[pid]]).iloc[0]
            if h is None:
                flag(pid, "heart-disease chance missing for an adult aged 40 to 79 with the measurements")
            elif abs(float(want.risk) - float(h["risk"])) > 1e-6:
                flag(pid, f"heart-disease chance shown {h['risk']:.4f} but recomputed {float(want.risk):.4f}")
        elif h is not None and f is not None and not (40 <= f.age <= 79):
            flag(pid, "heart-disease chance shown outside ages 40 to 79")
        # blood pressure category
        bp = next((v for v in d["conditions"] if v.get("status") == "reading"), None)
        if bp and f is not None and rs.bp_category(f.sbp, f.dbp) != bp["category"]:
            flag(pid, f"blood-pressure category shown {bp['category']} but recomputed {rs.bp_category(f.sbp, f.dbp)}")
        # urgent needs a reason
        if d["urgency"] == "Urgent" and not d["warning_signs"] and not any(v.get("status") == "found" for v in d["conditions"]) and not d.get("cards"):
            flag(pid, "Urgent with no warning sign, no found result and no card")
    print(f"Checked {checked} people.")
    if bad:
        print(f"{len(bad)} disagreement(s):")
        for m, msg in bad[:40]:
            print(f"  {m}: {msg}")
    else:
        print("Nothing disagrees: ages, sexes, diabetes chances and ranges, heart-disease chances, blood-pressure categories, "
              "children without scores, known diagnoses not predicted, urgent people with a reason.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
