"""
Extra dashboard pages: the outreach work queue, the patient's phone (WhatsApp-style chat with the real
chat assistant), and "Why was I contacted?" with consent choices. Used by dashboard.py.
"""
import html
import os
from datetime import datetime

import pandas as pd
import streamlit as st

import consent
import simulation
import utils
from agents import chat, outreach
from agents.priority import DEADLINE_DAYS

OUTREACH_LOG = os.path.join(utils.OUTPUTS, "outreach_log.csv")
CONSENT_LOG = os.path.join(utils.OUTPUTS, "consent_changes.csv")
PLAIN = {   # what a check is called when talking to a patient: never a disease, gene or risk score
    "well_child_visit": "child health check", "developmental_screen": "development check", "autism_screen": "development check",
    "mmr_vaccine": "vaccination", "bp_check": "blood pressure check", "diabetes_screen": "blood sugar check",
    "hba1c_test": "blood sugar test", "lipid_test": "blood test", "cholesterol_test": "blood test",
    "cervical_screening": "cervical screening", "breast_screening": "breast screening", "bowel_screening": "bowel screening",
    "depression_screen": "wellbeing check", "lung_screening": "lung check", "basic_health_check": "health check",
    "blood_sugar_test": "blood sugar test", "genetic_counselling": "appointment with our specialist team",
    "carrier_counselling": "appointment with our specialist team", "premarital_screening": "premarital health check",
    "results_review": "results appointment", "prediabetes_programme": "healthy lifestyle programme",
    "weight_support": "healthy weight support", "stop_smoking_service": "stop-smoking support",
    "family_nutrition_programme": "family nutrition programme",
}
URGENCY_COLOUR = {"Urgent": "#C62828", "High": "#E65100", "Routine": "#2E7D32"}

CSS = """
<style>
.pp-header {background: linear-gradient(90deg,#0B5394,#1F6FD1); color:#fff; padding:14px 20px; border-radius:12px; margin-bottom:14px;}
.pp-header h2 {color:#fff; margin:0; font-size:1.45rem;}
.pp-header span {opacity:.9; font-size:.9rem;}
.pp-pill {display:inline-block; background:rgba(255,255,255,.18); padding:2px 10px; border-radius:999px; margin-left:8px; font-size:.8rem;}
.pp-banner {background:#FFF4E5; border-left:5px solid #E65100; padding:8px 14px; border-radius:6px; font-weight:600; color:#5D2E00; margin-bottom:10px;}
.pp-badge {display:inline-block; color:#fff; padding:1px 10px; border-radius:999px; font-size:.8rem; font-weight:600;}
.phone {width:360px; max-width:100%; margin:0 auto; border:10px solid #222; border-radius:36px; overflow:hidden;
        background:#E5DDD5; box-shadow:0 8px 24px rgba(0,0,0,.25);}
.phone-top {background:#075E54; color:#fff; padding:12px 14px; font-weight:600;}
.phone-top small {display:block; font-weight:400; opacity:.85;}
.phone-body {padding:12px 10px; min-height:380px; max-height:520px; overflow-y:auto;}
.bubble {max-width:82%; padding:8px 10px 18px 10px; margin:6px 0; border-radius:10px; position:relative; font-size:.92rem;
         line-height:1.35; color:#111; box-shadow:0 1px 1px rgba(0,0,0,.12); word-wrap:break-word;}
.in {background:#fff; margin-right:auto; border-top-left-radius:2px;}
.out {background:#DCF8C6; margin-left:auto; border-top-right-radius:2px;}
.bubble .t {position:absolute; right:8px; bottom:3px; font-size:.68rem; color:#667;}
.sys {text-align:center; font-size:.75rem; color:#555; background:#E1F3FB; border-radius:8px; padding:3px 8px; margin:8px auto; width:fit-content;}
</style>
"""


def header(title, role, T):
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(f"<div class='pp-header'><h2>{html.escape(title)}</h2><span>Signed in as: {html.escape(role)}"
                "</span></div>", unsafe_allow_html=True)


def banner(text):
    st.markdown(f"<div class='pp-banner'>{html.escape(text)}</div>", unsafe_allow_html=True)


def badge(urgency):
    c = URGENCY_COLOUR.get(urgency, "#607D8B")
    return f"<span class='pp-badge' style='background:{c}'>{html.escape(str(urgency))}</span>"


# ------------------------------------------------------------------ a live context for the chat
@st.cache_resource
def live_context():
    import orchestrator
    import simulation
    return orchestrator.build_context("new", simulation.DESIGN["simulation"]["live_demo_seed"], dict(simulation.DEFAULT_PARAMS), utils.run_date())


