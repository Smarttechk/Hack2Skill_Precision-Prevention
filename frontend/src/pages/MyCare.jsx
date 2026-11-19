// What a signed-in patient or parent sees: their own and their children's checks in plain words (what each is and how to
// get ready), the nearest clinic with a map link, booked visits, the reminder choices, past visits and vaccines, a download
// of the whole record and the messages the clinic sent. Never risk scores, reasons or genetics (the server only sends plain
// names).
import React, { useState } from "react";
import { api, useApi } from "../api.js";
import { ErrorBox, Loading } from "../components.jsx";
import ContactChoices from "../ContactChoices.jsx";
import { CARE_INFO, nameOf } from "../careInfo.js";
import { useT, useTr } from "../i18n.js";

export default function MyCare() {
  const tr = useTr();
  const { data, error, loading } = useApi("/mycare");
  const [who, setWho] = useState(0);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (data.awaiting_record) {
    // a new patient: the account is open, the hospital has not registered their record yet
    return (
      <div className="card">
        <h3>{tr("Welcome. Your record is being set up.")}</h3>
        <p>{tr("Your account is active. We cannot show check-ups, visits or messages yet because the hospital has not registered you.")}</p>
        <ol className="small">
          <li>{tr("Visit your nearest clinic and register. Bring your Emirates ID.")}</li>
          <li>{tr("Once the clinic has registered you, the hospital links your record to this account.")}</li>
          <li>{tr("Come back here: your due check-ups, booked visits and choices appear, and you can add your children.")}</li>
        </ol>
        <div className="info small">{tr("Emergency: call {n}.", { n: data.emergency || "998" })}</div>
      </div>
    );
  }
  const people = data.people;
  const shown = people[Math.min(who, people.length - 1)];
  return (
    <>
      <div className="info small">{tr("Emergency: call {n}. You can change how we contact you below.", { n: data.emergency || "998" })}</div>
      <UrgentCare pid={shown.id} who={nameOf(shown, tr)} self={shown.self} emergency={data.emergency || "998"} />
      {/* one account covers the parent and every child they are guardian of: a switch between them */}
      {people.length > 1 && (
        <div className="family-tabs" role="tablist">
          {people.map((p, i) => (
            <button key={p.id} type="button" role="tab" aria-selected={p === shown} className={p === shown ? "on" : ""} onClick={() => setWho(i)}>
              {nameOf(p, tr)}
              {p.due.length > 0 && <span className="tab-n">{p.due.length}</span>}
            </button>
          ))}
        </div>
      )}
      <div className="my-card-wrap"><Person key={shown.id} p={shown} /></div>
      <AddFamily />
    </>
  );
}

// The family's own mobile number, for reminders by SMS or WhatsApp. A child's messages go to the guardian's number.
function PhoneNumber() {
  const tr = useTr();
  const { data, reload } = useApi("/mycare/phone");
  const [n, setN] = useState("");
  const [msg, setMsg] = useState(null);
  if (!data) return null;
  const save = async (e) => {
    e.preventDefault(); setMsg(null);
    try { await api.post("/mycare/phone", { number: n }); setN(""); setMsg({ ok: true, text: tr("Saved.") }); reload(); }
    catch (err) { setMsg({ ok: false, text: tr(String(err.message || err)) }); }
  };
  const remove = async () => { await api.post("/mycare/phone/clear", {}); reload(); };
  return (
    <div className="small" style={{ margin: "8px 0" }}>
      <b>{tr("Mobile number for reminders")}</b>{" "}
      {data.masked ? <><span dir="ltr">{data.masked}</span> <button type="button" className="btn" onClick={remove}>{tr("Remove")}</button></>
        : <span className="muted">{tr("none yet")}</span>}
      <form className="row" onSubmit={save} style={{ marginTop: 4 }}>
        <input value={n} onChange={(e) => setN(e.target.value)} placeholder="+9715..." dir="ltr" inputMode="tel" aria-label={tr("Mobile number")} />
        <button className="btn" type="submit" disabled={!n}>{tr(data.masked ? "Change number" : "Save number")}</button>
      </form>
      <div className="muted">{tr("Only used if you allow messages below. Messages for your children go to this number.")}</div>
      {msg && <div className={msg.ok ? "ok" : "error"}>{msg.text}</div>}
    </div>
  );
}

