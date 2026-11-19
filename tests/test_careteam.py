"""
Care teams (api/careteam.py): doctor profiles, rule-based assignment, what each doctor sees, leave and backup, the duty
doctor, break glass, sharing with another hospital, hospital admins who stay inside their hospital, and the open-mode preview.
    python tests/test_careteam.py
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
tmp = tempfile.mkdtemp()
USERS = os.path.join(tmp, "users.json")
os.environ["PP_USERS_FILE"] = USERS
os.environ["PP_SIGNUP_FILE"] = os.path.join(tmp, "signup_requests.json")
os.environ["PP_CARETEAM_FILE"] = os.path.join(tmp, "care_team.json")
os.environ.pop("PP_PASSWORD", None)
os.environ.pop("PP_DEMO", None)
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from api import app as appmod, auth, careteam  # noqa: E402

auth.ACCESS_LOG = os.path.join(tmp, "access_log.csv")
auth.LIMITS_FILE = os.path.join(tmp, "limits.json")
auth.SESSIONS_FILE = os.path.join(tmp, "sessions.json")
ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


PW = "care-pass-12345"


def acct(role, **extra):
    s, h = auth.hash_password(PW)
    return {"role": role, "salt": s, "hash": h, **extra}


p = appmod.people()
AD, AIN = "Abu Dhabi", "Al Ain"
parent = p[(p.is_child == 1) & p.guardian_id.notna()].guardian_id.value_counts().index[0]
accounts = {
    "boss": acct("admin"), "adm_ad": acct("admin", region=AD), "adm_ain": acct("admin", region=AIN),
    "d_ped": acct("doctor", region=AD, specialty=["Paediatrics"], languages=["Urdu"], panel_limit=40),
    "d_ped2": acct("doctor", region=AD, specialty="paediatric", languages=["Arabic"], panel_limit=40),
    "d_gp": acct("doctor", region=AD, specialty=["Family medicine"], languages=["Arabic"], panel_limit=60),
    "d_gp2": acct("doctor", region=AD, specialty=["Family medicine"], languages=["English"], panel_limit=60),
    "d_ain": acct("doctor", region=AIN, specialty=["Family medicine", "Paediatrics"], languages=["Arabic"], panel_limit=80),
    "d_old": acct("doctor", region=AD),                                  # an older doctor account: no care-team profile
    "fam": acct("patient", person=parent), "nurse": acct("outreach"),
}
json.dump({"users": accounts}, open(USERS, "w"))
c = TestClient(server.app)
A = {u: (u, PW) for u in accounts}
get = lambda path, who: c.get(path, auth=A[who])  # noqa: E731
post = lambda path, who, body=None: c.post(path, auth=A[who], json=body or {})  # noqa: E731
ids = lambda who: {x["id"] for x in get("/api/doctor/list?urgency=Urgent,High,Routine&limit=5000", who).json()["items"]}  # noqa: E731
seen = lambda who: get("/api/overview", who).json()["people"]  # noqa: E731

# ---- profiles and what a doctor sees before anyone is assigned
ct = get("/api/admin/careteam", "boss").json()
check("the central admin sees every hospital and every care-team doctor; a specialty typed as free text is matched",
      {h["hospital"] for h in ct["hospitals"]} >= {AD, AIN} and {d["user"] for d in ct["doctors"]} == {"d_ped", "d_ped2", "d_gp", "d_gp2", "d_ain"}
      and next(d for d in ct["doctors"] if d["user"] == "d_ped2")["specialty"] == ["Paediatrics"])
check("a care-team doctor with nobody assigned sees nobody; an older doctor account still sees its whole region",
      seen("d_ped") == 0 and seen("d_old") == int((p.region == AD).sum()))

ct_boss = get("/api/admin/careteam", "boss").json()
check("doctor accounts without a specialty are listed for the admin, not hidden; a hospital admin sees only their hospital's",
      [d["user"] for d in ct_boss["not_in_a_team"]] == ["d_old"] and [d["user"] for d in get("/api/admin/careteam", "adm_ain").json()["not_in_a_team"]] == []
      and [d["user"] for d in get("/api/admin/careteam", "adm_ad").json()["not_in_a_team"]] == ["d_old"])
join = post("/api/admin/careteam/doctor", "adm_ad", {"user": "d_old", "specialty": ["Family medicine"], "languages": ["Arabic", "English"]})
check("adding a specialty puts an older doctor account into a care team: they then see only their own patients",
      join.status_code == 200 and seen("d_old") == 0 and "d_old" in {d["user"] for d in get("/api/admin/careteam", "boss").json()["doctors"]}
      and json.load(open(USERS))["users"]["d_old"]["specialty"] == ["Family medicine"])
u_ = json.load(open(USERS)); u_["users"]["d_old"].pop("specialty"); u_["users"]["d_old"].pop("languages"); json.dump(u_, open(USERS, "w"))   # back to the older kind

# ---- proposing and applying
prop = post("/api/admin/careteam/proposal", "boss").json()
check("a proposal saves nothing, lists each doctor with counts, and the people left over with the reason",
      prop["total"] > 0 and {d["user"] for d in prop["per_doctor"]} <= {"d_ped", "d_ped2", "d_gp", "d_gp2", "d_ain"} and seen("d_ped") == 0
      and any(x["why"] in ("no doctor", "patient lists full") for x in prop["left"]))
n = post("/api/admin/careteam/apply", "boss").json()["applied"]
store = careteam.load()["assignments"]
by_doc = {u: [pid for pid, team in store.items() if u in team.values()] for u in ("d_ped", "d_ped2", "d_gp", "d_gp2", "d_ain")}
row = p.set_index("Id")
check(f"applied {n} assignments: every one inside the doctor's hospital and specialty (children to paediatrics, adults to family medicine)",
      n == sum(len(team) for team in store.values())
      and all(row.loc[i].is_child == 1 and row.loc[i].region == AD for u in ("d_ped", "d_ped2") for i in by_doc[u])
      and all(row.loc[i].is_child == 0 and row.loc[i].region == AD for u in ("d_gp", "d_gp2") for i in by_doc[u])
      and all(row.loc[i].region == AIN for i in by_doc["d_ain"]))
check("no panel goes past its limit, and the work is shared between doctors of one specialty",
      all(len(by_doc[u]) <= accounts[u]["panel_limit"] for u in by_doc) and len(by_doc["d_ped"]) > 0 and len(by_doc["d_ped2"]) > 0
      and len(by_doc["d_gp"]) > 0 and len(by_doc["d_gp2"]) > 0)
ad_adults = p[(p.region == AD) & (p.is_child == 0)]
share_arabic = lambda ids_: sum(row.loc[i].language == "Arabic" for i in ids_) / max(len(ids_), 1)  # noqa: E731
check("language is matched where possible: the Arabic-speaking family doctor has more Arabic speakers than the hospital's average",
      share_arabic(by_doc["d_gp"]) > (ad_adults.language == "Arabic").mean())
sib_ok = True
for g, kids in p[(p.is_child == 1) & (p.region == AD)].groupby("guardian_id"):
    docs = {next(iter(d for s, d in store[i].items() if s == "Paediatrics"), None) for i in kids.Id if i in store}
    if len(kids) > 1 and len(docs) > 1 and not all(i in store for i in kids.Id):
        sib_ok = False
    if len(kids) > 1 and all(i in store for i in kids.Id) and len(docs) > 1:
        sib_ok = sib_ok and False
check("children of one parent go to the same paediatrician", sib_ok)
check("applying again adds nothing: people who already have a doctor of that specialty are left alone",
      post("/api/admin/careteam/apply", "boss").json()["applied"] == 0)

# ---- what each doctor sees now
panel = set(by_doc["d_ped"])
check("a care-team doctor sees exactly their panel, and two doctors of one specialty see different people",
      ids("d_ped") <= panel and seen("d_ped") == len(panel) and not (set(by_doc["d_ped"]) & set(by_doc["d_ped2"]))
      and seen("d_ped2") == len(by_doc["d_ped2"]))
mine_one, other_one = by_doc["d_ped"][0], by_doc["d_ped2"][0]
check("a patient on another doctor's panel looks like nobody (404), even by direct link; an own patient opens",
      get(f"/api/person/{mine_one}", "d_ped").status_code == 200 and get(f"/api/person/{other_one}", "d_ped").status_code == 404
      and get(f"/api/fhir/Patient/{other_one}", "d_ped").status_code in (404, 503))
check("a doctor of another hospital cannot see this hospital's patients", get(f"/api/person/{mine_one}", "d_ain").status_code == 404)

# ---- assigning by hand
adult = by_doc["d_gp"][0]
r1 = post("/api/admin/careteam/assign", "boss", {"person": adult, "specialty": "Paediatrics", "doctor": "d_ped"})
r2 = post("/api/admin/careteam/assign", "boss", {"person": adult, "specialty": "Family medicine", "doctor": "d_ain"})
free_child = next(i for i in p[(p.is_child == 1) & (p.region == AD)].Id if i not in store)
post("/api/admin/careteam/doctor", "boss", {"user": "d_gp2", "panel_limit": 200})      # room for one more
r3 = post("/api/admin/careteam/assign", "boss", {"person": adult, "specialty": "Family medicine", "doctor": "d_gp2"})
check("by hand: an adult cannot go to a paediatrician, and nobody to a doctor of another hospital; a valid change works",
      r1.status_code == 400 and "children" in r1.json()["detail"].lower() and r2.status_code == 400 and "works at" in r2.json()["detail"]
      and r3.status_code == 200 and careteam.load()["assignments"][adult]["Family medicine"] == "d_gp2")
r4 = post("/api/admin/careteam/assign", "boss", {"person": adult, "specialty": "Family medicine", "doctor": None})
check("by hand: removing an assignment leaves the person without a doctor", r4.status_code == 200 and adult not in careteam.load()["assignments"])
careteam_docs = {d["user"]: d for d in careteam.doctors()}
check("by hand: a full panel refuses another patient",
      (lambda full: (post("/api/admin/careteam/doctor", "boss", {"user": "d_gp", "panel_limit": 1}).status_code == 200
                     and post("/api/admin/careteam/assign", "boss", {"person": adult, "specialty": "Family medicine", "doctor": "d_gp"}).status_code == 400
                     and post("/api/admin/careteam/doctor", "boss", {"user": "d_gp", "panel_limit": 60}).status_code == 200))(None))

# ---- leave and backup
post("/api/admin/careteam/doctor", "boss", {"user": "d_gp", "backup": "d_gp2"})
panel_of = lambda u: {pid for pid, team in careteam.load()["assignments"].items() if u in team.values()}  # noqa: E731
before = seen("d_gp2")
post("/api/admin/careteam/doctor", "boss", {"user": "d_gp", "on_leave": True})
check("a doctor on leave: the backup also sees that panel, and the doctor on leave gets no new patients",
      seen("d_gp2") == len(panel_of("d_gp2") | panel_of("d_gp")) and seen("d_gp2") > before
      and all(get(f"/api/person/{i}", "d_gp2").status_code == 200 for i in sorted(panel_of("d_gp"))[:5])
      and "d_gp" not in {d["user"] for d in post("/api/admin/careteam/proposal", "boss").json()["per_doctor"]})
post("/api/admin/careteam/doctor", "boss", {"user": "d_gp", "on_leave": False})
check("back from leave: the backup sees only their own panel again", seen("d_gp2") == before)
bad_backup = post("/api/admin/careteam/doctor", "boss", {"user": "d_gp", "backup": "d_ain"})
check("a backup must be another doctor of the same hospital", bad_backup.status_code == 400)

# ---- the duty doctor
free_urgent = {i for i in p[(p.region == AD) & (p.urgency == "Urgent") & (p.open_cards.map(len) > 0)].Id if i not in careteam.load()["assignments"]}
check("the duty doctor sees people of their hospital with an Urgent card and no doctor, and a non-duty doctor does not",
      len(free_urgent) > 0 and not (free_urgent & ids("d_gp2")))
post("/api/admin/careteam/doctor", "boss", {"user": "d_gp2", "duty": True})
duty_ids = ids("d_gp2")
check("...once on duty: those urgent unassigned people appear on the list", free_urgent <= duty_ids or free_urgent <= {x for x in free_urgent if get(f"/api/person/{x}", "d_gp2").status_code == 200})
post("/api/admin/careteam/doctor", "boss", {"user": "d_gp2", "duty": False})

# ---- break glass
outside = next(i for i in p[(p.region == AD) & (p.is_child == 0)].Id if i not in ids("d_ped"))
mrn_out = appmod.mrn(outside)
lk = get(f"/api/careteam/lookup?record={mrn_out}", "d_ped").json()
check("look-up by exact record number gives only age, sex, hospital and whether the person is on the list",
      lk["found"] and set(lk) == {"found", "id", "mrn", "age", "sex", "hospital", "on_my_list"} and lk["on_my_list"] is False
      and get("/api/careteam/lookup?record=ADH-0000000", "d_ped").json() == {"found": False}
      and get(f"/api/careteam/lookup?record={mrn_out}", "d_old").status_code == 403)
short = post("/api/careteam/break_glass", "d_ped", {"person": outside, "reason": "needs it"})
check("break glass needs a real reason, and opens nothing without one", short.status_code == 400 and get(f"/api/person/{outside}", "d_ped").status_code == 404)
bg = post("/api/careteam/break_glass", "d_ped", {"person": outside, "reason": "patient in the emergency room, chest pain"})
check("with a reason the doctor opens that one person for a few hours", bg.status_code == 200 and get(f"/api/person/{outside}", "d_ped").status_code == 200
      and get(f"/api/person/{next(i for i in p[(p.region == AD) & (p.is_child == 0)].Id if i not in ids('d_ped') and i != outside)}", "d_ped").status_code == 404)
log_text = open(auth.ACCESS_LOG, encoding="utf-8").read()
check("break glass is in the access log with who, which record and the reason; the admin sees it in the care-team activity",
      "break_glass" in log_text and mrn_out in log_text and "chest pain" in log_text
      and any(x["mrn"] == mrn_out and x["doctor"] == "d_ped" and x["active"] for x in get("/api/admin/careteam/activity", "boss").json()["break_glass"]))
d = careteam.load()
for g in d["break_glass"]:
    g["expires"] = "2000-01-01T00:00:00"
careteam.save(d)
check("after the hours are over the person is closed again", get(f"/api/person/{outside}", "d_ped").status_code == 404)

# ---- a family shares a record with another hospital
kid = p[(p.guardian_id == parent) & (p.is_child == 1)].Id.iloc[0]
home = row.loc[kid].region
away = AIN if home != AIN else AD
doc_away = "d_ain" if away == AIN else "d_gp"
check("before sharing, a doctor of another hospital cannot open the child", get(f"/api/person/{kid}", doc_away).status_code == 404)
sh = c.post("/api/mycare/sharing", auth=A["fam"], json={"person": kid, "hospital": away, "days": 10})
check("the family shares the child with another hospital: its doctors can open the record, and the page lists it",
      sh.status_code == 200 and get(f"/api/person/{kid}", doc_away).status_code == 200
      and any(x["id"] == kid and x["shared"] and x["shared"][0]["hospital"] == away for x in get("/api/mycare/sharing", "fam").json()["people"]))
check("sharing is refused for the home hospital, for another family's child, and for a hospital that does not exist",
      c.post("/api/mycare/sharing", auth=A["fam"], json={"person": kid, "hospital": home}).status_code == 400
      and c.post("/api/mycare/sharing", auth=A["fam"], json={"person": outside, "hospital": away}).status_code == 404
      and c.post("/api/mycare/sharing", auth=A["fam"], json={"person": kid, "hospital": "Nowhere"}).status_code == 400)
check("the hospital admin sees the share in the activity list, and it is in the access log",
      any(x["mrn"] == appmod.mrn(kid) and x["active"] for x in get("/api/admin/careteam/activity", "boss").json()["shares"])
      and "share_record" in open(auth.ACCESS_LOG, encoding="utf-8").read())
rv = c.post("/api/mycare/sharing/revoke", auth=A["fam"], json={"person": kid, "hospital": away})
check("the family takes the sharing back: the other hospital's doctors lose access at once",
      rv.status_code == 200 and rv.json()["revoked"] == 1 and get(f"/api/person/{kid}", doc_away).status_code == 404)

# ---- hospital admins stay inside their hospital
users_ad = {u["name"] for u in get("/api/admin/users", "adm_ad").json()["users"]}
users_all = {u["name"] for u in get("/api/admin/users", "boss").json()["users"]}
check("a hospital admin sees only their hospital's accounts; the central admin sees all",
      "d_ped" in users_ad and "adm_ad" in users_ad and not (users_ad & {"d_ain", "adm_ain", "boss"}) and users_all >= set(accounts))
check("a hospital admin cannot reset, unlock or remove another hospital's account (it looks like it does not exist)",
      post("/api/admin/users/d_ain/password", "adm_ad", {"password": "another-pass-1234"}).status_code == 404
      and post("/api/admin/users/d_ain/unlock", "adm_ad").status_code == 404 and post("/api/admin/users/d_ain/remove", "adm_ad").status_code == 404
      and post("/api/admin/users/d_ped/unlock", "adm_ad").status_code == 200)
mk = post("/api/admin/users", "adm_ad", {"name": "new_doc", "role": "doctor", "password": "new-doctor-pass-1", "region": AIN,
                                         "specialty": ["Paediatrics"], "languages": ["Urdu"], "panel_limit": 30})
mgr = post("/api/admin/users", "adm_ad", {"name": "new_mgr", "role": "manager", "password": "new-manager-pass-1"})
made = json.load(open(USERS))["users"]["new_doc"]
check("a hospital admin's new doctor always belongs to their own hospital, with the care-team profile; central roles are refused",
      mk.status_code == 200 and made["region"] == AD and made["specialty"] == ["Paediatrics"] and made["panel_limit"] == 30
      and mgr.status_code == 403 and "new_mgr" not in json.load(open(USERS))["users"])
ct_ad = get("/api/admin/careteam", "adm_ad").json()
check("a hospital admin's care-team page shows only their hospital and its doctors",
      [h["hospital"] for h in ct_ad["hospitals"]] == [AD] and all(d["hospital"] == AD for d in ct_ad["doctors"]) and ct_ad["hospital"] == AD
      and post("/api/admin/careteam/doctor", "adm_ad", {"user": "d_ain", "duty": True}).status_code == 404)
careteam.reset()
post("/api/admin/careteam/apply", "adm_ad")
st = careteam.load()["assignments"]
check("a hospital admin's assignments only touch their hospital's people", st and all(row.loc[i].region == AD for i in st))
check("a hospital admin sees only their hospital's people, and cannot reload results or reset the care-team data",
      seen("adm_ad") == int((p.region == AD).sum()) and post("/api/reload", "adm_ad").status_code == 403 and post("/api/admin/careteam/reset", "adm_ad").status_code == 403
      and post("/api/reload", "boss").status_code == 200)
fp = get(f"/api/admin/find_person?record={appmod.mrn(next(i for i in p[(p.region == AIN) & (p.is_child == 0)].Id))}", "adm_ad").json()
check("a hospital admin cannot find another hospital's patients to link an account to", fp["total"] == 0)
log_ad = get("/api/admin/access_log", "adm_ad").json()
check("a hospital admin reads the access log of their own hospital's accounts only", all(r["user"] not in ("d_ain", "adm_ain") for r in log_ad))

# ---- sign-up requests: a hospital admin decides their own hospital's staff and any patient request
c.post("/api/signup", json={"role": "doctor", "user": "req_ad", "password": "request-pass-12345", "details": {
    "full_name": "Dr A", "email": "a@x.ae", "phone": "+971501234567", "license_number": "GD1", "specialty": "Paediatrics", "facility": "x", "region": AD}})
c.post("/api/signup", json={"role": "doctor", "user": "req_ain", "password": "request-pass-12345", "details": {
    "full_name": "Dr B", "email": "b@x.ae", "phone": "+971501234568", "license_number": "GD2", "specialty": "Family medicine", "facility": "x", "region": AIN}})
req_ad = {r["user"] for r in get("/api/admin/requests", "adm_ad").json()}
req_ain = {r["user"] for r in get("/api/admin/requests", "adm_ain").json()}
req_all = {r["user"]: r["id"] for r in get("/api/admin/requests", "boss").json()}
check("a hospital admin sees the sign-up requests of their own hospital only; the central admin sees all",
      "req_ad" in req_ad and "req_ain" not in req_ad and "req_ain" in req_ain and "req_ad" not in req_ain and {"req_ad", "req_ain"} <= set(req_all))
ap = post(f"/api/admin/requests/{req_all['req_ad']}/approve", "adm_ad", {"region": AIN})
approved = json.load(open(USERS))["users"].get("req_ad", {})
check("approving staff as a hospital admin puts them in that hospital whatever region is sent; a doctor's typed specialty is kept and matched",
      ap.status_code == 200 and approved.get("region") == AD and careteam.match_specialty(approved.get("specialty")) == "Paediatrics"
      and "req_ad" in {d["user"] for d in careteam.doctors()})

# ---- other roles keep working
check("a patient's My care still works with the new rules, and staff roles cannot use the care-team pages",
      get("/api/mycare", "fam").status_code == 200 and get("/api/admin/careteam", "d_ped").status_code == 403
      and get("/api/careteam/me", "nurse").status_code == 403 and get("/api/careteam/me", "d_old").json() == {"managed": False}
      and get("/api/careteam/me", "d_ped").json()["managed"] is True)
me = get("/api/careteam/me", "d_ped").json()
check("a doctor's own summary: hospital, specialty, panel against the limit and what they can open",
      me["hospital"] == AD and me["specialty"] == ["Paediatrics"] and me["panel"] == len(careteam.load()["assignments"] and [1 for t in careteam.load()["assignments"].values() if "d_ped" in t.values()])
      and me["panel_limit"] == 40 and me["can_open"] >= me["panel"])

# ---- open mode: no accounts, preview as a made-up doctor or as one hospital's admin
os.environ["PP_USERS_FILE"] = os.path.join(tmp, "no_accounts_here.json")
careteam.reset()
demo = c.get("/api/careteam/demo").json()
check("open mode offers the made-up doctors and the hospitals for the preview",
      demo["open_mode"] and len(demo["doctors"]) >= 6 and set(demo["hospitals"]) >= {AD, AIN})
as_doc = lambda who, path: c.get(path, headers={"X-Preview": f"doctor:{who}"})  # noqa: E731
check("before assignments a previewed doctor sees nobody, while plain open mode still sees everyone",
      as_doc("dr.amina", "/api/overview").json()["people"] == 0 and c.get("/api/overview").json()["people"] == len(p))
n_demo = c.post("/api/admin/careteam/apply", json={}).json()["applied"]
a1, a2 = as_doc("dr.amina", "/api/overview").json()["people"], as_doc("dr.rashid", "/api/overview").json()["people"]
check(f"after applying ({n_demo} assignments) two previewed paediatricians see different, smaller panels",
      n_demo > 0 and 0 < a1 < len(p) and 0 < a2 < len(p) and a1 != len(p))
hosp = c.get("/api/overview", headers={"X-Preview": f"hospital-admin:{AIN}"}).json()
check("previewing one hospital's admin shows only that hospital", hosp["people"] == int((p.region == AIN).sum()) and hosp["regions"] == [AIN])
check("a bad preview header is ignored", c.get("/api/overview", headers={"X-Preview": "doctor:nobody"}).json()["people"] == len(p))
check("the preview header does nothing once accounts exist",
      (lambda: (os.environ.__setitem__("PP_USERS_FILE", USERS), c.get("/api/overview", headers={"X-Preview": "doctor:dr.amina"}).status_code)[1])() == 401)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
