"""
The outreach queue (api/outreach.py): a contact with its outcome leaves everyone's list, "being contacted" stops two people calling
the same family, no-answers come back and then escalate, and the history says who contacted whom and how.
    python tests/test_outreach.py
"""
import json
import os
import sys
import tempfile
import warnings
from datetime import datetime, timedelta

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
os.environ["PP_ANSWERS_FILE"] = os.path.join(tmp, "answers.json")      # clinic answers saved below go to a temporary file
os.environ.pop("PP_PASSWORD", None)
os.environ.pop("PP_DEMO", None)
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from api import app as appmod, auth, outreach  # noqa: E402

auth.ACCESS_LOG = os.path.join(tmp, "access_log.csv")
auth.LIMITS_FILE = os.path.join(tmp, "limits.json")
auth.SESSIONS_FILE = os.path.join(tmp, "sessions.json")
ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


PW = "outreach-pass-12345"
AD, AIN = "Abu Dhabi", "Al Ain"


def acct(role, **extra):
    s, h = auth.hash_password(PW)
    return {"role": role, "salt": s, "hash": h, **extra}


accounts = {"aisha": acct("outreach", region=AD, full_name="Aisha Nasser"), "faisal": acct("outreach", region=AD, full_name="Faisal Khan"),
            "maria": acct("outreach", region=AIN, full_name="Maria Santos"), "boss": acct("admin"), "doc": acct("doctor"),
            "pm": acct("manager"), "ain_admin": acct("admin", region=AIN)}
json.dump({"users": accounts}, open(USERS, "w"))
c = TestClient(server.app)
A = {u: (u, PW) for u in accounts}
get = lambda path, who: c.get(path, auth=A[who])  # noqa: E731
post = lambda path, who, body=None: c.post(path, auth=A[who], json=body or {})  # noqa: E731
row = appmod.people().set_index("Id")
CLAIMS = os.path.join(tmp, "outreach_claims.json")


def listed(who, n=400):
    return {i["id"]: i for i in get(f"/api/queue?n={n}", who).json()["items"]}


q0 = get("/api/queue?n=30", "aisha").json()
first, second, third = (i["id"] for i in q0["items"][:3])
check(f"the list starts with {q0['waiting']} waiting; each line has the planned channel, language and time, and no clinical detail",
      q0["waiting"] > 100 and len(q0["items"]) == 30 and all(i["planned_channel"] in outreach.CHANNELS for i in q0["items"])
      and q0["contacted_today"] == 0 and q0["escalated"] == 0 and "agreed" in q0["outcomes"] and "done" not in q0["outcomes"]
      and not any(w in json.dumps(q0["items"]).lower() for w in ("diabetes", "hypertension", "risk score", "brca")))
check("staff of one hospital see only that hospital's people", all(row.loc[i].region == AD for i in listed("aisha")) and all(row.loc[i].region == AIN for i in listed("maria")))

# ---- being contacted
cl = post("/api/queue/claim", "aisha", {"id": first})
check("starting a contact holds the person: a colleague sees who is contacting them and cannot start or record one",
      cl.status_code == 200 and listed("faisal")[first]["claimed_by"] == "Aisha Nasser" and listed("aisha")[first]["claimed_by_me"]
      and post("/api/queue/claim", "faisal", {"id": first}).status_code == 409
      and post("/api/queue/contact", "faisal", {"id": first, "channel": "SMS", "outcome": "agreed"}).status_code == 409)
check("a person outside the staff member's hospital cannot be claimed or recorded (looks like nobody)",
      post("/api/queue/claim", "maria", {"id": first}).status_code == 404
      and post("/api/queue/contact", "maria", {"id": first, "channel": "SMS", "outcome": "agreed"}).status_code == 404)
check("a doctor cannot use the outreach list", get("/api/queue", "doc").status_code == 403 and post("/api/queue/claim", "doc", {"id": first}).status_code == 403)
rl = post("/api/queue/release", "aisha", {"id": first})
check("letting go of a person frees them for others at once", rl.json()["released"] is True and listed("faisal")[first]["claimed_by"] is None
      and post("/api/queue/claim", "faisal", {"id": first}).status_code == 200)