// For someone who needs a hospital now: the emergency number first, then the nearest hospitals to their home
function UrgentCare({ pid, who, self, emergency }) {
  const tr = useTr();
  const { lang } = useT();
  const [open, setOpen] = useState(false);
  const { data, loading, error } = useApi(open ? `/mycare/urgent?pid=${encodeURIComponent(pid)}` : null, [pid, open]);
  return (
    <div className="card urgent-card" style={{ borderColor: "var(--urgent, #b3402a)" }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div><b>{self ? tr("Do you need a hospital now?") : tr("Does {who} need a hospital now?", { who })}</b>
          <div className="small muted">{tr("Call {n} for an emergency. Or see the nearest hospitals.", { n: emergency })}</div></div>
        <button type="button" className="btn primary" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? tr("Hide") : tr("Find the nearest hospital")}</button>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          {loading && <p className="small muted">{tr("Loading...")}</p>}
          {error && <p className="small error">{tr(String(error.message || error))}</p>}
          {data && (
            <>
              <p style={{ fontSize: 18 }}><b>{tr("Emergency: call {n}", { n: data.emergency })}</b></p>
              {data.hospitals.length === 0 ? <p className="small muted">{tr("We could not work out the nearest hospital from the map data. Ask the clinic, or go to the nearest emergency department.")}</p> : (
                <ul className="hist-list">{data.hospitals.map((h) => (
                  <li key={h.name}><span><b dir="auto">{lang === "ar" && h.name_ar ? h.name_ar : h.name}</b>
                    <span className="muted"> {tr("about {k} km away in a straight line", { k: h.km })}</span></span>
                    <a className="btn" href={h.map_url} target="_blank" rel="noopener noreferrer">{tr("Open on a map")}</a></li>
                ))}</ul>
              )}
              <ul className="small">{data.advice.map((a) => <li key={a}>{tr(a)}</li>)}</ul>
              <p className="small muted">{tr("Distances are in a straight line, so the drive is longer. This is not a booking or a diagnosis.")}</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// a parent adds a child that is not on their account yet: the hospital checks it against the child's record first
function AddFamily() {
  const tr = useTr();
  const { data, reload } = useApi("/mycare/family");
  const EMPTY = { first_name: "", family_name: "", dob: "", sex: "", relation: "", language: "", record_number: "", note: "" };
  const [f, setF] = useState(EMPTY);
  const [msg, setMsg] = useState(null);
  if (!data) return null;
  const send = async (e) => {
    e.preventDefault();
    setMsg(null);
    try { const r = await api.post("/mycare/family/request", f); setMsg({ ok: true, text: tr(r.message) }); setF(EMPTY); reload(); }
    catch (err) { setMsg({ ok: false, text: tr(String(err.message || err)) }); }
  };
  const label = { pending: "Waiting for the hospital", approved: "Added to your account", rejected: "Not added" };
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>{tr("Add a family member")}</h3>
      <p className="small muted">{tr("Add a child you look after who is not shown above. The hospital checks the details against the child's record before you can see anything. Adults, such as a husband, wife or parent, sign up for their own account so their record and choices stay their own.")}</p>
      <form className="form-grid" onSubmit={send}>
        <label>{tr("Child's first name")}<input value={f.first_name} onChange={(e) => setF({ ...f, first_name: e.target.value })} required maxLength={40} dir="auto" /></label>
        <label>{tr("Child's family name")}<input value={f.family_name} onChange={(e) => setF({ ...f, family_name: e.target.value })} maxLength={40} dir="auto" /></label>
        <label>{tr("Child's date of birth")}<input type="date" value={f.dob} onChange={(e) => setF({ ...f, dob: e.target.value })} required max={new Date().toISOString().slice(0, 10)} /></label>
        <label>{tr("Child is a")}
          <select value={f.sex} onChange={(e) => setF({ ...f, sex: e.target.value })}>
            <option value="">{tr("Choose")}</option>{data.sexes.map((r) => <option key={r} value={r}>{tr(r)}</option>)}
          </select>
        </label>
        <label>{tr("Language at home")}
          <select value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })}>
            <option value="">{tr("Choose")}</option>{data.languages.map((r) => <option key={r} value={r}>{tr(r)}</option>)}
          </select>
        </label>
        <label>{tr("You are the child's")}
          <select value={f.relation} onChange={(e) => setF({ ...f, relation: e.target.value })} required>
            <option value="">{tr("Choose")}</option>{data.relations.map((r) => <option key={r} value={r}>{tr(r)}</option>)}
          </select>
        </label>
        <label>{tr("Child's record number (if you know it)")}<input value={f.record_number} onChange={(e) => setF({ ...f, record_number: e.target.value })} maxLength={30} /></label>
        <label className="grow">{tr("Anything the hospital should know (optional)")}<input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} maxLength={200} dir="auto" /></label>
        <button className="btn primary" type="submit">{tr("Send to the hospital")}</button>
      </form>
      {msg && <div className={msg.ok ? "ok small" : "error small"} style={{ marginTop: 6 }}>{msg.text}</div>}
      {data.requests.length > 0 && (
        <ul className="hist-list" style={{ marginTop: 8 }}>{data.requests.map((r) => (
          <li key={r.id}><span><b dir="auto">{r.first_name}</b> <span className="muted">{tr("born {d}", { d: r.dob })}</span>
            {r.reason && <div className="small muted" dir="auto">{r.reason}</div>}</span>
            <span className={`chip ${r.status === "approved" ? "tag-teal" : ""}`}>{tr(label[r.status])}</span></li>
        ))}</ul>
      )}
      <p className="small muted" style={{ marginTop: 10 }}><b>{tr("Adult family members:")}</b> {tr("a husband, wife or parent signs up for their own account at the Sign up page, so their record and choices stay their own. Ask them to use the same hospital region as you.")}</p>
    </div>
  );
}

