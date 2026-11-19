// Audit: who opened which record and why. For the hospital admin, and to show a reviewer how access is controlled. Counts, the latest
// rows, break-glass openings with their reasons, family sharing, and a download of the log. It holds record numbers, never names or notes.
import React from "react";
import { useApi } from "../api.js";
import { ErrorBox, Kpi, Loading } from "../components.jsx";
import { useT, useTr } from "../i18n.js";

const when = (s) => (s || "").replace("T", " ").slice(0, 16);

export default function Audit() {
  const tr = useTr();
  const { t } = useT();
  const role = (r) => t.roles[r] || r;
  const { data, error, loading } = useApi("/admin/audit");
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorBox error={error} />;
  const k = data.kpis;
  const max = Math.max(1, ...data.by_day.map((d) => d.opened));
  return (
    <>
      <div className="grid g4">
        <Kpi label={tr("Records opened, 24 hours")} value={k.opened_24h.toLocaleString()} delta={tr("{n} in 7 days by {u} people", { n: k.opened_7d.toLocaleString(), u: k.people_opening_7d })} />
        <Kpi label={tr("Break-glass openings, 30 days")} value={k.break_glass_30d} delta={tr("each with the doctor's reason")} />
        <Kpi label={tr("Records shared with another hospital now")} value={k.shares_active} delta={tr("by the family, can be withdrawn")} />
        <Kpi label={tr("Failed sign-ins, 7 days")} value={k.failed_sign_ins_7d} negative={k.failed_sign_ins_7d > 20} delta={tr("{n} requests refused by role", { n: k.refused_7d })} />
      </div>

      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card">
          <h3>{tr("Records opened per day")}</h3>
          <div className="audit-bars" role="img" aria-label={tr("Records opened per day, last 7 days")}>
            {data.by_day.map((d) => (
              <div key={d.day} className="audit-col" title={`${d.day}: ${d.opened}`}>
                <span className="audit-n">{d.opened}</span>
                <span className="audit-bar" style={{ height: `${Math.max(3, Math.round((100 * d.opened) / max))}%` }} />
                <span className="small muted" dir="ltr">{d.day.slice(5)}</span>
              </div>
            ))}
          </div>
          <h4>{tr("Who opened the most, 7 days")}</h4>
          <div className="table-wrap"><table className="small"><tbody>
            {data.top_openers.map((x) => <tr key={x.user + x.role}><td>{x.user}</td><td>{role(x.role)}</td><td>{x.opened.toLocaleString()}</td></tr>)}
          </tbody></table></div>
        </div>
        <div className="card">
          <h3>{tr("What the log holds")}</h3>
          <ul className="small">{data.holds.map((x) => <li key={x}>{tr(x)}</li>)}</ul>
          <h4>{tr("What it never holds")}</h4>
          <ul className="small">{data.never_holds.map((x) => <li key={x}>{tr(x)}</li>)}</ul>
          <div className={data.integrity.ok ? "ok small" : "error small"} style={{ margin: "8px 0" }}>
            {data.integrity.ok
              ? tr("Log checked: {n} lines chained, none altered, removed or added. Head {h}.", { n: data.integrity.chained.toLocaleString(), h: data.integrity.head })
              : tr("The log does not match its chain from line {n} on: it was changed outside the app.", { n: data.integrity.first_bad_line })}
            {data.integrity.ok && data.integrity.before_chain > 0 && ` ${tr("{n} older lines were written before the chain existed.", { n: data.integrity.before_chain.toLocaleString() })}`}
          </div>
          <div className={data.integrity.key === "environment" ? "small" : "warn small"} style={{ margin: "6px 0" }}>
            {data.integrity.key === "environment"
              ? tr("Key: set by the PP_AUDIT_KEY environment variable.")
              : tr("Key: stored in a file next to the log on this computer. Set PP_AUDIT_KEY (kept off this computer) before a real deployment.")}
          </div>
          <p className="small muted">{tr(data.integrity_note)} {tr("Write the head down elsewhere with: python scripts/audit_head.py --record FILE")}</p>
          <a className="btn primary" href="/api/admin/audit/export?days=30" download>{tr("Download the log (30 days, CSV)")}</a>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>{tr("Break-glass openings")}</h3>
        <p className="small muted">{tr("A doctor opened a patient outside their own patients for an emergency and gave a reason. The opening lasts a few hours.")}</p>
        {data.break_glass.length === 0 ? <p className="small muted">{tr("None.")}</p> : (
          <div className="table-wrap"><table className="small">
            <thead><tr><th>{tr("When")}</th><th>{tr("Doctor")}</th><th>{tr("Record")}</th><th>{tr("Reason")}</th><th>{tr("Until")}</th></tr></thead>
            <tbody>{data.break_glass.map((g, i) => (
              <tr key={i}><td dir="ltr">{when(g.time)}</td><td>{g.doctor}</td><td>{g.mrn}</td><td dir="auto">{g.reason}</td>
                <td dir="ltr">{when(g.expires)}{g.active && <> <span className="chip warn-chip">{tr("open")}</span></>}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>{tr("Family sharing with another hospital")}</h3>
        {data.shares.length === 0 ? <p className="small muted">{tr("None.")}</p> : (
          <div className="table-wrap"><table className="small">
            <thead><tr><th>{tr("When")}</th><th>{tr("Shared by")}</th><th>{tr("Record")}</th><th>{tr("With")}</th><th>{tr("Until")}</th></tr></thead>
            <tbody>{data.shares.map((g, i) => (
              <tr key={i}><td dir="ltr">{when(g.time)}</td><td>{g.by}</td><td>{g.mrn}</td><td>{tr(g.hospital)}</td>
                <td dir="ltr">{when(g.expires)} {g.revoked ? <span className="chip">{tr("withdrawn")}</span> : g.active ? <span className="chip tag-teal">{tr("active")}</span> : null}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </div>

      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card">
          <h3>{tr("Latest records opened")}</h3>
          <div className="table-wrap"><table className="small">
            <thead><tr><th>{tr("When")}</th><th>{tr("Who")}</th><th>{tr("Opened")}</th></tr></thead>
            <tbody>{data.recent_opened.map((r, i) => (
              <tr key={i}><td dir="ltr">{when(r.time)}</td><td>{r.user}<div className="muted">{role(r.role)}</div></td><td>{tr(r.what)}<div className="muted">{r.record}</div></td></tr>
            ))}</tbody>
          </table></div>
        </div>
        <div className="card">
          <h3>{tr("Account and access decisions")}</h3>
          <div className="table-wrap"><table className="small">
            <thead><tr><th>{tr("When")}</th><th>{tr("Who")}</th><th>{tr("What")}</th></tr></thead>
            <tbody>{data.changes.map((r, i) => (
              <tr key={i}><td dir="ltr">{when(r.time)}</td><td>{r.user}</td><td>{tr(r.event)}<div className="muted">{r.detail}</div></td></tr>
            ))}</tbody>
          </table></div>
        </div>
      </div>
      <p className="small muted" style={{ marginTop: 10 }}>{tr(data.scope)} · {tr("Updated {t}.", { t: when(data.time) })}</p>
    </>
  );
}