def reset_person(ctx, pid, cards):
    """Put the person back to how the simulation left them: open cards (as agreed by a doctor), waiting to be contacted."""
    p = ctx.people[pid]
    p.cards = [dict(c, status="agreed" if c["status"] == "awaiting_doctor" else c["status"])
               for c in cards if c["status"] in ("ready", "agreed", "awaiting_doctor")]
    p.state, p.contacted_on, p.awaiting_reply, p.booked_day, p.paused_until = "Contacted", 0, True, None, -1
    p.booking_option = outreach.care_option(ctx, p, p.cards[0]["check"]) if p.cards else "Clinic visit"
    ctx.consents = [c for c in ctx.consents if c["person"] != p.pid]
    return p


def person_label(r):
    who = f"Child, {r.age} y" if r.is_child else f"Adult, {r.age} y, {r.sex}"
    return f"{who} | speaks {r.language} | {r.region} | {', '.join(c['check'] for c in r.open_cards[:2])}"


def pick_person(people_df, key, children_first=False):
    cand = people_df[(people_df.open_cards.map(len) > 0) & (people_df.get("may_contact", people_df.consent) == 1)].copy()
    cand = cand.sort_values(["is_child", "priority"], ascending=[not children_first, False]).head(simulation.DESIGN["phone_demo_people"])
    labels = {r.Id: person_label(r) for r in cand.itertuples()}
    pid = st.selectbox("Person", list(labels), format_func=lambda i: labels[i], key=key)
    return people_df.set_index("Id").loc[pid], pid


# ------------------------------------------------------------------ outreach work queue
def outreach_queue(T, people_df):
    banner(T["banner"])
    ctx = live_context()
    rank = {"Urgent": 0, "High": 1, "Routine": 2}
    q = people_df[(people_df.open_cards.map(len) > 0) & (people_df.get("may_contact", people_df.consent) == 1) &
                  people_df.state.isin(["Flagged", "Contacted"])].copy()
    q = q.assign(r=q.urgency.map(rank).fillna(3)).sort_values(["r", "priority"], ascending=[True, False])
    c1, c2, c3 = st.columns(3)
    c1.metric("Waiting for contact", len(q))
    c2.metric("Urgent", int((q.urgency == "Urgent").sum()))
    c3.metric("Need a staff call (no app, cannot read, or no template)", int(q.head(simulation.DESIGN["page_sizes"]["doctor_list"]).apply(
        lambda r: outreach.channel_for(ctx.guardian(ctx.people[r.Id])) in ("Voice call", "Community health worker visit"), axis=1).sum()))
    s = simulation.DESIGN["streamlit_view"]["calls_slider"]
    n = st.slider("Calls for today", s["min"], s["max"], s["default"], s["step"])
    rows = []
    for r in q.head(n).itertuples():
        p = ctx.people[r.Id]
        rec = ctx.guardian(p)
        card = sorted(r.open_cards, key=lambda c: c["invite_by_day"])[0]
        rows.append({"Done": False, "Urgency": r.urgency, "Contact": "Guardian of child" if rec is not p else "Person",
                     "Person": r.Id[:8], "Channel": outreach.channel_for(rec), "Language": rec.row.language,
                     "Best time": outreach.contact_window(ctx, rec, 0), "About": PLAIN.get(card["check"], card["check"]),
                     "Care option": outreach.care_option(ctx, p, card["check"]),
                     "Deadline (days)": DEADLINE_DAYS.get(r.urgency, DEADLINE_DAYS["Routine"]), "Needs doctor first": card["decision"] == "Clinician decides",
                     "Id": r.Id})
    table = pd.DataFrame(rows)
    edited = st.data_editor(table.drop(columns=["Id"]), hide_index=True, width="stretch",
                            disabled=[c for c in table.columns if c != "Done"], key="queue_editor")
    if st.button("Save calls marked done"):
        done = table[edited.Done.values]
        if len(done):
            done.assign(time=datetime.now().isoformat(timespec="seconds"))[["time", "Id", "Channel", "About"]].to_csv(
                OUTREACH_LOG, mode="a", header=not os.path.exists(OUTREACH_LOG), index=False)
        st.success(f"Saved {len(done)} call(s) to outputs/outreach_log.csv")
    st.caption("Messages for a child always go to the guardian. 'Needs doctor first' cases are contacted only after a doctor agrees.")


# ------------------------------------------------------------------ patient phone
def bubble(text, side, time):
    return (f"<div class='bubble {side}' dir='auto'>{html.escape(text)}<span class='t'>{html.escape(time)}</span></div>")


