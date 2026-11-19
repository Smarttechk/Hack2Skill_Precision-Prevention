// Outreach staff: today's contact list and the history of who has been contacted. A person leaves every colleague's list as soon
// as a contact is saved with its outcome; someone being contacted right now shows the name of the colleague; the lists refresh
// by themselves so nobody works from an old screen.
import React, { useEffect, useRef, useState } from "react";
import { api, useApi } from "../api.js";
import { Badge, Banner, ErrorBox, Kpi, Loading } from "../components.jsx";
import { useTr } from "../i18n.js";

const REFRESH_MS = 30000;

// reload now and then, and when the tab is shown again, so what colleagues did appears without pressing anything
function useAutoRefresh(reload) {
  const ref = useRef(reload);
  ref.current = reload;
  useEffect(() => {
    const id = setInterval(() => ref.current(), REFRESH_MS);
    const shown = () => { if (document.visibilityState === "visible") ref.current(); };
    document.addEventListener("visibilitychange", shown);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", shown); };
  }, []);
}

export default function Queue() {
  const tr = useTr();
  const [tab, setTab] = useState("todo");
  return (
    <>
      <div className="family-tabs" role="tablist">
        {[["todo", "To contact"], ["visits", "Appointments"], ["history", "Contacted so far"]].map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{tr(label)}</button>
        ))}
      </div>
      {tab === "todo" ? <ToContact /> : tab === "visits" ? <Appointments /> : <Contacted />}
      <Banner />
    </>
  );
}

const VISIT_LABEL = { to_confirm: "To confirm", upcoming: "Later", attended: "Attended", no_show: "Missed", rescheduled: "Moved" };

