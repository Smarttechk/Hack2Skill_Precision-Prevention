import React, { useEffect, useState } from "react";
import { api, useApi } from "../api.js";
import { Badge, Banner, CardsTable, ErrorBox, Loading, RiskBar, useMore } from "../components.jsx";
import CareTeamBanner from "../CareTeamBanner.jsx";
import { useT, useTr } from "../i18n.js";
import { checkLabel } from "../labels.js";

const LEVELS = ["Urgent", "High", "Routine"];

export default function Doctor() {
  const { t } = useT();
  const tr = useTr();
  const [levels, setLevels] = useState(["Urgent", "High"]);
  const [region, setRegion] = useState("");
  const [awaiting, setAwaiting] = useState(false);
  const [selected, setSelected] = useState(null);
  const [opened, setOpened] = useState(null);      // a patient opened outside the doctor's own patients (break glass): keep showing them
  const path = `/doctor/list?urgency=${levels.join(",")}&region=${encodeURIComponent(region)}&awaiting_only=${awaiting}&limit=200`;
  const list = useApi(path);
  const overview = useApi("/overview");
  const alerts = useApi("/alerts");
  useEffect(() => {
    if (list.data?.items?.length && selected !== opened && !list.data.items.some((i) => i.id === selected)) setSelected(list.data.items[0].id);
  }, [list.data]); // eslint-disable-line

  const toggle = (l) => setLevels((s) => (s.includes(l) ? s.filter((x) => x !== l) : [...s, l]));
  return (
    <>
      <CareTeamBanner onOpened={(id) => { setOpened(id); setSelected(id); list.reload(); }} />
      {alerts.data?.emergencies?.length > 0 && (
        <div className="error">
          <b>{tr("Emergency call-back needed:")}</b> {tr("{n} person(s) sent emergency words in the chat. They are at the top of the Urgent list. A nurse should call now.", { n: alerts.data.emergencies.length })}
          <ul className="small">{alerts.data.emergencies.map((e) => (
            <li key={e.person + e.time} className="row">
              <span><b>{e.person.slice(0, 8)}</b> {tr("at")} {e.time?.slice(11, 16)}: "{e.text}"</span>
              <button style={{ marginInlineStart: 8 }} onClick={async () => {
                await api.post(`/alerts/emergency/${e.person}/done`);
                alerts.reload(); list.reload();
              }}>{tr("Nurse called back")}</button>
            </li>
          ))}</ul>
        </div>
      )}
      {alerts.data?.alerts?.length > 0 && (
        <details className="card">
          <summary><b>{tr("Team alerts")}</b> <span className="small muted">({tr("{n} latest", { n: alerts.data.alerts.length })})</span></summary>
          <ul className="small">{alerts.data.alerts.map((a, i) => (
            <li key={i}><b>{a.date}</b> {tr(a.kind)}: {tr(a.detail)}{a.repeated ? <span className="muted"> ({tr("seen again {n} more day(s), last on day {d}", { n: a.repeated, d: a.last_seen_day })})</span> : null}</li>
          ))}</ul>
        </details>
      )}
      <div className="card row">
        <span className="small muted">{t.urgency}:</span>
        {LEVELS.map((l) => (
          <label key={l} className="toggle"><input type="checkbox" checked={levels.includes(l)} onChange={() => toggle(l)} /> <Badge urgency={l} /></label>
        ))}
        <span className="small muted" style={{ marginInlineStart: 16 }}>{t.region}:</span>
        <select value={region} onChange={(e) => setRegion(e.target.value)}>
          <option value="">{tr("All")}</option>
          {(overview.data?.regions || []).map((r) => <option key={r} value={r}>{tr(r)}</option>)}
        </select>
        <label className="toggle" style={{ marginInlineStart: 16 }}>
          <input type="checkbox" checked={awaiting} onChange={(e) => setAwaiting(e.target.checked)} /> {t.awaitingOnly}
        </label>
        {list.data && <span className="muted small" style={{ marginInlineStart: "auto" }}>{list.data.total.toLocaleString()} {t.people}</span>}
      </div>
      <div className="split doctor-split">
        <div className="card list-card">
          {list.loading && <Loading />}
          {list.error && <ErrorBox error={list.error} />}
          {list.data && <PeopleList items={list.data.items} selected={selected} onSelect={setSelected} />}
        </div>
        {selected && <PersonPanel pid={selected} />}
      </div>
    </>
  );
}

