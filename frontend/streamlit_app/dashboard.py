"""
Dashboard (guide Step 11): six pages for doctors, outreach staff, managers and judges.

    streamlit run frontend/streamlit_app/dashboard.py      (from the project folder)

Reads outputs/ written by orchestrator.py and evaluate.py. Doctor decisions are saved to outputs/doctor_feedback.csv.
"""
import json
import os
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

# the backend modules live two folders up, ui_pages.py sits next to this file
HERE = os.path.dirname(os.path.abspath(__file__))
for folder in (os.path.dirname(os.path.dirname(HERE)), HERE):
    if folder not in sys.path:
        sys.path.insert(0, folder)

import summaries
import simulation
from api import auth
import utils
from models.risk_scores import band as risk_band

CA = simulation.DESIGN["community_actions"]
SV = simulation.DESIGN["streamlit_view"]          # map and list display settings

st.set_page_config(page_title="Precision Prevention", layout="wide")

TEXT = {   # Arabic labels are drafts: a native speaker must check them
    "English": {"title": "Precision Prevention and Screening", "banner": "Priority list for outreach. Not a diagnosis. The doctor decides.",
                "pages": ["Doctor view", "Map", "Communities", "Family view", "Patient journey", "Results"], "lang": "Language"},
    "العربية": {
        "title": "الوقاية والفحص الدقيق",
        "banner": "قائمة أولويات للتواصل. ليست تشخيصاً. الطبيب هو من يقرر.",
        "pages": ["عرض الطبيب", "الخريطة", "المجتمعات",
                  "عرض الأسرة", "رحلة المريض", "النتائج"],
        "lang": "اللغة"},
}
FEEDBACK = os.path.join(utils.OUTPUTS, "doctor_feedback.csv")


# ------------------------------------------------------------------ data
def people(mode="new"):
    """Only the people the signed-in account may see (same rule as the web app: region, or own record and children)."""
    p = _load_people(mode)
    acc = st.session_state.get("account") or {}
    if acc.get("person"):
        return p[(p.Id == acc["person"]) | (p.guardian_id == acc["person"])]
    if acc.get("region"):
        return p[p.region == acc["region"]]
    return p


@st.cache_data
def _load_people(mode="new"):
    p = pd.read_csv(os.path.join(utils.OUTPUTS, mode, "people_final.csv"), dtype={"Id": str, "household_id": str, "guardian_id": str})
    for c in ("cards", "done", "detected", "diagnosed", "risk_reasons", "warning_signs", "data_used"):
        p[c] = p[c].map(json.loads)
    p["risks"] = p.risks.map(lambda s: json.loads(s) if isinstance(s, str) else {}) if "risks" in p.columns else [{}] * len(p)
    p["open_cards"] = p.cards.map(lambda cs: [c for c in cs if c["status"] in ("ready", "agreed", "awaiting_doctor")])
    return p


@st.cache_data
def events(mode="new"):
    return pd.read_csv(os.path.join(utils.OUTPUTS, mode, "events.csv"), dtype={"person": str})


@st.cache_data
def messages(mode="new"):
    return pd.read_csv(os.path.join(utils.OUTPUTS, mode, "messages.csv"), dtype={"person": str})


@st.cache_data
def results():
    return utils.load_json(os.path.join(utils.OUTPUTS, "results", "results.json"), {})


@st.cache_data
def facilities():
    return utils.load_facilities()


def outputs_ready():
    return os.path.exists(os.path.join(utils.OUTPUTS, "new", "people_final.csv"))


def save_feedback(pid, choice, note):
    who = (st.session_state.get("account") or {}).get("user", "local")
    row = pd.DataFrame([{"time": datetime.now().isoformat(timespec="seconds"), "person": pid, "decision": choice, "note": note,
                         "by": who}])
    row.to_csv(FEEDBACK, mode="a", header=not os.path.exists(FEEDBACK), index=False)


def risk_text(r):
    if r.data_sufficient != 1 or pd.isna(r.risk):
        return "Not enough data to judge. Basic check-up suggested." if r.age >= utils.config("clinical_rules")["adult_age"] else "No risk score for children (rules only)."
    return f"Risk {r.risk:.0%} (range {r.risk_low:.0%} to {r.risk_high:.0%})"


