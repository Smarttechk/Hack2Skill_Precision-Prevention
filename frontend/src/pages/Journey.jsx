import React, { useEffect, useState } from "react";
import { useApi } from "../api.js";
import { Badge, ErrorBox, Loading } from "../components.jsx";
import { useTr } from "../i18n.js";
import CareAssistant from "../CareAssistant.jsx";

const LABEL = {
  flagged: "Flagged", message: "Message sent", follow_up: "Follow-up", booked: "Booked", attended: "Visit completed",
  missed_visit: "Missed visit", rebook: "Rebooked", abnormal_result: "Abnormal result found", diagnosed: "Confirmed at results review",
  doctor_agree: "Doctor agreed", doctor_disagree: "Doctor disagreed", on_hold: "On hold", opt_out: "Opted out", opt_in: "Opted in",
  later: "Asked to wait", info: "Asked for information", emergency: "Emergency words", mobile_clinic_planned: "Mobile clinic planned",
};

export default function Journey() {
  const tr = useTr();
  const list = useApi("/journeys?limit=300");
  const [pid, setPid] = useState("");
  useEffect(() => {
    if (list.data?.length && !pid) setPid((list.data.find((x) => x.diagnosed) || list.data[0]).id);
  }, [list.data]); // eslint-disable-line
  if (list.loading) return <Loading />;
  if (list.error) return <ErrorBox error={list.error} />;
  return (
    <>
      <div className="card row">
        <span className="small muted">{tr("Person:")}</span>
        <select value={pid} onChange={(e) => setPid(e.target.value)} style={{ flex: "1 1 240px", minWidth: 0, maxWidth: 980 }}>
          {list.data.map((p) => <option key={p.id} value={p.id}>{tr(p.label)}{p.diagnosed ? ` (${tr("condition found")})` : ""}</option>)}
        </select>
      </div>
      {pid && <Timeline pid={pid} />}
      {pid && <CareAssistant key={pid} pid={pid} />}
    </>
  );
}

function Timeline({ pid }) {
  const tr = useTr();
  const { data, error, loading } = useApi(`/journey/${pid}`);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const p = data.person;
  return (
    <div className="split">
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3>{tr(p.is_child ? "Child" : "Adult")}, {tr("{n} years", { n: p.age })}, {tr(p.sex)}</h3><Badge urgency={p.urgency} />
        </div>
        <p className="muted small">{tr(p.nationality)}, {tr("speaks {l}", { l: tr(p.language) })}, {tr(p.region)}, {tr("{n} min to {f}", { n: Math.round(data.travel_min), f: data.nearest_facility })}</p>
        <ul className="timeline">
          {data.events.map((e, i) => (
            <li key={i} className={`ev-${e.event}`}>
              <div className="when">{tr("Day {d}", { d: e.day })} &middot; {e.date}</div>
              <b>{tr(LABEL[e.event] || e.event.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()))}</b>
              {e.detail && <div className="small muted">{tr(e.detail)}</div>}
            </li>
          ))}
        </ul>
      </div>
      <div className="card">
        <h3>{tr("Messages and replies")}</h3>
        {data.messages.length === 0 && <p className="muted">{tr("No messages.")}</p>}
        {data.messages.map((m, i) => (
          <div key={i} className="info small">
            <div className="muted">{tr("Day {d}", { d: m.day })} &middot; {tr(m.channel)}{m.language ? ` · ${tr(m.language)}` : ""}{m.window ? ` · ${m.window}` : ""}</div>
            <div dir="auto">{m.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