post("/api/queue/release", "faisal", {"id": first})
post("/api/queue/claim", "aisha", {"id": first})
claims = json.load(open(CLAIMS))
claims[first]["until"] = "2000-01-01T00:00:00"
json.dump(claims, open(CLAIMS, "w"))
check("after 30 minutes the hold ends by itself", post("/api/queue/claim", "faisal", {"id": first}).status_code == 200)
post("/api/queue/release", "faisal", {"id": first})

# ---- recording a contact
bad1 = post("/api/queue/contact", "aisha", {"id": first, "channel": "SMS", "outcome": "nonsense"})
bad2 = post("/api/queue/contact", "aisha", {"id": first, "channel": "carrier pigeon", "outcome": "agreed"})
check("an unknown outcome or channel is refused and saves nothing", bad1.status_code == 400 and bad2.status_code == 400 and first in listed("aisha"))
n0 = get("/api/queue?n=30", "faisal").json()["waiting"]
ag = post("/api/queue/contact", "aisha", {"id": first, "channel": "WhatsApp", "outcome": "agreed", "note": "booked the 12th, prefers mornings"})
q1 = get("/api/queue?n=400", "faisal").json()
check("once Aisha records that the person agreed to book, they are gone from Faisal's list and the waiting count falls by one",
      ag.status_code == 200 and ag.json()["saved"] and first not in {i["id"] for i in q1["items"]} and q1["waiting"] == n0 - 1 and q1["contacted_today"] == 1)
hist = get("/api/queue/contacts?mine_only=0", "faisal").json()
r0 = hist["rows"][0]
check("the history says when, who, how, what happened, the note and the person (record number, age, language)",
      r0["staff"] == "Aisha Nasser" and r0["channel"] == "WhatsApp" and r0["outcome"] == "agreed" and r0["outcome_label"] == "Reached: agreed to book"
      and r0["note"].startswith("booked") and r0["mrn"] == appmod.mrn(first) and r0["age"] == int(row.loc[first].age) and r0["language"] == row.loc[first].language
      and hist["counts"] == {"Reached: agreed to book": 1} and {"user": "aisha", "name": "Aisha Nasser"} in hist["staff"])

# ---- no answer: back later, then escalated


def go_back_in_time(person):
    f = os.path.join(tmp, "outreach_contacts.csv")
    d = pd.read_csv(f, dtype=str).fillna("")
    d.loc[d.person == person, "next_try"] = "2000-01-01T00:00:00"
    d.to_csv(f, index=False)


na = post("/api/queue/contact", "aisha", {"id": second, "channel": "Phone call", "outcome": "no_answer"})
check("no answer: the person leaves the list for now and comes back after a few hours",
      na.status_code == 200 and na.json()["back_at"] and second not in listed("aisha") and second not in listed("faisal"))
go_back_in_time(second)
check("after the wait the person is back in the list, with the number of tries", second in listed("aisha") and listed("aisha")[second]["tries"] == 1)
post("/api/queue/contact", "faisal", {"id": second, "channel": "Phone call", "outcome": "no_answer"})
go_back_in_time(second)
r3 = post("/api/queue/contact", "aisha", {"id": second, "channel": "Phone call", "outcome": "no_answer"})
st = get("/api/queue/contacts", "boss").json()
check("after three no-answers the person is escalated for another route and no longer in the daily list",
      r3.json()["escalated"] is True and second not in listed("aisha") and any(e["mrn"] == appmod.mrn(second) and e["tries"] == 3 for e in st["escalated"])
      and get("/api/queue?n=30", "aisha").json()["escalated"] == 1)
wn = post("/api/queue/contact", "aisha", {"id": third, "channel": "Phone call", "outcome": "wrong_number", "note": "number belongs to someone else"})
check("a wrong number escalates at once", wn.json()["escalated"] is True and third not in listed("aisha")
      and any(e["why"] == "wrong number" for e in get("/api/queue/contacts?mine_only=0", "aisha").json()["escalated"]))

# ---- asked to be called later, declined, needs a doctor
pool = [i for i in listed("aisha") if i not in (first, second, third)]
later_id, decl_id, doc_id = pool[:3]
lt = post("/api/queue/contact", "aisha", {"id": later_id, "channel": "WhatsApp", "outcome": "later"})
dc = post("/api/queue/contact", "aisha", {"id": decl_id, "channel": "SMS", "outcome": "declined"})
post("/api/queue/contact", "aisha", {"id": doc_id, "channel": "Phone call", "outcome": "needs_doctor"})
check("asked to be called later: back in about three days; declined and needs-a-doctor close the contact",
      lt.json()["back_at"][:10] == (datetime.now() + timedelta(days=3)).date().isoformat() and later_id not in listed("faisal")
      and decl_id not in listed("faisal") and doc_id not in listed("faisal") and not dc.json()["back_at"])
