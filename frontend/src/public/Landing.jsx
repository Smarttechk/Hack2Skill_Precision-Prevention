// Public landing page: what the project is, how it works, who it is for (with a sign-up per role), results and FAQ.
import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, pct } from "../api.js";
import Icon from "../icons.jsx";
import { ROLE_INFO, ROLE_ORDER } from "../roleInfo.js";
import { useTr } from "../i18n.js";
import { LangSwitch, SourceNote } from "../components.jsx";

const STEPS = [
  { icon: "search", title: "Find care gaps", text: "Every record is checked against UAE and international guidelines: missed child check-ups and vaccines, overdue screenings, long-term illness without tests, and warning signs such as an abnormal result nobody followed up." },
  { icon: "scale", title: "Rank who needs care first", text: "A calibrated risk score with its range and reasons, plus warning signs, sets the urgency (Urgent, High, Routine) and a contact deadline. A doctor decides every clinical step." },
  { icon: "message", title: "Reach every family", text: "The right person (a guardian for a child), in their language, by the channel that works (WhatsApp, SMS, a call or a community health worker), at a good time. Replies such as BOOK or STOP work in Arabic too." },
  { icon: "bell", title: "Follow up and learn", text: "Reminders, rebooking, extra support for people likely to miss a visit, and a weekly check that every language, nationality and region is reached fairly." },
];

const AGENTS = [
  { icon: "search", name: "Care-gap agent", text: "Overdue checks, vaccines, screenings, genetic pathways (with consent) and warning signs." },
  { icon: "brain", name: "Risk agent", text: "Chance of undiagnosed diabetes or high blood pressure now, with a range, top reasons and the data used." },
  { icon: "scale", name: "Priority agent", text: "Urgency, priority score, contact deadline and a pathway card for each gap." },
  { icon: "megaphone", name: "Outreach agent", text: "Recipient, channel, approved message, best time and care option (clinic, telehealth, mobile clinic)." },
  { icon: "message", name: "Chat agent", text: "Answers replies: book, more information, later, stop, preferences, and emergency words (998)." },
  { icon: "calendar", name: "Follow-up agent", text: "Reminders, attendance, rebooking and a learner that finds the next best action." },
  { icon: "chart", name: "Evaluation agent", text: "Results against usual care, fairness by group, alerts for the team and the cost." },
];

const FAQ = [
  ["Is this a diagnosis?", "No. It is a priority list for outreach. Every clinical decision is the doctor's, and an abnormal result is always reviewed by a clinician."],
  ["Why must my account be approved after I sign up?", "Health records are private. The hospital admin checks who you are (a licence, a staff number, or your Emirates ID and date of birth) before an account can open any record. Patients only ever see their own record and their children's."],
  ["What does the system send to AI?", "Only approved, non-identifying fields (such as an age group or a check name). Never names, IDs, phone numbers or genetic results. It still works when AI is switched off."],
  ["Can I stop the messages?", "Yes. Reply STOP (or توقف) at any time, or change your choices on the 'Why was I contacted?' page. Emergency words are always answered with the 998 number."],
  ["Who can see what I do in the system?", "Every sign-in and every opened patient record is written to an access log that the hospital admin can review."],
];

