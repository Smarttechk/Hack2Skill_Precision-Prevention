// "Who sees what": one public page with the detail of each role (what it can do, which pages it opens, what it can
// never open, how the account is checked) and a table comparing all five. The pages per role come from the server
// (/api/roles, config/roles.json), the same list the server enforces, so this page cannot drift from the real access.
import React, { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import Icon from "../icons.jsx";
import { useT, useTr } from "../i18n.js";
import { LangSwitch } from "../components.jsx";
import { PAGE_INFO, ROLE_DETAILS, ROLE_INFO, ROLE_ORDER } from "../roleInfo.js";

export default function Roles({ signedIn }) {
  const { role = "patient" } = useParams();
  const navigate = useNavigate();
  const tr = useTr();
  const { t } = useT();
  const [pages, setPages] = useState(null);
  useEffect(() => { api.get("/roles").then((r) => setPages(r.roles)).catch(() => setPages({})); }, []);
  useEffect(() => { window.scrollTo(0, 0); }, []);
  const r = ROLE_INFO[role];
  const d = ROLE_DETAILS[role];
  if (!r) return <div className="pub auth-page"><div className="auth-card">Unknown role. <Link to="/roles">{tr("Who sees what")}</Link></div></div>;
  const mine = pages?.[role] || [];
  const all = pages ? [...new Set(ROLE_ORDER.flatMap((k) => pages[k] || []))] : [];
  const name = (p) => t.pages[p] || p;
  return (
    <div className="pub roles-page">
      <div className="roles-wrap">
        <div className="signup-top">
          <div>
            <h1>{tr("Who sees what")}</h1>
            <p className="muted">{tr("Each role opens only the pages and people it needs. The server enforces this, not the menu.")}</p>
          </div>
          <div className="row">
            <button className="back-home" onClick={() => navigate("/welcome")}><Icon name="arrowLeft" size={16} /> {tr("Back to home")}</button>
            <LangSwitch />
          </div>
        </div>
        <div className="role-tabs">
          {ROLE_ORDER.map((k) => (
            <button key={k} type="button" className={k === role ? "on" : ""} onClick={() => navigate(`/roles/${k}`, { replace: true })}>{tr(ROLE_INFO[k].title)}</button>
          ))}
        </div>

        <div className="role-detail" style={{ "--rc": r.color }}>
          <div className="role-detail-head">
            <div className="signup-head">
              <span className="pub-ico" style={{ color: "#fff", background: r.color, border: "none" }}><Icon name={r.icon} size={24} /></span>
              <div><h2>{tr(r.title)}</h2><p className="muted">{tr(r.who)}</p></div>
            </div>
            {!signedIn && (
              <div className="row">
                <Link className="pub-btn sm" to={`/signup/${role}`} style={{ background: r.color, borderColor: r.color, color: "#fff" }}>
                  {tr("Sign up as {role}", { role: tr(r.title).toLowerCase() })} <Icon name="arrowRight" size={16} />
                </Link>
                <Link className="pub-btn ghost sm" to="/signin">{tr("Sign in")}</Link>
              </div>
            )}
          </div>
          <div className="role-cols">
            <section>
              <h3><Icon name="check" size={16} /> {tr("What you can do")}</h3>
              <ul className="ticks">{d.can.map((x) => <li key={x}>{tr(x)}</li>)}</ul>
            </section>
            <section>
              <h3><Icon name="eye" size={16} /> {tr("Pages you can open")}</h3>
              {!pages ? <p className="muted small">Loading…</p> : (
                <ul className={`page-list ${mine.length > 5 ? "many" : ""}`}>{mine.map((p) => <li key={p}><b>{name(p)}</b><span>{tr(PAGE_INFO[p] || "")}</span></li>)}</ul>
              )}
            </section>
            <section>
              <h3><Icon name="users" size={16} /> {tr("Whose records")}</h3>
              <p>{tr(r.sees)}</p>
              <h3><Icon name="lock" size={16} /> {tr("What you cannot open")}</h3>
              <ul className="locks">{d.cannot.map((x) => <li key={x}>{tr(x)}</li>)}</ul>
              <h3><Icon name="shield" size={16} /> {tr("How your account is checked")}</h3>
              <p>{tr(r.approval)}</p>
            </section>
          </div>
        </div>

        {all.length > 0 && (
          <div className="role-matrix">
            <h3>{tr("All roles side by side")}</h3>
            <div className="matrix-scroll">
              <table>
                <thead>
                  <tr><th>{tr("Page")}</th>{ROLE_ORDER.map((k) => <th key={k} className={k === role ? "on" : ""}>{tr(ROLE_INFO[k].title)}</th>)}</tr>
                </thead>
                <tbody>
                  {all.map((p) => (
                    <tr key={p}>
                      <td>{name(p)}</td>
                      {ROLE_ORDER.map((k) => (
                        <td key={k} className={k === role ? "on" : ""}>
                          {(pages[k] || []).includes(p) ? <span className="yes" style={{ color: ROLE_INFO[k].color }} aria-label={tr("Yes")}><Icon name="check" size={16} /></span> : <span className="no" aria-label={tr("No")}>–</span>}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <div className="pub-info"><Icon name="shield" size={16} /><span>{tr("For every role: every sign-in and every opened patient record is written to an access log that the hospital admin can review. Genetic findings are used only with separate consent.")}</span></div>
      </div>
    </div>
  );
}