go_back_in_time(later_id)
check("...and when the three days are over they return", later_id in listed("faisal"))

# ---- the history list
h_all = get("/api/queue/contacts", "boss").json()
h_one = get("/api/queue/contacts?staff=aisha&outcome=no_answer", "boss").json()
h_day = get("/api/queue/contacts?days=1", "boss").json()
check("the history filters by staff member, outcome and period, newest first, with counts per outcome",
      h_all["total"] == 8 and h_all["rows"][0]["time"] >= h_all["rows"][-1]["time"] and h_one["total"] == 2 and all(r["staff"] == "Aisha Nasser" for r in h_one["rows"])
      and h_day["total"] == h_all["total"] and h_all["counts"]["No answer"] == 3 and h_all["counts"]["Reached: agreed to book"] == 1)
check("a hospital's staff read only their own hospital's history; the other hospital's staff see none of it",
      get("/api/queue/contacts", "maria").json()["total"] == 0 and get("/api/queue/contacts?mine_only=0", "faisal").json()["total"] == h_all["total"])
check("the history carries no diagnosis or risk, only the plain contact details",
      not any(w in json.dumps(h_all["rows"]).lower() for w in ("diabetes", "hypertension", "risk", "genetic")))
log = open(auth.ACCESS_LOG, encoding="utf-8").read()
check("every contact is in the access log (who, which record, how, what happened) without the note",
      "outreach_contact" in log and appmod.mrn(first) in log and "agreed" in log and "prefers mornings" not in log)

# ---- outreach staff see who and what is due, never the clinical detail
hh_list = get("/api/households", "aisha").json()
hh_one = get(f"/api/household/{hh_list[0]['household_id']}", "aisha").json()
check("outreach staff cannot open the clinical person record (doctors and admins can)",
      get(f"/api/person/{first}", "aisha").status_code == 403 and get(f"/api/person/{first}", "boss").status_code == 200)
check("in the family and journey views outreach staff get no risk score and no clinical reasons",
      all(m["risk"] is None and m["risk_band"] is None and m["reasons"] == [] for m in hh_one["members"])
      and get(f"/api/journey/{first}", "aisha").json()["person"]["reasons"] == [])
check("a doctor's Disagree needs a reason; Agree does not",
      post(f"/api/person/{first}/feedback", "boss", {"decision": "Disagree"}).status_code == 400
      and post(f"/api/person/{first}/feedback", "boss", {"decision": "Agree"}).status_code == 200)

# ---- the programme manager's Operations numbers: totals only
op = get("/api/operations", "pm")
o = op.json()
mix = {m["outcome"]: m["count"] for m in o["outreach"]["mix"]}
check("the programme manager gets the Operations totals; doctors and outreach staff do not",
      op.status_code == 200 and get("/api/operations", "doc").status_code == 403 and get("/api/operations", "aisha").status_code == 403
      and o["scope"] == "All hospitals")
check("the outreach totals match the contact history: attempts, outcome mix, reached and no-answer rates, escalations",
      o["outreach"]["contacts"] == h_all["total"] and sum(mix.values()) == h_all["total"] and mix["no_answer"] == 3 and mix["agreed"] == 1
      and o["outreach"]["today"] == h_all["total"] and o["outreach"]["no_answer_pct"] == round(100 * 3 / h_all["total"], 1)
      and o["outreach"]["escalated"] == len(h_all["escalated"]) and 0 < o["outreach"]["reached_pct"] < 100
      and sum(ch["attempts"] for ch in o["outreach"]["channels"]) == h_all["total"] and o["outreach"]["hours_to_reach_median"] is not None)
check("the coverage totals, the safeguards counts and the policy settings are present",
      o["coverage"]["doctors"] >= 0 and len(o["coverage"]["hospitals"]) == 3 and "urgent_without_doctor" in o["coverage"]
      and set(o["safeguards"]) >= {"break_glass_30d", "shares_active"} and any(x["name"].startswith("Tries without") and x["value"] == outreach.MAX_TRIES for x in o["policy"]))