function Person({ p }) {
  const tr = useTr();
  const { lang } = useT();
  const choices = useApi(`/why/${p.id}`);
  const [showHistory, setShowHistory] = useState(false);
  const clinicName = lang === "ar" && p.clinic?.name_ar ? p.clinic.name_ar : p.clinic?.name;
  return (
    <div className="card">
      <h3>{nameOf(p, tr)}</h3>
      {p.due.length ? (
        <>
          <p><b>{tr("Due now:")}</b></p>
          <ul className="due-list">
            {p.due.map((x) => {
              const info = CARE_INFO[x];
              return (
                <li key={x}>
                  <b style={{ textTransform: "capitalize" }}>{tr(x)}</b>
                  {info && <span className="small muted">{tr(info.what)}{info.prep ? ` ${tr("To get ready:")} ${tr(info.prep)}` : ""}</span>}
                </li>
              );
            })}
          </ul>
        </>
      ) : <p><b>{tr("Due now:")}</b> {tr("nothing right now")}</p>}
      {p.clinic && (
        <p className="small">
          <b>{tr("Nearest clinic:")}</b> <bdi>{clinicName}</bdi>, {tr("about {n} min away", { n: p.clinic.minutes })}
          {p.clinic.map_url && <> · <a href={p.clinic.map_url} target="_blank" rel="noopener noreferrer">{tr("Open on a map")}</a></>}
        </p>
      )}
      {p.booked && p.last_visit?.outcome !== "attended" && <p><b>{tr("Booked:")}</b> {p.booked.date} ({tr(p.booked.how)})</p>}
      {p.last_visit?.outcome === "attended" && <p className="small"><b>{tr("Last visit:")}</b> {tr("recorded as attended on {d}. Thank you.", { d: p.last_visit.date })}</p>}
      {p.last_visit?.outcome === "no_show" && <p className="small warn-text"><b>{tr("Last visit:")}</b> {tr("we missed you on {d}. We will contact you to book another time.", { d: p.last_visit.date })}</p>}
      {p.last_visit?.outcome === "rescheduled" && <p className="small"><b>{tr("Last visit:")}</b> {tr("moved on {d}. We will be in touch about a new date.", { d: p.last_visit.date })}</p>}
      {p.done.length > 0 && <p className="small"><b>{tr("Done:")}</b> {p.done.map((x) => tr(x)).join(tr(", "))}</p>}
      {p.self && <PhoneNumber />}
      <h4>{tr("Your choices")}</h4>
      {choices.data ? <ContactChoices pid={p.id} child={!p.self} consent={choices.data.consent} />
        : <p className="small muted">{p.may_contact ? tr("We may contact you about this.") : tr("You asked us not to send messages.")}</p>}
      <details className="history" onToggle={(e) => setShowHistory(e.currentTarget.open)}>
        <summary><h4 style={{ display: "inline" }}>{tr("Past visits and vaccines")}</h4></summary>
        {showHistory && <History pid={p.id} />}
      </details>
      <Sharing p={p} />
      <DownloadRecord p={p} />
      <h4>{tr("Messages from the clinic")}</h4>
      {p.messages.length === 0 ? <p className="small muted">{tr("No messages yet.")}</p> : (
        <ul className="small">{p.messages.map((m, i) => (
          <li key={i} dir="auto"><span className="muted">{m.from === "you" ? tr("You") : `${tr("Clinic")}${m.channel ? ` (${tr(m.channel)})` : ""}`}:</span> {m.text}</li>
        ))}</ul>
      )}
    </div>
  );
}

