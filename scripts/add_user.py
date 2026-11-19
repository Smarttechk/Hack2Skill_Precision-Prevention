"""
Add or update a sign-in account from the command line (the hospital admin can also do this on the Accounts page).
Accounts are stored in config/users.json as salted PBKDF2 hashes, never plain passwords. There is no self sign-up.

    python scripts/add_user.py hospital_admin admin                     asks for the password (the first admin)
    python scripts/add_user.py dr_amina doctor --region "Abu Dhabi"     a doctor who sees only Abu Dhabi residents
    python scripts/add_user.py nurse_omar outreach --region "Al Ain"
    python scripts/add_user.py planner manager
    python scripts/add_user.py family_1 patient --person <person Id>     only after checking who they are
    python scripts/add_user.py --demo        one account per role with random passwords (saved OUTSIDE the project)
    python scripts/add_user.py --list
    python scripts/add_user.py --remove dr_amina

Roles (config/roles.json): admin (hospital admin), doctor, outreach, manager, patient.
Once config/users.json exists, everyone must sign in.
"""
import argparse
import getpass
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api import auth  # noqa: E402

# demo passwords are kept in your user folder, never in the project, so they are not shared when the project is
# example names for the demo accounts (made up; shown at the top of the app)
DEMO_NAMES = {"admin": "Mariam Al Hashemi", "doctor": "Dr Amina Al Mansoori", "outreach": "Omar Khalid",
              "manager": "Sara Haddad", "patient": "Fatima Ahmed"}
DEMO_FILE = os.path.join(os.path.expanduser("~"), ".precision_prevention", "demo_accounts.txt")


def record(role, password, region=None, person=None):
    try:
        return auth.account_record(role, password, region, person)
    except ValueError as e:
        sys.exit(str(e))


def demo():
    """One account per role, for showing the app: random passwords, written only to DEMO_FILE."""
    p = auth._people()
    if p is None:
        sys.exit("Run python orchestrator.py first.")
    regions = p.region.value_counts().index.tolist()
    family = p[(p.is_child == 1) & p.guardian_id.notna()].guardian_id.value_counts().index[0]   # a parent with children
    plan = [("hospital_admin", "admin", None, None), ("dr_" + regions[0].lower().replace(" ", "_"), "doctor", regions[0], None),
            ("outreach_" + regions[1].lower().replace(" ", "_"), "outreach", regions[1], None),
            ("programme_manager", "manager", None, None), ("family_demo", "patient", None, family)]
    names = DEMO_NAMES
    lines = ["Demo accounts for the Precision Prevention prototype (synthetic data). Remove them before any real use:",
             "python scripts/add_user.py --remove NAME", ""]
    for name, role, region, person in plan:
        pw = secrets.token_urlsafe(9)
        auth.save_account(name, {**record(role, pw, region, person), "full_name": names[role]}, by="add_user.py --demo")
        lines.append(f"{name:22} {pw:16} {role}" + (f", only {region}" if region else "") + (", own record and children" if person else ""))
    os.makedirs(os.path.dirname(DEMO_FILE), exist_ok=True)
    with open(DEMO_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    old = os.path.join(ROOT, "config", "demo_accounts.txt")
    if os.path.exists(old):
        os.remove(old)                                  # never leave passwords inside the project
    print(f"Created {len(plan)} demo accounts; user names and passwords are in {DEMO_FILE} (outside the project)")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?")
    ap.add_argument("role", nargs="?")
    ap.add_argument("--region")
    ap.add_argument("--person")
    ap.add_argument("--full-name", help="name shown at the top of the app (default: the user name)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--remove")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for name, rec in sorted((auth.load_users() or {}).items()):
            print(f"{name}: {rec['role']}" + (f", region {rec['region']}" if rec.get("region") else "")
                  + (f", person {rec['person']}" if rec.get("person") else ""))
        return
    if a.remove:
        try:
            auth.remove_account(a.remove, by="add_user.py")
        except ValueError as e:
            sys.exit(str(e))
        print(f"Removed {a.remove}")
        return
    if a.demo:
        demo()
        return
    if not (a.name and a.role):
        sys.exit(__doc__)
    password = os.environ.get("PP_NEW_PASSWORD") or getpass.getpass(f"Password for {a.name}: ")
    rec = record(a.role, password, a.region, a.person)
    if a.full_name:
        rec["full_name"] = a.full_name
    users = auth.load_users() or {}
    if a.name in users and not a.full_name and users[a.name].get("full_name"):
        rec["full_name"] = users[a.name]["full_name"]     # keep the name when only the password changes
    auth.save_account(a.name, rec, by="add_user.py")
    print(f"Saved {a.name} in {auth.users_file()}")


if __name__ == "__main__":
    main(sys.argv[1:])
