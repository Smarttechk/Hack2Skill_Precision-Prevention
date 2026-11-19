// "How this meets each requirement": the five things the challenge asks for, what the prototype does for each, and the
// page that shows it. Public, so a judge can read it before signing in; the pages it names need the right role.
import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, pct } from "../api.js";
import Icon from "../icons.jsx";
import { useT, useTr } from "../i18n.js";
import { LangSwitch } from "../components.jsx";

const REQUIREMENTS = [
  { title: "Transparent risk and care-gap identification",
    text: "Every record is checked against guidelines for missed Well-Child visits, vaccines, overdue screenings and chronic-illness tests. Two risk models (undiagnosed diabetes and high blood pressure) add a chance with its range and top reasons. Family history, genetic markers (only with consent), developmental and behavioural concerns, visit history and distance to a clinic are all used, and each flag shows its reasons and the data used.",
    pages: ["doctor", "journey", "queue"] },
  { title: "Personalized preventive pathways",
    text: "Each gap becomes a pathway card with an urgency, a contact deadline, recall timing and follow-up, within approved guidelines. A card is a priority for outreach, not a diagnosis: the doctor decides every clinical step.",
    pages: ["doctor", "queue", "journey"] },
  { title: "Culturally appropriate engagement",
    text: "Each message goes to the right person (a guardian for a child), in their language, by the channel and at the time that work for them. Families can stop messages with one reply and choose whether genetic results may be used. The whole app works in English and Arabic.",
    pages: ["queue", "why", "mycare", "phone"] },
  { title: "Local population safeguards",
    text: "Reach, uptake, false positives, false negatives and flag rates are shown by nationality, region, language, insurance, sex and age group, with 95% ranges and real gaps flagged. Groups under 10 people are hidden and groups under 30 are marked not reliable. Places with few residents are tagged the same way.",
    pages: ["results", "map", "communities"] },
  { title: "Outcome-based evaluation",
    text: "A comparison run compares this system with usual care on the same people and the same staff capacity: completed Well-Child visits and adult screenings, timely attendance, follow-up within 30 days, calibration by group, hidden cases found and cost. 64 harder settings are tested, and the source of every number is listed.",
    pages: ["results", "home"] },
];

export default function About() {
  const navigate = useNavigate();
  const tr = useTr();
  const { t } = useT();
  const [info, setInfo] = useState(null);
  useEffect(() => { api.get("/public/info").then(setInfo).catch(() => setInfo({})); window.scrollTo(0, 0); }, []);
  const h = info?.headline;
  const name = (p) => t.pages[p] || p;
  return (
    <div className="pub roles-page">
      <div className="roles-wrap">
        <div className="signup-top">
          <div>
            <h1>{tr("How this meets each requirement")}</h1>
            <p className="muted">{tr("The challenge asks for five things. Each row says what the prototype does and which page shows it.")}</p>
          </div>
          <div className="row">
            <button className="back-home" onClick={() => navigate("/welcome")}><Icon name="arrowLeft" size={16} /> {tr("Back to home")}</button>
            <LangSwitch />
          </div>
        </div>

        <ol className="req-list">
          {REQUIREMENTS.map((r, i) => (
            <li className="req-row" key={r.title}>
              <span className="req-n">{i + 1}</span>
              <div>
                <h3>{tr(r.title)}</h3>
                <p>{tr(r.text)}</p>
                <div className="req-pages">
                  <span className="small muted">{tr("Shown on:")}</span>
                  {r.pages.map((p) => <Link key={p} to={`/${p}`} className="req-chip">{name(p)}{p === "phone" ? ` (${tr("demo")})` : ""}</Link>)}
                </div>
                {i === 4 && h && (
                  <div className="req-evidence">
                    {[["Children with completed check-ups", h.well_child_new, h.well_child_old], ["Adults with completed screening", h.adult_new, h.adult_old],
                      ["Results followed up within 30 days", h.followup_new, h.followup_old]].map(([label, a, b]) => (
                      <span key={label}><b>{pct(a)}</b> {tr(label)} <small className="muted">{tr("usual care: {v}", { v: pct(b) })}</small></span>
                    ))}
                    <span><b>{h.found_new}</b> {tr("Hidden cases found")} <small className="muted">{tr("usual care: {v}", { v: h.found_old })}</small></span>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>

        <div className="pub-info">
          <Icon name="shield" size={16} />
          <span>{tr("Keep in mind: every patient in this prototype is generated, not real. The risk score is a published questionnaire (nothing is trained here) and is checked on real survey data; on the generated people its power is modest, so most hidden cases are found through care gaps, warning signs and family pathways. A clinician should review every translation and clinical rule before real use.")}</span>
        </div>
        <p className="small muted center" style={{ marginTop: 12 }}>
          {tr("Some pages need a sign-in with the right role.")} <Link to="/roles">{tr("Who sees what")}</Link>
        </p>
      </div>
    </div>
  );
}
