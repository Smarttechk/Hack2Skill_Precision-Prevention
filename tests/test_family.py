"""
Adding a child to a patient account (api/family.py): the parent asks, the hospital admin checks the record and approves,
and only then does the child appear in My care. Adults are not added under someone else's account.
    python tests/test_family.py
"""
import json
import os
import sys
import tempfile
import warnings
from datetime import date, timedelta

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp()
USERS = os.path.join(tmp, "users.json")
os.environ["PP_USERS_FILE"] = USERS
os.environ["PP_SIGNUP_FILE"] = os.path.join(tmp, "signup_requests.json")
os.environ["PP_CARETEAM_FILE"] = os.path.join(tmp, "care_team.json")
os.environ["PP_OUTREACH_DIR"] = tmp
os.environ["PP_FAMILY_FILE"] = os.path.join(tmp, "family_requests.json")
os.environ.pop("PP_PASSWORD", None)
os.environ.pop("PP_DEMO", None)
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from api import app as appmod, auth, family  # noqa: E402

auth.ACCESS_LOG = os.path.join(tmp, "access_log.csv")
auth.LIMITS_FILE = os.path.join(tmp, "limits.json")
auth.SESSIONS_FILE = os.path.join(tmp, "sessions.json")
ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


PW = "family-pass-12345"
AD, AIN = "Abu Dhabi", "Al Ain"
p = appmod.people()
adults = p[(p.is_child == 0) & (p.region == AD)]
has_kids = set(p[p.is_child == 1].guardian_id.dropna())
mum = adults[~adults.Id.isin(has_kids)].Id.iloc[0]                       # an Abu Dhabi adult with no registered children
kids = p[(p.is_child == 1) & (p.region == AD)]
kid = kids.iloc[0]                                                          # someone else's child, same hospital
far_kid = p[(p.is_child == 1) & (p.region == AIN)].iloc[0]
stranger = adults.Id.iloc[1] if adults.Id.iloc[0] == mum else adults.Id.iloc[0]


def acct(role, **extra):
    s, h = auth.hash_password(PW)
    return {"role": role, "salt": s, "hash": h, **extra}


json.dump({"users": {"mum": acct("patient", person=mum), "other": acct("patient", person=stranger), "boss": acct("admin"),
                     "ain_admin": acct("admin", region=AIN), "ad_admin": acct("admin", region=AD), "doc": acct("doctor")}}, open(USERS, "w"))
c = TestClient(server.app)
A = {u: (u, PW) for u in ("mum", "other", "boss", "ain_admin", "ad_admin", "doc")}
get = lambda path, who: c.get(path, auth=A[who])  # noqa: E731
post = lambda path, who, body=None: c.post(path, auth=A[who], json=body or {})  # noqa: E731
dob = lambda years: (date.today() - timedelta(days=int(365.25 * years) + 5)).isoformat()  # noqa: E731
GOOD = {"first_name": "Salem", "dob": dob(4), "relation": "Mother", "record_number": appmod.mrn(kid.Id), "note": "my son"}

n0 = len(get("/api/mycare", "mum").json()["people"])
check("before any request the parent sees only themselves", n0 == 1 and get("/api/mycare/family", "mum").json()["requests"] == [])

# ---- the request
bad = lambda **kw: post("/api/mycare/family/request", "mum", {**GOOD, **kw}).status_code  # noqa: E731
check("an adult cannot be added (they sign up for their own account), nor a future date, a short name or an unknown relation",
      bad(dob=dob(30)) == 400 and bad(dob=(date.today() + timedelta(days=5)).isoformat()) == 400 and bad(first_name="S") == 400
      and bad(relation="Cousin") == 400 and bad(dob="not-a-date") == 400)
r1 = post("/api/mycare/family/request", "mum", GOOD)
check("a valid request is saved as waiting for the hospital", r1.status_code == 200 and r1.json()["saved"]
      and get("/api/mycare/family", "mum").json()["requests"][0]["status"] == "pending")
check("the same request twice is refused", post("/api/mycare/family/request", "mum", GOOD).status_code == 400)
post("/api/mycare/family/request", "mum", {**GOOD, "first_name": "Mona", "dob": dob(2)})
post("/api/mycare/family/request", "mum", {**GOOD, "first_name": "Huda", "dob": dob(7)})
check("at most three requests wait at a time", post("/api/mycare/family/request", "mum", {**GOOD, "first_name": "Rashid", "dob": dob(9)}).status_code == 400)
check("before approval the child is not visible at all: not in My care, not by record, not by FHIR",
      len(get("/api/mycare", "mum").json()["people"]) == n0 and get(f"/api/fhir/Patient/{kid.Id}", "mum").status_code == 404
      and get(f"/api/mycare/{kid.Id}/history", "mum").status_code == 404)