// booked visits: staff record whether the person came. Attended closes the person; a missed visit puts them back on the contact
// list the next day to rebook (after three missed visits they are escalated); a moved visit returns in a week.
function Appointments() {
  const tr = useTr();
  const [f, setF] = useState("to_confirm");
  const { data, error, loading, reload } = useApi(`/visits?status=${f}&n=100`);
  const [msg, setMsg] = useState(null);
  useAutoRefresh(reload);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorBox error={error} />;
  const record = async (i, outcome) => {
    setMsg(null);
    try {
      const r = await api.post("/visits/outcome", { id: i.id, outcome });
      setMsg({ ok: true, text: r.escalated ? tr("Saved: {o}. This person now needs another way to be reached.", { o: tr(r.outcome) })
        : r.back_at ? tr("Saved: {o}. They come back to the list on {d}.", { o: tr(r.outcome), d: r.back_at.replace("T", " ").slice(0, 16) })
        : tr("Saved: {o}. They are off everyone's list.", { o: tr(r.outcome) }) });
      reload();
    } catch (e) { setMsg({ ok: false, text: tr(String(e.message || e)) }); }
  };
  const c = data.counts;
  return (
    <>
      <div className="grid g4">
        <Kpi label={tr("To confirm")} value={c.to_confirm} delta={tr("booked within a week or already past")} />
        <Kpi label={tr("Attended")} value={c.attended} />
        <Kpi label={tr("Missed")} value={c.no_show} negative delta={tr("back on the contact list to rebook")} />
        <Kpi label={tr("Attendance")} value={data.attendance_pct == null ? "-" : `${data.attendance_pct}%`} delta={tr("of visits recorded")} />
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="row">
            <span className="small muted">{tr("Show:")}</span>
            {["to_confirm", "upcoming", "attended", "no_show", "rescheduled", "all"].map((k) => (
              <button key={k} className={`btn ${f === k ? "primary" : ""}`} onClick={() => setF(k)}>{k === "all" ? tr("All") : tr(VISIT_LABEL[k])}</button>
            ))}
          </div>
          <span className="small muted">{tr("{n} visits", { n: data.total })}</span>
        </div>
        {msg && <div className={msg.ok ? "ok" : "error"} style={{ marginTop: 8 }}>{msg.text}</div>}
        {data.items.length === 0 ? <p className="small muted">{tr("No visits in this list.")}</p> : (
          <div className="table-wrap" style={{ marginTop: 10, maxHeight: 560, overflowY: "auto" }}>
            <table>
              <thead><tr><th>{tr("Date")}</th><th>{tr("Who")}</th><th>{tr("How")}</th><th>{tr("Status")}</th><th>{tr("What happened?")}</th></tr></thead>
              <tbody>{data.items.map((i) => (
                <tr key={i.id}>
                  <td dir="ltr">{i.date}{i.reminder_due && <div><span className="chip tag-teal">{tr("Reminder due")}</span></div>}</td>
                  <td>{i.short}<div className="small muted">{tr(i.who)}, {i.age} y, {tr(i.language)}</div></td>
                  <td>{i.how ? tr(i.how) : "-"}</td>
                  <td><span className={`chip ${i.status === "attended" ? "tag-teal" : i.status === "no_show" ? "warn-chip" : ""}`}>{tr(VISIT_LABEL[i.status] || i.status)}</span></td>
                  <td className="row">
                    <button className="btn good" onClick={() => record(i, "attended")}>{tr("Attended")}</button>
                    <button className="btn bad" onClick={() => record(i, "no_show")}>{tr("Missed")}</button>
                    <button className="btn" onClick={() => record(i, "rescheduled")}>{tr("Moved")}</button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        <p className="small muted">{tr("Recording what happened keeps the follow-up honest: a missed visit is never lost, the person comes back to the contact list to rebook, and the totals the programme sees count only recorded visits.")}</p>
      </div>
    </>
  );
}

function ContactForm({ item, channels, outcomes, onDone, onCancel }) {
  const tr = useTr();
  const [channel, setChannel] = useState(channels.includes(item.planned_channel) ? item.planned_channel : channels[0]);
  const [outcome, setOutcome] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState(null);
  const [sent, setSent] = useState(null);
  const canSend = channel === "SMS" || channel === "WhatsApp";
  const send = async () => {
    setErr(null); setSent(null);
    try { setSent(await api.post("/queue/send", { id: item.id, channel })); } catch (e) { setErr(tr(String(e.message || e))); }
  };
  const save = async () => {
    setErr(null);
    try {
      const r = await api.post("/queue/contact", { id: item.id, channel, outcome, note });
      onDone(r.escalated ? tr("Saved: {o}. This person now needs another way to be reached.", { o: tr(r.outcome) })
        : r.back_at ? tr("Saved: {o}. They come back to the list on {d}.", { o: tr(r.outcome), d: r.back_at.replace("T", " ").slice(0, 16) })
        : tr("Saved: {o}. They are off everyone's list.", { o: tr(r.outcome) }));
    } catch (e) { setErr(tr(String(e.message || e))); }
  };
  const letGo = async () => { try { await api.post("/queue/release", { id: item.id }); } catch { /* already free */ } onCancel(); };
  return (
    <div className="contact-form">
      <label className="small">{tr("How did you contact them?")}
        <select value={channel} onChange={(e) => setChannel(e.target.value)}>{channels.map((c) => <option key={c} value={c}>{tr(c)}</option>)}</select>
      </label>
      <label className="small">{tr("What happened?")}
        <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
          <option value="">{tr("Choose")}</option>{Object.entries(outcomes).map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
        </select>
      </label>
      <label className="small grow">{tr("Note (optional)")}
        <input value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder={tr("For example: prefers mornings")} dir="auto" />
      </label>
      {canSend && (
        <div className="small" style={{ flexBasis: "100%" }}>
          <button type="button" className="btn" onClick={send}>{tr("Send the approved message by {c}", { c: tr(channel) })}</button>{" "}
          {sent && <span className={sent.status === "failed" ? "error" : "ok"}>
            {sent.status === "sent" ? tr("Message sent in {l}.", { l: tr(sent.language) })
              : sent.status === "dry run" ? tr("Practice only: nothing was sent, because messaging is not set up yet.") : tr(sent.detail)}</span>}
        </div>
      )}
      <div className="row">
        <button className="btn primary" onClick={save} disabled={!outcome}>{tr("Save contact")}</button>
        <button className="btn" onClick={letGo}>{tr("Let go")}</button>
      </div>
      {err && <div className="small" style={{ color: "var(--urgent)", flexBasis: "100%" }}>{err}</div>}
    </div>
  );
}

function ToContact() {
  const tr = useTr();
  const [n, setN] = useState(30);
  const { data, error, loading, reload } = useApi(`/queue?n=${n}`);
  const [open, setOpen] = useState(null);
  const [msg, setMsg] = useState(null);
  useAutoRefresh(reload);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorBox error={error} />;
  const start = async (i) => {
    setMsg(null);
    try { await api.post("/queue/claim", { id: i.id }); setOpen(i.id); } catch (e) { setMsg({ ok: false, text: tr(String(e.message || e)) }); }
    reload();
  };
  return (
    <>
      <div className="grid g4">
        <Kpi label={tr("Waiting for contact")} value={data.waiting.toLocaleString()} />
        <Kpi label={tr("Urgent")} value={data.urgent} negative />
        <Kpi label={tr("Contacted today")} value={data.contacted_today} delta={tr("by everyone in your team")} />
        <Kpi label={tr("Escalated")} value={data.escalated} delta={tr("need another way to be reached")} />
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="row">
            <span className="small muted">{tr("Calls for today:")}</span>
            {[20, 30, 60, 120].map((x) => <button key={x} className={`btn ${x === n ? "primary" : ""}`} onClick={() => setN(x)}>{x}</button>)}
          </div>
          <span className="small muted">{tr("This list refreshes by itself every 30 seconds.")}</span>
        </div>
        {msg && <div className={msg.ok ? "ok" : "error"} style={{ marginTop: 8 }}>{msg.text}</div>}
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table>
            <thead><tr><th>{tr("Urgency")}</th><th>{tr("Who")}</th><th>{tr("How and when")}</th><th>{tr("About")}</th><th>{tr("Contact within")}</th><th></th></tr></thead>
            <tbody>
              {data.items.map((i) => (
                <React.Fragment key={i.id}>
                  <tr style={i.claimed_by ? { opacity: 0.6 } : {}}>
                    <td><Badge urgency={i.urgency} /></td>
                    <td>{tr(i.contact)}<div className="small muted">{i.short}</div>
                      {i.missed_visit && <div><span className="chip warn-chip">{tr("Missed visit: rebook")}</span></div>}</td>
                    <td>{tr(i.channel)} · {tr(i.language)}<div className="small muted">{tr("best time {t}", { t: i.best_time })}</div></td>
                    <td>{tr(i.about)}<div className="small muted">{tr(i.care_option)}</div></td>
                    <td>{tr("{n} days", { n: i.deadline_days })}{i.needs_doctor && <div><span className="chip tag-teal">{tr("doctor first")}</span></div>}
                      {i.tries > 0 && <div className="small muted">{tr("{n} tries so far", { n: i.tries })}</div>}</td>
                    <td>
                      {i.claimed_by ? <span className="chip warn-chip">{tr("Being contacted by {n} until {t}", { n: i.claimed_by, t: i.claimed_until })}</span>
                        : open === i.id ? <span className="small muted">{tr("In progress")}</span>
                        : <button className="btn primary" onClick={() => (i.claimed_by_me ? setOpen(i.id) : start(i))}>{i.claimed_by_me ? tr("Continue") : tr("Contact")}</button>}
                    </td>
                  </tr>
                  {open === i.id && (
                    <tr><td colSpan={6}>
                      <ContactForm item={i} channels={data.channels} outcomes={data.outcomes}
                        onDone={(text) => { setOpen(null); setMsg({ ok: true, text }); reload(); }} onCancel={() => { setOpen(null); reload(); }} />
                    </td></tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted">{tr("Messages for a child always go to the guardian. \"Doctor first\" cases are contacted only after a doctor agrees.")}</p>
      </div>
    </>
  );
}

function Outbox() {
  const tr = useTr();
  const { data } = useApi("/admin/outbox?limit=15");
  if (!data) return null;
  return (
    <div className="card">
      <h3>{tr("Messages sent from the queue")}</h3>
      <p className="small muted">{data.mode === "live" ? tr("Messaging is live. No message text or phone number is kept here.") : tr("Practice mode: messaging is not set up, so nothing leaves this computer. The set-up is described in the README.")}</p>
      {data.rows.length === 0 ? <p className="small muted">{tr("No messages yet.")}</p> : (
        <div className="table-wrap"><table className="small">
          <thead><tr><th>{tr("When")}</th><th>{tr("Staff")}</th><th>{tr("Person")}</th><th>{tr("How")}</th><th>{tr("Result")}</th></tr></thead>
          <tbody>{data.rows.map((r, i) => (
            <tr key={i}><td dir="ltr">{r.time.replace("T", " ").slice(0, 16)}</td><td>{r.staff}</td><td>{r.record}</td><td>{tr(r.channel)}</td><td>{tr(r.status)}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}

function Contacted() {
  const tr = useTr();
  const [f, setF] = useState({ staff: "", outcome: "", days: 0, mine: "" });
  const { data, error, loading, reload } = useApi(`/queue/contacts?staff=${encodeURIComponent(f.staff)}&outcome=${f.outcome}&days=${f.days}&mine_only=${f.mine}`);
  useAutoRefresh(reload);
  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorBox error={error} />;
  const when = (s) => s.replace("T", " ").slice(0, 16);
  return (
    <>
      {data.escalated.length > 0 && (
        <div className="card warn-card">
          <h3>{tr("Need another way to be reached")} ({data.escalated.length})</h3>
          <p className="small muted">{tr("Three calls without an answer, or a wrong number. They are off the daily list: a health worker visit or the doctor can reach them.")}</p>
          <div className="table-wrap"><table className="small"><tbody>
            {data.escalated.map((e, i) => (
              <tr key={i}><td>{e.mrn}</td><td>{e.age} y, {tr(e.language)}</td><td>{tr(e.why)}</td><td>{when(e.time)} · {e.staff || tr("Earlier tick")}</td></tr>
            ))}
          </tbody></table></div>
        </div>
      )}
      {data.by_staff.length > 0 && (
        <div className="card">
          <h3>{tr("Team activity")}</h3>
          <p className="small muted">{tr("What each staff member did in the period below. Pick a name to see exactly who they contacted.")}</p>
          <div className="table-wrap"><table>
            <thead><tr><th>{tr("Staff")}</th><th>{tr("Contacts")}</th><th>{tr("People reached")}</th><th>{tr("Agreed to book")}</th><th>{tr("No answer")}</th><th>{tr("Wrong number")}</th><th>{tr("Visits recorded")}</th><th>{tr("Last activity")}</th></tr></thead>
            <tbody>{data.by_staff.map((s) => (
              <tr key={s.user} style={f.staff === s.user ? { background: "var(--soft, #e4f1f3)" } : {}}>
                <td><button type="button" className="linklike" onClick={() => setF({ ...f, staff: f.staff === s.user ? "" : s.user })}><b>{s.name}</b></button></td>
                <td>{s.contacts}</td><td>{s.reached}</td><td>{s.agreed}</td><td>{s.no_answer}</td><td>{s.wrong_number}</td><td>{s.visits_recorded}</td>
                <td className="small" dir="ltr">{when(s.last)}</td>
              </tr>
            ))}</tbody>
          </table></div>
        </div>
      )}
      <div className="card">
        <h3>{data.own ? tr("My contacts") : tr("Who contacted whom")} ({data.total})</h3>
        {data.role === "outreach" ? (
          <div className="row" style={{ marginBottom: 8 }}>
            <button type="button" className={`btn ${data.own ? "primary" : ""}`} onClick={() => setF({ ...f, mine: "1" })}>{tr("My contacts")}</button>
            <button type="button" className={`btn ${!data.own ? "primary" : ""}`} onClick={() => setF({ ...f, mine: "0", staff: "" })}>{tr("Whole team")}</button>
            <span className="small muted">{data.own ? tr("Only the people you contacted, signed in as {n}.", { n: data.me.name }) : tr("Everyone on the team, so nobody is called twice.")}</span>
          </div>
        ) : <p className="small muted">{tr("Every contact, with the staff member who made it. Use the filters or pick a name above.")}</p>}
        <div className="row">
          {!data.own && <select value={f.staff} onChange={(e) => setF({ ...f, staff: e.target.value })} aria-label={tr("Staff")}>
            <option value="">{tr("All staff")}</option>{data.staff.map((s) => <option key={s.user} value={s.user}>{s.name}</option>)}
          </select>}
          <select value={f.outcome} onChange={(e) => setF({ ...f, outcome: e.target.value })} aria-label={tr("Result")}>
            <option value="">{tr("All results")}</option>{Object.entries(data.outcomes).map(([k, label]) => <option key={k} value={k}>{tr(label)}</option>)}
          </select>
          <select value={f.days} onChange={(e) => setF({ ...f, days: Number(e.target.value) })} aria-label={tr("Period")}>
            <option value={1}>{tr("Today")}</option><option value={7}>{tr("Last 7 days")}</option><option value={30}>{tr("Last 30 days")}</option><option value={0}>{tr("All time")}</option>
          </select>
        </div>
        <div className="row" style={{ margin: "8px 0" }}>{Object.entries(data.counts).map(([label, n]) => <span key={label} className="chip">{tr(label)}: {n}</span>)}</div>
        {data.rows.length === 0 ? <p className="small muted">{tr("No contacts yet.")}</p> : (
          <div className="table-wrap" style={{ maxHeight: 520, overflowY: "auto" }}>
            <table>
              <thead><tr><th>{tr("When")}</th><th>{tr("Staff")}</th><th>{tr("Person")}</th><th>{tr("How")}</th><th>{tr("Result")}</th></tr></thead>
              <tbody>{data.rows.map((r, i) => (
                <tr key={i}>
                  <td className="small" dir="ltr">{when(r.time)}</td>
                  <td>{r.staff === "Earlier tick" ? tr(r.staff) : r.staff}</td>
                  <td>{r.mrn}<div className="small muted">{r.child ? tr("Child") : tr("Adult")}, {r.age} y, {tr(r.language)}</div></td>
                  <td>{r.channel ? tr(r.channel) : "-"}</td>
                  <td>{tr(r.outcome_label)}{r.note && <div className="small muted" dir="auto">{r.note}</div>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </div>
      {data.role !== "outreach" && <Outbox />}
    </>
  );
}
