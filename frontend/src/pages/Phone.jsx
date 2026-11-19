import React, { useEffect, useRef, useState } from "react";
import { api, useApi } from "../api.js";
import { ErrorBox, Loading } from "../components.jsx";
import { useTr } from "../i18n.js";

const QUICK = ["BOOK", "INFO", "LATER", "STOP", "START", "حجز", "توقف", "call me", "evening"];

export default function Phone() {
  const tr = useTr();
  const people = useApi("/phone/people");
  const [pid, setPid] = useState("");
  const [chat, setChat] = useState(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const bodyRef = useRef(null);

  useEffect(() => { if (people.data?.length && !pid) setPid(people.data[0].id); }, [people.data]); // eslint-disable-line
  useEffect(() => {
    if (!pid) return;
    setErr(null);
    api.post(`/phone/${pid}/start`).then(setChat).catch(setErr);
  }, [pid]);
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight; }, [chat]);

  const send = async (msg) => {
    if (!msg.trim() || busy) return;
    setBusy(true);
    try {
      const r = await api.post(`/phone/${pid}/reply`, { text: msg });
      setChat((c) => ({ ...c, ...r })); setText("");
    } catch (e) { setErr(e); }
    setBusy(false);
  };

  if (people.loading) return <Loading />;
  if (people.error) return <ErrorBox error={people.error} />;
  const s = chat?.status;
  return (
    <div className="split" style={{ gridTemplateColumns: "minmax(0,1fr) 400px" }}>
      <div>
        <div className="card">
          <h3>{tr("The family's phone")}</h3>
          <p className="muted small">{tr("This is what the family receives. Reply the way a parent or patient would: BOOK, INFO, LATER, STOP, START, the Arabic words, a preference such as \"call me\" or \"evening\", or any question. Emergency words (for example \"chest pain\") get the 998 answer.")}</p>
          <div className="field">
            {tr("Person")}
            <select value={pid} onChange={(e) => setPid(e.target.value)}>
              {people.data.map((p) => <option key={p.id} value={p.id}>{tr(p.label)}</option>)}
            </select>
          </div>
          <div className="quick">
            {QUICK.map((q) => <button key={q} className="btn" disabled={busy} onClick={() => send(q)}>{q}</button>)}
          </div>
          <button className="btn" onClick={() => api.post(`/phone/${pid}/start`).then(setChat)}>{tr("Start this conversation again")}</button>
        </div>
        {s && (
          <div className="card">
            <h3>{tr("What the system did")}</h3>
            <table><tbody>
              <tr><td>{tr("State")}</td><td><b>{tr(s.state)}</b></td></tr>
              <tr><td>{tr("Booked for")}</td><td>{s.booked_for || "-"}</td></tr>
              <tr><td>{tr("Care option")}</td><td>{tr(s.care_option) || "-"}</td></tr>
              <tr><td>{tr("May be contacted")}</td><td>{tr(s.may_contact ? "Yes" : "No")}</td></tr>
              <tr><td>{tr("Saved channel preference")}</td><td>{tr(s.channel_preference) || "-"}</td></tr>
              <tr><td>{tr("Saved time preference")}</td><td>{tr(s.time_preference) || "-"}</td></tr>
            </tbody></table>
          </div>
        )}
        {err && <ErrorBox error={err} />}
      </div>
      <div>
        <div className="phone" dir="ltr">
          <div className="phone-top"><b>Abu Dhabi Health Services</b><span>to the {chat?.recipient || "family"} &middot; {chat?.language || ""} &middot; {chat?.channel || ""}</span></div>
          <div className="phone-body" ref={bodyRef}>
            {!chat && <div className="sys">Loading...</div>}
            {chat?.conversation.map((m, i) =>
              m.from === "system"
                ? <div key={i} className="sys">{m.text}</div>
                : <div key={i} className={`bubble ${m.from}`} dir="auto">{m.text}<span className="t">{m.time}</span></div>
            )}
          </div>
          <form className="phone-input" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a reply..." dir="auto" />
            <button type="submit" disabled={busy} aria-label="Send">&#10148;</button>
          </form>
        </div>
      </div>
    </div>
  );
}