check("only patient accounts can ask (staff get a refusal)", post("/api/mycare/family/request", "doc", GOOD).status_code == 403)

# ---- the hospital admin
q = get("/api/admin/family/requests", "boss").json()
check("the admin sees the waiting requests with the parent's record number, never a clinical detail",
      len(q) == 3 and all(x["parent"] == appmod.mrn(mum) for x in q) and "risk" not in json.dumps(q).lower())
check("a hospital admin of another hospital does not see them; this hospital's admin does",
      get("/api/admin/family/requests", "ain_admin").json() == [] and len(get("/api/admin/family/requests", "ad_admin").json()) == 3)
check("a parent and a doctor cannot read or decide the requests",
      get("/api/admin/family/requests", "mum").status_code == 403 and get("/api/admin/family/requests", "doc").status_code == 403)
rid = next(x["id"] for x in q if x["first_name"] == "Salem")
found = get(f"/api/admin/family/find?record={appmod.mrn(kid.Id)}", "ad_admin").json()
check("the admin finds the child by record number and sees who the child is registered to",
      found["total"] >= 1 and any(x["id"] == kid.Id and x["registered_to"] == appmod.mrn(kid.guardian_id) for x in found["children"]))
check("the other hospital's admin cannot find or approve a child of this hospital",
      get(f"/api/admin/family/find?record={appmod.mrn(kid.Id)}", "ain_admin").json()["total"] == 0
      and post(f"/api/admin/family/{rid}/approve", "ain_admin", {"child": kid.Id}).status_code == 400)
check("an adult's record cannot be chosen as the child", post(f"/api/admin/family/{rid}/approve", "ad_admin", {"child": stranger}).status_code == 400)
check("a child of another hospital cannot be chosen", post(f"/api/admin/family/{rid}/approve", "boss", {"child": far_kid.Id}).status_code == 400)
check("approving needs a child record", post(f"/api/admin/family/{rid}/approve", "boss", {}).status_code == 400)
check("a rejection needs a reason", post(f"/api/admin/family/{rid}/reject", "boss", {"reason": ""}).status_code == 400)

rej = next(x["id"] for x in q if x["first_name"] == "Mona")
post(f"/api/admin/family/{rej}/reject", "boss", {"reason": "No child with that date of birth: please register at the clinic"})
mine_now = {x["first_name"]: x for x in get("/api/mycare/family", "mum").json()["requests"]}
check("a rejected request shows the parent the reason", mine_now["Mona"]["status"] == "rejected" and "register at the clinic" in mine_now["Mona"]["reason"])

ap = post(f"/api/admin/family/{rid}/approve", "ad_admin", {"child": kid.Id})
mc = get("/api/mycare", "mum").json()
check("after approval the child appears in the parent's My care, and the parent keeps their own record",
      ap.status_code == 200 and len(mc["people"]) == n0 + 1 and any(not x["self"] for x in mc["people"]))
check("the registered guardian is not replaced: the child stays linked to them, and the request is closed",
      appmod.people().set_index("Id").loc[kid.Id].guardian_id == kid.guardian_id
      and post(f"/api/admin/family/{rid}/approve", "boss", {"child": kid.Id}).status_code == 400)
check("the parent can open the child's history and FHIR record; another family's account still cannot",
      get(f"/api/mycare/{kid.Id}/history", "mum").status_code == 200 and get(f"/api/fhir/Patient/{kid.Id}", "mum").status_code == 200
      and get(f"/api/fhir/Patient/{kid.Id}", "other").status_code == 404 and get(f"/api/mycare/{kid.Id}/history", "other").status_code == 404)
check("the parent can set the child's contact choices", post(f"/api/why/{kid.Id}/consent", "mum", {"contact": True, "use_genetic": False}).status_code == 200)
check("the parent can share the child's record with another hospital",
      post("/api/mycare/sharing", "mum", {"person": kid.Id, "hospital": AIN, "days": 7}).status_code == 200)

# the registered guardian asking for their own child is refused at approval
reg = kid.guardian_id
json.dump({"users": {**json.load(open(USERS))["users"], "reg": acct("patient", person=reg)}}, open(USERS, "w"))
A["reg"] = ("reg", PW)
post("/api/mycare/family/request", "reg", {**GOOD, "first_name": "Again", "dob": dob(5)})
rid2 = next(x["id"] for x in get("/api/admin/family/requests", "boss").json() if x["first_name"] == "Again")
check("a parent who is already the registered guardian cannot be added again",
      post(f"/api/admin/family/{rid2}/approve", "boss", {"child": kid.Id}).status_code == 400)