def patient_phone(T, people_df):
    ctx = live_context()
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("### The family's phone")
        st.caption("This is what the family receives. Type a reply the way a parent or patient would: BOOK, INFO, LATER, STOP, "
                   "START, the Arabic words (حجز, توقف), a preference like 'call me' or 'evening', or any question.")
        row, pid = pick_person(people_df, "phone_person", children_first=True)
    key = f"chat_{pid}"
    p = ctx.people[pid]
    if key not in st.session_state or st.session_state.get("phone_last") != pid:
        reset_person(ctx, pid, row.cards)
        rec = ctx.guardian(p)
        convo = []
        if not p.cards:
            convo.append(("sys", "Nothing to send for this person right now", ""))
        else:
            card = p.cards[0]
            audience = "guardian" if p.is_child else "adult"
            text, own = outreach.safe_message(ctx, card["check"], audience, rec.row.language, p.booking_option)
            window = outreach.contact_window(ctx, rec, 0)
            if text is None:
                convo.append(("sys", f"No approved message for this check in {rec.row.language}: a nurse will call instead", ""))
            else:
                convo.append(("sys", f"Sent by {outreach.channel_for(rec)} at {window.split('-')[0]}"
                                     f"{'' if own else f' (in English: no approved {rec.row.language} template yet)'}", ""))
                convo.append(("in", text, window.split("-")[0]))
        st.session_state[key] = convo
        st.session_state["phone_last"] = pid
    convo = st.session_state[key]
    with right:
        rec = ctx.guardian(p)
        name = "Abu Dhabi Health Services"
        sub = f"to {'the parent / guardian' if p.is_child else 'the patient'} · {rec.row.language}"
        body = "".join(f"<div class='sys'>{html.escape(t)}</div>" if side == "sys" else bubble(t, side, tm) for side, t, tm in convo)
        st.markdown(f"<div class='phone'><div class='phone-top'>{name}<small>{html.escape(sub)}</small></div>"
                    f"<div class='phone-body'>{body}</div></div>", unsafe_allow_html=True)
    with left:
        quick = st.columns(6)
        pressed = None
        for col, word in zip(quick, ["BOOK", "INFO", "LATER", "STOP", "START", "حجز"]):
            if col.button(word, key=f"q_{word}_{pid}"):
                pressed = word
        typed = st.chat_input("Type a reply as the family...")
        reply = pressed or typed
        if reply:
            now = datetime.now().strftime("%H:%M")
            convo.append(("out", reply, now))
            if not chat.reply_allowed(ctx, p, reply):          # same rule as the API: emergencies are always answered
                convo.append(("sys", "No reply: this person has opted out (reply START to join again)", ""))
            else:
                answer = chat.handle_reply(ctx, p, reply, 0)
                convo.append(("in", answer, now))
            st.session_state[key] = convo
            st.rerun()
        g = ctx.guardian(p)
        st.markdown("#### What the system did")
        status = {"State": p.state, "Booked for": ctx.date(p.booked_day).date().isoformat() if p.booked_day else "-",
                  "Care option": p.booking_option or "-",
                  "May be contacted": "yes" if consent.has(ctx, p, "contact") else "no",
                  "Saved channel preference": g.preferred_channel or "-", "Saved time preference": g.preferred_time or "-"}
        st.table(pd.DataFrame(status.items(), columns=["", "Now"]))
        if st.button("Start this conversation again"):
            del st.session_state[key]
            st.rerun()


# ------------------------------------------------------------------ why was I contacted
def why_contacted(T, people_df):
    st.markdown("### Why was I contacted?")
    st.caption("The page a person opens from the link in their message. Plain words only: no diagnosis, no risk score, no genetic result.")
    row, pid = pick_person(people_df, "why_person")
    ctx = live_context()
    p = ctx.people[pid]
    who = "your child" if row.is_child else "you"
    st.markdown(f"**We contacted {'you about ' + who if row.is_child else who} because these routine checks are due:**")
    for c in row.open_cards:
        st.markdown(f"- {PLAIN.get(c['check'], c['check']).capitalize()}")
    signs = [s for s in row.warning_signs if not any(w in s.lower() for w in ("genetic", "abnormal", "risk"))]
    if signs:
        st.markdown("**Things we noticed in the records:** " + "; ".join(s.lower() for s in signs) + ".")
    st.markdown("**Information we used:** " + ", ".join(d for d in row.data_used if "Genetic" not in d)
                + (". With your permission, also genetic information." if row.consent_genetic else "."))
    st.info("This is an invitation for a routine check, not a diagnosis. A doctor or nurse will explain any result with you.")
    st.markdown("#### Your choices")
    decider = "As the parent or guardian, you decide for your child." if row.is_child else "You decide."
    st.caption(decider + " You can change your mind at any time.")
    c1 = st.toggle("You may contact me with reminders", value=consent.has(ctx, p, "contact"), key=f"cc_{pid}")
    c2 = st.toggle("Genetic results may be used to plan my check-ups", value=consent.has(ctx, p, "use_genetic"), key=f"cg_{pid}")
    if st.button("Save my choices"):
        given_by = row.guardian_id if row.is_child and isinstance(row.guardian_id, str) else pid
        for purpose, val in (("contact", c1), ("use_genetic", c2)):
            consent.record(ctx, 0, pid, purpose, val, given_by)
            pd.DataFrame([{"time": datetime.now().isoformat(timespec="seconds"), "person": pid, "purpose": purpose,
                           "granted": val, "given_by": given_by}]).to_csv(CONSENT_LOG, mode="a",
                                                                           header=not os.path.exists(CONSENT_LOG), index=False)
        st.success("Saved. Your choices are recorded with the date, and every change is in the audit log.")
