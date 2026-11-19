// Hospital admin: care teams. Which doctor looks after which patients (by hospital, specialty, language, panel size), who covers a
// doctor on leave, and who opened a record outside their own patients or was given one by a family. A hospital admin sees only their hospital.
import React, { useState } from "react";
import { api, useApi } from "../api.js";
import { Badge, ErrorBox, Loading } from "../components.jsx";
import { useTr } from "../i18n.js";

function Bar({ used, limit }) {
  const share = Math.min(1, used / Math.max(limit, 1));
  return (
    <div className="panel-bar" title={`${used} / ${limit}`}>
      <span style={{ width: `${share * 100}%`, background: share > 0.9 ? "var(--urgent)" : "var(--primary)" }} />
      <b>{used}</b><small> / {limit}</small>
    </div>
  );
}

function EditDoctor({ d, d2, onDone, onCancel }) {
  const tr = useTr();
  const [f, setF] = useState({ specialty: d.specialty, languages: d.languages, panel_limit: d.panel_limit, duty: d.duty, on_leave: d.on_leave, backup: d.backup || "" });
  const [err, setErr] = useState(null);
  const toggle = (key, v) => setF((x) => ({ ...x, [key]: x[key].includes(v) ? x[key].filter((y) => y !== v) : [...x[key], v] }));
  const save = async () => {
    setErr(null);
    try { await api.post("/admin/careteam/doctor", { user: d.user, ...f, panel_limit: Number(f.panel_limit) }); onDone(tr("Saved {u}.", { u: d.user })); }
    catch (e) { setErr(tr(String(e.message || e))); }
  };
  const colleagues = d2.doctors.filter((x) => x.hospital === d.hospital && x.user !== d.user);
  return (
    <tr><td colSpan={6}>
      <div className="edit-doctor">
        <div><b>{tr("Specialty")}</b>
          {Object.entries(d2.specialties).map(([k, text]) => (
            <label key={k} className="toggle" title={tr(text)}><input type="checkbox" checked={f.specialty.includes(k)} onChange={() => toggle("specialty", k)} /> {tr(k)}</label>
          ))}
        </div>
        <div><b>{tr("Languages spoken")}</b>
          {d2.languages.map((l) => <label key={l} className="toggle"><input type="checkbox" checked={f.languages.includes(l)} onChange={() => toggle("languages", l)} /> {tr(l)}</label>)}
        </div>
        <div className="row">
          <label className="small">{tr("Maximum patients")} <input type="number" min="1" max="5000" value={f.panel_limit} onChange={(e) => setF({ ...f, panel_limit: e.target.value })} style={{ width: 90 }} /></label>
          <label className="toggle"><input type="checkbox" checked={f.duty} onChange={(e) => setF({ ...f, duty: e.target.checked })} /> {tr("Duty doctor (sees urgent people nobody looks after)")}</label>
          <label className="toggle"><input type="checkbox" checked={f.on_leave} onChange={(e) => setF({ ...f, on_leave: e.target.checked })} /> {tr("On leave")}</label>
          <label className="small">{tr("Backup")}
            <select value={f.backup} onChange={(e) => setF({ ...f, backup: e.target.value })}>
              <option value="">{tr("None")}</option>{colleagues.map((x) => <option key={x.user} value={x.user}>{x.name}</option>)}
            </select>
          </label>
        </div>
        {err && <div className="small" style={{ color: "var(--urgent)" }}>{err}</div>}
        <div className="row"><button className="btn primary" onClick={save}>{tr("Save")}</button><button className="btn" onClick={onCancel}>{tr("Cancel")}</button></div>
      </div>
    </td></tr>
  );
}