text = json.dumps(o)
check("the Operations numbers name no person, record number or staff member",
      "Aisha" not in text and "Faisal" not in text and appmod.mrn(first) not in text and first not in text and "aisha" not in text)
oa = get("/api/operations", "ain_admin").json()
check("a hospital admin sees only their own hospital (no Abu Dhabi contacts, one hospital row)",
      oa["scope"] == AIN and oa["outreach"]["contacts"] == 0 and [h["hospital"] for h in oa["coverage"]["hospitals"]] == [AIN])

# ---- the older one-tick way still works
old_id = next(iter(listed("aisha")))
d_ = post("/api/queue/done", "aisha", {"ids": [old_id]}).json()
check("the older one-tick way still closes a person (recorded as Marked done)",
      d_["saved"] == 1 and old_id not in listed("faisal") and get("/api/queue/contacts?outcome=done", "boss").json()["rows"][0]["outcome_label"] == "Marked done")
legacy_id = next(iter(listed("aisha")))
with open(os.path.join(tmp, "outreach_log.csv"), "w") as fh:
    fh.write(f"time,Id\n2026-11-01T10:00:00,{legacy_id}\n")
check("people ticked off in the earlier log file stay closed", legacy_id not in listed("aisha"))

# ---- booked visits: attended or missed
vl = get("/api/visits?status=to_confirm&n=500", "aisha").json()
a_id, m_id = vl["items"][0]["id"], vl["items"][1]["id"]            # booked people (not on the contact list: their state is Booked)
check("staff see the booked visits of their own hospital that are due to be confirmed, with no clinical detail",
      vl["total"] > 50 and vl["counts"]["to_confirm"] >= vl["total"] and all(row.loc[i["id"]].region == AD for i in vl["items"])
      and all(set(i) <= {"id", "short", "who", "age", "language", "date", "how", "status", "recorded_at", "reminder_due"} for i in vl["items"])
      and all(i["status"] == "to_confirm" for i in vl["items"]))
check("the other hospital's staff see only their own visits; a doctor and the programme manager cannot use this",
      all(row.loc[i["id"]].region == AIN for i in get("/api/visits?n=500", "maria").json()["items"])
      and get("/api/visits", "doc").status_code == 403 and get("/api/visits", "pm").status_code == 403)
bad_v = post("/api/visits/outcome", "aisha", {"id": a_id, "outcome": "agreed"})
check("only attended, missed or moved can be recorded for a visit; a person of another hospital looks like no one",
      bad_v.status_code == 400 and post("/api/visits/outcome", "maria", {"id": a_id, "outcome": "attended"}).status_code == 404)
n_contacts_before = get("/api/queue/contacts", "boss").json()["total"]
ra = post("/api/visits/outcome", "aisha", {"id": a_id, "outcome": "attended", "note": "came on time"}).json()
rm = post("/api/visits/outcome", "aisha", {"id": m_id, "outcome": "no_show"}).json()
vl2 = get("/api/visits?status=all&n=500", "faisal").json()
st_ = {i["id"]: i["status"] for i in vl2["items"]}
check("attended closes the person (off every colleague's list); a missed visit is recorded and the person waits a day to rebook",
      ra["saved"] and not ra["back_at"] and rm["back_at"][:10] == (datetime.now() + timedelta(days=1)).date().isoformat()
      and st_[a_id] == "attended" and st_[m_id] == "no_show" and a_id not in listed("faisal", 3000) and m_id not in listed("faisal", 3000))
check("the visit counts: one attended, one missed (attendance 50%), and the visit moves out of 'to confirm'",
      vl2["counts"]["attended"] == 1 and vl2["counts"]["no_show"] == 1 and vl2["attendance_pct"] == 50.0
      and vl2["counts"]["to_confirm"] == vl["counts"]["to_confirm"] - 2)
go_back_in_time(m_id)
back = listed("faisal", 3000)
check("a day later the missed visit is back on the list, marked as a missed visit to rebook", m_id in back and back[m_id]["missed_visit"] is True)
for _ in range(2):
    post("/api/visits/outcome", "aisha", {"id": m_id, "outcome": "no_show"})
    go_back_in_time(m_id)