def cards_table(cards):
    if not cards:
        return pd.DataFrame()
    return pd.DataFrame(cards)[["pathway", "check", "reason", "decision", "status", "guideline", "recall_months"]]


# ------------------------------------------------------------------ pages
def doctor_view(T):
    import ui_pages
    ui_pages.banner(T["banner"])
    p = people()
    open_ = p[p.open_cards.map(len) > 0].copy()
    c1, c2, c3 = st.columns(3)
    urg = c1.multiselect("Urgency", ["Urgent", "High", "Routine"], default=["Urgent", "High"])
    region = c2.multiselect("Region", sorted(p.region.unique()), default=sorted(p.region.unique()))
    only_clin = c3.checkbox("Only cards waiting for a doctor", value=False)
    q = open_[open_.urgency.isin(urg) & open_.region.isin(region)]
    if only_clin:
        q = q[q.open_cards.map(lambda cs: any(c["status"] == "awaiting_doctor" for c in cs))]
    rank = {"Urgent": 0, "High": 1, "Routine": 2}
    q = q.assign(r=q.urgency.map(rank)).sort_values(["r", "priority"], ascending=[True, False])
    st.caption(f"{len(q)} people match")
    top = q.head(simulation.DESIGN["page_sizes"]["doctor_list"])
    rows = [r for _, r in top.iterrows()]   # plain lists: DataFrame.apply can return a DataFrame and crash assign()
    show = top.assign(risk_range=[risk_text(r) for r in rows],
                      reasons=[", ".join(list(r.warning_signs) + list(r.risk_reasons)) for r in rows],   # signs decide urgency
                      checks=[", ".join(c["check"] for c in r.open_cards) for r in rows])
    st.dataframe(show[["Id", "urgency", "age", "sex", "region", "risk_range", "reasons", "checks"]], hide_index=True, width="stretch")
    if q.empty:
        return
    pid = st.selectbox("Open a person", q.Id.head(SV["person_picker_rows"]))
    r = p.set_index("Id").loc[pid]
    acc = st.session_state.get("account")
    if acc and st.session_state.get("_logged_pid") != pid:     # who opened which record, as in the web app
        auth.log("opened_record", acc["user"], acc["role"], "streamlit", f"Doctor view {pid}")
        st.session_state["_logged_pid"] = pid
    st.subheader(f"Person {pid[:8]}, {r.age} years, {r.sex}, {r.region}")
    if r.warning_signs:
        st.write("Warning signs: " + ", ".join(r.warning_signs))
    # each condition separately, as in the web app: a finding replaces the prediction, a known diagnosis is not predicted
    risks = r.risks if isinstance(r.risks, dict) else {}
    for c, name in (("diabetes", "Diabetes"), ("hypertension", "High blood pressure")):
        if c in (r.detected or {}):
            st.write(f"**{name}:** found at a visit on day {r.detected[c]}: results review needed")
        elif int(r.get(f"known_{c}", 0) or 0):
            st.write(f"**{name}:** already diagnosed, not predicted")
        elif risks.get(c):
            s = risks[c]
            st.write(f"**{name}:** chance of having it now, not yet diagnosed: {s['risk']:.1%} "
                     f"(likely {s['low']:.1%} to {s['high']:.1%}; {risk_band(s['risk'], c)}); main factors: {', '.join(s['reasons'])}")
    if not risks and not r.detected:
        st.write(risk_text(r))
    st.caption("Data used: " + ", ".join(r.data_used))
    band = risk_band(r.risk)
    st.info(summaries.clinician_summary(band, r.risk_reasons, r.warning_signs, r.urgency if isinstance(r.urgency, str) else None))
    st.dataframe(cards_table(r.cards), hide_index=True, width="stretch")
    choice = st.radio("Do you agree with this flag?", ["Agree", "Disagree"], horizontal=True, key=f"c_{pid}")
    note = st.text_input("Note (optional)", key=f"n_{pid}")
    if st.button("Save", key=f"s_{pid}"):
        save_feedback(pid, choice, note)
        st.success("Saved. Disagree puts the person on hold and stops contact." if choice == "Disagree" else "Saved. The case goes to outreach.")


