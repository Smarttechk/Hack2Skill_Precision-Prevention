"""
Checks every API endpoint the React frontend uses.
    python tests/test_api.py
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
# the endpoint checks run in open mode, even when config/users.json (real or demo accounts) exists
os.environ["PP_USERS_FILE"] = os.path.join(ROOT, "tests", "no_accounts_here.json")
os.environ["PP_DEMO"] = "1"          # these tests also play the patient phone, a demo tool hidden in production
os.environ.pop("PP_PASSWORD", None)
import tempfile  # noqa: E402
os.environ["PP_OUTREACH_DIR"] = tempfile.mkdtemp()      # contacts recorded below go to a temporary folder, never the real outputs
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from api import auth as _a  # noqa: E402
_a.SESSIONS_FILE = os.path.join(tempfile.mkdtemp(), "sessions.json")   # tests never touch the real sessions or access log
_a.ACCESS_LOG = os.path.join(os.path.dirname(_a.SESSIONS_FILE), "access_log.csv")
_a.LIMITS_FILE = os.path.join(os.path.dirname(_a.SESSIONS_FILE), "auth_limits.json")
_a._FAILURES.clear(); _a._SIGNUPS.clear()       # start from no counts (the real file may hold some)
from api.app import app  # noqa: E402

c = TestClient(app)
ok = []


def check(name, cond):
    ok.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)


def get(url):
    r = c.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code} {r.text[:200]}"
    return r.json()


check("status", get("/api/status")["ready"])
# open mode (no accounts): "Preview as patient" shows My care for the largest family, with its history
_open = get("/api/mycare")
_hist_open = get(f"/api/mycare/{_open['people'][-1]['id']}/history")
check(f"open mode: My care previews a sample family ({len(_open['people'])} people, one marked as the parent) with the clinic and history",
      len(_open["people"]) > 2 and sum(x["self"] for x in _open["people"]) == 1 and all(x["clinic"] for x in _open["people"])
      and _hist_open["available"] in (True, False) and c.get("/api/mycare/not-a-person/history").status_code == 404)
ov = get("/api/overview")
check(f"overview ({ov['people']} people, {ov['urgent']} urgent)", ov["people"] > 4000 and ov["regions"])
dl = get("/api/doctor/list?urgency=Urgent,High,Routine&limit=50")
check(f"doctor list sorted Urgent first ({dl['total']} open)", dl["items"] and dl["items"][0]["urgency"] == "Urgent")
pid = dl["items"][0]["id"]
pr = get(f"/api/person/{pid}")
check("person detail has risk text, cards and data used", pr["risk_text"] and pr["cards"] and pr["data_used"] and pr["summary"])
r = c.post(f"/api/person/{pid}/feedback", json={"decision": "Disagree", "note": "test"})
check("doctor Disagree is saved", r.status_code == 200 and "hold" in r.json()["effect"])
check("bad decision rejected", c.post(f"/api/person/{pid}/feedback", json={"decision": "Maybe"}).status_code == 400)
after = get(f"/api/person/{pid}")
left = {c["check"] for c in after["cards"] if c["status"] in ("ready", "agreed", "awaiting_doctor")}
check("Disagree holds every card except a results review or emergency call-back",
      left <= {"results_review", "emergency_callback"}
      and (after["state"] == "On hold" if not left else pid in {i["id"] for i in get("/api/doctor/list?urgency=Urgent,High,Routine&limit=5000")["items"]}))
held = next((i for i in get("/api/doctor/list?urgency=High,Routine&limit=500")["items"]
             if not set(i["checks"]) & {"results_review", "emergency_callback"}), None)
if held:
    c.post(f"/api/person/{held['id']}/feedback", json={"decision": "Disagree", "note": "test"})
    check("Disagree with no urgent card takes the person off the doctor list and the queue",
          all(i["id"] != held["id"] for i in get("/api/doctor/list?urgency=Urgent,High,Routine&limit=5000")["items"])
          and all(i["id"] != held["id"] for i in get("/api/queue?n=5000")["items"]) and get(f"/api/person/{held['id']}")["state"] == "On hold")
import server  # noqa: E402
sc = TestClient(server.app)
import os  # noqa: E402
os.environ["PP_PASSWORD"] = "test-pw"
locked = sc.get("/api/status").status_code
ok_in = sc.get("/api/status", auth=("clinic", "test-pw")).status_code
wrong = sc.get("/api/status", auth=("clinic", "nope")).status_code
del os.environ["PP_PASSWORD"]
check("with PP_PASSWORD set, every request needs the password", locked == 401 and ok_in == 200 and wrong == 401)
import json as _json, tempfile  # noqa: E402
import api.app as appmod  # noqa: E402
from api import auth as _auth  # noqa: E402
uf = os.path.join(tempfile.mkdtemp(), "users.json")
_pp = appmod.people()
_family = _pp[(_pp.is_child == 1) & _pp.guardian_id.notna()].guardian_id.value_counts().index[0]
_region = _pp.region.value_counts().index[0]


def _acct(role, pw, **extra):
    s_, h_ = _auth.hash_password(pw)
    return {"role": role, "salt": s_, "hash": h_, **extra}


_json.dump({"users": {"dr_test": _acct("doctor", "doctor-pass-123"),
                      "dr_region": _acct("doctor", "region-pass-123", region=_region),
                      "boss": _acct("admin", "admin-pass-123"),
                      "fam": _acct("patient", "family-pass-123", person=_family)}}, open(uf, "w"))
os.environ["PP_USERS_FILE"] = uf
d_list = sc.get("/api/doctor/list", auth=("dr_test", "doctor-pass-123")).status_code
d_queue = sc.get("/api/queue", auth=("dr_test", "doctor-pass-123")).status_code
d_me = sc.get("/api/me", auth=("dr_test", "doctor-pass-123")).json()
d_bad = sc.get("/api/doctor/list", auth=("dr_test", "wrong")).status_code
check("accounts: a doctor can open the doctor list but not the outreach queue (checked on the server)",
      d_list == 200 and d_queue == 403 and d_bad == 401 and d_me["role"] == "doctor" and d_me["signed_in"])
anon = sc.get("/api/me").json()
page = sc.get("/doctor")
check("before signing in: the app loads, says sign-in is needed, and no data is served without a session",
      page.status_code == 200 and anon["sign_in_required"] and not anon["signed_in"]
      and sc.get("/api/doctor/list").status_code == 401 and "WWW-Authenticate" not in sc.get("/api/doctor/list").headers)




def _login(user, pw):
    """A separate browser per user: the session lives in its HttpOnly cookie."""
    b = TestClient(server.app)
    r = b.post("/api/login", json={"user": user, "password": pw})
    return r, b


r_, B = _login("dr_region", "region-pass-123")
ck = r_.headers.get("set-cookie", "").lower()
check("signing in sets an HttpOnly, SameSite=Strict cookie; the token is not in the page's reach or stored in plain text",
      r_.status_code == 200 and "httponly" in ck and "samesite=strict" in ck and "token" not in r_.json()
      and B.cookies.get(_auth.COOKIE) not in open(_auth.SESSIONS_FILE).read())
dl_ = B.get("/api/doctor/list?urgency=Urgent,High,Routine&limit=5000").json()["items"]
other = _pp[_pp.region != _region].Id.iloc[0]
check(f"a doctor with a region sees only people in {_region}, and nobody else even by direct link",
      r_.json()["region"] == _region and dl_ and all(i["region"] == _region for i in dl_)
      and B.get(f"/api/person/{other}").status_code == 404 and B.get("/api/overview").json()["regions"] == [_region])
B.get(f"/api/person/{dl_[0]['id']}")
appmod.live.cache_clear()                                   # a server restart: the session is kept (stored as a hash)
check("a sign-in survives a server restart, and opening a record is in the access log",
      B.get("/api/overview").status_code == 200
      and any(l.startswith("20") and "dr_region" in l and "opened_record" in l and dl_[0]["id"] in l for l in open(_auth.ACCESS_LOG)))
r_, B = _login("fam", "family-pass-123")
fam_ids = set(_pp[(_pp.Id == _family) | (_pp.guardian_id == _family)].Id)
mc = B.get("/api/mycare").json()
check("a patient account has My care and Why only: its own record and children in plain words, nothing of anyone else",
      r_.json()["pages"] == ["mycare", "why"] and {x["id"] for x in mc["people"]} == fam_ids
      and not any(w in str(mc).lower() for w in ("risk", "brca", "gene", "diabetes", "hypertension"))
      and B.get(f"/api/why/{other}").status_code == 404 and B.get(f"/api/why/{_family}").status_code == 200
      and B.get("/api/doctor/list").status_code == 403 and B.get("/api/results").status_code == 403
      and B.get("/api/phone/people").status_code == 403)
wp = B.get("/api/why/people")
check("the patient's Why page lists only themselves and their children",
      wp.status_code == 200 and {x["id"] for x in wp.json()} == fam_ids and wp.json()[0]["label"] == "You")
check(f"one account covers a parent and all {len(fam_ids) - 1} of their children, each with the nearest clinic and the trip there",
      len(fam_ids) > 2 and len(mc["people"]) == len(fam_ids) and sum(x["self"] for x in mc["people"]) == 1
      and all(x["clinic"] and x["clinic"]["name"] and x["clinic"]["minutes"] > 0 for x in mc["people"]))
# a guardian covers children only: an adult who still carries a guardian link is not opened to that account
_fake = pd.DataFrame({"Id": ["P", "K1", "K2", "A1", "X"], "guardian_id": [None, "P", "P", "P", "Q"], "is_child": [0, 1, 1, 0, 1]})
_tok = appmod.ACCOUNT.set({"person": "P", "role": "patient"})
_seen = appmod.mine(_fake).Id.tolist()
appmod.ACCOUNT.reset(_tok)
check("a patient account sees its own record and its children, never an adult with a guardian link or another family's child",
      _seen == ["P", "K1", "K2"])
_child = _pp[_pp.is_child == 1].Id.iloc[0]
try:
    _auth.account_record("patient", "family-pass-123", person=_child)
    _refused = False
except ValueError as e_:
    _refused = "child" in str(e_)
check("a patient account cannot be linked to a child's record: children come with a parent's account", _refused)
_kid = next(x["id"] for x in mc["people"] if not x["self"])
_hist = B.get(f"/api/mycare/{_kid}/history").json()
check("My care history: a child's last visits (date and kind only) and vaccines with their dates, read-only; nobody else's",
      _hist["available"] and 0 < len(_hist["visits"]) <= 12 and all(set(v) == {"date", "kind"} for v in _hist["visits"])
      and all(v["kind"] in appmod.VISIT_KIND.values() for v in _hist["visits"])
      and all(v["name"] and v["dates"] for v in _hist["vaccines"])
      and not any(w in str(_hist).lower() for w in ("diabetes", "hypertension", "risk", "brca"))
      and B.get(f"/api/mycare/{other}/history").status_code == 404 and B.get(f"/api/mycare/{_kid}/history").status_code == 200)
_u = next(x["clinic"]["map_url"] for x in mc["people"] if x["clinic"] and x["clinic"]["map_url"])
_lat, _lon = (float(v) for v in __import__("re").search(r"query=([\d.]+),([\d.]+)", _u).groups())
_fac = appmod.facility_table()
check("the clinic map link opens a real clinic's position and nothing about the person",
      _u.startswith("https://www.google.com/maps/search/?api=1&query=") and (((_fac.lat - _lat).abs() < 1e-4) & ((_fac.lon - _lon).abs() < 1e-4)).any()
      and all(x["clinic"]["minutes"] > 0 for x in mc["people"] if x["clinic"]))
_json.dump({"users": {**_json.load(open(uf))["users"], "plan": _acct("manager", "manager-pass-123")}}, open(uf, "w"))
r_, B = _login("plan", "manager-pass-123")
check("a programme manager sees totals (results, map, communities) but no individual patient record",
      B.get("/api/results").status_code == 200 and B.get("/api/map").status_code == 200
      and B.get(f"/api/person/{other}").status_code == 403 and B.get("/api/doctor/list").status_code == 403)
r_, A = _login("boss", "admin-pass-123")
check("the hospital admin sees every page and every region, and may reload results",
      set(r_.json()["pages"]) == set(_auth.ROLE_PAGES["admin"]) and len(A.get("/api/overview").json()["regions"]) > 1
      and A.post("/api/reload").status_code == 200)
made = A.post("/api/admin/users", json={"name": "dr_new", "role": "doctor", "password": "new-doctor-123", "region": _region})
short = A.post("/api/admin/users", json={"name": "dr_x", "role": "doctor", "password": "short"})
nopat = A.post("/api/admin/users", json={"name": "pat_x", "role": "patient", "password": "patient-pass-123"})
r_, N = _login("dr_new", "new-doctor-123")
check("the admin creates accounts on the Accounts page (short passwords and unlinked patient accounts are refused)",
      made.status_code == 200 and short.status_code == 400 and nopat.status_code == 400 and r_.status_code == 200
      and any(u["name"] == "dr_new" and u["region"] == _region for u in A.get("/api/admin/users").json()["users"]))
A.post("/api/admin/users/dr_new/password", json={"password": "reset-doctor-123"})
check("an admin password reset signs the person out everywhere", N.get("/api/overview").status_code == 401
      and _login("dr_new", "reset-doctor-123")[0].status_code == 200)
r_, N = _login("dr_new", "reset-doctor-123")
ch = N.post("/api/me/password", json={"old_password": "reset-doctor-123", "new_password": "own-choice-1234"})
check("everyone can change their own password (the old one is checked)",
      ch.status_code == 200 and _login("dr_new", "own-choice-1234")[0].status_code == 200
      and N.post("/api/me/password", json={"old_password": "wrong", "new_password": "x" * 12}).status_code in (400, 401))
r_, N = _login("dr_new", "own-choice-1234")
A.post("/api/admin/users/dr_new/remove")
check("removing an account ends its session at once, and the admin cannot remove their own account",
      N.get("/api/overview").status_code == 401 and A.post("/api/admin/users/boss/remove").status_code == 400)
A.post("/api/logout")
check("signing out ends the session", A.get("/api/overview").status_code == 401)
_auth._FAILURES.clear()                                   # start counting from zero (an earlier check used a wrong password)
tries = [sc.post("/api/login", json={"user": "dr_test", "password": "wrong"}).status_code for _ in range(_auth.MAX_FAILURES)]
locked_ok = sc.post("/api/login", json={"user": "dr_test", "password": "doctor-pass-123"}).status_code
elsewhere = _auth.verify("dr_test", "doctor-pass-123", "another-computer")
check(f"after {_auth.MAX_FAILURES} wrong passwords that user name is locked on that computer, but not for the real "
      "doctor on another computer; the admin sees it locked and can unlock it",
      set(tries[:-1]) == {401} and tries[-1] in (401, 429) and locked_ok == 429 and elsewhere is not None
      and "dr_test" in _auth.locked_users())
_auth._FAILURES.clear(); _auth._load_limits()          # a server restart: counts come back from the file
check("a lock survives a server restart (counts are kept in a file)", "dr_test" in _auth.locked_users())
_auth.unlock("dr_test")
# ---- sign-up: every role can ask, the admin approves (the first admin is active at once)
os.environ["PP_SIGNUP_FILE"] = os.path.join(os.path.dirname(uf), "signup_requests.json")
_auth._SIGNUPS.clear()
pub = TestClient(server.app)
inf = pub.get("/api/public/info")
check("before signing in, the landing page gets sign-up fields and totals only (no person data)",
      inf.status_code == 200 and set(inf.json()["fields"]) == set(_auth.ROLE_PAGES) and "Id" not in str(inf.json()))
sd = pub.post("/api/signup", json={"role": "doctor", "user": "dr_signup", "password": "signup-doctor-1",
                                    "details": {"full_name": "Dr Amina K", "email": "a@x.ae", "phone": "+971501234567",
                                                "license_number": "GD123", "specialty": "Family medicine", "facility": "Clinic A",
                                                "region": _region}})
waiting = _login("dr_signup", "signup-doctor-1")[0]
check("a doctor can sign up; until approved, signing in says the request is waiting (a wrong password gets no hint)",
      sd.status_code == 200 and sd.json()["status"] == "pending" and waiting.status_code == 403
      and "waiting" in waiting.json()["detail"] and _login("dr_signup", "wrong-password-1")[0].status_code == 401)
bad_eid = pub.post("/api/signup", json={"role": "patient", "user": "pat_s", "password": "signup-patient-1",
                                        "details": {"full_name": "Sara M", "phone": "+971500000000", "date_of_birth": "1990-01-01",
                                                    "emirates_id": "12345", "language": "Arabic"}})
sp = pub.post("/api/signup", json={"role": "patient", "user": "pat_s", "password": "signup-patient-1",
                                   "details": {"full_name": "Sara M", "phone": "+971500000000", "date_of_birth": "1990-01-01",
                                               "emirates_id": "784-1990-1234567-1", "language": "Arabic"}})
missing = pub.post("/api/signup", json={"role": "outreach", "user": "out_s", "password": "signup-outreach-1", "details": {"full_name": "X"}})
taken = pub.post("/api/signup", json={"role": "manager", "user": "dr_signup", "password": "signup-manager-1",
                                      "details": {"full_name": "M", "email": "m@x.ae", "phone": "1", "department": "PH"}})
stored = open(os.environ["PP_SIGNUP_FILE"]).read()
check("a patient signs up with an Emirates ID (checked format; only the last 4 digits and a hash are kept; no password kept)",
      bad_eid.status_code == 400 and sp.status_code == 200 and "784-1990-1234567" not in stored
      and "784199012345671" not in stored and '"emirates_id_last4": "5671"' in stored
      and "signup-patient-1" not in stored)
check("missing details and taken user names are refused", missing.status_code == 400 and taken.status_code == 400)
r_, A = _login("boss", "admin-pass-123")
reqs = A.get("/api/admin/requests").json()
check("the hospital admin is told how many sign-ups are waiting", A.get("/api/me").json()["pending_requests"] == 2)
rid = {r["user"]: r["id"] for r in reqs}
# the record finder: the admin looks up the patient's own record by date of birth or record number (adults only)
_fp = A.get(f"/api/admin/find_person?record={_family[:8]}").json()
_born = _fp["people"][0]["born"] if _fp["people"] else ""
_fd = A.get(f"/api/admin/find_person?dob={_born}").json()
check("the admin finds a patient's record by record number or date of birth; children are never offered; others cannot search",
      _fp["people"] and _fp["people"][0]["id"] == _family and any(x["id"] == _family for x in _fd["people"])
      and all(x["age"] >= 18 for x in _fd["people"]) and A.get("/api/admin/find_person").status_code == 400
      and pub.get(f"/api/admin/find_person?dob={_born}").status_code == 401)
no_link = A.post(f"/api/admin/requests/{rid['pat_s']}/approve", json={})
A.post(f"/api/admin/requests/{rid['dr_signup']}/approve", json={"region": _region})
A.post(f"/api/admin/requests/{rid['pat_s']}/approve", json={"person": _family})
r_, D = _login("dr_signup", "signup-doctor-1")
r2, Pt = _login("pat_s", "signup-patient-1")
check("the admin sees the requests and approves them: the doctor gets their region, the patient must be linked to a record",
      len(reqs) == 2 and no_link.status_code == 400 and r_.status_code == 200 and r_.json()["region"] == _region
      and r2.status_code == 200 and r2.json()["person"] == _family and A.get("/api/admin/requests").json() == []
      and pub.get("/api/admin/requests").status_code == 401 and D.get("/api/admin/requests").status_code == 403)
first = os.path.join(os.path.dirname(uf), "empty_users.json")
os.environ["PP_USERS_FILE"] = first
_auth._SIGNUPS.clear()
fa = pub.post("/api/signup", json={"role": "admin", "user": "first_admin", "password": "first-admin-123",
                                   "details": {"full_name": "H A", "email": "h@x.ae", "phone": "1", "hospital": "H", "job_title": "IT"}})
check("with no accounts yet, the first hospital admin to sign up is active at once",
      fa.json().get("status") == "active" and _login("first_admin", "first-admin-123")[0].status_code == 200)
os.environ["PP_USERS_FILE"] = uf
del os.environ["PP_SIGNUP_FILE"]
check("after unlocking, the right password works again",
      sc.post("/api/login", json={"user": "dr_test", "password": "doctor-pass-123"}).status_code == 200)
_auth._FAILURES.clear()
del os.environ["PP_USERS_FILE"]
os.environ["PP_USERS_FILE"] = os.path.join(ROOT, "tests", "no_accounts_here.json")
os.environ["PP_DEMO"] = "1"          # these tests also play the patient phone, a demo tool hidden in production
check("the web server never returns files outside the app",
      all("[fonts]" not in sc.get(u).text and "usd_to_aed" not in sc.get(u).text
          for u in ("/C:%5CWindows%5Cwin.ini", "/..%2Fconfig%2Freal_values.json", "/../config/real_values.json")))
q = get("/api/queue?n=20")
check(f"outreach queue ({q['waiting']} waiting)", len(q["items"]) == 20 and all(i["best_time"] for i in q["items"]))
check("queue marks children's contact as guardian", any(i["contact"] == "Guardian of child" for i in get("/api/queue?n=120")["items"]))
check("queue done saved", c.post("/api/queue/done", json={"ids": [q["items"][0]["id"]]}).json()["saved"] == 1)
hh = get("/api/households?limit=5")
h = get(f"/api/household/{hh[0]['household_id']}")
check(f"household view ({len(h['members'])} members)", len(h["members"]) > 1)
js = get("/api/journeys?limit=20")
j = get(f"/api/journey/{js[0]['id']}")
check(f"patient journey ({len(j['events'])} events, {len(j['messages'])} messages)", j["events"] and j["messages"])
ppl = get("/api/phone/people")
child = ppl[0]["id"]
s = c.post(f"/api/phone/{child}/start").json()
check("phone starts with the invitation", any(m["from"] == "clinic" for m in s["conversation"]) or "nurse" in str(s))
b = c.post(f"/api/phone/{child}/reply", json={"text": "BOOK"}).json()
check("BOOK books a visit", b["status"]["state"] == "Booked" and b["status"]["booked_for"])
st = c.post(f"/api/phone/{child}/reply", json={"text": "توقف"}).json()
check("Arabic STOP opts out", st["status"]["state"] == "Opted out" and not st["status"]["may_contact"])
import api.app as appmod  # noqa: E402
appmod.live.cache_clear(); appmod.SESSIONS.clear(); appmod.CONSENT_CHANGES.clear()     # a server restart
check("after a restart the phone demo still knows about the STOP", appmod.live().people[child].state == "Opted out"
      and child in appmod.SESSIONS)
st2 = c.post(f"/api/phone/{child}/reply", json={"text": "hello?"}).json()
check("no answer after opting out", "opted out" in st2["conversation"][-1]["text"])
stE = c.post(f"/api/phone/{child}/reply", json={"text": "he fainted"}).json()
check("emergency words are answered even after opting out", "998" in stE["conversation"][-1]["text"])
c.post(f"/api/alerts/emergency/{child}/done")
st3 = c.post(f"/api/phone/{child}/reply", json={"text": "START"}).json()
check("START opts back in", st3["status"]["may_contact"])
em = c.post(f"/api/phone/{child}/reply", json={"text": "my son fainted"}).json()
al = get("/api/alerts")
check("emergency words in the chat show on the Doctor page", any(e["person"] == child for e in al["emergencies"]))
top = get("/api/doctor/list?urgency=Urgent&limit=5")["items"]
check("the emergency person is first on the Urgent list", top and top[0]["id"] == child and "emergency_callback" in top[0]["checks"])
dn = c.post(f"/api/alerts/emergency/{child}/done").json()
check("Nurse called back closes the emergency", dn["closed"] and all(e["person"] != child for e in get("/api/alerts")["emergencies"]))
em = c.post(f"/api/phone/{child}/reply", json={"text": "my child has chest pain"}).json()
check("emergency answer gives 998", "998" in em["conversation"][-1]["text"])
c.post(f"/api/alerts/emergency/{child}/done")          # leave no open test emergency on the Doctor page
w = get(f"/api/why/{child}")
check("why page uses plain words only", w["checks"] and not any(x in str(w).lower() for x in ("brca", "gene_", "risk score")))
cs = c.post(f"/api/why/{child}/consent", json={"contact": False, "use_genetic": False}).json()
check("a child's consent is given by the guardian", cs["given_by"] == "guardian")
mp = get("/api/map")
check(f"map ({len(mp['places'])} places, {len(mp['facilities'])} facilities, attribution)", mp["places"] and mp["facilities"] and "OpenStreetMap" in mp["attribution"])
cm = get("/api/communities")
check(f"communities ({len(cm)}) with actions", cm and "actions" in cm[0])
rs = get("/api/results")
check("results with daily series, fairness and models", rs["daily"] and rs["fairness_new"] and rs.get("nhanes"))
check("unknown person returns 404", c.get("/api/person/nope").status_code == 404)

# a page of another site cannot make this server change anything
rel = {"id": "nope"}
foreign = c.post("/api/queue/release", json=rel, headers={"Origin": "http://evil.example"})
cross = c.post("/api/queue/release", json=rel, headers={"Sec-Fetch-Site": "cross-site"})
same = c.post("/api/queue/release", json=rel, headers={"Origin": "http://testserver"})
plain = c.post("/api/queue/release", json=rel)
check("a state-changing request from another site's page is refused; the app's own page, scripts and reads are not",
      foreign.status_code == 403 and "another site" in foreign.json()["detail"] and cross.status_code == 403
      and same.status_code != 403 and plain.status_code != 403 and c.get("/api/status", headers={"Origin": "http://evil.example"}).status_code == 200)

for f in ("doctor_feedback.csv", "outreach_log.csv", "consent_changes.csv", "phone_log.csv"):
    p = os.path.join(ROOT, "outputs", f)
    if os.path.exists(p):
        os.remove(p)
print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
