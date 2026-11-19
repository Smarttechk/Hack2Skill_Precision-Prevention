"""
The demo-data script, the urgent-care and family-form endpoints, the messaging module and the staff views, on scratch files.
    python tests/test_demo_data.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
tmp = tempfile.mkdtemp()
for k, v in {"PP_USERS_FILE": "users.json", "PP_ACCESS_LOG": "a.csv", "PP_SESSIONS_FILE": "s.json", "PP_LIMITS_FILE": "l.json", "PP_ANSWERS_FILE": "ans.json",
             "PP_CARETEAM_FILE": "ct.json", "PP_FAMILY_FILE": "f.json", "PP_SIGNUP_FILE": "su.json", "PP_DEMO_TEAM_FILE": "team.json",
             "PP_DEMO_DATA_FILE": "data.json", "PP_CONTACT_FILE": "contacts.json", "PP_OUTBOX_FILE": "outbox.csv"}.items():
    os.environ[k] = os.path.join(tmp, v)
os.environ["PP_OUTREACH_DIR"] = tmp
for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_SMS_FROM", "TWILIO_WHATSAPP_FROM", "PP_MESSAGING", "PP_PASSWORD"):
    os.environ.pop(k, None)

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


import messaging  # noqa: E402
from api import auth  # noqa: E402

# ---- messaging module
check("a number is cleaned to international form", messaging.clean_number("+971 50-123 4567") == "+971501234567" and messaging.clean_number("00971501234567") == "+971501234567")
for bad in ("0501234567", "12345", "+971", "abc"):
    try:
        messaging.clean_number(bad)
        check(f"a bad number is refused: {bad}", False)
    except ValueError:
        check(f"a bad number is refused: {bad}", True)
check("messaging is a dry run with no provider details", messaging.mode() == "dry run" and not messaging.live_ready())
r = messaging.send("SMS", "+971501234567", "Hello", staff="s", record="ADH-1")
rows = messaging.outbox()
check("a dry run sends nothing and is logged without the text or the full number",
      r["status"] == "dry run" and rows and rows[0]["to_last4"] == "4567" and rows[0]["characters"] == "5" and "Hello" not in open(messaging.outbox_path(), encoding="utf-8-sig").read()
      and "501234567" not in open(messaging.outbox_path(), encoding="utf-8-sig").read())
try:
    messaging.send("Fax", "+971501234567", "x")
    check("an unknown channel is refused", False)
except ValueError:
    check("an unknown channel is refused", True)
# live mode talks to the provider's address with the right form, with the network call replaced
os.environ.update(TWILIO_ACCOUNT_SID="ACtest", TWILIO_AUTH_TOKEN="tok", TWILIO_WHATSAPP_FROM="+15550001111", TWILIO_SMS_FROM="+15550002222")
seen = {}


class FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps({"sid": "SM123", "status": "queued"}).encode()


import urllib.request as _ur  # noqa: E402
real_open = _ur.urlopen


def fake_open(req, timeout=0):
    seen["url"], seen["data"] = req.full_url, req.data.decode()
    seen["auth"] = req.get_header("Authorization")
    return FakeResp()


_ur.urlopen = fake_open
try:
    r = messaging.send("WhatsApp", "+971501234567", "Hi there", staff="s", record="ADH-2")
finally:
    _ur.urlopen = real_open
check("live mode posts to the provider with the WhatsApp prefix and basic authentication",
      r["status"] == "sent" and "Accounts/ACtest/Messages.json" in seen["url"] and "To=whatsapp%3A%2B971501234567" in seen["data"] and seen["auth"].startswith("Basic "))
for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM", "TWILIO_SMS_FROM"):
    os.environ.pop(k, None)

# ---- the seed script, then the app on top of it
json.dump({"users": {"hospital_admin": auth.account_record("admin", "Admin-pass-12345")}}, open(os.environ["PP_USERS_FILE"], "w"))
import seed_demo_data as S  # noqa: E402
S.seed()
users = auth.load_users()
demo = json.load(open(os.environ["PP_DEMO_DATA_FILE"], encoding="utf-8"))
check("it made 7 staff accounts and 10 patient or family accounts, all of different people",
      len(demo["staff"]) == 7 and len(demo["patients"]) == 10 and len({users[u]["person"] for u in demo["patients"]}) == 10)
roles = [users[u]["role"] for u in demo["staff"]]
check("the staff are hospital admins, outreach staff and a programme manager", roles.count("admin") == 3 and roles.count("outreach") == 3 and roles.count("manager") == 1)
check("practice phone numbers were added for the ten (messaging is not live)", len(demo["phones"]) == 10 and messaging.get_number(demo["phones"][0]))
check("no password is written inside the project", "passwords" in demo and not os.path.exists(os.path.join(ROOT, "config", "demo_data.json")))

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402
from api import app as A  # noqa: E402

c = TestClient(server.app)


def login(name, pw):
    cl = TestClient(server.app)
    r = cl.post("/api/login", json={"user": name, "password": pw})
    return cl, r


pw_lines = {ln.split()[0]: ln.split()[1] for ln in demo["passwords"].splitlines()[2:] if ln.strip()}
pat_user = demo["patients"][0]
cl, r = login(pat_user, pw_lines[pat_user])
check("a demo patient can sign in", r.status_code == 200)
mc = cl.get("/api/mycare").json()
check("My care shows the family: the parent and the children", len(mc["people"]) >= 3)
u = cl.get("/api/mycare/urgent")
check("the urgent-care endpoint answers with the emergency number and the nearest hospitals with distances and map links",
      u.status_code == 200 and u.json()["emergency"] == "998" and len(u.json()["hospitals"]) >= 1 and all(h["km"] >= 0 and "maps" in h["map_url"] for h in u.json()["hospitals"]))
hs = u.json()["hospitals"]
check("the nearest hospitals come nearest first", [h["km"] for h in hs] == sorted(h["km"] for h in hs))
child = next(p for p in mc["people"] if not p["self"])
check("urgent care can be asked for a child of the family", cl.get(f"/api/mycare/urgent?pid={child['id']}").status_code == 200)
check("urgent care for someone outside the family looks like nobody", cl.get("/api/mycare/urgent?pid=not-a-person").status_code == 404)
fam = cl.get("/api/mycare/family").json()
check("the family form offers sexes and languages as well as relations", fam["sexes"] and fam["languages"] and fam["relations"])
r = cl.post("/api/mycare/family/request", json={"first_name": "Salem", "family_name": "Test", "dob": "2021-04-02", "sex": "Boy", "language": "Arabic",
                                                 "relation": "Father", "record_number": "", "note": ""})
check("a parent can send the longer form to the hospital", r.status_code == 200)
r = cl.post("/api/mycare/phone", json={"number": "0501234567"})
check("a number without the country code is refused with a plain reason", r.status_code == 400 and "country code" in r.json()["detail"])
r = cl.post("/api/mycare/phone", json={"number": "+971 50 765 4321"})
check("the family can save a mobile number and only the last digits come back", r.status_code == 200 and r.json()["masked"].endswith("4321") and "7654" not in r.json()["masked"])

# staff views: the outreach worker sees their own contacts, the admin sees everyone and a summary
out_user = "demo.outreach.layla"
oc, r = login(out_user, pw_lines[out_user])
mine_ = oc.get("/api/queue/contacts").json()
team_ = oc.get("/api/queue/contacts?mine_only=0").json()
check("outreach staff see only their own contacts by default, and can switch to the whole team",
      mine_["own"] and all(r_["staff"] == "Layla Hassan" for r_ in mine_["rows"]) and team_["total"] > mine_["total"] and not team_["own"])
check("outreach staff are not shown a comparison of colleagues", mine_["by_staff"] == [] and team_["by_staff"] == [])
ac, r = login("demo.admin.abudhabi", pw_lines["demo.admin.abudhabi"])
adm = ac.get("/api/queue/contacts").json()
check("the hospital admin sees everyone's contacts and one line per staff member",
      not adm["own"] and len(adm["by_staff"]) >= 2 and all(s["contacts"] >= 0 for s in adm["by_staff"]) and adm["total"] >= team_["total"] - 1)
check("outreach staff cannot read the admin's outbox", oc.get("/api/admin/outbox").status_code in (401, 403))
# sending: refused without a number, works as a practice with one
q = oc.get("/api/queue?n=40").json()["items"]
_p = A.people().set_index("Id")
for it in q[:3]:                                    # give three people in the queue a (practice) number, as their family would in My care
    row_ = _p.loc[it["id"]]
    messaging.set_number(row_.guardian_id if row_.is_child == 1 else it["id"], "+971500009999")
sent_ok = refused = 0
for it in q:
    rr = oc.post("/api/queue/send", json={"id": it["id"], "channel": "SMS"})
    if rr.status_code == 200 and rr.json()["status"] == "dry run":
        sent_ok += 1
    elif rr.status_code == 400:
        refused += 1
check("the queue can send the approved message as a practice for people with a number, and refuses the rest with a reason", sent_ok >= 1 and refused >= 1)
box = ac.get("/api/admin/outbox").json()
check("the admin sees the practice in the outbox with the mode", box["mode"] == "dry run" and len(box["rows"]) >= sent_ok)
# households show the guardian and the children's ages
hh = ac.get("/api/households?limit=5").json()
check("each household row names the guardian's record number and the children's ages", hh and all(h.get("guardian") and h.get("children_ages") for h in hh))
det = ac.get(f"/api/household/{hh[0]['household_id']}").json()
check("a household says how it is identified and labels each member", det["how"] and {m["relation"] for m in det["members"]} >= {"Child"} and det["guardian_record"])
# the admin's Overview no longer repeats Today; results are the manager's
adm_pages = auth.ROLE_PAGES["admin"]
check("the hospital admin's pages no longer include Results (the manager's page)", "results" not in adm_pages and "results" in auth.ROLE_PAGES["manager"])
# removing it all
S.remove()
check("removing the demo data removes its accounts and practice numbers",
      not any(u_ in (auth.load_users() or {}) for u_ in demo["staff"] + demo["patients"]) and messaging.get_number(demo["phones"][0]) is None)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