def map_page(T):
    import pydeck as pdk
    p = people()
    adults = p.assign(gap=p.open_cards.map(len) > 0)
    places = adults.groupby("home_place").agg(lat=("home_lat", "mean"), lon=("home_lon", "mean"), people=("Id", "size"),
                                              gap_rate=("gap", "mean"), region=("region", "first")).reset_index()
    places = places[places.people >= simulation.DESIGN["map_min_people"]]
    places["radius"] = SV["place_radius_base_m"] + places.people * SV["place_radius_per_person_m"]
    places["color"] = places.gap_rate.map(lambda g: [int(60 + 190 * g), int(120 - 80 * g), 200 - int(150 * g), 170])   # blue to red
    fac = facilities()
    layers = [pdk.Layer("ScatterplotLayer", places, get_position="[lon, lat]", get_radius="radius", get_fill_color="color", pickable=True),
              pdk.Layer("ScatterplotLayer", fac, get_position="[lon, lat]", get_radius=SV["facility_radius_m"], get_fill_color=[30, 30, 30, 200], pickable=True)]
    lat, lon = SV["map_centre"]
    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(latitude=lat, longitude=lon, zoom=SV["map_zoom"]),
                             tooltip={"text": "{home_place}\nPeople: {people}\nOpen care gaps: {gap_rate}"}, map_style="light"))
    st.caption(f"Circles: places with {simulation.DESIGN['map_min_people']} or more residents, redder = more open care gaps. Dark dots: health facilities. "
               "Map data © OpenStreetMap contributors.")
    st.dataframe(places.sort_values("gap_rate", ascending=False)[["home_place", "region", "people", "gap_rate"]].round(2),
                 hide_index=True, width="stretch")


def community_actions(c):
    actions = []
    if c.children_overdue >= CA["children_overdue_for_school_day"]:
        actions.append("School or nursery screening day for check-ups, vaccines and development screens")
    if c.area_type == "remote" and c.share_with_gaps > CA["remote_gap_share_for_mobile_clinic"]:
        actions.append("Mobile clinic visit for the whole community")
    if any(g in ("diabetes_screen", "bp_check") for g in c.top_gaps):
        actions.append("Community health day with blood sugar and blood pressure checks")
    if c.uptake < CA["low_uptake_for_health_talk"]:
        actions.append(f"Health talk in {c.main_language} with a trusted local leader")
    if "carrier_counselling" in c.top_gaps or "premarital_screening" in c.top_gaps:
        actions.append("Group information session on carrier screening before marriage")
    return actions


def communities(T):
    p = people()
    rows = []
    for place, d in p.groupby("home_place"):
        if len(d) < CA["min_people"]:
            continue                                               # small-group rule
        gaps = pd.Series([c["check"] for cs in d.cards for c in cs]).value_counts()
        rec = d[d.cards.map(len) > 0]
        rows.append({"place": place, "region": d.region.iloc[0], "area_type": d.area_type.iloc[0], "people": len(d),
                     "share_with_gaps": round((d.n_gaps_start.fillna(0) > 0).mean(), 2), "top_gaps": list(gaps.index[:3]),
                     "children_overdue": int(((d.is_child == 1) & d.cards.map(lambda cs: any(c["check"] == "well_child_visit" for c in cs))).sum()),
                     "uptake": round((rec.done.map(len) > 0).mean(), 2) if len(rec) else 0.0,
                     "avg_travel_min": round(d.travel_min.mean(), 0), "main_language": d.language.mode().iloc[0],
                     "reliable": "OK" if len(d) >= CA["reliable_people"] else f"Not reliable: under {CA['reliable_people']} people"})
    t = pd.DataFrame(rows).sort_values("share_with_gaps", ascending=False)
    t["actions"] = t.apply(lambda c: "; ".join(community_actions(c)), axis=1)
    st.dataframe(t, hide_index=True, width="stretch")
    place = st.selectbox("Plan for a community", t.place)
    c = t.set_index("place").loc[place]
    st.write(f"**Suggested actions for {place}:** " + (c.actions or "none needed right now"))