# ---- a new patient with no hospital record yet
newp = c.post("/api/signup", json={"role": "patient", "user": "newmum", "password": PW, "details": {
    "full_name": "Newcomer Test", "phone": "+971500000009", "date_of_birth": "1992-06-01", "emirates_id": "784-1992-7654321-3", "language": "Arabic",
    "home_region": AD}})
pend = next(x for x in get("/api/admin/requests", "ad_admin").json() if x["user"] == "newmum")
check("a new patient's sign-up waits; approving it without a record is refused unless the admin says it is a new patient",
      newp.status_code == 200 and post(f"/api/admin/requests/{pend['id']}/approve", "ad_admin", {}).status_code == 400)
adult_in_ain = p[(p.is_child == 0) & (p.region == AIN)].Id.iloc[0]
check("a hospital admin can approve a new patient without a record (no record needs choosing)",
      post(f"/api/admin/requests/{pend['id']}/approve", "ad_admin", {"without_record": True}).status_code == 200)
me = get("/api/me", "newmum").json() if False else c.get("/api/me", auth=("newmum", PW)).json()
check("the new patient signs in and gets only the My care page, marked as waiting for a record",
      me["role"] == "patient" and me["awaiting_record"] is True and me["pages"] == ["mycare"] and me["person"] is None)
A["newmum"] = ("newmum", PW)
mc0 = get("/api/mycare", "newmum").json()
check("My care says the record is being set up and shows no person", mc0["awaiting_record"] is True and mc0["people"] == [])
seen = {"/api/overview": get("/api/overview", "newmum").json(), "why": get("/api/why/people", "newmum").json()}
check("nothing about anyone else leaks: the overview counts nobody, the Why list is empty, no record can be opened",
      seen["/api/overview"]["people"] == 0 and seen["why"] == [] and get(f"/api/fhir/Patient/{kid.Id}", "newmum").status_code in (403, 404)
      and get(f"/api/why/{kid.Id}", "newmum").status_code in (403, 404) and get(f"/api/mycare/{kid.Id}/history", "newmum").status_code in (400, 404)
      and get("/api/doctor/list", "newmum").status_code == 403 and get("/api/queue", "newmum").status_code == 403)
check("a patient without a record cannot ask to add a child yet", post("/api/mycare/family/request", "newmum", GOOD).status_code == 400)
users = {u["name"]: u for u in get("/api/admin/users", "ad_admin").json()["users"]}
check("the admin list marks the account as waiting for a record, with the date of birth from the sign-up to find it by",
      users["newmum"]["awaiting_record"] and users["newmum"]["signup_dob"] == "1992-06-01"
      and "newmum" not in {u["name"] for u in get("/api/admin/users", "ain_admin").json()["users"]})
check("the other hospital's admin cannot link it; a child's record is refused; an adult of another hospital is refused",
      post("/api/admin/users/newmum/link", "ain_admin", {"person": adult_in_ain}).status_code == 404
      and post("/api/admin/users/newmum/link", "ad_admin", {"person": kid.Id}).status_code == 400
      and post("/api/admin/users/newmum/link", "ad_admin", {"person": adult_in_ain}).status_code == 400)
check("the overview counts the patient waiting for a record", get("/api/overview", "ad_admin").json()["awaiting_records"] == 1)
newrec = adults[~adults.Id.isin(has_kids) & (adults.Id != mum)].Id.iloc[3]
check("linking the record the hospital created opens the account fully",
      post("/api/admin/users/newmum/link", "ad_admin", {"person": newrec}).status_code == 200
      and c.get("/api/me", auth=("newmum", PW)).json()["pages"] == ["mycare", "why"]
      and get("/api/mycare", "newmum").json()["people"][0]["self"] is True and get("/api/overview", "ad_admin").json()["awaiting_records"] == 0)
check("an account that is already linked cannot be linked again", post("/api/admin/users/newmum/link", "ad_admin", {"person": newrec}).status_code == 400)
check("an account opened without a record keeps no stray marker once linked",
      "awaiting_record" not in json.load(open(USERS))["users"]["newmum"])

log = open(auth.ACCESS_LOG, encoding="utf-8").read()
check("the request, the approval and the rejection are in the access log",
      "family_request" in log and "family_approved" in log and "family_rejected" in log)
check("the file holds no clinical detail, only what the parent typed and the decision",
      not any(w in open(family.path(), encoding="utf-8").read().lower() for w in ("diabetes", "risk", "genetic")))
ov = get("/api/overview", "boss").json()
check("the admin's overview counts the family requests still waiting", ov["pending_family"] == 2)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