// one tidy row per person: urgency, who, the main reason on one line, the checks as chips
function PeopleList({ items, selected, onSelect }) {
  const tr = useTr();
  const [shown, more] = useMore(items, 25);
  if (!items.length) return <p className="muted">{tr("Nobody matches these filters.")}</p>;
  return (
    <>
      <div className="plist">
        {shown.map((p) => (
          <button key={p.id} className={`prow ${p.id === selected ? "sel" : ""}`} onClick={() => onSelect(p.id)}>
            <div className="prow-top">
              <Badge urgency={p.urgency} />
              <span className="prow-who">{tr(p.is_child ? "Child" : "Adult")} · {p.age} {tr(p.sex)} · <span className="muted">{tr(p.region)}</span></span>
              <span className="prow-risk">{p.risk_band ? <span className={`band-dot band-${p.risk_band.toLowerCase()}`}>{tr(`${p.risk_band} risk`)}</span>
                : <span className="muted small">{tr(p.is_child ? "rules only" : "no score")}</span>}</span>
            </div>
            <div className="prow-why" title={p.reasons.map((r) => tr(r)).join(tr("; "))}>{p.reasons[0] ? tr(p.reasons[0]) : tr("Routine check due")}</div>
            <div className="prow-meta">
              {p.checks.length > 0 && <span>{tr(checkLabel(p.checks[0]))}{p.checks.length > 1 ? ` +${p.checks.length - 1}` : ""}</span>}
              {p.awaiting_doctor && <span className="meta-teal">{tr("waiting for you")}</span>}
              {!p.may_contact && <span className="meta-warn" title={tr("No contact consent: messages are never sent. Decide how to reach this person, for example at their next visit.")}>{tr("no contact consent")}</span>}
              {p.live_demo && <span title={tr("Changed in the live phone demo")}>{tr("live chat")}</span>}
            </div>
          </button>
        ))}
      </div>
      {more}
      <Banner />
    </>
  );
}

// one condition: a finding replaces the prediction; a known diagnosis is not predicted; otherwise the chance with its
// range (one decimal when small, "about" when the range is narrower than the shown precision) and its own factors
function ConditionRisk({ c, tr }) {
  const name = tr(c.name);
  if (c.status === "found") {
    return <div className="cond found"><b>{name}</b><span>{tr("Found at a visit on day {d}: results review needed", { d: c.day })}</span></div>;
  }
  if (c.status === "known") return <div className="cond quiet"><b>{name}</b><span>{tr("Already diagnosed: not predicted")}</span></div>;
  if (c.status === "none") return null;
  if (c.status === "reading") {
    const raised = c.category && c.category !== "Normal";
    return (
      <div className={`cond ${raised && c.category !== "Elevated" ? "found" : "reading"}`}>
        <b>{name}</b>
        <span>{tr("Last reading:")} <bdi dir="ltr">{c.sbp}/{c.dbp}</bdi>{c.date && <> <bdi dir="ltr">({c.date})</bdi></>} · <b>{tr(c.category)}</b>
          {c.months_ago != null && c.months_ago >= 12 ? ` · ${tr("{n} months ago: a new reading is due", { n: c.months_ago })}` : ""}</span>
        <span className="small muted">{tr(c.note)}</span>
      </div>
    );
  }
  const lo = riskPct(c.low), hi = riskPct(c.high);
  return (
    <div className="cond">
      <div className="cond-row">
        <b className="cond-name">{name}</b>
        {c.band && <span className={`band-dot band-${c.band.toLowerCase()}`}>{tr(c.band)}</span>}
        <span className="cond-pct">{riskPct(c.risk)}</span>
      </div>
      <RiskBar risk={c.risk} low={c.low} high={c.high} bare />
      <div className="cond-note">
        {lo === hi ? tr("about {a}", { a: riskPct(c.risk) }) : tr("likely between {a} and {b}", { a: lo, b: hi })}
        {c.reasons?.length > 0 && <> · {tr("Main factors:")} {c.reasons.map((r) => tr(r)).join(tr(", "))}</>}
      </div>
      {c.inputs_total != null && <div className="cond-note muted">{tr("{n} of {m} inputs on record", { n: c.inputs_recorded, m: c.inputs_total })}</div>}
      {c.note && <div className="cond-note muted">{tr(c.note)}</div>}
    </div>
  );
}