check("after three missed visits the person is escalated (another way to reach them) and leaves the daily list",
      m_id not in listed("faisal", 3000) and get("/api/queue", "faisal").json()["escalated"] >= 1)
check("a recorded visit is not counted as a contact: contacted today and the history's contact counts are unchanged by it",
      get("/api/queue", "aisha").json()["contacted_today"] == get("/api/queue", "faisal").json()["contacted_today"]
      and "attended" not in get("/api/queue", "aisha").json()["outcomes"] and "no_show" not in get("/api/queue", "aisha").json()["outcomes"])
ov_ = get("/api/operations", "pm").json()
check("the programme manager sees visit totals only (recorded, attended, missed, attendance rate, waiting to be confirmed), no names",
      ov_["visits"]["attended"] == 1 and ov_["visits"]["no_show"] == 1 and ov_["visits"]["attendance_pct"] == 50.0
      and ov_["visits"]["to_confirm"] > 50 and "Aisha" not in json.dumps(ov_["visits"]) and appmod.mrn(a_id) not in json.dumps(ov_["visits"])
      and ov_["outreach"]["contacts"] == get("/api/operations", "pm").json()["outreach"]["contacts"]
      and all(m["outcome"] not in ("attended", "no_show", "rescheduled") for m in ov_["outreach"]["mix"]))
log2 = open(auth.ACCESS_LOG, encoding="utf-8").read()
check("every recorded visit is in the access log (who, which record, what)", "visit_outcome" in log2 and appmod.mrn(a_id) in log2 and "no_note" not in log2 and "came on time" not in log2)
# the patient sees what was recorded
json.dump({"users": {**json.load(open(USERS))["users"], "pat": acct("patient", person=a_id)}}, open(USERS, "w")) if not row.loc[a_id].is_child else None
if not row.loc[a_id].is_child:
    A["pat"] = ("pat", PW)
    mc_ = get("/api/mycare", "pat").json()["people"][0]
    check("the patient's My care shows that the visit was recorded as attended", mc_["last_visit"]["outcome"] == "attended")

# ---- the hospital admin's Today and Audit pages
td = get("/api/admin/today", "boss").json()
w = {x["key"]: x["count"] for x in td["work"]}
check("Today lists what needs action with counts that match the other pages: visits to confirm, people who could not be reached",
      td["scope"] == "All hospitals" and w["visits_to_confirm"] == get("/api/visits?status=to_confirm", "boss").json()["counts"]["to_confirm"]
      and w["escalated"] == len(td["escalated"]) >= 1 and td["needs_action"] >= w["visits_to_confirm"] and set(w) >= {"urgent_no_doctor", "signup_requests", "break_glass_open"})
tda = get("/api/admin/today", "ain_admin").json()
check("a hospital admin's Today covers their hospital only (no Abu Dhabi visits or escalations)",
      tda["scope"] == AIN and {x["key"]: x["count"] for x in tda["work"]}["visits_to_confirm"] == get("/api/visits?status=to_confirm", "maria").json()["counts"]["to_confirm"]
      and tda["escalated"] == [] and all(x["hospital"] == AIN for x in tda["urgent_no_doctor"]))
check("Today shows record numbers, age and language, never a name or a clinical detail",
      not any(w_ in json.dumps(td).lower() for w_ in ("diabet", "hypertens", "risk", "genetic", "aisha")))
check("Today and Audit are for the hospital admin only",
      all(get(u, who).status_code == 403 for u in ("/api/admin/today", "/api/admin/audit", "/api/admin/audit/export") for who in ("doc", "pm", "aisha")))
au = get("/api/admin/audit", "boss").json()
raw = json.dumps(au)
check("Audit counts the records opened and the sign-in trouble, with a 7-day chart and who opened the most",
      len(au["by_day"]) == 7 and au["kpis"]["opened_7d"] >= 0 and set(au["kpis"]) >= {"opened_24h", "break_glass_30d", "failed_sign_ins_7d", "refused_7d"}
      and au["never_holds"] and au["holds"])
