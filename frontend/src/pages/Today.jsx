// The hospital admin's "Today": everything that needs a decision or a follow-up, in one place, most urgent first. Counts open the page where
// each is dealt with. A hospital admin sees their own hospital; the central admin sees all.
import React, { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api.js";
import { Badge, ErrorBox, Kpi, Loading } from "../components.jsx";
import { useTr } from "../i18n.js";

const LABEL = {
  emergencies: "Emergency call-backs open", urgent_no_doctor: "Urgent people with no doctor", break_glass_open: "Break-glass openings open now",
  escalated: "People who could not be reached", visits_to_confirm: "Booked visits to confirm", missed_visits: "Missed visits to rebook",
  signup_requests: "Sign-up requests to decide", family_requests: "Family requests to check", awaiting_records: "New patients waiting for a record",
  urgent_waiting: "Urgent people waiting for contact", panels_full: "Doctors with nearly full patient lists", locked_accounts: "Locked accounts",
  audit_log_altered: "Audit log does not match its chain",
};
const HINT = {
  emergencies: "A nurse calls back, outside the daily list", urgent_no_doctor: "Assign a doctor in Care teams", break_glass_open: "A doctor opened a record outside their own patients: read the reason",
  escalated: "Three tries without an answer, or a wrong number: another way is needed", visits_to_confirm: "Staff record attended or missed",
  missed_visits: "Back on the contact list to book another time", signup_requests: "Check the details before approving", family_requests: "Check against the child's record",
  awaiting_records: "Link the record once the clinic has registered them", urgent_waiting: "On the outreach list now", panels_full: "Raise a limit or add a doctor",
  locked_accounts: "Too many wrong passwords: unlock after checking",
  audit_log_altered: "A line of the access log was changed, removed or added outside the app: open the Audit page",
};

export default function Today() {
  const tr = useTr();
  const { data, error, loading, reload } = useApi("/admin/today");
  const ref = useRef(reload);
  ref.current = reload;
  useEffect(() => { const id = setInterval(() => ref.current(), 60000); return () => clearInterval(id); }, []);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorBox error={error} />;
  const todo = data.work.filter((w) => w.count > 0);
  const clear = data.work.filter((w) => w.count === 0);
  const s = data.security;
  return (
    <>
      <div className="grid g4">
        <Kpi label={tr("Needs action now")} value={data.needs_action.toLocaleString()} negative={data.needs_action > 0} delta={tr(data.scope)} />
        <Kpi label={tr("Visits to confirm")} value={data.visits.to_confirm.toLocaleString()} delta={data.visits.attendance_pct == null ? tr("no visit recorded yet") : tr("attendance {p}%", { p: data.visits.attendance_pct })} />
        <Kpi label={tr("Failed sign-ins, 24 hours")} value={s.failed_sign_ins_24h} negative={s.failed_sign_ins_24h > 10} />
        <Kpi label={tr("Locked accounts")} value={s.locked_accounts.length} negative={s.locked_accounts.length > 0} />
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h3>{tr("Needs attention today")}</h3>
        {todo.length === 0 ? <p className="small muted">{tr("Nothing is waiting: all clear.")}</p> : (
          <div className="grid g3">
            {todo.map((w) => (
              <Link key={w.key} to={`/${w.to}`} className="todo" style={{ textDecoration: "none", color: "inherit" }}>
                <span className="todo-n hot">{w.count.toLocaleString()}</span>
                <span>{tr(LABEL[w.key])} <span className="arrow">&rarr;</span><span className="small muted" style={{ display: "block" }}>{tr(HINT[w.key])}</span></span>
              </Link>
            ))}
          </div>
        )}
        {clear.length > 0 && <p className="small muted" style={{ marginTop: 10 }}>{tr("All clear:")} {clear.map((w) => tr(LABEL[w.key])).join(tr(", "))}.</p>}
      </div>

      {data.break_glass.length > 0 && (
        <div className="card warn-card" style={{ marginTop: 16 }}>
          <h3>{tr("Break-glass openings open now")} ({data.break_glass.length})</h3>
          <p className="small muted">{tr("A doctor opened a patient outside their own patients and gave a reason. It closes by itself; every opening is in the Audit page.")}</p>
          <div className="table-wrap"><table className="small">
            <thead><tr><th>{tr("Doctor")}</th><th>{tr("Record")}</th><th>{tr("Reason")}</th><th>{tr("Open until")}</th></tr></thead>
            <tbody>{data.break_glass.map((g, i) => (
              <tr key={i}><td>{g.doctor}</td><td>{g.mrn}</td><td dir="auto">{g.reason}</td>
                <td dir="ltr">{g.expires.replace("T", " ").slice(0, 16)}{g.ending_soon && <> <span className="chip warn-chip">{tr("ending soon")}</span></>}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
      )}

      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card">
          <h3>{tr("Urgent people with no doctor")}</h3>
          {data.urgent_no_doctor.length === 0 ? <p className="small muted">{tr("Everyone urgent has a doctor.")}</p> : (
            <>
              <div className="table-wrap"><table className="small"><tbody>
                {data.urgent_no_doctor.map((x) => (
                  <tr key={x.mrn}><td><Badge urgency="Urgent" /></td><td>{x.mrn}</td><td>{x.age} y, {tr(x.language)}</td><td>{tr(x.hospital)}</td></tr>
                ))}
              </tbody></table></div>
              <p className="small"><Link to="/careteam">{tr("Match patients to doctors")} &rarr;</Link></p>
            </>
          )}
        </div>
        <div className="card">
          <h3>{tr("People who could not be reached")}</h3>
          {data.escalated.length === 0 ? <p className="small muted">{tr("Nobody is escalated.")}</p> : (
            <div className="table-wrap"><table className="small"><tbody>
              {data.escalated.map((x) => (
                <tr key={x.mrn}><td>{x.mrn}</td><td>{x.age} y, {tr(x.language)}</td><td>{tr(x.why)}</td><td dir="ltr">{(x.time || "").replace("T", " ").slice(0, 16)}</td></tr>
              ))}
            </tbody></table></div>
          )}
        </div>
      </div>

      {(data.panels.length > 0 || data.on_leave.length > 0) && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>{tr("Doctors")}</h3>
          {data.panels.length > 0 && <p className="small">{tr("Patient lists nearly full:")} {data.panels.map((x) => `${x.name} (${x.panel}/${x.limit})`).join(tr(", "))}.</p>}
          {data.on_leave.length > 0 && <p className="small">{tr("On leave:")} {data.on_leave.join(tr(", "))}.</p>}
        </div>
      )}
      <p className="small muted" style={{ marginTop: 10 }}>{tr("Updated {t}. This page refreshes every minute.", { t: data.time.replace("T", " ").slice(0, 16) })}</p>
    </>
  );
}
