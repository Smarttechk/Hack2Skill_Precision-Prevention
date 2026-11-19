import React, { useEffect, useState } from "react";
import { useApi } from "../api.js";
import { ErrorBox, Loading } from "../components.jsx";
import ContactChoices from "../ContactChoices.jsx";
import { nameOf } from "../careInfo.js";
import { useTr } from "../i18n.js";

export default function Why() {
  const tr = useTr();
  const people = useApi("/why/people");      // a patient gets only themselves and their children
  const [pid, setPid] = useState("");
  useEffect(() => { if (people.data?.length && !pid) setPid(people.data[0].id); }, [people.data]); // eslint-disable-line
  if (people.loading) return <Loading />;
  if (people.error) return <ErrorBox error={people.error} />;
  return (
    <>
      <div className="card row">
        <span className="small muted">{tr("Showing the page for:")}</span>
        <select value={pid} onChange={(e) => setPid(e.target.value)} style={{ flex: "1 1 240px", minWidth: 0, maxWidth: 980 }}>
          {people.data.map((p) => <option key={p.id} value={p.id}>{p.age !== undefined ? nameOf(p, tr) : tr(p.label)}</option>)}
        </select>
      </div>
      {pid && <WhyPage pid={pid} />}
    </>
  );
}

function WhyPage({ pid }) {
  const tr = useTr();
  const { data, error, loading } = useApi(`/why/${pid}`);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const child = data.is_child;
  return (
    <div className="patient-page">
      <div className="card">
        <h2>{tr("Why was I contacted?")}</h2>
        {data.checks.length > 0 ? (
          <>
            <p>{tr(child ? "We contacted you about your child because these routine checks are due for your child:" : "We contacted you because these routine checks are due for you:")}</p>
            <ul>{[...new Set(data.checks)].map((c, i) => <li key={i} style={{ textTransform: "capitalize" }}>{tr(c)}</li>)}</ul>
          </>
        ) : <p>{tr(child ? "No check is due for your child right now, so we are not contacting you about your child." : "No check is due for you right now, so we are not contacting you.")}</p>}
        {data.noticed.length > 0 && <p><b>{tr("Things we noticed in the records:")}</b> {data.noticed.map((s) => tr(s)).join(tr("; "))}.</p>}
        <p><b>{tr("Information we used:")}</b> {data.data_used.map((s) => tr(s)).join(tr(", "))}{data.genetic_used ? `. ${tr("With your permission, also genetic information.")}` : "."}</p>
        <div className="info">{tr("This is an invitation for a routine check, not a diagnosis. A doctor or nurse will explain any result with you.")}</div>
      </div>
      <div className="card">
        <h3>{tr("Your choices")}</h3>
        <ContactChoices pid={pid} child={child} consent={data.consent} />
      </div>
    </div>
  );
}