check("Audit never shows a raw record Id, a password or a note",
      not __import__("re").search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", raw) and PW not in raw and "came on time" not in raw)
ex = get("/api/admin/audit/export?days=30", "boss")
ex_ain = get("/api/admin/audit/export?days=30", "ain_admin").text
check("the log downloads as a CSV with record numbers, only the admin's own hospital's accounts for a hospital admin, and the download is logged",
      ex.status_code == 200 and ex.headers["content-type"].startswith("text/csv") and ex.text.startswith("time,user,role,computer,event,detail")
      and "visit_outcome" in ex.text and "aisha" in ex.text and "aisha" not in ex_ain
      and any(r["event"] == "audit_export" for r in get("/api/admin/audit", "boss").json()["changes"]))

# ---- the access log is chained: an edited, removed or added line is found
import csv as _csv
import shutil
vl_ = auth.verify_log()
check("the access log is chained from its first line: every line verifies, none is older than the chain",
      vl_["ok"] and vl_["chained"] == vl_["lines"] > 10 and vl_["before_chain"] == 0 and len(vl_["head"]) == 16
      and get("/api/admin/audit", "boss").json()["integrity"]["ok"] and "chain" not in get("/api/admin/access_log", "boss").json()[0])
shutil.copy(auth.ACCESS_LOG, auth.ACCESS_LOG + ".keep")


def tamper(edit):
    rows = list(_csv.DictReader(open(auth.ACCESS_LOG, newline="", encoding="utf-8")))
    rows = edit(rows)
    with open(auth.ACCESS_LOG, "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=auth.LOG_FIELDS)
        w.writeheader()
        w.writerows(rows)


def restore():
    shutil.copy(auth.ACCESS_LOG + ".keep", auth.ACCESS_LOG)


def _edit(rows):
    rows[5]["detail"] = "nothing to see"
    return rows


tamper(_edit)
edited = auth.verify_log()
check("a line that was edited is found: the chain breaks at that line, the Audit page says so and Today raises it",
      not edited["ok"] and edited["first_bad_line"] == 6 and get("/api/admin/audit", "boss").json()["integrity"]["first_bad_line"] == 6
      and {x["key"]: x["count"] for x in get("/api/admin/today", "boss").json()["work"]}["audit_log_altered"] == 1)
restore()
tamper(lambda rows: rows[:4] + rows[5:])
check("a line that was removed is found at the line after it", auth.verify_log()["first_bad_line"] == 5)
restore()
n_lines = vl_["lines"]
tamper(lambda rows: rows + [{**rows[-1], "detail": "added by hand", "chain": ""}])
check("a line added by hand (no valid chain) is found at that line", not auth.verify_log()["ok"] and auth.verify_log()["first_bad_line"] == n_lines + 1)
restore()
tamper(lambda rows: rows[:-1])
check("removing the newest lines is not visible to the chain itself, but the head hash changes, so a head written down earlier shows it",
      auth.verify_log()["ok"] and auth.verify_log()["head"] != vl_["head"])
restore()
os.remove(auth.ACCESS_LOG + ".keep")
check("the repaired log verifies again", auth.verify_log()["ok"] and auth.verify_log()["head"] == vl_["head"])
# an older log (no chain column) is upgraded when the next line is written; its old lines are reported as written before the chain
real_log = auth.ACCESS_LOG
auth.ACCESS_LOG = os.path.join(tmp, "old_log.csv")
open(auth.ACCESS_LOG, "w", encoding="utf-8").write("time,user,role,computer,event,detail\n2026-01-01T00:00:00,old,admin,,sign_in,\n2026-01-01T00:01:00,old,admin,,sign_out,\n")
auth.log("sign_in", "new", "admin", "x", "")
auth.log("sign_out", "new", "admin", "x", "")
up = auth.verify_log()
auth.ACCESS_LOG = real_log
check("an older log is upgraded in place: its 2 old lines are kept and reported as before the chain, the new lines verify",
      up["ok"] and up["before_chain"] == 2 and up["chained"] == 2 and up["lines"] == 4)

# ---- the patient journeys list is worked out once and still correct
import time as _t
t0 = _t.time(); j1 = get("/api/journeys", "aisha").json(); first_s = _t.time() - t0
t0 = _t.time(); j2 = get("/api/journeys", "aisha").json(); second_s = _t.time() - t0
evs = appmod.events()
dgn = set(evs[evs.event == "diagnosed"].person)
check(f"the journeys list is cached ({second_s:.2f} s the second time), the same both times, limited to the staff member's hospital, and flags who was diagnosed",
      j1 == j2 and len(j1) > 20 and all(x["label"].endswith(AD) for x in j1) and all(x["diagnosed"] == (x["id"] in dgn) for x in j1) and second_s < 1.0)

# ---- the audit key and a head written down elsewhere
check("the audit page says where the key lives (a file next to the log unless PP_AUDIT_KEY is set)",
      get("/api/admin/audit", "boss").json()["integrity"]["key"] == "file" and auth.key_source() == "file"
      and (os.environ.__setitem__("PP_AUDIT_KEY", "x") or auth.key_source() == "environment") and (os.environ.pop("PP_AUDIT_KEY") or True) and auth.key_source() == "file")
import subprocess
heads = os.path.join(tmp, "heads.txt")
run_head = lambda *a: subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "audit_head.py"), *a], capture_output=True, text=True, encoding="utf-8",  # noqa: E731
                                     env={**os.environ, "PP_ACCESS_LOG": auth.ACCESS_LOG, "PYTHONIOENCODING": "utf-8"})
