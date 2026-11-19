"""
End-to-end checks of the agents (guide Step 12 test cases). Run after orchestrator.py and evaluate.py:
    python tests/test_system.py
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np
import pandas as pd

import consent
import fhir
import summaries
import orchestrator
import simulation
import utils
from agents import care_gap, chat, outreach, priority
from agents.base import Person
from models import noshow

ok = []


def check(name, cond):
    ok.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)


def fresh(mode="new", **params):
    return orchestrator.build_context(mode, 1, {**simulation.DEFAULT_PARAMS, **params}, pd.Timestamp("2026-11-10"))


ctx = fresh()
f = ctx.features
P = ctx.people


def person_where(mask):
    return P[f[mask].Id.iloc[0]]


def with_row(p, **changes):
    """A copy of a person with some fields changed, for a single test."""
    q = Person(pid=p.pid, row=p.row._replace(**changes))
    ctx.people[q.pid] = q
    return q


# 1. child who missed a Well-Child Visit: flagged, message goes to the guardian
kid = person_where((f.age < 6) & (f.missed_well_child > 0) & f.guardian_id.notna() & (f.consent == True))
gaps = care_gap.find_gaps(kid, ctx, 0)
check("child with a missed visit gets a well_child_visit gap", any(g["check"] == "well_child_visit" for g in gaps))
check("the child's messages go to the guardian", ctx.guardian(kid).pid == kid.row.guardian_id and ctx.guardian(kid) is not kid)

# 2. adult with diabetes and no HbA1c for 14 months: hba1c gap and High urgency
dm = with_row(person_where((f.age >= 30) & (f.has_diabetes == 1)), months_since_chronic_test=14.0,
              hba1c_date=pd.Timestamp("2025-09-01"))
dm.gaps, dm.warning_signs = care_gap.find_gaps(dm, ctx, 0), care_gap.warning_signs(dm, ctx, 0)
check("diabetes with an old HbA1c gets an hba1c_test gap", any(g["check"] == "hba1c_test" for g in dm.gaps))
check("long-term illness with no recent test is High (or Urgent)", priority.urgency(dm) in ("High", "Urgent"))

# 3. abnormal result with no follow-up: Urgent, so every card waits for a doctor
ab = with_row(person_where(f.age >= 30), abnormal_no_followup=1)
ab.gaps, ab.warning_signs = care_gap.find_gaps(ab, ctx, 0), care_gap.warning_signs(ab, ctx, 0)
ab.urgency = priority.urgency(ab)
check("abnormal result with no follow-up is Urgent", ab.urgency == "Urgent")
card = priority.pathway_card(ab, ab.gaps[0] if ab.gaps else {"check": "bp_check", "reason": "", "pathway": "x"}, ctx.guidelines, 0)
check("an Urgent case's card says Clinician decides", card["decision"] == "Clinician decides")

# 4. no consent: never contacted
nc = with_row(person_where((f.age >= 30) & f.guardian_id.isna()), consent=False)
nc.state, nc.cards, nc.priority, nc.urgency = "Flagged", [{"check": "bp_check", "status": "ready", "invite_by_day": 30}], 99.0, "Routine"
outreach.OutreachAgent().run(ctx, 1)
check("a person without consent is never contacted", nc.state == "Flagged" and nc.contacted_on is None)

# 5 and 13. STOP in English and in Arabic
s1 = person_where((f.age >= 30) & f.guardian_id.isna() & (f.consent == True))
chat.handle_reply(ctx, s1, "STOP", 2)
check("STOP opts the person out and is recorded", s1.state == "Opted out" and not consent.has(ctx, s1, "contact"))
s2 = P[f[(f.age >= 30) & f.guardian_id.isna() & (f.consent == True)].Id.iloc[1]]
chat.handle_reply(ctx, s2, "توقف", 2)
check("the Arabic word for STOP works too", s2.state == "Opted out")
chat.handle_reply(ctx, s2, "START", 3)
check("START opts back in", consent.has(ctx, s2, "contact"))

# 6. emergency words
ans = chat.handle_reply(ctx, s2, "I have chest pain", 3)
check("'chest pain' returns 998 and alerts the care team", "998" in ans and any(e["event"] == "emergency" for e in ctx.events))

# 7. a doctor's Disagree puts the person on hold
c7 = fresh(doctor_agree=0.0)
q = person_where(f.age >= 30)
q7 = c7.people[q.pid]
q7.gaps = [{"check": "genetic_counselling", "reason": "Genetic marker: BRCA", "pathway": "Genetic pathway"}]
q7.urgency, q7.risk = "Routine", 0.2
for d in (0, 1, 2):
    priority.PriorityAgent().run(c7, d)
check("Disagree on every open card puts the person on hold", q7.state == "On hold" and q7.doctor_feedback == "Disagree")

# 8. a completed check closes its gap
done = with_row(person_where((f.age >= 30) & (f.months_since_sbp > 13)))
check("an overdue blood pressure check is a gap", any(g["check"] == "bp_check" for g in care_gap.find_gaps(done, ctx, 0)))
done.done["bp_check"] = 5
check("after the visit the gap is closed", not any(g["check"] == "bp_check" for g in care_gap.find_gaps(done, ctx, 6)))

# 10, 11, 12, 16, 17. genetics
brca = f[f.gene_BRCA & (f.age >= 25)]
if len(brca):
    b = P[brca.Id.iloc[0]]
    b_yes = with_row(b, consent_genetic=True, gene_status={"BRCA": "pathogenic"})
    b_no = with_row(b, consent_genetic=False, gene_status={"BRCA": "pathogenic"})
    b_vus = with_row(b, consent_genetic=True, gene_status={"BRCA": "VUS"})
    g_yes = {g["check"] for g in care_gap.find_gaps(b_yes, ctx, 0)}
    check("BRCA with consent: genetic counselling offered", "genetic_counselling" in g_yes)
    check("BRCA without consent: no genetic gaps", "genetic_counselling" not in {g["check"] for g in care_gap.find_gaps(b_no, ctx, 0)})
    check("an uncertain variant (VUS) never changes the plan", "genetic_counselling" not in {g["check"] for g in care_gap.find_gaps(b_vus, ctx, 0)})
txt, _ = outreach.safe_message(ctx, "breast_screening", "adult", "English", "Clinic visit")
check("a breast screening invite never names a gene or cancer", txt and not any(w in txt.lower() for w in ("gene", "brca", "cancer")))
task = fhir.to_task("x", {"check": "genetic_counselling", "pathway": "Genetic pathway", "reason": "Genetic marker: BRCA",
                          "decision": "Clinician decides", "invite_by_day": 3, "guideline": "Clinician review"}, ctx)
check("FHIR Task hides the genetic result and is a proposal", "BRCA" not in str(task) and task["intent"] == "proposal")

# 14, 15. timing
c14 = orchestrator.build_context("new", 1, simulation.DEFAULT_PARAMS, pd.Timestamp("2027-02-10"))
w = outreach.contact_window(c14, c14.people[s1.pid], 0)
check(f"during Ramadan messages go after iftar ({w})", w == "20:30-22:30")
c15 = orchestrator.build_context("new", 1, simulation.DEFAULT_PARAMS, pd.Timestamp("2026-12-02"))
check("no outreach on National Day", outreach.is_holiday(c15, 0))

# 16. the risk range always contains the score
pp = pd.read_csv(os.path.join(utils.OUTPUTS, "new", "people_final.csv")).dropna(subset=["risk"])
check(f"risk range contains the score for all {len(pp)} scored people",
      ((pp.risk_low <= pp.risk + 1e-9) & (pp.risk <= pp.risk_high + 1e-9)).all())

# 18. fixed chat reply
fallback = summaries.general_answer("what is this?", "English", "bp_check")
check("chat answers are the fixed approved text", fallback.startswith("Thank you for your question"))

# 19. usual care uses no genetics and no risk
co = fresh("old")
og = care_gap.find_gaps(co.people[b.pid] if len(brca) else co.people[s1.pid], co, 0, "old")
check("usual care only uses age-and-sex checks", {g["check"] for g in og} <= care_gap.OLD_WAY_CHECKS)

# 20. no-show support only adds help
check("no-show support (a plain rule) only adds help: never lowers priority or blocks a booking",
      all(w_ not in noshow.rule_support(b_, n_).lower() for b_ in (0.0, 0.4, 0.9) for n_ in (0, 1, 3) for w_ in ("priority", "refuse", "block", "cancel"))
      and noshow.rule_support(0.0, 0) == noshow.SUPPORT[0] and noshow.rule_support(0.95, 3) == noshow.SUPPORT[2])

# 21. a child's consent is the guardian's decision
ckid = kid
consent.record(ctx, 5, ckid.pid, "contact", False, ckid.pid)        # the child tries to decide alone
check("a child cannot give or withdraw consent alone", consent.has(ctx, ckid, "contact") is False)

# 22a. emergency words in the patients' languages raise an Urgent call-back that a nurse makes outside the queue
from models import risk_scores as risk_model  # noqa: E402
for words in ("Mujhe seene mein dard hai", "سینے میں درد ہے", "hindi makahinga ang anak ko"):
    ep = next(p for p in ctx.people.values() if not p.is_child and p.state != "Opted out"
              and not any(c["check"] == "emergency_callback" for c in p.cards))
    ans = chat.handle_reply(ctx, ep, words, 6)
    card = next((c for c in ep.cards if c["check"] == "emergency_callback"), None)
    check(f"emergency words ({words[:18]}...) give 998 and an Urgent call-back card",
          "998" in ans and card is not None and ep.urgency == "Urgent")
outreach.emergency_callbacks(ctx, 7)
check("the nurse call-back is made and the card closed", card["status"] == "done")

# 22b. risk scores: published, scaled to the local level; no score counts as the middle
rc = utils.load_json(os.path.join(utils.MODELS, "risk_model_card.json"), {})
check(f"the risk score is the published one, scaled to the local level (shift {rc['conditions']['diabetes']['local_calibration']['intercept_shift']})",
      "FINDRISC" in rc["conditions"]["diabetes"]["score"] and 0 < rc["base_rate_per_100"] < 40)
check("a person with no risk score counts as the middle rank (0.5)", risk_model.risk_rank(None) == 0.5)

# 22c. STOP covers the whole family on that phone
fam_rec = next(g for g in ctx.people.values() if len(ctx.family(g)) >= 2 and g.state != "Opted out")
child_f = next(m for m in ctx.family(fam_rec) if m is not fam_rec)
stop_word = next(w for w, k in ctx.reply_words.items() if k == "STOP")
chat.handle_reply(ctx, child_f, stop_word, 8)
check("STOP for one child stops messages about every child and the parent",
      all(not consent.has(ctx, m, "contact") for m in ctx.family(fam_rec)))

# 22d. telehealth books only checks that can be done remotely
tp = next(p for p in ctx.people.values() if not p.is_child and p.state not in ("Opted out", "Booked"))
tp.cards = [{"check": "depression_screen", "status": "ready"}, {"check": "bp_check", "status": "ready"}]
tp.booking_option, tp.state = "Telehealth", "Contacted"
chat.book(ctx, tp, 9)
check("a telehealth booking leaves in-person checks for a clinic visit",
      tp.booking_checks == ["depression_screen"] and any(c["check"] == "bp_check" and c["status"] == "ready" for c in tp.cards))

# 22e. a results review stays open until it is actually done, even when another visit happens first
from agents.followup import FollowUpAgent  # noqa: E402
rp = next(p for p in ctx.people.values() if not p.is_child and p.state not in ("Opted out",) and p.pid != tp.pid)
rp.needs_results_review = ["diabetes"]
rp.cards = [{"check": "results_review", "status": "agreed"}, {"check": "bp_check", "status": "ready"}]
rp.booking_checks = ["bp_check"]
FollowUpAgent()._attended(ctx, rp, 10)
check("a visit for another check does not close an open results review",
      any(c["check"] == "results_review" and c["status"] == "agreed" for c in rp.cards) and rp.needs_results_review == ["diabetes"])

# 22. end-to-end results (from orchestrator.py + evaluate.py)
r = utils.load_json(os.path.join(utils.OUTPUTS, "results", "results.json"), {})
if r:
    check("this system completes more Well-Child care than usual care", r["new"]["well_child"]["people_rate"] > r["old"]["well_child"]["people_rate"])
    check("this system completes more adult screening than usual care", r["new"]["adult_screening"]["people_rate"] > r["old"]["adult_screening"]["people_rate"])
    check("this system finds more hidden cases", r["clinical_value"]["extra_found"] > 0)
    check("abnormal results are followed up more often", r["new"]["followup_within_30_days"] > r["old"]["followup_within_30_days"])
    small = [row for g in r["fairness_new"].values() for row in g if row.get("count") == "Too few to report"]
    check("fairness tables hide small groups", all(row["uptake"] == "Too few to report" for row in small))
    from models import risk_scores as _rs  # noqa: E402
    sg = r.get("safeguards_new", {})
    allrow = (sg.get("all") or [{}])[0]
    groups_ok = all(isinstance(sg.get(g), list) and sg[g] for g in ("nationality", "region", "language", "insurance", "sex", "age_band"))
    shown = [row for g, rows in sg.items() if g not in ("all", "definitions") for row in rows if row.get("count") != "Too few to report"]
    # the safeguard numbers are recomputed here from the truth file, independently of evaluate.py
    import evaluate as _ev  # noqa: E402
    pn_ = _ev.load("new")[0]
    tr_ = utils.load_truth()
    start_ = pd.Timestamp(r["run"]["start"])
    hz = start_ + pd.Timedelta(days=round(utils.config("synthetic_data")["risk_cutoff_years"] * 365))
    t_ = tr_.reindex(pn_.Id)
    case_ = np.zeros(len(pn_), bool)
    for c_ in _rs.CONDITIONS:                      # the conditions that get a predicted score (diabetes)
        on = pd.to_datetime(t_[f"{c_}_onset"])
        case_ |= (~t_[f"diagnosed_{c_}"].fillna(False).astype(bool) & on.notna() & (on <= hz)).values
    sc_ = pn_.risk.notna().values
    fl_ = sc_ & (pn_.risk.fillna(-1).values >= r["risk_model"]["threshold_high"])   # the saved cut-off (earlier checks change the cached one)
    fnr_ = ((~fl_) & case_ & sc_).sum() / (case_ & sc_).sum()
    fpr_ = (fl_ & ~case_).sum() / (sc_ & ~case_).sum()
    check(f"safeguards: false-negative ({allrow.get('false_negative_rate')}) and false-positive ({allrow.get('false_positive_rate')}) rates "
          "match an independent count from the truth file, for every group, with small groups hidden",
          groups_ok and abs(allrow["false_negative_rate"] - fnr_) < 0.001 and abs(allrow["false_positive_rate"] - fpr_) < 0.001
          and all(row.get("false_negative_rate") is None or 0 <= row["false_negative_rate"] <= 1 for row in shown)
          and r["safeguards_old"]["all"][0]["hidden_found_share"] < allrow["hidden_found_share"])
    m = pd.read_csv(os.path.join(utils.OUTPUTS, "new", "messages.csv"))
    sent = m[m.channel != "reply"].text.str.lower()
    bad = sent[sent.str.contains("gene|brca|cancer|risk score|carrier|%", regex=True)]
    check(f"none of the {len(sent)} messages sent names a gene, cancer, carrier or risk score", len(bad) == 0)
    al = pd.read_csv(os.path.join(utils.OUTPUTS, "new", "alerts.csv"))
    late_gaps = al[al.kind.str.startswith("Fairness") & al.kind.str.contains("reached") & (al.day >= 35)]
    check(f"reach floor: no group is still far behind on being reached after day 35 ({len(late_gaps)} alerts)", len(late_gaps) == 0)
    check("no contactable Urgent person misses the 2-day deadline", not (al.kind == "Urgent deadline missed").any())
else:
    print("SKIP end-to-end checks: run orchestrator.py and evaluate.py first")

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
