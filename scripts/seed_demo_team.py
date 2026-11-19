"""
Make a realistic demo care team (made-up doctors) so the Operations and Today numbers look like a real hospital network for a demo or video.

    python scripts/seed_demo_team.py            create the doctors, give every patient a doctor by the care-team rules (accounts must exist)
    python scripts/seed_demo_team.py --remove   remove the doctors this script made and clear the assignments

Why: with only a few doctor accounts almost every patient is "left without the right specialty" and every panel is full. Here: 13 made-up
doctors across the three hospitals, panel limits that fit each hospital's population, no Genetic counselling outside Abu Dhabi and no
Internal medicine in Al Dhafra, so a small, honest gap remains. Names are made up; passwords are random and written to your user folder
(outside the project), never here. Remove them before any real use.
"""
import argparse
import json
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api import auth, careteam  # noqa: E402

FILE = os.environ.get("PP_DEMO_TEAM_FILE") or os.path.join(os.path.expanduser("~"), ".precision_prevention", "demo_team.json")
AD, AIN, DH = "Abu Dhabi", "Al Ain", "Al Dhafra"
TEAM = [  # user, full name, hospital, specialties, languages, panel limit
    ("demo.dr.amna", "Dr Amna Al Nuaimi", AD, ["Paediatrics"], ["Arabic", "English"], 400),
    ("demo.dr.vikram", "Dr Vikram Rao", AD, ["Paediatrics"], ["Hindi", "English", "Urdu"], 400),
    ("demo.dr.khalifa", "Dr Khalifa Al Neyadi", AD, ["Family medicine"], ["Arabic", "English"], 900),
    ("demo.dr.rosa", "Dr Rosa Mendoza", AD, ["Family medicine"], ["Tagalog", "English"], 900),
    ("demo.dr.imran", "Dr Imran Siddiqui", AD, ["Family medicine", "Internal medicine"], ["Urdu", "English", "Hindi"], 900),
    ("demo.dr.salma", "Dr Salma Haddad", AD, ["Internal medicine"], ["Arabic", "English"], 700),
    ("demo.dr.noura", "Dr Noura Al Mazrouei", AD, ["Genetic counselling"], ["Arabic", "English"], 250),
    ("demo.dr.faisal", "Dr Faisal Al Ketbi", AIN, ["Paediatrics", "Family medicine"], ["Arabic", "English"], 800),
    ("demo.dr.priya", "Dr Priya Menon", AIN, ["Family medicine"], ["English", "Hindi"], 900),
    ("demo.dr.hamad", "Dr Hamad Al Shamsi", AIN, ["Family medicine", "Internal medicine"], ["Arabic", "Urdu"], 700),
    ("demo.dr.maryam", "Dr Maryam Al Hosani", AIN, ["Paediatrics"], ["Arabic", "English"], 400),
    ("demo.dr.sultan", "Dr Sultan Al Dhaheri", DH, ["Paediatrics", "Family medicine"], ["Arabic", "English"], 500),
    ("demo.dr.joseph", "Dr Joseph Dela Cruz", DH, ["Family medicine", "Paediatrics"], ["Tagalog", "English", "Hindi"], 500),
]


def seed():
    if auth.load_users() is None:
        sys.exit("No accounts yet: the app is in open mode, which already shows made-up doctors. Create the first admin account first.")
    lines, made = ["Made-up demo doctors (remove with: python scripts/seed_demo_team.py --remove)", ""], []
    for user, name, hospital, specs, langs, limit in TEAM:
        pw = secrets.token_urlsafe(9)
        rec = auth.account_record("doctor", pw, hospital)
        rec.update(full_name=name, specialty=specs, languages=langs, panel_limit=limit)
        auth.save_account(user, rec, by="seed_demo_team.py")
        made.append(user)
        lines.append(f"{user:18} {pw:14} {hospital}: {', '.join(specs)}")
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    with open(FILE, "w", encoding="utf-8") as fh:
        json.dump({"users": made, "passwords": "\n".join(lines)}, fh)
    assign()
    print(f"Created {len(made)} demo doctors and assigned every patient the rules allow.\nUser names and passwords are in {FILE} (outside the project).")


def assign():
    from api import app as A
    p = A.people()
    prop = careteam.propose(p, careteam.doctors())
    n = careteam.apply(prop)
    left = sum(x["people"] for x in prop["left"])
    print(f"{len(prop['items'])} assignments; {left} people still without the right specialty: "
          + "; ".join(f"{x['hospital']} {x['specialty']} {x['people']} ({x['why']})" for x in prop["left"]))
    return n


def remove():
    if not os.path.exists(FILE):
        sys.exit("No demo team to remove.")
    with open(FILE, encoding="utf-8") as fh:
        made = json.load(fh)["users"]
    for user in made:
        try:
            auth.remove_account(user, by="seed_demo_team.py")
        except ValueError:
            pass
    d = careteam.load()                                    # only these doctors' assignments go; break-glass and sharing records stay
    for pid in list(d["assignments"]):
        d["assignments"][pid] = {s: u for s, u in d["assignments"][pid].items() if u not in made}
        if not d["assignments"][pid]:
            del d["assignments"][pid]
    careteam.save(d)
    os.remove(FILE)
    print(f"Removed {len(made)} demo doctors and their assignments.")


def confirm(action, yes):
    """Say which files will change and ask first: this edits the real accounts and care-team files unless they are moved (PP_USERS_FILE,
    PP_CARETEAM_FILE, PP_ACCESS_LOG). --yes skips the question for scripts and tests."""
    print(f"{action}\n  accounts:   {auth.users_file()}\n  care teams: {careteam.store_path()}\n  access log: {auth.ACCESS_LOG}")
    if yes:
        return
    if input("Type yes to continue: ").strip().lower() != "yes":
        sys.exit("Nothing changed.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args()
    confirm("This removes the 13 demo doctors and their assignments." if a.remove else "This creates 13 made-up demo doctors and assigns every patient a doctor.", a.yes)
    remove() if a.remove else seed()