// two minutes in clinic: waist and two lifestyle answers. The records lack them, so the diabetes range is wide; with them it narrows.
function ClinicAnswers({ pid, saved, onSaved, tr, wide }) {
  const [f, setF] = useState({ waist: "", ex: "", veg: "" });
  const [msg, setMsg] = useState(null);
  useEffect(() => { setF({ waist: saved?.waist_cm ?? "", ex: saved?.exercise_daily == null ? "" : String(saved.exercise_daily), veg: saved?.veg_daily == null ? "" : String(saved.veg_daily) }); }, [pid, saved]); // eslint-disable-line
  useEffect(() => { setMsg(null); }, [pid]);                  // the "Saved" line stays until another person is opened
  const yn = (v) => (v === "" ? null : v === "true");
  const save = async () => {
    setMsg(null);
    try {
      await api.post(`/person/${pid}/answers`, { waist_cm: f.waist === "" ? null : Number(f.waist), exercise_daily: yn(f.ex), veg_daily: yn(f.veg) });
      setMsg({ ok: true, text: tr("Saved. The diabetes range is worked out again.") });
      onSaved();
    } catch (e) { setMsg({ ok: false, text: tr(String(e.message || e)) }); }
  };
  return (
    <details className={`pp-more ${wide ? "ask" : ""}`} open={wide || undefined}>
      <summary>{wide ? <b>{tr("The diabetes range is wide: two quick answers narrow it")}</b> : tr("Add waist and lifestyle answers (narrows the diabetes range)")}
        {saved && <span className="chip tag-teal" style={{ marginInlineStart: 8 }}>{tr("added")}</span>}</summary>
      <p className="small muted">{tr("The records do not hold these, so the diabetes range is wide. Two minutes in clinic narrows it. They inform you only: they do not change anyone's priority.")}</p>
      <div className="row">
        <label className="small">{tr("Waist (cm)")} <input type="number" min="40" max="200" value={f.waist} onChange={(e) => setF({ ...f, waist: e.target.value })} style={{ width: 90 }} /></label>
        <label className="small">{tr("30 minutes of activity most days?")}
          <select value={f.ex} onChange={(e) => setF({ ...f, ex: e.target.value })}><option value="">{tr("Not asked")}</option><option value="true">{tr("Yes")}</option><option value="false">{tr("No")}</option></select></label>
        <label className="small">{tr("Vegetables or fruit every day?")}
          <select value={f.veg} onChange={(e) => setF({ ...f, veg: e.target.value })}><option value="">{tr("Not asked")}</option><option value="true">{tr("Yes")}</option><option value="false">{tr("No")}</option></select></label>
        <button className="btn primary" onClick={save} disabled={f.waist === "" && f.ex === "" && f.veg === ""}>{tr("Save")}</button>
      </div>
      {msg && <div className={msg.ok ? "ok small" : "error small"}>{msg.text}</div>}
    </details>
  );
}

// risks under 10% get one decimal, so a narrow range does not read "2% to 2%"
function riskPct(v) {
  if (v === null || v === undefined) return "-";
  return v < 0.1 ? `${(v * 100).toFixed(1)}%` : `${Math.round(v * 100)}%`;
}