def family_view(T):
    p = people()
    fams = p.groupby("household_id").filter(lambda d: len(d) > 1 and (d.open_cards.map(len) > 0).any())
    hh = st.selectbox("Household", fams.household_id.unique())
    d = p[p.household_id == hh]
    guardian = d[d.is_child == 0]
    st.write(f"Messages for the children go to the guardian ({guardian.Id.iloc[0][:8] if len(guardian) else 'unknown'}), "
             f"in {d.language.iloc[0]}. Consent for children is the guardian's decision.")
    for r in d.itertuples():
        with st.expander(f"{'Child' if r.is_child else 'Adult'}, {r.age} years, {r.sex}: {len(r.open_cards)} open card(s), state {r.state}"):
            st.write(risk_text(r))
            st.dataframe(cards_table(r.cards), hide_index=True, width="stretch")


def patient_journey(T):
    ev = events()
    p = people()
    interesting = [i for i in ev[ev.event.isin(["diagnosed", "attended"])].person.unique() if i in set(p.Id)]
    pid = st.selectbox("Person (with a completed visit)", interesting[:500])
    r = p.set_index("Id").loc[pid]
    st.write(f"{r.age} years, {r.sex}, {r.nationality}, speaks {r.language}, {r.region} ({r.travel_min:.0f} min to {r.nearest_facility})")
    st.dataframe(ev[ev.person == pid][["date", "event", "detail"]], hide_index=True, width="stretch")
    m = messages()
    st.markdown("**Messages and replies**")
    st.dataframe(m[m.person == pid][["day", "channel", "language", "window", "text"]], hide_index=True, width="stretch")


