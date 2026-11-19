// Programme manager's "Operations" panel: how outreach and care teams are doing, as totals only. No person, record number or
// staff name is ever sent to this panel.
import React from "react";
import { useApi } from "../api.js";
import { ErrorBox, Kpi, Loading } from "../components.jsx";
import { useTr } from "../i18n.js";

const show = (v, unit = "") => (v == null ? "-" : `${v}${unit}`);

function Bar({ value, max }) {
  const w = max ? Math.max(2, Math.round((100 * value) / max)) : 0;
  return <span className="ops-bar" aria-hidden="true"><span style={{ width: `${value ? w : 0}%` }} /></span>;
}

export default function Operations() {
  const tr = useTr();
  const { data, error, loading } = useApi("/operations");
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorBox error={error} />;
  const { outreach: o, coverage: c, safeguards: s, policy } = data;
  const maxMix = Math.max(1, ...o.mix.map((m) => m.count));
  const maxCh = Math.max(1, ...o.channels.map((m) => m.attempts));
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>{tr("Operations")} <span className="small muted">({tr(data.scope)})</span></h3>
      <p className="small muted">{tr("Totals only: no patient, record number or staff name appears here.")}</p>

      <h4>{tr("Outreach")}</h4>
      <div className="grid g4">
        <Kpi label={tr("Contacts today")} value={o.today.toLocaleString()} delta={tr("{n} in the last 7 days", { n: o.this_week })} />
        <Kpi label={tr("People reached")} value={show(o.reached_pct, "%")} delta={tr("of {n} contact attempts", { n: o.contacts })} />
        <Kpi label={tr("No answer")} value={show(o.no_answer_pct, "%")} delta={tr("of attempts")} negative />
        <Kpi label={tr("Escalated")} value={o.escalated.toLocaleString()} delta={o.escalated_pct == null ? tr("none yet") : tr("{p}% of people contacted", { p: o.escalated_pct })} negative />
      </div>
      <div className="grid g2" style={{ marginTop: 12 }}>
        <div>
          <div className="small muted">{tr("What happened")}</div>
          <table className="small"><tbody>
            {o.mix.map((m) => (
              <tr key={m.outcome}><td>{tr(m.label)}</td><td style={{ width: 120 }}><Bar value={m.count} max={maxMix} /></td><td style={{ textAlign: "end" }}>{m.count}</td></tr>
            ))}
          </tbody></table>
        </div>
        <div>
          <div className="small muted">{tr("Which way reaches people")}</div>
          <table className="small"><tbody>
            {o.channels.map((m) => (
              <tr key={m.channel}><td>{tr(m.channel)}</td><td style={{ width: 120 }}><Bar value={m.attempts} max={maxCh} /></td>
                <td style={{ textAlign: "end" }}>{m.attempts ? tr("{r}% reached of {n}", { r: m.reached_pct, n: m.attempts }) : "-"}</td></tr>
            ))}
          </tbody></table>
        </div>
      </div>
      <p className="small muted" style={{ marginTop: 8 }}>
        {o.agreed_pct != null && <>{tr("Of people reached, {a}% agreed to book.", { a: o.agreed_pct })}{" "}</>}
        {o.hours_to_reach_median == null ? tr("Time to reach someone: no data yet.")
          : tr("Median time from the first attempt to reaching someone: {h} hours (over {n} people).", { h: o.hours_to_reach_median, n: o.reached_people })}{" "}
        {tr("Still waiting for a first contact: {n}.", { n: o.waiting_now.toLocaleString() })}
      </p>

      {data.visits && (
        <>
          <h4>{tr("Booked visits")}</h4>
          <div className="grid g4">
            <Kpi label={tr("Visits recorded by staff")} value={data.visits.recorded.toLocaleString()} />
            <Kpi label={tr("Attended")} value={data.visits.attended.toLocaleString()} />
            <Kpi label={tr("Missed")} value={data.visits.no_show.toLocaleString()} negative delta={tr("back on the contact list to rebook")} />
            <Kpi label={tr("Attendance")} value={show(data.visits.attendance_pct, "%")} delta={tr("{n} still to confirm", { n: data.visits.to_confirm.toLocaleString() })} />
          </div>
        </>
      )}

      {data.collection && (
        <>
          <h4>{tr("Data collection")}</h4>
          <div className="grid g4">
            <Kpi label={tr("Waist and lifestyle answers recorded")} value={data.collection.answers_recorded.toLocaleString()}
              delta={tr("{p}% of {n} adults with a diabetes score", { p: data.collection.answers_pct ?? 0, n: data.collection.scored_adults.toLocaleString() })} />
          </div>
          <p className="small muted">{tr("Each answer narrows that person's diabetes range, so this is the number to raise.")}</p>
        </>
      )}

      <h4>{tr("Care-team coverage")}</h4>
      <div className="grid g4">
        <Kpi label={tr("People with a doctor")} value={c.with_a_doctor.toLocaleString()} />
        <Kpi label={tr("Urgent, no doctor")} value={c.urgent_without_doctor.toLocaleString()} negative />
        <Kpi label={tr("Patient lists nearly full")} value={c.panels_near_full} delta={tr("of {n} doctors", { n: c.doctors })} />
        <Kpi label={tr("Left without the right specialty")} value={c.left_out_people.toLocaleString()} negative />
      </div>
      <div className="table-wrap" style={{ marginTop: 10 }}>
        <table className="small">
          <thead><tr><th>{tr("Hospital")}</th><th>{tr("People")}</th><th>{tr("With a doctor")}</th><th>{tr("Urgent without a doctor")}</th><th>{tr("Doctors")}</th></tr></thead>
          <tbody>{c.hospitals.map((h) => (
            <tr key={h.hospital}><td>{tr(h.hospital)}</td><td>{h.people.toLocaleString()}</td><td>{h.with_a_doctor.toLocaleString()} ({show(h.with_a_doctor_pct, "%")})</td>
              <td>{h.urgent_without_doctor}</td><td>{h.doctors}</td></tr>
          ))}</tbody>
        </table>
      </div>
      {c.left_out.length > 0 && (
        <p className="small muted" style={{ marginTop: 8 }}>
          {tr("Gaps")}: {c.left_out.map((x) => `${tr(x.hospital)}, ${tr(x.specialty)}: ${x.people} (${tr(x.why)})`).join(" · ")}
        </p>
      )}

      <h4>{tr("Safeguards")}</h4>
      <div className="grid g4">
        <Kpi label={tr("Break-glass openings, last 30 days")} value={s.break_glass_30d} delta={tr("{n} in total", { n: s.break_glass_total })} />
        <Kpi label={tr("Records shared by families now")} value={s.shares_active} />
        <Kpi label={tr("New shares, last 30 days")} value={s.shares_30d} />
        <Kpi label={tr("Shares withdrawn")} value={s.shares_revoked} />
      </div>

      <h4>{tr("Policy in force")}</h4>
      <div className="table-wrap">
        <table className="small"><tbody>
          {policy.map((x) => <tr key={x.name}><td>{tr(x.name)}</td><td><strong>{x.value}</strong> {tr(x.unit)}</td></tr>)}
        </tbody></table>
      </div>
      <p className="small muted">{tr("These settings are fixed in the configuration files and are listed here so they can be reviewed; changing them is a decision for the programme.")}</p>
    </div>
  );
}