r1 = run_head("--record", heads)
auth.log("sign_in", "later", "admin", "x", "")
r2 = run_head("--check", heads)
shutil.copy(auth.ACCESS_LOG, auth.ACCESS_LOG + ".keep2")
tamper(lambda rows: rows[:-2])
r3 = run_head("--check", heads)
check("a head recorded elsewhere shows what the chain alone cannot: the log still matches it after new lines, and removing the newest lines is caught",
      r1.returncode == 0 and "recorded" in r1.stdout and r2.returncode == 0 and "matches the head recorded" in r2.stdout
      and r3.returncode == 1 and "LOG SHORTER" in r3.stdout and auth.verify_log()["ok"])
shutil.copy(auth.ACCESS_LOG + ".keep2", auth.ACCESS_LOG)
os.remove(auth.ACCESS_LOG + ".keep2")

# ---- how much waist and lifestyle information clinics have collected
scored_id = appmod.people()[lambda d: d.risk.notna() & (d.is_child == 0)].Id.iloc[0]
c0 = get("/api/operations", "pm").json()["collection"]
post(f"/api/person/{scored_id}/answers", "doc", {"waist_cm": 92, "exercise_daily": True})
c1 = get("/api/operations", "pm").json()["collection"]
check("the programme manager sees how many adults with a diabetes score have waist and lifestyle answers recorded (a count, no names)",
      c0["answers_recorded"] == 0 and c1["answers_recorded"] == 1 and c1["scored_adults"] > 1000 and c1["answers_pct"] > 0 and set(c1) == {"scored_adults", "answers_recorded", "answers_pct"})

# ---- open mode: two made-up staff members on the same list
os.environ["PP_USERS_FILE"] = os.path.join(tmp, "no_accounts_here.json")
os.environ["PP_OUTREACH_DIR"] = tempfile.mkdtemp()
as_staff = lambda who: {"X-Preview": f"outreach:{who}"}  # noqa: E731
demo = c.get("/api/careteam/demo").json()
a_items = c.get("/api/queue?n=50", headers=as_staff("aisha.n")).json()
pid = a_items["items"][0]["id"]
c.post("/api/queue/claim", json={"id": pid}, headers=as_staff("aisha.n"))
f_items = {i["id"]: i for i in c.get("/api/queue?n=50", headers=as_staff("faisal.k")).json()["items"]}
check("open mode: made-up outreach staff; one holds a person and the other sees who, as in a real team",
      len(demo["outreach"]) == 4 and f_items[pid]["claimed_by"] == "Aisha Nasser"
      and all(row.loc[i["id"]].region == AD for i in a_items["items"]) and c.post("/api/queue/claim", json={"id": pid}, headers=as_staff("faisal.k")).status_code == 409)
c.post("/api/queue/contact", json={"id": pid, "channel": "SMS", "outcome": "agreed"}, headers=as_staff("aisha.n"))
check("open mode: after Aisha records the contact it is gone from Faisal's list and in the history under her name",
      pid not in {i["id"] for i in c.get("/api/queue?n=300", headers=as_staff("faisal.k")).json()["items"]}
      and c.get("/api/queue/contacts?mine_only=0", headers=as_staff("faisal.k")).json()["rows"][0]["staff"] == "Aisha Nasser")

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
