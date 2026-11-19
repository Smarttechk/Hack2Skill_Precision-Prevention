"""
Fill the app with made-up people so every page has something to show.

    python scripts/seed_demo_data.py            create it all (asks first)
    python scripts/seed_demo_data.py --remove   take the accounts and practice numbers out again

It makes: the 13 demo doctors (if they are missing), a hospital admin for each hospital, 3 outreach staff, a programme manager, and 10
patient or family accounts on 10 different records (four families with several children, six adults of different kinds). It also adds
waist and lifestyle answers for a few adults, about 30 outreach contacts and 20 visit outcomes.

Passwords are random and saved in your user folder, not in the project. Practice mobile numbers are added only while messaging is not
live, so no real person can be texted by mistake. Nothing here is real; remove it before real use.
"""
import argparse
import json
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import messaging  # noqa: E402
from api import auth, careteam, clinic_answers  # noqa: E402
from api import outreach as contact_log  # noqa: E402

FILE = os.environ.get("PP_DEMO_DATA_FILE") or os.path.join(os.path.expanduser("~"), ".precision_prevention", "demo_data.json")
AD, AIN, DH = "Abu Dhabi", "Al Ain", "Al Dhafra"
STAFF = [  # user, full name, role, hospital
    ("demo.admin.abudhabi", "Mariam Al Hashemi", "admin", AD),
    ("demo.admin.alain", "Rashed Al Dhaheri", "admin", AIN),
    ("demo.admin.aldhafra", "Shamma Al Mazrouei", "admin", DH),
    ("demo.outreach.layla", "Layla Hassan", "outreach", AD),
    ("demo.outreach.omar", "Omar Farouk", "outreach", AD),
    ("demo.outreach.mei", "Mei Santos", "outreach", AIN),
    ("demo.manager", "Khalid Al Suwaidi", "manager", None),
]


def _pick(df, taken, n=1):
    out = []
    for r in df.itertuples():
        if r.Id not in taken and len(out) < n:
            out.append(r.Id)
            taken.add(r.Id)
    return out


def choose_patients(p):
    """Ten different people: four guardians of several children and six adults of different kinds."""
    taken = set(a["person"] for a in (auth.load_users() or {}).values() if a.get("person"))
    adults = p[(p.is_child == 0) & (p.age >= 18)]
    kids = p[p.is_child == 1]
    kid_count = kids.groupby("guardian_id").size()
    guardians = adults[adults.Id.isin(kid_count[kid_count >= 2].index)].assign(n=lambda d: d.Id.map(kid_count)).sort_values(["n", "Id"], ascending=[False, True])
    chosen, notes = [], {}
    for lang, region in (("Arabic", AD), ("Hindi", AIN), ("Urdu", AD), ("Tagalog", AD)):
        pool = guardians[(guardians.language == lang) & (guardians.region == region)]
        pool = pool if len(pool) else guardians[guardians.language == lang]
        pool = pool if len(pool) else guardians
        for pid in _pick(pool, taken):
            chosen.append(pid); notes[pid] = f"family, {lang}"
    has_card = adults.open_cards.map(len) > 0
    singles = [
        ("older adult with a high blood-pressure result", adults[(adults.age >= 55) & (adults.urgency == "Urgent") & (adults.language == "Arabic")].sort_values("Id")),
        ("woman due for screening", adults[(adults.sex == "F") & (adults.age.between(40, 65)) & has_card & (adults.language == "Tagalog")].sort_values("Id")),
        ("raised diabetes chance", adults[adults.risk.notna() & (adults.risk >= adults.risk.quantile(0.9)) & (adults.language == "Urdu")].sort_values("Id")),
        ("young adult", adults[(adults.age.between(22, 30)) & (adults.language == "English") & has_card].sort_values("Id")),
        ("opted out of messages", adults[(adults.consent == 0)].sort_values("Id")),
        ("adult in Al Ain", adults[(adults.region == AIN) & has_card & (adults.age.between(35, 60))].sort_values("Id")),
    ]
    for label, pool in singles:
        pool = pool if len(pool) else adults[has_card].sort_values("Id")
        for pid in _pick(pool, taken):
            chosen.append(pid); notes[pid] = label
    return chosen, notes