export default function Landing({ signedIn }) {
  const navigate = useNavigate();
  const tr = useTr();
  const [info, setInfo] = useState(null);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => { api.get("/public/info").then(setInfo).catch(() => setInfo({})); }, []);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 10);
    window.addEventListener("scroll", on);
    return () => window.removeEventListener("scroll", on);
  }, []);
  const go = (id) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  const h = info?.headline;
  return (
    <div className="pub">
      <nav className={`pub-nav ${scrolled ? "solid" : ""}`}>
        <button className="pub-brand" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}>
          <span className="pub-logo"><Icon name="logo" size={20} /></span>
          <span><b className="grad">Precision</b> Prevention</span>
        </button>
        <div className="pub-links">
          {[["how", "How it works"], ["agents", "What it does"], ["roles", "Who it's for"], ["results", "Results"], ["faq", "FAQ"]].map(([id, label]) => (
            <button key={id} onClick={() => go(id)}>{tr(label)}</button>
          ))}
        </div>
        <div className="row">
          <LangSwitch />
          {signedIn ? <button className="pub-btn primary sm" onClick={() => navigate("/")}>{tr("Open my dashboard")}</button> : (
            <>
              <button className="pub-btn ghost sm" onClick={() => navigate("/signin")}>{tr("Sign in")}</button>
              <button className="pub-btn primary sm" onClick={() => navigate("/signup/patient")}>{tr("Sign up")}</button>
            </>
          )}
        </div>
      </nav>

      <header className="pub-hero">
        <div className="orb o1" /><div className="orb o2" /><div className="grid-bg" />
        <div className="pub-hero-grid">
        <div className="pub-hero-inner">
          <span className="pub-tag"><Icon name="sparkles" size={15} /> {tr("Future Health Challenge · Abu Dhabi")}</span>
          <h1>{tr("Find who needs care first.")}<br /><span className="grad">{tr("Reach every family in their language.")}</span></h1>
          <p>{tr("Precision Prevention and Screening finds missed check-ups, vaccines and screenings across a whole population, ranks who needs care most, and invites each family by the channel and language that works for them, with a doctor deciding every clinical step. It is a priority list for outreach, not a diagnosis.")}</p>
          <div className="row center">
            <button className="pub-btn primary lg" onClick={() => navigate(signedIn ? "/" : "/signup/patient")}>{tr("Get started")} <Icon name="arrowRight" size={18} /></button>
            <button className="pub-btn ghost lg" onClick={() => go("how")}>{tr("How it works")}</button>
          </div>
          <div className="row center pub-badges">
            {[["stethoscope", "The doctor decides"], ["shield", "Consent first"], ["scale", "Fair reach checked weekly"], ["lock", "Every record opening logged"]].map(([i, t]) => (
              <span key={t}><Icon name={i} size={14} /> {tr(t)}</span>
            ))}
          </div>
        </div>
        {/* sign-up and sign-in sit beside the headline, so nobody has to scroll to find them */}
        <aside className="hero-card">
          {signedIn ? (
            <>
              <h2>{tr("Welcome back")}</h2>
              <button className="pub-btn primary full" onClick={() => navigate("/")}>{tr("Open my dashboard")} <Icon name="arrowRight" size={16} /></button>
            </>
          ) : (
            <>
              <h2>{tr("Create your account")}</h2>
              <p className="muted small">{tr("Pick your role. One click opens your sign-up form.")}</p>
              <div className="hero-roles">
                {ROLE_ORDER.map((r) => {
                  const x = ROLE_INFO[r];
                  return (
                    <Link className="hero-role" key={r} to={`/signup/${r}`} style={{ "--rc": x.color }}>
                      <span className="pub-ico small" style={{ color: x.color, background: `${x.color}18` }}><Icon name={x.icon} size={20} /></span>
                      <span><b>{tr(x.title)}</b><small>{tr(x.who)}</small></span>
                      <Icon name="arrowRight" size={16} />
                    </Link>
                  );
                })}
              </div>
              <button className="pub-btn ghost full" onClick={() => navigate("/signin")}>{tr("Already have an account?")} {tr("Sign in")}</button>
              <p className="center" style={{ marginTop: 10 }}><Link className="more-link" to="/roles">{tr("What will each role see? Compare them")}</Link></p>
            </>
          )}
        </aside>
        </div>
      </header>

      <section id="how" className="pub-sec">
        <SectionHead tag={tr("4 steps")} title={tr("How it works")} sub={tr("From records to a booked visit, with people in charge at every step.")} />
        <div className="pub-grid g4">
          {STEPS.map((s, i) => (
            <div className={`pub-card ${i === 0 ? "accent" : ""}`} key={s.title}>
              <div className="row between"><span className="pub-ico"><Icon name={s.icon} size={28} /></span><span className="pub-step grad">{String(i + 1).padStart(2, "0")}</span></div>
              <h3>{tr(s.title)}</h3><p>{tr(s.text)}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="agents" className="pub-sec alt">
        {/* the heading is the first cell of the grid, so seven cards and the heading fill two full rows of four */}
        <div className="pub-grid agents">
          <SectionHead tag={tr("Seven agents")} title={tr("What it does every day")} sub={tr("Seven cooperating agents run in order each day; each one's work is explained and can be checked.")} />
          {AGENTS.map((a) => (
            <div className="pub-card" key={a.name}>
              <span className="pub-ico small"><Icon name={a.icon} size={22} /></span>
              <h3>{tr(a.name)}</h3><p>{tr(a.text)}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="roles" className="pub-sec">
        <SectionHead tag={tr("One platform, five roles")} title={tr("Who it's for")} sub={tr("Each person sees only what their role needs. Pick your role to sign up.")} />
        <div className="pub-grid g5">
          {ROLE_ORDER.map((r) => {
            const x = ROLE_INFO[r];
            return (
              <div className="pub-card role" key={r} style={{ "--rc": x.color }}>
                <span className="pub-ico" style={{ color: x.color, background: `${x.color}18` }}><Icon name={x.icon} size={26} /></span>
                <h3>{tr(x.title)}</h3>
                <p className="muted small">{tr(x.who)}</p>
                <p>{tr(x.does)}</p>
                <p className="small"><b>{tr("Sees:")}</b> {tr(x.sees)}. <Link className="more-link" to={`/roles/${r}`}>{tr("Full details")}</Link></p>
                <Link className="pub-btn role-btn" to={`/signup/${r}`} style={{ background: x.color }}>{tr("Sign up as {role}", { role: tr(x.title).toLowerCase() })} <Icon name="arrowRight" size={16} /></Link>
              </div>
            );
          })}
        </div>
        <p className="center small muted" style={{ marginTop: 18 }}>{tr("Already have an account?")} <Link to="/signin">{tr("Sign in")}</Link> · <Link to="/roles">{tr("Who sees what")}</Link></p>
      </section>

      <section id="results" className="pub-sec alt">
        <SectionHead tag={tr("Comparison")} title={tr("Results against usual care")}
          sub={tr("Same people and same daily capacity both ways; behaviour set from published UAE and international studies.")} />
        {h ? (
          <div className="pub-grid g4">
            <Stat label={tr("Hidden cases found")} a={h.found_new} b={tr("usual care: {v}", { v: h.found_old })} />
            <Stat label={tr("Results followed up within 30 days")} a={pct(h.followup_new)} b={tr("usual care: {v}", { v: pct(h.followup_old) })} />
            <Stat label={tr("Children with completed check-ups")} a={pct(h.well_child_new)} b={tr("usual care: {v}", { v: pct(h.well_child_old) })} />
            <Stat label={tr("Adults with completed screening")} a={pct(h.adult_new)} b={tr("usual care: {v}", { v: pct(h.adult_old) })} />
          </div>
        ) : <p className="center muted">{tr("Results appear here once the evaluation has run.")}</p>}
        {h && <div className="center"><SourceNote source={h.source} /></div>}
      </section>

      {/* the questions on one side and the safety note beside them, so neither leaves half the screen empty */}
      <section id="faq" className="pub-sec">
        <SectionHead tag={tr("Questions")} title={tr("Frequently asked")} />
        <div className="faq-grid">
          <div className="pub-faq">
            {FAQ.map(([q, a]) => <details key={q}><summary>{tr(q)}</summary><p>{tr(a)}</p></details>)}
          </div>
          <div className="pub-note">
            <Icon name="shield" size={22} />
            <div>
              <h4>{tr("Safety and privacy")}</h4>
              <p>{tr("Priority list for outreach, not a diagnosis; the doctor decides. Messages need consent and stop with one reply. Genetic findings are used only with separate consent and never sent to AI. Every sign-in and opened record is logged. Emergency? Call {n}.", { n: info?.emergency || "998" })}</p>
            </div>
          </div>
        </div>
      </section>

      <footer className="pub-foot">
        <span className="pub-brand static"><span className="pub-logo"><Icon name="logo" size={18} /></span> Precision Prevention and Screening</span>
        <span className="small muted">{tr("Future Health Challenge, Abu Dhabi · prototype")} · <Link to="/about">{tr("How this meets each requirement")}</Link></span>
      </footer>
    </div>
  );
}

function SectionHead({ tag, title, sub }) {
  return (
    <div className="pub-head">
      <div>
        <span className="pub-tag">{tag}</span>
        <h2>{title}</h2>
      </div>
      {sub && <p>{sub}</p>}
    </div>
  );
}

function Stat({ label, a, b }) {
  return (
    <div className="pub-card stat">
      <div className="pub-big grad">{a ?? "-"}</div>
      <div className="small muted">{b}</div>
      <p>{label}</p>
    </div>
  );
}
