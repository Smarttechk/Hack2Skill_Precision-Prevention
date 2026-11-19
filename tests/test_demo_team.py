"""
The demo care-team seeding script (scripts/seed_demo_team.py): realistic numbers for a demo, and a clean way to remove them.
    python tests/test_demo_team.py
"""
import json
import os
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
tmp = tempfile.mkdtemp()
USERS = os.path.join(tmp, "users.json")
for k, v in {"PP_USERS_FILE": USERS, "PP_SIGNUP_FILE": os.path.join(tmp, "signup.json"), "PP_CARETEAM_FILE": os.path.join(tmp, "ct.json"),
             "PP_OUTREACH_DIR": tmp, "PP_FAMILY_FILE": os.path.join(tmp, "fam.json"), "PP_ACCESS_LOG": os.path.join(tmp, "access_log.csv"),
             "PP_SESSIONS_FILE": os.path.join(tmp, "sessions.json"), "PP_LIMITS_FILE": os.path.join(tmp, "limits.json"),
             "PP_DEMO_TEAM_FILE": os.path.join(tmp, "demo_team.json")}.items():
    os.environ[k] = v
os.environ.pop("PP_PASSWORD", None)
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from api import app as appmod, auth, careteam  # noqa: E402
import seed_demo_team as seed  # noqa: E402

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


PW = "demo-team-pass-123"
s, h = auth.hash_password(PW)
json.dump({"users": {"boss": {"role": "admin", "salt": s, "hash": h}, "pm": {"role": "manager", "salt": s, "hash": h}}}, open(USERS, "w"))
c = TestClient(server.app)
get = lambda path, who: c.get(path, auth=(who, PW))  # noqa: E731

import subprocess  # noqa: E402
ask = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "seed_demo_team.py")], input="no\n", capture_output=True, text=True, encoding="utf-8",
                     env={**os.environ, "PYTHONIOENCODING": "utf-8"})
check("run from the command line the script names the files it will change and asks first: answering no changes nothing",
      ask.returncode != 0 and USERS in ask.stdout and "Type yes" in ask.stdout and "Nothing changed" in ask.stderr
      and set(auth.load_users()) == {"boss", "pm"} and careteam.doctors() == [])
before = get("/api/operations", "pm").json()["coverage"]
check(f"with no doctors every patient is left out ({before['left_out_people']:,} people) and nobody has a doctor", before["doctors"] == 0 and before["with_a_doctor"] == 0)
seed.seed()
docs = careteam.doctors()
after = get("/api/operations", "pm").json()["coverage"]
check("the script makes 13 made-up doctors across the three hospitals, each with specialties, languages and a panel limit",
      len(docs) == 13 and {d["hospital"] for d in docs} == {"Abu Dhabi", "Al Ain", "Al Dhafra"} and all(d["specialty"] and d["languages"] and d["panel_limit"] for d in docs))
check(f"almost everyone now has a doctor: {after['with_a_doctor']:,} people, and only a small honest gap remains ({after['left_out_people']} people)",
      after["with_a_doctor"] > 4000 and after["left_out_people"] < 0.06 * 5000 and after["left_out_people"] < before["left_out_people"] / 4)
check("the gaps that remain are the ones the script leaves on purpose: no genetic counselling outside Abu Dhabi, no internal medicine in Al Dhafra",
      all((g["specialty"], g["hospital"]) in {("Genetic counselling", "Al Ain"), ("Genetic counselling", "Al Dhafra"), ("Internal medicine", "Al Dhafra")}
          and g["why"] == "no doctor" for g in after["left_out"]))
a = careteam.load()["assignments"]
check("no panel goes past its limit, and every urgent person in a hospital with the right doctors has one",
      all(careteam.panel_size(d["user"], a) <= d["panel_limit"] for d in docs) and after["urgent_without_doctor"] <= 5)
pw_file = json.load(open(seed.FILE))
check("the passwords are written outside the project, never in it, and are not weak", all(u in pw_file["passwords"] for u in pw_file["users"])
      and not os.path.abspath(seed.FILE).startswith(ROOT) and all(len(line.split()[1]) >= 10 for line in pw_file["passwords"].splitlines()[2:]))
seed.seed()
check("running it twice is safe (the same 13 doctors, no duplicates)", len(careteam.doctors()) == 13)
seed.remove()
check("removing it takes away the doctors, their accounts and their assignments, and nothing else",
      careteam.doctors() == [] and not any(u.startswith("demo.") for u in (auth.load_users() or {})) and careteam.load()["assignments"] == {}
      and set(auth.load_users()) == {"boss", "pm"} and not os.path.exists(seed.FILE))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