def results_page(T):
    r = results()
    if not r:
        st.warning("Run python evaluate.py first.")
        return
    n, o, cv = r["new"], r["old"], r["clinical_value"]
    st.caption("Simulated results; not real patient outcomes.")
    c = st.columns(4)
    c[0].metric("Well-Child care completed", f"{n['well_child']['people_rate']:.0%}", f"{(n['well_child']['people_rate'] - o['well_child']['people_rate']) * 100:+.0f} pts vs usual care")
    c[1].metric("Adult screening completed", f"{n['adult_screening']['people_rate']:.0%}", f"{(n['adult_screening']['people_rate'] - o['adult_screening']['people_rate']) * 100:+.0f} pts")
    sn, so = cv["share_of_hidden_cases_found_new"], cv["share_of_hidden_cases_found_old"]
    end_key = [k for k in sn if k.startswith("found_by_day_") and k != "found_by_day_30"][0]
    c[2].metric("Hidden cases found", f"{sn[end_key]:.0%}", f"{(sn[end_key] - so[end_key]) * 100:+.0f} pts")
    c[3].metric("Results followed up in 30 days", f"{n['followup_within_30_days']:.0%}", f"{(n['followup_within_30_days'] - o['followup_within_30_days']) * 100:+.0f} pts")
    daily = pd.concat([pd.read_csv(os.path.join(utils.OUTPUTS, m_, "daily.csv")).assign(way=lbl)
                       for m_, lbl in (("new", "This system"), ("old", "Usual care"))])
    st.line_chart(daily.pivot(index="day", columns="way", values="people_with_completed_care"))
    st.markdown("**This system against usual care**")
    rows = [("Contacted", n["contacted"], o["contacted"]), ("Messages sent", n["messages_sent"], o["messages_sent"]),
            (f"Timely attendance (within {simulation.DESIGN['outcome_window_days']} days)", n["timely_attendance"], o["timely_attendance"]),
            ("Abnormal results found", n["abnormal_found"], o["abnormal_found"]),
            ("Contacts per case found", n["contacts_per_case_found"], o["contacts_per_case_found"]),
            ("Opt-out rate", n["opt_out_rate"], o["opt_out_rate"]),
            ("Cost per completed check (AED, published prices)", r["cost_new"]["cost_per_completed_check_aed"], r["cost_old"]["cost_per_completed_check_aed"]),
            ("Cost per person with completed care (AED, outreach + visits)", r["cost_new"].get("cost_per_person_completed_aed"), r["cost_old"].get("cost_per_person_completed_aed"))]
    st.dataframe(pd.DataFrame(rows, columns=["Measure", "This system", "Usual care"]), hide_index=True, width="stretch")
    if cv.get("avg_days_earlier_when_both_found") is not None:
        st.caption(f"For cases found by both ways, the difference in timing was {cv['avg_days_earlier_when_both_found']} days "
                   "(negative = usual care was earlier for those few, easier-to-reach cases).")
    if "sensitivity_worst_case" in r:
        w = r["sensitivity_worst_case"]
        st.markdown(f"**Worst case** (telehealth boost {w['telehealth_boost']}, base attendance {w['base_attendance_logit']}, "
                    f"reply rate x{w['reply_mult']}): {w['extra_found']:.0f} more cases found, {w['extra_people_completed']:.0f} more people with completed care.")
    sg = simulation.DESIGN["small_groups"]
    st.markdown(f"**Fairness: uptake by group (groups under {sg['hide_under']} hidden, under {sg['not_reliable_under']} marked)**")
    g = st.selectbox("Group", list(r["fairness_new"].keys()))
    st.dataframe(pd.DataFrame(r["fairness_new"][g]), hide_index=True, width="stretch")
    if r.get("safeguards_new"):     # requirement 4: errors and missed cases by group, against the hidden truth
        s = r["safeguards_new"]
        a = s["all"][0]
        st.markdown("**Safeguards: false positives, false negatives and missed hidden cases by group**")
        st.caption(f"A true case is {s['definitions']['true_case']}. Everyone scored: {a['flag_rate']:.0%} flagged High, "
                   f"{a['false_positive_rate']:.0%} false positives, {a['false_negative_rate']:.0%} false negatives; "
                   f"visits found {a['hidden_found_share']:.0%} of hidden cases.")
        cols = ["group", "count", "scored", "true_cases", "flag_rate", "false_positive_rate", "false_negative_rate",
                "cases_not_scored", "hidden_found_share", "reliable"]
        t = pd.DataFrame(s.get(g, []))
        st.dataframe(t[[c for c in cols if c in t.columns]], hide_index=True, width="stretch")
    st.markdown("**Real-data check of the risk method (NHANES)**")
    nh = r.get("nhanes") or {}
    if nh:
        st.write(f"The diabetes score that sets the order is the published FINDRISC questionnaire (nothing is trained; a trained second opinion is "
                 f"shown to doctors in the web app as information only). On {nh['adults']:,} real NHANES adults "
                 f"it ranks undiagnosed diabetes with AUC {nh['headline']['auc']} ({nh['headline']['auc_low']} to {nh['headline']['auc_high']}).")
        st.dataframe(pd.DataFrame(nh["scores"])[["name", "auc", "auc_low", "auc_high", "cases_found_share", "invited_share"]], hide_index=True, width="stretch")
    rm = r.get("risk_model") or {}
    if rm:
        st.write(f"{rm.get('method', '')}. Not used as inputs: {', '.join(rm['not_used'])}.")
    fl = os.path.join(utils.OUTPUTS, "new", "followup_learning.csv")
    if os.path.exists(fl):
        st.markdown("**What the follow-up agent learned**")
        st.dataframe(pd.read_csv(fl).sort_values("tries", ascending=False).head(SV["learning_rows"]), hide_index=True, width="stretch")


# ------------------------------------------------------------------ main
import ui_pages  # noqa: E402

PAGES = {   # key: (English, Arabic draft)
    "doctor": ("Doctor view", "عرض الطبيب"),
    "queue": ("Outreach queue", "قائمة التواصل"),
    "family": ("Family view", "عرض الأسرة"),
    "journey": ("Patient journey", "رحلة المريض"),
    "phone": ("Patient phone", "هاتف المريض"),
    "why": ("Why was I contacted?", "لماذا تم التواصل معي؟"),
    "map": ("Map", "الخريطة"),
    "communities": ("Communities", "المجتمعات"),
    "results": ("Results", "النتائج"),
}
ROLE_NAMES = {"admin": "Hospital admin (all pages)", "doctor": "Doctor", "outreach": "Outreach staff",
              "manager": "Programme manager", "patient": "Patient or family"}   # display names only