function PersonPanel({ pid }) {
  const { t } = useT();
  const tr = useTr();
  const { data, error, loading, reload } = useApi(`/person/${pid}`);
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState(null);
  const [need, setNeed] = useState(null);
  useEffect(() => { setSaved(null); setNeed(null); setNote(""); }, [pid]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const decide = async (decision) => {
    setSaved(null); setNeed(null);
    if (decision === "Disagree" && note.trim().length < 3) { setNeed(tr("Please say why you disagree (a few words): it helps improve the flags.")); return; }
    try {
      const r = await api.post(`/person/${pid}/feedback`, { decision, note });
      setSaved(r.effect); reload();
    } catch (e) { setNeed(tr(String(e.message || e))); }
  };
  const facts = [tr(data.nationality), tr("speaks {l}", { l: tr(data.language) }), tr(data.region),
    tr("{n} min to {f}", { n: Math.round(data.travel_min), f: data.nearest_facility }), tr("{i} plan", { i: tr(data.insurance) })];
  return (
    <div className="card person-panel">
      <div className="pp-head">
        <div>
          <h3>{tr(data.is_child ? "Child" : "Adult")}, {tr("{n} years", { n: data.age })}, {tr(data.sex)}</h3>
          <div className="small muted">{tr("Record {id}", { id: data.short })}</div>
        </div>
        <Badge urgency={data.urgency} />
      </div>
      <div className="facts">{facts.map((f) => <span key={f}>{f}</span>)}</div>

      {data.warning_signs.length > 0 && (
        <section className="pp-sec">
          <h4>{tr("Why {u}", { u: tr(data.urgency ? data.urgency.toLowerCase() : "flagged") })}</h4>
          <ul className="signs">{data.warning_signs.map((s) => <li key={s}>{tr(s)}</li>)}</ul>
        </section>
      )}

      <section className="pp-sec">
        <h4>{tr("Chance of having it now, not yet diagnosed")}</h4>
        {(data.conditions || []).map((c) => <ConditionRisk key={c.condition} c={c} tr={tr} />)}
        {data.normal_result_day != null && (
          <p className="small muted">{tr("Normal result at a visit on day {d}: the next check follows the usual recall.", { d: data.normal_result_day })}</p>
        )}
        {(data.conditions || []).every((c) => c.status === "none") && <p className="muted small">{tr(data.risk_text)}</p>}
        {!data.is_child && (data.conditions || []).some((c) => c.condition === "diabetes" && c.status === "score") && (
          <ClinicAnswers key={pid} pid={pid} saved={data.clinic_answers} onSaved={reload} tr={tr}
            wide={!data.clinic_answers && (data.conditions || []).some((c) => c.condition === "diabetes" && c.status === "score" && c.high - c.low >= 0.08)} />
        )}
      </section>

      <section className="pp-sec">
        <h4>{tr("Pathway cards")}</h4>
        <CardsTable cards={data.cards} />
      </section>

      <details className="pp-more">
        <summary>{tr("Data used and summary")}</summary>
        <p className="small">{data.data_used.map((d) => tr(d)).join(" · ")}</p>
        <p className="small muted">{tr(data.summary)}</p>
      </details>

      <section className="pp-sec decide">
        <h4>{tr("Do you agree with this flag?")}</h4>
        <input type="text" placeholder={t.note} value={note} onChange={(e) => setNote(e.target.value)} style={{ width: "100%", marginBottom: 10 }} />
        <div className="row">
          <button className="btn good" onClick={() => decide("Agree")}>{t.agree}</button>
          <button className="btn bad" onClick={() => decide("Disagree")}>{t.disagree}</button>
        </div>
        {saved && <div className="ok">{tr("Saved.")} {tr(saved)}</div>}
        {need && <div className="error">{need}</div>}
        {data.feedback?.length > 0 && (
          <p className="small muted" style={{ marginTop: 8 }}>{tr("Earlier decisions:")} {data.feedback.map((f) => `${tr(f.decision)} (${f.time})`).join(tr(", "))}</p>
        )}
      </section>
    </div>
  );
}