// the family decides who else may open this record: another hospital's doctors, for some days, and can take it back
function Sharing({ p }) {
  const tr = useTr();
  const { data, reload } = useApi("/mycare/sharing");
  const [hospital, setHospital] = useState("");
  const [days, setDays] = useState(30);
  const [msg, setMsg] = useState(null);
  const me = data?.people.find((x) => x.id === p.id);
  if (!me) return null;
  const share = async () => {
    setMsg(null);
    try { const r = await api.post("/mycare/sharing", { person: p.id, hospital, days: Number(days) }); setMsg({ ok: true, text: tr("Shared with {h} until {d}.", { h: tr(r.shared), d: r.until }) }); setHospital(""); reload(); }
    catch (e) { setMsg({ ok: false, text: tr(String(e.message || e)) }); }
  };
  const unshare = async (h) => {
    try { await api.post("/mycare/sharing/revoke", { person: p.id, hospital: h }); setMsg({ ok: true, text: tr("Taken back from {h}.", { h: tr(h) }) }); reload(); }
    catch (e) { setMsg({ ok: false, text: tr(String(e.message || e)) }); }
  };
  return (
    <details className="history">
      <summary><h4 style={{ display: "inline" }}>{tr("Sharing with another hospital")}</h4></summary>
      <p className="small muted">{tr("Doctors of another hospital can open this record only while you share it, and only for care. Every opening is logged. You can take it back at any time.")}</p>
      {me.shared.length > 0 && (
        <ul className="hist-list">{me.shared.map((s) => (
          <li key={s.hospital}><span>{tr(s.hospital)} <span className="muted">{tr("until")} <span dir="ltr">{s.until}</span></span></span>
            <button className="btn" onClick={() => unshare(s.hospital)}>{tr("Take back")}</button></li>
        ))}</ul>
      )}
      <div className="row" style={{ marginTop: 8 }}>
        <select value={hospital} onChange={(e) => setHospital(e.target.value)} aria-label={tr("Hospital")}>
          <option value="">{tr("Choose a hospital")}</option>{me.options.map((h) => <option key={h} value={h}>{tr(h)}</option>)}
        </select>
        <select value={days} onChange={(e) => setDays(e.target.value)} aria-label={tr("For how long")}>
          {[7, 30, 90, 365].filter((d) => d <= data.max_days).map((d) => <option key={d} value={d}>{tr("{n} days", { n: d })}</option>)}
        </select>
        <button className="btn primary" onClick={share} disabled={!hospital}>{tr("Share")}</button>
      </div>
      {msg && <div className={msg.ok ? "ok small" : "error small"} style={{ marginTop: 6 }}>{msg.text}</div>}
    </details>
  );
}

// read-only: the last visits (date and kind, never the reason) and each vaccine with its dates
function History({ pid }) {
  const tr = useTr();
  const { data, error, loading } = useApi(`/mycare/${pid}/history`);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data.available) return <p className="small muted">{tr("The records are not available here.")}</p>;
  return (
    <div className="hist-grid">
      <div>
        <h5>{tr("Visits")}</h5>
        {data.visits.length === 0 ? <p className="small muted">{tr("No visits on record.")}</p> : (
          <ul className="hist-list">{data.visits.map((v, i) => <li key={i}><span dir="ltr">{v.date}</span> <span className="muted">{tr(v.kind)}</span></li>)}</ul>
        )}
      </div>
      <div>
        <h5>{tr("Vaccines")}</h5>
        {data.vaccines.length === 0 ? <p className="small muted">{tr("No vaccines on record.")}</p> : (
          <ul className="hist-list">{data.vaccines.map((v) => (
            <li key={v.name}><span dir="auto">{v.name}</span> <span className="muted" dir="ltr">{v.dates.join(", ")}</span></li>
          ))}</ul>
        )}
      </div>
    </div>
  );
}

// the whole record as a FHIR file (the standard hospital systems exchange); opening it is written to the access log
function DownloadRecord({ p }) {
  const tr = useTr();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const download = async () => {
    setBusy(true);
    setNote("");
    try {
      const r = await fetch(`/api/fhir/Patient/${p.id}/$everything`, { credentials: "same-origin" });
      if (!r.ok) throw new Error(String(r.status));
      const url = URL.createObjectURL(new Blob([await r.text()], { type: "application/fhir+json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `my-record-${(p.first_name || "me").replace(/[^A-Za-z0-9]/g, "").toLowerCase() || "me"}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setNote(tr("The record could not be downloaded here."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="download-record">
      <button className="btn" onClick={download} disabled={busy}>{tr("Download my record")}</button>
      <span className="small muted">{tr("One file with the whole record in a standard format (FHIR). Keep it private.")}</span>
      {note && <div className="small" style={{ color: "var(--high)" }}>{note}</div>}
    </div>
  );
}