function PanelView({ doctor, name, version }) {
  const tr = useTr();
  const { data, error, loading } = useApi(`/admin/careteam/panel?doctor=${encodeURIComponent(doctor)}&limit=12`, [version]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return (
    <div className="card" style={{ margin: 0 }}>
      <h4>{name} <span className="small muted">· {tr("{n} patients", { n: data.total })}</span></h4>
      {data.people.length === 0 ? <p className="small muted">{tr("Nobody is assigned yet.")}</p> : (
        <table className="small"><thead><tr><th>{tr("Record")}</th><th>{tr("Age")}</th><th>{tr("Language")}</th><th>{tr("Urgency")}</th><th>{tr("Specialty")}</th></tr></thead>
          <tbody>{data.people.map((x) => (
            <tr key={x.id}><td>{x.mrn}</td><td>{x.age}</td><td>{tr(x.language)}</td><td>{x.urgency ? <Badge urgency={x.urgency} /> : "-"}</td><td>{x.specialty.map((s) => tr(s)).join(", ")}</td></tr>
          ))}</tbody></table>
      )}
      {data.total > data.people.length && <p className="small muted">{tr("Showing the most urgent {n}.", { n: data.people.length })}</p>}
    </div>
  );
}

function Proposal({ d2, onApplied }) {
  const tr = useTr();
  const [prop, setProp] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const run = async () => {
    setBusy(true); setErr(null);
    try { setProp(await api.post("/admin/careteam/proposal")); } catch (e) { setErr(tr(String(e.message || e))); } finally { setBusy(false); }
  };
  const apply = async (doctors) => {
    setBusy(true); setErr(null);
    try { const r = await api.post("/admin/careteam/apply", { doctors }); setProp(null); onApplied(tr("{n} assignments saved.", { n: r.applied })); }
    catch (e) { setErr(tr(String(e.message || e))); } finally { setBusy(false); }
  };
  return (
    <div className="card">
      <h3>{tr("Match patients to doctors")}</h3>
      <p className="small muted">{tr("The system proposes; you approve. Each person goes to a doctor of their own hospital whose specialty fits (children to paediatrics, adults to family medicine, adults with diabetes or high blood pressure also to internal medicine, people with a genetic pathway and genetic consent also to genetic counselling). Urgent people first, children of one parent together, a doctor who speaks their language when there is room, nobody past a doctor's patient limit, nobody to a doctor on leave.")}</p>
      <div className="row"><button className="btn primary" onClick={run} disabled={busy || d2.doctors.length === 0}>{tr("Propose assignments")}</button>
        {d2.doctors.length === 0 && <span className="small muted">{tr("Add a doctor with a specialty first.")}</span>}</div>
      {err && <div className="error">{err}</div>}
      {prop && (
        <>
          <p><b>{tr("{n} assignments would be made.", { n: prop.total.toLocaleString() })}</b> <span className="small muted">{tr("Nothing is saved until you approve.")}</span></p>
          {prop.per_doctor.length > 0 && (
            <div className="table-wrap"><table>
              <thead><tr><th>{tr("Doctor")}</th><th>{tr("Hospital")}</th><th>{tr("New patients")}</th><th>{tr("Urgent")}</th><th>{tr("Same language")}</th><th>{tr("Children kept with siblings")}</th><th>{tr("Examples")}</th><th></th></tr></thead>
              <tbody>{prop.per_doctor.map((x) => (
                <tr key={x.user}>
                  <td>{x.name}</td><td>{tr(x.hospital)}</td><td><b>{x.new}</b></td><td>{x.urgent}</td>
                  <td>{Math.round((100 * x.language_match) / x.new)}%</td><td>{x.together}</td>
                  <td className="small">{x.sample.map((s) => `${s.mrn} (${s.age} y, ${tr(s.language)})`).join(" · ")}</td>
                  <td><button className="btn" disabled={busy} onClick={() => apply([x.user])}>{tr("Approve for this doctor")}</button></td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
          {prop.left.length > 0 && (
            <div className="warn small">
              <b>{tr("Left without a doctor:")}</b>{" "}
              {prop.left.map((l) => `${tr(l.hospital)}, ${tr(l.specialty)}: ${l.people} (${tr(l.why === "no doctor" ? "no doctor with this specialty" : "all patient lists are full")})`).join("; ")}
            </div>
          )}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn good" disabled={busy || prop.total === 0} onClick={() => apply([])}>{tr("Approve all")}</button>
            <button className="btn" onClick={() => setProp(null)}>{tr("Discard")}</button>
          </div>
        </>
      )}
    </div>
  );
}

function ByHand({ d2, onDone }) {
  const tr = useTr();
  const [q, setQ] = useState("");
  const [found, setFound] = useState(null);
  const [err, setErr] = useState(null);
  const search = async () => {
    setErr(null);
    try { setFound(await api.get(`/admin/careteam/find?record=${encodeURIComponent(q)}`)); } catch (e) { setErr(tr(String(e.message || e))); setFound(null); }
  };
  const set = async (person, specialty, doctor) => {
    setErr(null);
    try { await api.post("/admin/careteam/assign", { person, specialty, doctor }); onDone(tr("Changed.")); search(); }
    catch (e) { setErr(tr(String(e.message || e))); }
  };
  const name = (u) => d2.doctors.find((x) => x.user === u)?.name || u;
  return (
    <div className="card">
      <h3>{tr("Change one patient by hand")}</h3>
      <p className="small muted">{tr("Search by record number. A patient can have one doctor for each specialty; the rules above still apply.")}</p>
      <div className="row">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="ADH-1234567" onKeyDown={(e) => e.key === "Enter" && search()} />
        <button className="btn" onClick={search} disabled={q.trim().length < 3}>{tr("Search")}</button>
      </div>
      {err && <div className="small" style={{ color: "var(--urgent)" }}>{err}</div>}
      {found && found.length === 0 && <p className="small muted">{tr("Nobody found.")}</p>}
      {found && found.map((x) => (
        <div className="card" key={x.id} style={{ margin: "10px 0 0" }}>
          <div className="row"><b>{x.mrn}</b><span className="small muted">{x.child ? tr("Child") : tr("Adult")}, {x.age} y, {tr(x.language)}, {tr(x.hospital)}</span>{x.urgency && <Badge urgency={x.urgency} />}</div>
          <table className="small"><tbody>
            {Object.keys(d2.specialties).map((spec) => {
              const options = d2.doctors.filter((d) => d.hospital === x.hospital && d.specialty.includes(spec));
              if (options.length === 0 && !x.team[spec]) return null;
              return (
                <tr key={spec}><td>{tr(spec)}</td>
                  <td>
                    <select value={x.team[spec] || ""} onChange={(e) => set(x.id, spec, e.target.value || null)}>
                      <option value="">{tr("No doctor")}</option>
                      {options.map((d) => <option key={d.user} value={d.user}>{d.name}</option>)}
                      {x.team[spec] && !options.some((d) => d.user === x.team[spec]) && <option value={x.team[spec]}>{name(x.team[spec])}</option>}
                    </select>
                  </td></tr>
              );
            })}
          </tbody></table>
        </div>
      ))}
    </div>
  );
}

function Activity({ version }) {
  const tr = useTr();
  const { data, error, loading } = useApi("/admin/careteam/activity", [version]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const when = (s) => s.replace("T", " ").slice(0, 16);
  return (
    <div className="grid g2">
      <div className="card">
        <h3>{tr("Opened outside the doctor's own patients (emergency access)")}</h3>
        <p className="small muted">{tr("A doctor gave a reason and opened one patient for a few hours. Each one is in the access log.")}</p>
        {data.break_glass.length === 0 ? <p className="small muted">{tr("None yet.")}</p> : (
          <div className="table-wrap"><table className="small">
            <thead><tr><th>{tr("When")}</th><th>{tr("Doctor")}</th><th>{tr("Record")}</th><th>{tr("Reason")}</th><th>{tr("Until")}</th></tr></thead>
            <tbody>{data.break_glass.map((g, i) => <tr key={i}><td>{when(g.time)}</td><td>{g.doctor}</td><td>{g.mrn} <span className="muted">{tr(g.home)}</span></td><td dir="auto">{g.reason}</td><td>{g.active ? when(g.expires) : tr("ended")}</td></tr>)}</tbody>
          </table></div>
        )}
      </div>
      <div className="card">
        <h3>{tr("Records shared by families")}</h3>
        <p className="small muted">{tr("A family let another hospital's doctors open a record for some days. They can take it back at any time.")}</p>
        {data.shares.length === 0 ? <p className="small muted">{tr("None yet.")}</p> : (
          <div className="table-wrap"><table className="small">
            <thead><tr><th>{tr("When")}</th><th>{tr("Record")}</th><th>{tr("Shared with")}</th><th>{tr("Status")}</th></tr></thead>
            <tbody>{data.shares.map((g, i) => <tr key={i}><td>{when(g.time)}</td><td>{g.mrn} <span className="muted">{tr(g.home)}</span></td><td>{tr(g.hospital)}</td>
              <td>{g.revoked ? tr("taken back") : g.active ? `${tr("until")} ${when(g.expires)}` : tr("ended")}</td></tr>)}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}

export default function CareTeam() {
  const tr = useTr();
  const [version, setVersion] = useState(0);
  const { data: d, error, loading, reload } = useApi("/admin/careteam", [version]);
  const [editing, setEditing] = useState(null);
  const [viewing, setViewing] = useState([]);
  const [msg, setMsg] = useState(null);
  if (loading && !d) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const changed = (text) => { setMsg({ ok: true, text }); setEditing(null); setVersion((v) => v + 1); reload(); };
  const reset = async () => {
    if (window.confirm(tr("Clear every assignment, leave, break-glass opening and sharing, and start over?"))) {
      await api.post("/admin/careteam/reset"); changed(tr("Care-team data cleared."));
    }
  };
  const toggleView = (u) => setViewing((v) => (v.includes(u) ? v.filter((x) => x !== u) : [...v, u].slice(-2)));
  return (
    <>
      {d.open_mode && <div className="info small">{tr("No accounts are set up, so these are made-up doctors. Approve assignments, then use \"Preview as\" in the menu to look at the app as one doctor or as one hospital's admin.")}</div>}
      {d.hospital && <div className="info small">{tr("You are the admin of {h}: you see and change this hospital only.", { h: tr(d.hospital) })}</div>}
      {msg && <div className={msg.ok ? "info" : "error"}>{msg.text}</div>}
      <div className="grid g3">
        {d.hospitals.map((h) => (
          <div className="card" key={h.hospital} style={{ margin: 0 }}>
            <h3>{tr(h.hospital)}</h3>
            <p className="small">{tr("{a} of {b} patients have a doctor", { a: h.with_a_doctor.toLocaleString(), b: h.people.toLocaleString() })} · {tr("{n} doctors", { n: h.doctors })}</p>
            <p className="small" style={{ color: h.urgent_without_doctor ? "var(--urgent)" : undefined }}>{tr("{n} urgent without a doctor", { n: h.urgent_without_doctor })}</p>
          </div>
        ))}
      </div>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3>{tr("Doctors")} ({d.doctors.length})</h3>
          {!d.hospital && <button className="btn" onClick={reset}>{tr("Start over")}</button>}
        </div>
        {d.doctors.length === 0 ? <p className="small muted">{tr("No doctor has a specialty yet. Set one when you create or approve a doctor's account.")}</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>{tr("Doctor")}</th><th>{tr("Hospital")}</th><th>{tr("Specialty")}</th><th>{tr("Languages")}</th><th>{tr("Patients")}</th><th></th></tr></thead>
            <tbody>{d.doctors.map((x) => (
              <React.Fragment key={x.user}>
                <tr>
                  <td><b>{x.name}</b><div className="small muted">{x.user}</div></td>
                  <td>{tr(x.hospital)}</td>
                  <td className="small">{x.specialty.map((s) => tr(s)).join(", ")}</td>
                  <td className="small">{x.languages.map((l) => tr(l)).join(", ") || "-"}</td>
                  <td><Bar used={x.panel_size} limit={x.panel_limit} />
                    {x.duty && <span className="chip">{tr("Duty")}</span>}{" "}
                    {x.on_leave && <span className="chip warn-chip">{tr("On leave")}{x.backup ? ` → ${d.doctors.find((y) => y.user === x.backup)?.name || x.backup}` : ""}</span>}
                  </td>
                  <td className="row">
                    <button className="btn" onClick={() => setEditing(editing === x.user ? null : x.user)}>{tr("Edit")}</button>
                    <button className={`btn ${viewing.includes(x.user) ? "primary" : ""}`} onClick={() => toggleView(x.user)}>{tr("Patients")}</button>
                  </td>
                </tr>
                {editing === x.user && <EditDoctor d={x} d2={d} onDone={changed} onCancel={() => setEditing(null)} />}
              </React.Fragment>
            ))}</tbody>
          </table></div>
        )}
        {d.not_in_a_team.length > 0 && (
          <>
            <h4 style={{ marginTop: 14 }}>{tr("Doctor accounts not in a care team")}</h4>
            <p className="small muted">{tr("These doctors have no specialty yet, so they still see their whole region. Add a specialty to put them in a care team: then they see only their own patients.")}</p>
            <div className="table-wrap"><table className="small"><tbody>
              {d.not_in_a_team.map((x) => (
                <React.Fragment key={x.user}>
                  <tr><td><b>{x.name}</b> <span className="muted">{x.user}</span></td><td>{x.hospital ? tr(x.hospital) : tr("All regions")}</td>
                    <td><button className="btn" onClick={() => setEditing(editing === x.user ? null : x.user)}>{tr("Add to a care team")}</button></td></tr>
                  {editing === x.user && <EditDoctor d={x} d2={d} onDone={changed} onCancel={() => setEditing(null)} />}
                </React.Fragment>
              ))}
            </tbody></table></div>
          </>
        )}
        {viewing.length > 0 && (
          <>
            <p className="small muted" style={{ marginTop: 12 }}>{tr("Pick two doctors with \"Patients\" to compare who each one sees.")}</p>
            <div className="grid g2">{viewing.map((u) => <PanelView key={u} doctor={u} name={d.doctors.find((x) => x.user === u)?.name || u} version={version} />)}</div>
          </>
        )}
      </div>
      <Proposal d2={d} onApplied={changed} />
      <ByHand d2={d} onDone={(t) => { setMsg({ ok: true, text: t }); setVersion((v) => v + 1); }} />
      <Activity version={version} />
    </>
  );
}