# who sees which pages: the same lists as the React app and the server (config/roles.json), so roles are defined once
ROLE_KEYS = list(utils.config("roles")["role_pages"])
ROLES = {ROLE_NAMES.get(k, k): [pg for pg in utils.role_pages()[k] if pg in PAGES] for k in ROLE_KEYS}   # demo tools only with PP_DEMO=1


def main():
    lang = st.sidebar.radio("Language / اللغة", list(TEXT.keys()))
    T = TEXT[lang]
    ar = lang != "English"
    if ar:
        st.markdown("<style>.stMarkdown, .stText, h1, h2, h3, p {direction: rtl; text-align: right;}</style>", unsafe_allow_html=True)
    qp = st.query_params
    if auth.sign_in_required():
        # the same accounts, lockout and access log as the web app: the role and limits come from the account
        acc = st.session_state.get("account")
        if acc is None:
            st.title(T["title"])
            with st.form("sign_in"):
                user = st.text_input("User name")
                pw = st.text_input("Password", type="password")
                if st.form_submit_button("Sign in"):
                    acc = auth.verify(user.strip(), pw, "streamlit")
                    auth.log("sign_in" if acc else "sign_in_failed", user.strip(), acc["role"] if acc else "", "streamlit",
                             "Streamlit dashboard" if acc else ("locked" if auth.is_locked(user.strip(), "streamlit") else "wrong user name or password"))
                    if acc:
                        st.session_state["account"] = acc
                        st.rerun()
                    st.error("Wrong user name or password, or the account is locked for a while.")
            st.caption("Accounts are created by the hospital admin (there is no self sign-up).")
            return
        if acc["user"] not in (auth.load_users() or {acc["user"]: 1}):
            st.session_state.pop("account", None)       # the account was removed
            st.rerun()
        role = ROLE_NAMES.get(acc["role"], acc["role"])
        st.sidebar.write(f"Signed in: **{acc['user']}** ({role})" + (f", only {acc['region']}" if acc.get("region") else "")
                         + (", own record and children" if acc.get("person") else ""))
        if st.sidebar.button("Sign out"):
            auth.log("sign_out", acc["user"], acc["role"], "streamlit", "Streamlit dashboard")
            st.session_state.pop("account", None)
            st.rerun()
    else:
        # open mode (no accounts, own computer): preview any role; deep links e.g. ?role=patient&page=why
        want_role = qp.get("role", "admin")
        role = st.sidebar.selectbox("Preview as (no accounts set up)", list(ROLES),
                                    index=ROLE_KEYS.index(want_role) if want_role in ROLE_KEYS else 0)
    ui_pages.header(T["title"], role, T)
    if not outputs_ready():
        st.warning("No simulation results yet. Run: python orchestrator.py, then python evaluate.py")
        return
    keys = ROLES[role]
    labels = [PAGES[k][1 if ar else 0] for k in keys]
    want = qp.get("page")
    page = st.sidebar.radio("Page", labels, index=keys.index(want) if want in keys else 0)
    key = keys[labels.index(page)]
    if not auth.sign_in_required():
        st.sidebar.caption("No accounts set up: anyone on this computer can preview every role (python scripts/add_user.py).")
    st.sidebar.caption("This is the older Streamlit dashboard, kept as a fallback. The main app (landing page, sign-up, "
                       "Accounts, My care, Arabic throughout) is python server.py.")
    p = people()
    {"doctor": lambda: doctor_view(T), "queue": lambda: ui_pages.outreach_queue(T, p), "family": lambda: family_view(T),
     "journey": lambda: patient_journey(T), "phone": lambda: ui_pages.patient_phone(T, p), "why": lambda: ui_pages.why_contacted(T, p),
     "map": lambda: map_page(T), "communities": lambda: communities(T), "results": lambda: results_page(T)}[key]()


main()
