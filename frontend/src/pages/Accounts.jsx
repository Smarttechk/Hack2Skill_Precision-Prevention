// Hospital admin: create accounts (there is no self sign-up), reset passwords, unlock, remove, and read the access log.
import React, { useState } from "react";
import { api, useApi } from "../api.js";
import { ErrorBox, Loading } from "../components.jsx";
import { useT, useTr } from "../i18n.js";

const EMPTY = { name: "", full_name: "", role: "doctor", password: "", region: "", person: "", specialty: [], languages: [], panel_limit: "" };
const LANGUAGES = ["Arabic", "English", "Urdu", "Hindi", "Tagalog"];
const LABEL = { full_name: "Name", email: "Email", phone: "Mobile", hospital: "Hospital", job_title: "Job title", license_number: "DoH licence",
  specialty: "Specialty", facility: "Works at", staff_id: "Staff number", languages: "Languages", department: "Department",
  date_of_birth: "Date of birth", emirates_id_last4: "Emirates ID ends in", language: "Language", region: "Region asked",
  record_number: "Record number given", children: "Children", home_region: "Hospital region used" };

// a patient sign-up is linked to their own record: search by date of birth (filled in from the sign-up) or record number
function RecordFinder({ dob, chosen, onPick }) {
  const tr = useTr();
  const [q, setQ] = useState({ dob: dob || "", record: "" });
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const search = async (query = q) => {
    setErr(null);
    try { setRes(await api.get(`/admin/find_person?dob=${encodeURIComponent(query.dob)}&record=${encodeURIComponent(query.record)}`)); }
    catch (e) { setErr(tr(String(e.message || e))); setRes(null); }
  };
  React.useEffect(() => { if (dob) search({ dob, record: "" }); }, [dob]); // eslint-disable-line
  return (
    <div className="finder">
      <div className="small"><b>{tr("Link to their own record")}</b> <span className="muted">{tr("(check their Emirates ID card first)")}</span></div>
      <div className="finder-q">
        <input type="date" value={q.dob} onChange={(e) => setQ({ ...q, dob: e.target.value })} aria-label={tr("Date of birth")} />
        <input placeholder={tr("or name / record number")} value={q.record} onChange={(e) => setQ({ ...q, record: e.target.value })} />
        <button className="btn" type="button" onClick={() => search()}>{tr("Search")}</button>
      </div>
      {err && <div className="small" style={{ color: "var(--urgent)" }}>{err}</div>}
      {res && (
        <>
          <div className="small muted">{res.people.length === 0 ? tr("No adult record matches. Ask them for their record number, or reject.")
            : res.exact ? tr("{n} record(s) match:", { n: res.total }) : tr("Nobody is born on that date. People born in {y}:", { y: q.dob.slice(0, 4) })}</div>
          <ul className="finder-list">{res.people.map((x) => (
            <li key={x.id} className={x.id === chosen ? "sel" : ""}>
              <span>{x.name && <b>{x.name} </b>}<span className="muted">{x.short}</span> · {tr("born {d}", { d: x.born })} · {tr(x.sex)}, {x.age} · {tr(x.nationality)} · {tr(x.language)} · {tr(x.region)}
                {x.children > 0 && ` · ${x.children === 1 ? tr("1 child") : tr("{n} children", { n: x.children })}`}</span>
              <button className={`btn ${x.id === chosen ? "primary" : ""}`} type="button" onClick={() => onPick(x.id)}>{x.id === chosen ? tr("Selected") : tr("Use this record")}</button>
            </li>
          ))}</ul>
        </>
      )}
      {chosen && <div className="small" style={{ color: "var(--primary-dark)" }}>{tr("Will be linked to record {id}.", { id: res?.people.find((x) => x.id === chosen)?.short || chosen.slice(0, 8) })}</div>}
    </div>
  );
}