def seed(yes=False):
    if auth.load_users() is None:
        sys.exit("No accounts yet: the app is in open mode. Create the first admin account first (python scripts/add_user.py NAME admin).")
    from api import app as A
    from api import careteam as CT
    import seed_demo_team as team
    if not any(d["user"].startswith("demo.dr.") for d in CT.doctors()):
        team.seed()
    p = A.people()
    ids = A.identity()
    made = {"staff": [], "patients": [], "phones": []}
    lines = ["Made-up demo accounts (remove with: python scripts/seed_demo_data.py --remove)", ""]
    users = auth.load_users() or {}
    for user, name, role, hospital in STAFF:
        if user in users:
            continue
        pw = secrets.token_urlsafe(9)
        rec = auth.account_record(role, pw, hospital)
        rec["full_name"] = name
        auth.save_account(user, rec, by="seed_demo_data.py")
        made["staff"].append(user); lines.append(f"{user:24} {pw:14} {role}{', ' + hospital if hospital else ''}")
    chosen, notes = choose_patients(p)
    by_id = p.set_index("Id")
    for k, pid in enumerate(chosen, 1):
        first = (str(ids.name.get(pid) or f"patient{k}").split() or [f"patient{k}"])[0]
        user = f"demo.patient{k:02d}.{first.lower()}"[:40]
        pw = secrets.token_urlsafe(9)
        rec = auth.account_record("patient", pw, None, pid)
        rec["full_name"] = str(ids.name.get(pid) or first)
        auth.save_account(user, rec, by="seed_demo_data.py")
        made["patients"].append(user)
        lines.append(f"{user:24} {pw:14} patient ({notes[pid]}) record {A.mrn(pid)}, {by_id.loc[pid].region}")
        if not messaging.live_ready():                   # practice numbers only while nothing can really be sent
            messaging.set_number(pid, f"+9715000001{k:02d}", by="seed_demo_data.py")
            made["phones"].append(pid)
    # waist and lifestyle answers for some adults with a diabetes chance
    n_ans = 0
    for pid in chosen:
        if n_ans < 4 and by_id.loc[pid].is_child == 0 and not (by_id.loc[pid].risk != by_id.loc[pid].risk):
            clinic_answers.save(pid, "demo.dr.amna", 78 + 9 * n_ans, bool(n_ans % 2), n_ans < 2)
            n_ans += 1
    # contacts and visit outcomes by the demo outreach staff
    staff = [("demo.outreach.layla", "Layla Hassan"), ("demo.outreach.omar", "Omar Farouk"), ("demo.outreach.mei", "Mei Santos")]
    plan = ["agreed"] * 9 + ["later"] * 4 + ["declined"] * 2 + ["needs_doctor"] * 3 + ["no_answer"] * 8 + ["wrong_number"] * 1
    channels = ["WhatsApp", "WhatsApp", "SMS", "Phone call", "WhatsApp", "SMS", "Visit by a health worker"]
    q = A.queue(n=80)["items"]
    done = 0
    for i, (item, outcome) in enumerate(zip(q, plan)):
        user, name = staff[i % len(staff)]
        try:
            contact_log.record(item["id"], user, name, channels[i % len(channels)], outcome, "")
            done += 1
        except Exception:
            continue
    visits = [v for v in A.visits_list(status="to_confirm", n=200)["items"] if v["status"] == "to_confirm"][:20]
    for i, v in enumerate(visits):
        user, name = staff[i % len(staff)]
        try:
            contact_log.record(v["id"], user, name, "", "no_show" if i % 5 == 4 else "attended", "")
        except Exception:
            continue
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    with open(FILE, "w", encoding="utf-8") as fh:
        json.dump({**made, "passwords": "\n".join(lines)}, fh)
    print(f"Created {len(made['staff'])} staff and {len(made['patients'])} patient or family accounts, {done} contacts, {len(visits)} visit outcomes.\n"
          f"{'Practice mobile numbers added (messaging is not live).' if made['phones'] else 'No phone numbers added (messaging is live).'}\n"
          f"User names and passwords are in {FILE} (outside the project).")


def remove():
    if not os.path.exists(FILE):
        sys.exit("No demo data to remove.")
    with open(FILE, encoding="utf-8") as fh:
        made = json.load(fh)
    for user in made["staff"] + made["patients"]:
        try:
            auth.remove_account(user, by="seed_demo_data.py")
        except ValueError:
            pass
    for pid in made["phones"]:
        messaging.clear_number(pid)
    os.remove(FILE)
    print(f"Removed {len(made['staff']) + len(made['patients'])} demo accounts and {len(made['phones'])} practice numbers. "
          "Contacts and visit outcomes stay in the outreach log (it is an audit record).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args()
    print(("This removes the demo accounts." if a.remove else "This creates made-up staff, patient and family accounts, contacts and visit outcomes.")
          + f"\n  accounts:   {auth.users_file()}\n  care teams: {careteam.store_path()}\n  outreach:   {contact_log._paths()[0]}\n  access log: {auth.ACCESS_LOG}")
    if not a.yes and input("Type yes to continue: ").strip().lower() != "yes":
        sys.exit("Nothing changed.")
    remove() if a.remove else seed()