function Requests({ regions, scope, onDone }) {
  const { t } = useT();
  const tr = useTr();
  const reqs = useApi("/admin/requests");
  const [pick, setPick] = useState({});
  if (reqs.loading || reqs.error || !reqs.data.length) {
    return reqs.error ? <ErrorBox error={reqs.error} /> : (
      <div className="card"><h3>{tr("Sign-up requests")}</h3><p className="small muted">{reqs.loading ? t.loading : tr("No requests waiting.")}</p></div>
    );
  }
  const decide = async (r, ok, newPatient = false) => {
    const p = pick[r.id] || {};
    try {
      if (ok) await api.post(`/admin/requests/${r.id}/approve`, { region: p.region ?? (r.details.region || r.details.home_region || ""), person: newPatient ? "" : (p.person ?? (r.details.record_number || "")), without_record: newPatient });
      else await api.post(`/admin/requests/${r.id}/reject`);
      onDone({ ok: true, text: tr(ok ? "Approved {u}." : "Rejected {u}.", { u: r.user }) });
      reqs.reload();
    } catch (e) { onDone({ ok: false, text: String(e.message || e) }); }
  };
  return (
    <div className="card">
      <h3>{tr("Sign-up requests")} ({reqs.data.length})</h3>
      <p className="small muted">{tr("Check each person's details (licence, staff number, or Emirates ID and date of birth against their card) before approving.")}</p>
      <div className="grid g2">
        {reqs.data.map((r) => {
          const p = pick[r.id] || {};
          return (
            <div className="card" key={r.id} style={{ margin: 0 }}>
              <div className="row" style={{ justifyContent: "space-between" }}><b>{r.user}</b><span className="chip">{t.roles[r.role] || r.role}</span></div>
              <table className="small"><tbody>
                {Object.entries(r.details).filter(([k]) => k !== "emirates_id_hash").map(([k, v]) => <tr key={k}><td className="muted">{tr(LABEL[k] || k)}</td><td>{tr(v)}</td></tr>)}
                <tr><td className="muted">{tr("Asked on")}</td><td>{r.requested}</td></tr>
              </tbody></table>
              {scope.region.includes(r.role) && (
                <label className="small">{tr("Sees region")}{" "}
                  <select value={p.region ?? (r.details.region || "")} onChange={(e) => setPick({ ...pick, [r.id]: { ...p, region: e.target.value } })}>
                    <option value="">{tr("All regions")}</option>{regions.map((x) => <option key={x} value={x}>{tr(x)}</option>)}
                  </select>
                </label>
              )}
              {scope.person.includes(r.role) && (
                <RecordFinder dob={r.details.date_of_birth} chosen={p.person ?? (r.details.record_number || "")}
                  onPick={(id) => setPick({ ...pick, [r.id]: { ...p, person: id } })} />
              )}
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn good" onClick={() => decide(r, true)}
                  disabled={scope.person.includes(r.role) && !(p.person ?? r.details.record_number)}
                  title={scope.person.includes(r.role) && !(p.person ?? r.details.record_number) ? tr("Choose their record first") : undefined}>{tr("Approve")}</button>
                {scope.person.includes(r.role) && (
                  <button className="btn" onClick={() => decide(r, true, true)}
                    title={tr("They have no hospital record yet. Their account opens now and shows no record until you link one.")}>{tr("Approve as a new patient (no record yet)")}</button>
                )}
                <button className="btn bad" onClick={() => decide(r, false)}>{tr("Reject")}</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// a parent asked to add a child: the admin finds the child's record, compares, and approves or rejects with a reason
function FamilyRequests({ onDone }) {
  const tr = useTr();
  const reqs = useApi("/admin/family/requests");
  const [chosen, setChosen] = useState({});
  const [found, setFound] = useState({});
  const [q, setQ] = useState({});
  const [why, setWhy] = useState({});
  if (reqs.error) return <ErrorBox error={reqs.error} />;
  if (reqs.loading || !reqs.data.length) {
    return <div className="card"><h3>{tr("Family requests")}</h3><p className="small muted">{reqs.loading ? "…" : tr("No family requests waiting.")}</p></div>;
  }
  const search = async (r) => {
    const v = q[r.id] ?? { dob: r.dob, record: r.record_number || "" };
    try { setFound({ ...found, [r.id]: await api.get(`/admin/family/find?dob=${encodeURIComponent(v.dob)}&record=${encodeURIComponent(v.record)}`) }); }
    catch (e) { onDone({ ok: false, text: tr(String(e.message || e)) }); }
  };
  const decide = async (r, ok) => {
    try {
      if (ok) await api.post(`/admin/family/${r.id}/approve`, { child: chosen[r.id] });
      else await api.post(`/admin/family/${r.id}/reject`, { reason: why[r.id] || "" });
      onDone({ ok: true, text: tr(ok ? "Approved: {n} can now see this child." : "Rejected the request from {n}.", { n: r.parent }) });
      reqs.reload();
    } catch (e) { onDone({ ok: false, text: tr(String(e.message || e)) }); }
  };
  return (
    <div className="card">
      <h3>{tr("Family requests")} ({reqs.data.length})</h3>
      <p className="small muted">{tr("A parent asks to add a child to their account. Find the child's record, check the name, date of birth and who the child is registered to, then approve. The registered guardian stays.")}</p>
      <div className="grid g2">
        {reqs.data.map((r) => {
          const res = found[r.id];
          return (
            <div className="card" key={r.id} style={{ margin: 0 }}>
              <table className="small"><tbody>
                <tr><td className="muted">{tr("Asked by")}</td><td>{r.parent}{r.parent_name && ` · ${r.parent_name}`} · {tr(r.relation)}</td></tr>
                <tr><td className="muted">{tr("Child")}</td><td>{r.first_name}{r.family_name ? ` ${r.family_name}` : ""} · {tr("born {d}", { d: r.dob })}{r.sex ? ` · ${tr(r.sex)}` : ""}{r.language ? ` · ${tr(r.language)}` : ""}</td></tr>
                {r.record_number && <tr><td className="muted">{tr("Record number given")}</td><td>{r.record_number}</td></tr>}
                {r.note && <tr><td className="muted">{tr("Note")}</td><td dir="auto">{r.note}</td></tr>}
              </tbody></table>
              <div className="finder-q" style={{ marginTop: 6 }}>
                <input type="date" value={(q[r.id] ?? { dob: r.dob }).dob} onChange={(e) => setQ({ ...q, [r.id]: { record: "", ...q[r.id], dob: e.target.value } })} aria-label={tr("Date of birth")} />
                <input placeholder={tr("or name / record number")} value={(q[r.id] ?? { record: r.record_number || "" }).record ?? ""} onChange={(e) => setQ({ ...q, [r.id]: { dob: r.dob, ...q[r.id], record: e.target.value } })} />
                <button className="btn" type="button" onClick={() => search(r)}>{tr("Search")}</button>
              </div>
              {res && (
                <ul className="finder-list">
                  {res.children.length === 0 && <li className="muted small">{tr("No child record matches in this hospital. Ask the parent to register the child at the clinic, then reject with that reason.")}</li>}
                  {res.children.map((x) => (
                    <li key={x.id} className={x.id === chosen[r.id] ? "sel" : ""}>
                      <span>{x.name && <b>{x.name} </b>}<span className="muted">{x.short}</span> · {tr("born {d}", { d: x.born })} · {tr(x.sex)}, {x.age} · {tr(x.language)}
                        {x.registered_to && ` · ${tr("registered to {r}", { r: x.registered_to })}`}</span>
                      <button className={`btn ${x.id === chosen[r.id] ? "primary" : ""}`} type="button" onClick={() => setChosen({ ...chosen, [r.id]: x.id })}>{x.id === chosen[r.id] ? tr("Selected") : tr("Use this record")}</button>
                    </li>
                  ))}
                </ul>
              )}
              <input placeholder={tr("Reason, if you reject (the parent sees it)")} value={why[r.id] || ""} onChange={(e) => setWhy({ ...why, [r.id]: e.target.value })} style={{ width: "100%", margin: "6px 0" }} dir="auto" />
              <div className="row">
                <button className="btn good" onClick={() => decide(r, true)} disabled={!chosen[r.id]} title={!chosen[r.id] ? tr("Choose the child's record first") : undefined}>{tr("Approve")}</button>
                <button className="btn bad" onClick={() => decide(r, false)}>{tr("Reject")}</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function Accounts() {
  const { t } = useT();
  const tr = useTr();
  const list = useApi("/admin/users");
  const [form, setForm] = useState(EMPTY);
  const [msg, setMsg] = useState(null);
  const [linking, setLinking] = useState(null);
  const [linkPick, setLinkPick] = useState("");
  const [filter, setFilter] = useState({ user: "", event: "" });
  const log = useApi(`/admin/access_log?user=${encodeURIComponent(filter.user)}&event=${encodeURIComponent(filter.event)}`, [msg]);
  if (list.loading) return <Loading />;
  if (list.error) return <ErrorBox error={list.error} />;
  const d = list.data;
  const act = async (fn, ok) => {
    try { await fn(); setMsg({ ok: true, text: ok }); list.reload(); } catch (e) { setMsg({ ok: false, text: String(e.message || e) }); }
  };
  const create = (e) => {
    e.preventDefault();
    act(() => api.post("/admin/users", { ...form, panel_limit: Number(form.panel_limit) || 0 }), tr("Saved account {u}.", { u: form.name })).then(() => setForm(EMPTY));
  };
  const reset = (name) => {
    const pw = window.prompt(tr("New password for {u} (at least {n} characters). Give it to them in person.", { u: name, n: d.min_password }));
    if (pw) act(() => api.post(`/admin/users/${encodeURIComponent(name)}/password`, { password: pw }), tr("Password reset for {u}; their sessions were ended.", { u: name }));
  };
  const remove = (name) => {
    if (window.confirm(tr("Remove the account {u}? They are signed out at once. This cannot be undone.", { u: name }))) {
      act(() => api.post(`/admin/users/${encodeURIComponent(name)}/remove`), tr("Removed {u}.", { u: name }));
    }
  };
  const needsRegion = d.scope.region.includes(form.role);
  const needsPerson = d.scope.person.includes(form.role);
  return (
    <>
      {!d.accounts_on && <div className="warn small">{tr("No accounts yet: the app is open on this computer until the first account is saved.")}</div>}
      {msg && <div className={msg.ok ? "info" : "error"}>{msg.text}</div>}
      <Requests regions={d.regions} scope={d.scope} onDone={(m) => { setMsg(m); list.reload(); }} />
      <FamilyRequests onDone={(m) => setMsg(m)} />
      <div className="grid g2">
        <div className="card">
          <h3>{tr("New account")}</h3>
          <form className="form-grid" onSubmit={create}>
            <label>{tr("Full name (shown at the top of the app)")}<input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} placeholder="Dr Amina Al Mansoori" /></label>
            <label>{tr("User name")}<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
            <label>{tr("Role")}
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value, region: "", person: "", specialty: [], languages: [], panel_limit: "" })}>
                {d.roles.filter((r) => !d.hospital || d.scope.region.includes(r) || d.scope.person.includes(r)).map((r) => <option key={r} value={r}>{t.roles[r] || r}</option>)}
              </select>
            </label>
            {needsRegion && (
              <label>{d.hospital ? tr("Hospital") : tr("Region (empty = all regions)")}
                <select value={d.hospital || form.region} disabled={!!d.hospital} onChange={(e) => setForm({ ...form, region: e.target.value })}>
                  {!d.hospital && <option value="">{tr("All regions")}</option>}{d.regions.map((r) => <option key={r} value={r}>{tr(r)}</option>)}
                </select>
              </label>
            )}
            {form.role === "doctor" && (
              <div className="doctor-profile">
                <div><b className="small">{tr("Specialty")}</b>{" "}
                  {Object.entries(d.specialties || {}).map(([k, text]) => (
                    <label key={k} className="toggle" title={tr(text)}><input type="checkbox" checked={form.specialty.includes(k)}
                      onChange={() => setForm({ ...form, specialty: form.specialty.includes(k) ? form.specialty.filter((x) => x !== k) : [...form.specialty, k] })} /> {tr(k)}</label>
                  ))}
                </div>
                <div><b className="small">{tr("Languages spoken")}</b>{" "}
                  {LANGUAGES.map((l) => (
                    <label key={l} className="toggle"><input type="checkbox" checked={form.languages.includes(l)}
                      onChange={() => setForm({ ...form, languages: form.languages.includes(l) ? form.languages.filter((x) => x !== l) : [...form.languages, l] })} /> {tr(l)}</label>
                  ))}
                </div>
                <label className="small">{tr("Maximum patients")} <input type="number" min="1" max="5000" value={form.panel_limit} placeholder="400" style={{ width: 90 }}
                  onChange={(e) => setForm({ ...form, panel_limit: e.target.value })} /></label>
                <p className="small muted">{tr("With a specialty the doctor sees only the patients assigned to them (Care teams). Without one they see their whole region.")}</p>
              </div>
            )}
            {needsPerson && (
              <label>{tr("Patient record Id (checked identity)")}
                <input value={form.person} onChange={(e) => setForm({ ...form, person: e.target.value })} required />
              </label>
            )}
            <label>{tr("First password (at least {n} characters)", { n: d.min_password })}
              <input type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
            </label>
            <button className="btn primary" type="submit">{tr("Save account")}</button>
          </form>
        </div>
        <div className="card">
          <h3>{tr("Accounts")} ({d.users.length})</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>{tr("User")}</th><th>{tr("Role")}</th><th>{tr("Sees")}</th><th></th></tr></thead>
              <tbody>
                {d.users.map((u) => (
                  <tr key={u.name}>
                    <td>{u.name} {u.locked && <span className="chip warn-chip">{tr("locked")}</span>}</td>
                    <td>{t.roles[u.role] || u.role}{u.specialty?.length > 0 && <div className="small muted">{u.specialty.map((s) => tr(s)).join(", ")}</div>}</td>
                    <td className="small">{u.person ? tr("own record and children") : u.awaiting_record ? <span className="chip warn-chip">{tr("no record yet")}</span> : u.region ? tr("only {r}", { r: tr(u.region) }) : tr("all")}</td>
                    <td className="row">
                      {u.awaiting_record && <button className="btn primary" onClick={() => { setLinking(u); setLinkPick(""); }}>{tr("Link record")}</button>}
                      <button className="btn" onClick={() => reset(u.name)}>{tr("Reset password")}</button>
                      {u.locked && <button className="btn" onClick={() => act(() => api.post(`/admin/users/${encodeURIComponent(u.name)}/unlock`), tr("Unlocked {u}.", { u: u.name }))}>{tr("Unlock")}</button>}
                      <button className="btn bad" onClick={() => remove(u.name)}>{tr("Remove")}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {linking && (
            <div className="card" style={{ marginTop: 12 }}>
              <h4>{tr("Link a record to {u}", { u: linking.name })}</h4>
              <p className="small muted">{tr("Once the hospital has registered this patient, find their record (check their Emirates ID card) and link it. Until then they see only a page saying their record is being set up.")}</p>
              <RecordFinder key={linking.name} dob={linking.signup_dob} chosen={linkPick} onPick={setLinkPick} />
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn good" disabled={!linkPick}
                  onClick={() => act(() => api.post(`/admin/users/${encodeURIComponent(linking.name)}/link`, { person: linkPick }), tr("Linked {u} to their record.", { u: linking.name })).then(() => setLinking(null))}>{tr("Link")}</button>
                <button className="btn" onClick={() => setLinking(null)}>{tr("Cancel")}</button>
              </div>
            </div>
          )}
        </div>
      </div>
      <div className="card">
        <h3>{tr("Access log")}</h3>
        <p className="small muted">{tr("Every sign-in, failed sign-in, sign-out, refused page, account change and opened patient record: who, when and from which computer.")}</p>
        <div className="row">
          <select value={filter.user} onChange={(e) => setFilter({ ...filter, user: e.target.value })}>
            <option value="">{tr("All users")}</option>{d.users.map((u) => <option key={u.name}>{u.name}</option>)}
          </select>
          <select value={filter.event} onChange={(e) => setFilter({ ...filter, event: e.target.value })}>
            <option value="">{tr("All events")}</option>
            {["sign_in", "sign_in_failed", "sign_out", "opened_record", "refused", "account_created", "account_changed", "account_removed", "password_changed", "account_unlocked", "break_glass", "share_record", "share_revoked", "assignments_applied", "assignment_changed", "profile_changed"].map((x) => <option key={x} value={x}>{tr(x)}</option>)}
          </select>
        </div>
        {log.loading ? <Loading /> : log.error ? <ErrorBox error={log.error} /> : (
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
            <table>
              <thead><tr><th>{tr("Time")}</th><th>{tr("User")}</th><th>{tr("Role")}</th><th>{tr("Computer")}</th><th>{tr("Event")}</th><th>{tr("Detail")}</th></tr></thead>
              <tbody>{log.data.map((r, i) => <tr key={i}><td className="small">{r.time}</td><td>{r.user}</td><td>{t.roles[r.role] || r.role}</td><td className="small">{r.computer}</td><td>{tr(r.event)}</td><td className="small">{r.detail}</td></tr>)}</tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
