import React, { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ErrorBar, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { pct, useApi } from "../api.js";
import { ErrorBox, Kpi, Loading, SourceNote } from "../components.jsx";
import { C, NEW, OLD } from "../theme.js";
import { useTr } from "../i18n.js";

const STATUS = {   // where each number comes from: sage = published, slate = calibrated, grey = design, ochre = assumption
  published: ["Published", "routine"], calibrated: ["Calibrated to published", "slate"],
  design: ["Design choice", "line"], assumption: ["Assumption (no published value)", "high"],
};
const statusStyle = (s) => {
  const k = STATUS[s]?.[1] || "line";
  return k === "line" ? { "--b": "var(--ink-2)", "--b-soft": "var(--line-2)" }
    : k === "slate" ? { "--b": "var(--slate-dark)", "--b-soft": "var(--slate-soft)" } : { "--b": `var(--${k})`, "--b-soft": `var(--${k}-soft)` };
};
// readable names for the group switch and the follow-up actions
const GROUP_LABEL = { nationality: "Nationality", region: "Region", language: "Language", insurance: "Insurance", sex: "Sex", age_band: "Age group" };
const groupLabel = (g) => GROUP_LABEL[g] || g;
const ACTION_LABEL = { switch_channel: "Switch channel", reminder_same_channel: "Reminder, same channel", community_health_worker: "Community health worker" };
const condName = (c) => (c === "diabetes" ? "Diabetes" : "High blood pressure");
// follow-up learner: rates of the three actions, a near tie (top two within 3 points) and groups with few tries
const FEW_TRIES = 60;
const rates = (r) => [r.success_reminder_same_channel, r.success_switch_channel, r.success_community_health_worker].filter((x) => typeof x === "number").sort((a, b) => b - a);
const closeCall = (r) => { const s = rates(r); return s.length > 1 && s[0] - s[1] <= 0.03; };
function learnSummary(rows, tr) {
  const by = {};
  rows.filter((r) => !closeCall(r) && r.tries >= FEW_TRIES).forEach((r) => {
    const a = tr(ACTION_LABEL[r.best_action] || r.best_action);
    (by[a] = by[a] || []).push(`${tr(r.language)} ${tr(r.area_type)}${r.child ? ` (${tr("children")})` : ""}`);
  });
  return Object.entries(by).map(([a, g]) => `${a}: ${g.join(tr(", "))}`).join(tr("; "));
}

// calibration note: sample-size warning first, then how far the average prediction is from what was observed
function calNote(r, tr) {
  if (r.reliable && r.reliable !== "OK") return tr(r.reliable);
  const gaps = [["diabetes", "Diabetes"], ["hypertension", "Blood pressure"]].map(([k, name]) => {
    const p = r[`${k}_predicted`], o = r[`${k}_observed`];
    if (typeof p !== "number" || typeof o !== "number") return null;
    const d = Math.round((p - o) * 100);
    if (Math.abs(d) < 5) return null;
    return tr(d > 0 ? "{name} over-predicted by {n} pts" : "{name} under-predicted by {n} pts", { name: tr(name), n: Math.abs(d) });
  }).filter(Boolean);
  return gaps.length ? <span style={{ color: "var(--high)" }}>{gaps.join(tr("; "))}</span> : tr("Close");
}

// Challenge requirement 4: false positives, false negatives, missed hidden cases and calibration, by group
const num = (v) => (typeof v === "number" ? v : null);
function Safeguards({ data, group, setGroup }) {
  const tr = useTr();
  const s = data.safeguards_new, so = data.safeguards_old, all = s.all[0], allOld = so?.all?.[0];
  const rows = s[group] || [];
  const oldBy = Object.fromEntries((so?.[group] || []).map((r) => [r.group, r]));
  const byc = s.by_condition || [];
  const gap = data.missed_case_gap_between_groups?.[group];
  const cell = (v, digits = 0) => (num(v) === null ? <span className="muted">{v === null ? tr("too few") : tr(String(v))}</span> : pct(v, digits));
  const range = (a, b) => tr("{a} to {b}", { a: pct(a), b: pct(b) });
  const gl = tr(groupLabel(group));
  const hasBp = rows.some((r) => typeof r.hypertension_predicted === "number");     // high blood pressure has no predicted score now
  const conds = data.risk_model?.conditions || {};
  const auc = (k) => conds[k]?.auc_test_2021 ?? "-";
  // what picking people at random would give: the share of true cases among the people scored
  const atRandom = (cases, scored) => (scored ? (100 * cases / scored).toFixed(1) : "-");
  const lifts = byc.filter((c) => typeof c.cases_per_100_flagged === "number" && c.observed)
    .map((c) => ({ name: tr(condName(c.condition)), got: c.cases_per_100_flagged, rnd: +(100 * c.observed).toFixed(1) }));
  const noBetter = lifts.length > 0 && lifts.every((l) => l.got < 1.2 * l.rnd);
  return (
    <details className="card sources">
      <summary><h3 style={{ display: "inline" }}>{tr("Safeguards: errors and missed cases by group")}</h3>
        <span className="small muted"> · {tr("{n} hidden cases still missed", { n: all.hidden_missed })}{noBetter ? ` · ${tr("the risk score is no better than chance on this data")}` : ""}</span></summary>
      <div className="row" style={{ justifyContent: "space-between", margin: "10px 0" }}>
        <p className="small muted" style={{ margin: 0 }}>{tr("Checked against the hidden truth, which the system never sees. A true case is {d}.", { d: tr(s.definitions.true_case) })}</p>
        <select value={group} onChange={(e) => setGroup(e.target.value)}>
          {Object.keys(s).filter((g) => !["all", "definitions", "by_condition"].includes(g)).map((g) => <option key={g} value={g}>{tr(groupLabel(g))}</option>)}
        </select>
      </div>
      <div className="grid g4" style={{ margin: "12px 0" }}>
        <Kpi label={tr("Flagged High (people scored)")} value={pct(all.flag_rate)} delta={tr("{a} of {b} people", { a: all.flagged_high, b: all.scored })} />
        <Kpi label={tr("False positives (flagged, not a case)")} value={pct(all.false_positive_rate)} delta={tr("{n} true cases per 100 flagged ({b} if picked at random)", { n: all.cases_per_100_flagged, b: atRandom(all.true_cases, all.scored) })} />
        <Kpi label={tr("False negatives (cases not flagged)")} value={pct(all.false_negative_rate)} negative
          delta={all.fnr_range ? tr("range {r}", { r: range(all.fnr_range[0], all.fnr_range[1]) }) : "-"} />
        <Kpi label={tr("Hidden cases found by the visits")} value={pct(all.hidden_found_share)}
          delta={allOld ? tr("usual care {a}; {n} still missed", { a: pct(allOld.hidden_found_share), n: all.hidden_missed }) : tr("{n} still missed", { n: all.hidden_missed })} />
      </div>
      {byc.length > 0 && (
        <div className="table-wrap" style={{ marginBottom: 14 }}>
          <table>
            <thead><tr><th>{tr("Condition")}</th><th>{tr("Scored")}</th><th>{tr("Undiagnosed now")}</th><th>{tr("Predicted (mean)")}</th><th>{tr("Flagged High")}</th>
              <th>{tr("False positive")}</th><th>{tr("False negative")}</th><th>{tr("Cases per 100 flagged")}</th><th>{tr("If picked at random")}</th></tr></thead>
            <tbody>{byc.map((c) => (
              <tr key={c.condition}><td><b>{tr(condName(c.condition))}</b></td><td>{c.scored}</td>
                <td>{cell(c.observed, 1)}</td><td>{cell(c.predicted, 1)}</td><td>{cell(c.flag_rate)}</td><td>{cell(c.false_positive_rate)}</td>
                <td>{cell(c.false_negative_rate)}</td><td>{c.cases_per_100_flagged ?? "-"}</td><td className="muted">{c.observed != null ? (100 * c.observed).toFixed(1) : "-"}</td></tr>
            ))}</tbody>
          </table>
          {lifts.length > 0 && (
            <p className={noBetter ? "warn small" : "small"} style={{ marginTop: 8 }}>
              {tr("Cases per 100 flagged against picking people at random: {list}.", { list: lifts.map((l) => tr("{name} {a} vs {b}", { name: l.name, a: l.got, b: l.rnd })).join(tr("; ")) })}{" "}
              {noBetter
                ? tr("On this data flagging High is no better than chance, so the risk score is not what finds the hidden cases here: care gaps, warning signs and family pathways are.")
                : tr("Flagging High finds more cases than picking at random.")}
            </p>
          )}
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead><tr><th>{gl}</th><th>{tr("People")}</th><th>{tr("Scored")}</th><th>{tr("True cases")}</th><th>{tr("Flagged High")}</th><th>{tr("False positive")}</th>
            <th>{tr("False negative")}</th><th>{tr("Hidden cases not scored")}</th><th>{tr("Hidden cases found")}</th><th>{tr("Usual care found")}</th><th>{tr("Note")}</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.group}>
              <td>{tr(String(r.group))}</td><td>{r.count}</td><td>{r.scored}</td><td>{r.true_cases}</td><td>{cell(r.flag_rate)}</td>
              <td>{cell(r.false_positive_rate)}</td>
              <td>{cell(r.false_negative_rate)}{Array.isArray(r.fnr_range) && <div className="small muted">{range(r.fnr_range[0], r.fnr_range[1])}</div>}</td>
              <td>{r.cases_not_scored}</td><td>{cell(r.hidden_found_share)}</td><td>{cell(oldBy[r.group]?.hidden_found_share)}</td>
              <td className="small">{tr(r.reliable)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {gap != null && <p className="small">{tr("Largest gap in hidden cases found between groups ({g}):", { g: gl })} <b>{tr("{n} points", { n: Math.round(gap * 100) })}</b>.
        {gap >= 0.1 && ` ${tr("Groups that fall behind are moved up the outreach queue automatically; this gap is the one to watch.")}`}</p>}
      <p className="small muted">{tr("Children and adults with too little data are not given a risk score: their hidden cases are counted under \"hidden cases not scored\". Rates from fewer than 10 people or cases are not shown.")}</p>
      <h3 style={{ marginTop: 18 }}>{tr("Local calibration by group ({g})", { g: gl })}</h3>
      <p className="small muted">{tr("Each model's average predicted chance against the observed rate of undiagnosed cases in the same people (closer is better; 5 points or more apart is marked).")}</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>{gl}</th><th>{tr("Diabetes predicted")}</th><th>{tr("Diabetes observed")}</th>
            {hasBp && <><th>{tr("High blood pressure predicted")}</th><th>{tr("High blood pressure observed")}</th></>}<th>{tr("Note")}</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.group}><td>{tr(String(r.group))}</td><td>{cell(r.diabetes_predicted, 1)}</td><td>{cell(r.diabetes_observed, 1)}</td>
              {hasBp && <><td>{cell(r.hypertension_predicted, 1)}</td><td>{cell(r.hypertension_observed, 1)}</td></>}<td className="small">{calNote(r, tr)}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </details>
  );
}

// display names in the sources table
const groupName = (g) => (g === "Synthetic data" ? "Population model" : g);
const plain = (s) => String(s).replace(/\bSynthea\b/g, "the patient records").replace(/config\/synthetic_data\.json/g, "the population settings");
const nice = (s) => s.replace(/_/g, " ").replace(/\busd\b/g, "USD").replace(/\baed\b/g, "AED").replace(/^./, (c) => c.toUpperCase());
const show = (v) => (v !== null && typeof v === "object"
  ? Object.entries(v).map(([k, x]) => `${k} ${x !== null && typeof x === "object" ? (Array.isArray(x) ? x.join("/") : show(x)) : x}`).join(", ")
  : String(v));

// the sensitivity analysis in plain words: which harder settings were tried and what the worst one still gives
function WorstCase({ w, f }) {
  const tr = useTr();
  const harder = (x) => [
    x.telehealth_boost === 0 && tr("no benefit from telehealth"),
    x.reply_mult < 1 && tr("{n}% fewer replies", { n: Math.round((1 - x.reply_mult) * 100) }),
    x.doctor_agree != null && x.doctor_agree < 0.8 && tr("doctors agreeing with only {p} of flags", { p: pct(x.doctor_agree) }),
    x.clinic_slots_per_day != null && x.clinic_slots_per_day < 40 && tr("only {n} clinic slots a day", { n: x.clinic_slots_per_day }),
    x.old_followup_rate != null && x.old_followup_rate > 0.9 && tr("usual care following up {p} of results", { p: pct(x.old_followup_rate) }),
  ].filter(Boolean);
  const setting = (x) => tr("Setting: {s}.", { s: harder(x).join(tr(", ")) || tr("the least favourable values") });
  return (
    <details className="card sources">
      <summary><h3 style={{ display: "inline" }}>{tr("How sure are we? 64 harder settings tested")}</h3>
        <span className="small muted"> · {tr("even the worst setting finds {n} more hidden cases than usual care", { n: Math.round(w.extra_found) })}</span></summary>
      <p className="small muted" style={{ marginTop: 10 }}>{tr("Every uncertain number was set to its less favourable value, alone and together, and the whole comparison re-run.")}</p>
      <div className="grid g2" style={{ marginTop: 10 }}>
        <div className="kpi">
          <div className="label">{tr("Worst setting for finding cases")}</div>
          <div className="value">+{Math.round(w.extra_found)}</div>
          <div className="delta">{tr("more hidden cases found, and {n} more people with completed care, than usual care", { n: Math.round(w.extra_people_completed) })}</div>
          <p className="small muted" style={{ marginBottom: 0 }}>{setting(w)}</p>
        </div>
        {f && (
          <div className="kpi">
            <div className="label">{tr("Worst setting for result follow-up")}</div>
            <div className="value" style={{ color: f.followup_gap >= 0 ? "var(--primary)" : "var(--high)" }}>{pct(f.followup_new)}</div>
            <div className={`delta ${f.followup_gap < 0 ? "neg" : ""}`}>{tr("followed up within 30 days, against {p} for usual care", { p: pct(f.followup_old) })}</div>
            <p className="small muted" style={{ marginBottom: 0 }}>{setting(f)} {tr("When usual care is at its best, it can match this system on follow-up; the clinic's own result handling keeps running and the nurse call is added on top.")}</p>
          </div>
        )}
      </div>
    </details>
  );
}

function Sources({ rows, check }) {
  const tr = useTr();
  const [only, setOnly] = useState("");
  const counts = rows.reduce((a, r) => ({ ...a, [r.status]: (a[r.status] || 0) + 1 }), {});
  const kinds = Object.keys(STATUS).filter((s) => counts[s]);
  return (
    <details className="card sources">
      <summary><h3 style={{ display: "inline" }}>{tr("Where every number comes from")}</h3>
        <span className="small muted"> · {tr("{n} values:", { n: rows.length })} {kinds.map((s) => `${counts[s]} ${tr(STATUS[s][0])}`).join(tr(", "))}</span></summary>
      <div className="row" style={{ margin: "12px 0 8px" }}>
        <button className={`btn ${only === "" ? "primary" : ""}`} onClick={() => setOnly("")}>{tr("All")} ({rows.length})</button>
        {kinds.map((s) => (
          <button key={s} className={`btn ${only === s ? "primary" : ""}`} onClick={() => setOnly(s)}>{tr(STATUS[s][0])} ({counts[s]})</button>
        ))}
      </div>
      {check && (
        <p className="small">{tr("Calibration check, usual care: attended {a} of booked visits in this run", { a: pct(check.usual_care_attendance) })}
          {check.usual_care_attendance_6_runs ? ` ${tr("and {b} over the 6 calibration runs", { b: pct(check.usual_care_attendance_6_runs, 1) })}` : ""}
          {" "}{tr("(published {p}), {o} opted out (published {q}). One run has only about 450 usual-care visits, so it can be several points off.", {
            p: pct(check.usual_care_attendance_target), o: pct(check.usual_care_opt_out, 1), q: pct(check.usual_care_opt_out_target, 1) })}</p>
      )}
      <p className="small muted">{tr("Names and sources are kept in English, as published.")}</p>
      <div className="table-wrap"><table className="sources-table">
        <colgroup><col style={{ width: "26%" }} /><col style={{ width: "16%" }} /><col style={{ width: "14%" }} /><col /></colgroup>
        <thead><tr><th>{tr("Number")}</th><th>{tr("Value")}</th><th>{tr("Status")}</th><th>{tr("Source")}</th></tr></thead>
        <tbody>{rows.filter((r) => !only || r.status === only).map((r) => (
          <tr key={r.group + r.name}>
            <td dir="ltr"><span className="small muted">{groupName(r.group)}</span><div>{nice(r.name)}</div></td>
            <td className="val" dir="ltr"><b>{shortValue(r.value)}</b></td>
            <td><span className="badge" style={statusStyle(r.status)}>{tr(STATUS[r.status]?.[0] || r.status)}</span></td>
            <td className="small src" dir="ltr">{plain(r.source)}{r.url && <> &middot; <a href={r.url} target="_blank" rel="noreferrer">link</a></>}</td>
          </tr>
        ))}</tbody>
      </table></div>
      <p className="small muted">{tr("Assumptions are numbers with no published value. They are kept visible here and varied in the sensitivity analysis rather than hidden.")}</p>
    </details>
  );
}

// long lists and settings shown briefly in the table (the full value is in config/)
function shortValue(v) {
  const s = show(v);
  return s.length > 60 ? `${s.slice(0, 57)}…` : s;
}

export default function Results() {
  const tr = useTr();
  const { data, error, loading } = useApi("/results");
  const [group, setGroup] = useState("nationality");
  const [measure, setMeasure] = useState("completed");
  const series = useMemo(() => {
    if (!data) return [];
    const byDay = {};
    data.daily.forEach((d) => { byDay[d.day] = byDay[d.day] || { day: d.day }; byDay[d.day][d.way] = d[measure]; });
    return Object.values(byDay);
  }, [data, measure]);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const aed = (v) => tr("{n} AED", { n: Math.round(v).toLocaleString() });
  const n = data.new, o = data.old, cv = data.clinical_value;
  const sn = cv.share_of_hidden_cases_found_new, so = cv.share_of_hidden_cases_found_old;
  const endKey = Object.keys(sn).find((k) => k.startsWith("found_by_day_") && k !== "found_by_day_30");
  const pts = (a, b) => tr("{d} points vs usual care ({b})", { d: `${a - b >= 0 ? "+" : ""}${Math.round((a - b) * 100)}`, b: pct(b) });
  const fair = (data.fairness_new[group] || []).map((r) => ({
    group: r.group, label: tr(String(r.group)), uptake: typeof r.uptake === "number" ? r.uptake : null,
    err: typeof r.uptake === "number" && Array.isArray(r.uptake_range) ? [r.uptake - r.uptake_range[0], r.uptake_range[1] - r.uptake] : [0, 0],
    count: r.count, range: r.uptake_range, reliable: r.reliable,
  }));
  // pairs of groups whose 95% ranges do not overlap: a gap that is unlikely to be chance
  const gaps = [];
  fair.filter((f) => Array.isArray(f.range)).forEach((a, i, arr) => arr.slice(i + 1).forEach((b) => {
    if (a.range[1] < b.range[0]) gaps.push({ lo: a, hi: b });
    else if (b.range[1] < a.range[0]) gaps.push({ lo: b, hi: a });
  }));
  const w = data.sensitivity_worst_case;
  const nh = data.nhanes, rm = data.risk_model, ns = data.noshow_model;
  // headline: the average over the seed runs when available (one run alone can be several points off)
  const ss = data.seed_spread, sm = ss?.summary;
  const avg = (k, mode) => sm?.[`${k}_${mode}`];
  const uc = (v, runs) => (runs ? tr("usual care {v} (runs {a} to {b})", { v, a: runs[0], b: runs[1] }) : tr("usual care {v}", { v }));
  const runsPct = (k) => (avg(k, "new") ? [pct(avg(k, "new").min), pct(avg(k, "new").max)] : null);
  const foundLabel = tr("Hidden cases found (of {n})", { n: sn.hidden_cases_at_start });
  const headline = sm ? [
    { label: tr("Well-Child care completed"), value: pct(avg("well_child", "new").mean), delta: uc(pct(avg("well_child", "old").mean), runsPct("well_child")) },
    { label: tr("Adult screening completed"), value: pct(avg("adult_screening", "new").mean), delta: uc(pct(avg("adult_screening", "old").mean), runsPct("adult_screening")) },
    { label: foundLabel, value: Math.round(avg("found", "new").mean).toLocaleString(),
      delta: uc(Math.round(avg("found", "old").mean), [Math.round(avg("found", "new").min), Math.round(avg("found", "new").max)]) },
    { label: tr("Results followed up within 30 days"), value: pct(avg("followup", "new").mean), delta: uc(pct(avg("followup", "old").mean), runsPct("followup")) },
  ] : [
    { label: tr("Well-Child care completed"), value: pct(n.well_child.people_rate), delta: pts(n.well_child.people_rate, o.well_child.people_rate) },
    { label: tr("Adult screening completed"), value: pct(n.adult_screening.people_rate), delta: pts(n.adult_screening.people_rate, o.adult_screening.people_rate) },
    { label: foundLabel, value: pct(sn[endKey]), delta: pts(sn[endKey], so[endKey]) },
    { label: tr("Results followed up within 30 days"), value: pct(n.followup_within_30_days), delta: pts(n.followup_within_30_days, o.followup_within_30_days) },
  ];
  const days = cv.avg_days_earlier_when_both_found;
  const techSummary = [
    nh?.headline && tr("diabetes score on real data (NHANES): AUC {d} ({a} to {b})", { d: nh.headline.auc, a: nh.headline.auc_low, b: nh.headline.auc_high }),
  ].filter(Boolean).join(" · ");
  return (
    <>

      <div className="grid g4">
        {headline.map((k) => <Kpi key={k.label} label={k.label} value={k.value} delta={k.delta} negative={k.negative} />)}
      </div>
      <div className="split" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3>{tr("Day by day")}</h3>
            <select value={measure} onChange={(e) => setMeasure(e.target.value)}>
              <option value="completed">{tr("People with completed care")}</option>
              <option value="found">{tr("Abnormal results found")}</option>
              <option value="contacted">{tr("People contacted")}</option>
            </select>
          </div>
          <div dir="ltr">
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={series} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={C.line} />
                <XAxis dataKey="day" label={{ value: tr("day"), position: "insideBottomRight", offset: -2 }} />
                <YAxis />
                <Tooltip labelFormatter={(d) => tr("Day {d}", { d })} />
                <Legend />
                <Line type="monotone" dataKey="This system" name={tr("This system")} stroke={NEW} strokeWidth={3} dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="Usual care" name={tr("Usual care")} stroke={OLD} strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="card">
          <h3>{tr("This system against usual care")}</h3>
          <table>
            <thead><tr><th>{tr("Measure")}</th><th>{tr("This system")}</th><th>{tr("Usual care")}</th></tr></thead>
            <tbody>
              <tr><td>{tr("People contacted")}</td><td>{n.contacted}</td><td>{o.contacted}</td></tr>
              <tr><td>{tr("Messages sent")}</td><td>{n.messages_sent}</td><td>{o.messages_sent}</td></tr>
              <tr><td>{tr("Timely attendance (30 days)")}</td><td>{pct(n.timely_attendance)}</td><td>{pct(o.timely_attendance)}</td></tr>
              <tr><td>{tr("Abnormal results found")}</td><td>{n.abnormal_found}</td><td>{o.abnormal_found}</td></tr>
              <tr><td>{tr("Contacts per case found")}</td><td>{n.contacts_per_case_found}</td><td>{o.contacts_per_case_found}</td></tr>
              <tr><td>{tr("Opt-out rate")}</td><td>{pct(n.opt_out_rate, 1)}</td><td>{pct(o.opt_out_rate, 1)}</td></tr>
              <tr><td>{tr("Attendance of booked visits")}</td><td>{pct(n.attendance_of_booked_visits)}</td><td>{pct(o.attendance_of_booked_visits)}</td></tr>
              <tr><td>{tr("Visits attended")}</td><td>{n.visits_attended}</td><td>{o.visits_attended}</td></tr>
              <tr><td>{tr("Running cost of the period* (outreach + visits)")}</td><td>{aed(data.cost_new.total_cost_aed)}</td><td>{aed(data.cost_old.total_cost_aed)}</td></tr>
              {data.cost_new.cost_per_person_completed_aed != null && <>
                <tr><td>{tr("Cost per person with completed care*")}</td><td>{aed(data.cost_new.cost_per_person_completed_aed)}
                  {data.cost_new.cost_per_person_completed_range_aed && <div className="small muted">{tr("{a} to {b} AED at a {c} to {d} AED visit", {
                    a: Math.round(data.cost_new.cost_per_person_completed_range_aed[0]), b: Math.round(data.cost_new.cost_per_person_completed_range_aed[1]),
                    c: data.prices?.visit_range_aed?.[0], d: data.prices?.visit_range_aed?.[1] })}</div>}</td>
                  <td>{aed(data.cost_old.cost_per_person_completed_aed)}
                  {data.cost_old.cost_per_person_completed_range_aed && <div className="small muted">{tr("{a} to {b} AED", {
                    a: Math.round(data.cost_old.cost_per_person_completed_range_aed[0]), b: Math.round(data.cost_old.cost_per_person_completed_range_aed[1]) })}</div>}</td></tr>
                <tr><td>{tr("Outreach cost per person with completed care*")}</td><td>{aed(data.cost_new.outreach_cost_per_person_completed_aed)}</td><td>{aed(data.cost_old.outreach_cost_per_person_completed_aed)}</td></tr>
              </>}
              <tr><td>{tr("Cost per completed check*")}</td><td>{aed(data.cost_new.cost_per_completed_check_aed)}</td><td>{aed(data.cost_old.cost_per_completed_check_aed)}</td></tr>
            </tbody>
          </table>
          <p className="small muted">{tr("* From published prices: WhatsApp and SMS rates, Azure UAE North server, UAE nurse salary, and a typical Abu Dhabi GP consultation (AED {v}, a price-directory estimate) for every attended visit. This system books many more visits, so it costs more in total; per person with completed care the cost is similar, and outreach is cheaper. Each source is listed at the bottom of this page.", { v: data.prices?.visit_aed })}{" "}
            {tr("Cost parts (this system): {list}.", { list: Object.entries(data.cost_new.parts_aed || {}).map(([k, v]) => `${tr(k)} ${aed(v)}`).join(tr(", ")) })}</p>
          {data.value?.extra_cost_per_extra_case_found_aed != null && (
            <p className="small">{tr("Each extra hidden case found costs {c} more than usual care.", { c: aed(data.value.extra_cost_per_extra_case_found_aed) })}{" "}
              {tr("For scale, one year of diabetes care in the UAE costs about {c} per person (IDF Diabetes Atlas). No published UAE figure gives the saving from finding a case early, so no return on investment is claimed.", { c: aed(data.value.diabetes_cost_per_person_year_aed) })}</p>
          )}
          {typeof days === "number" && (
            <p className="small muted">{days >= 0
              ? tr("For the cases both ways found, this system found them {n} days earlier on average.", { n: Math.abs(days).toFixed(1) })
              : tr("For the cases both ways found, usual care found them {n} days earlier on average: they are the few, easier-to-reach cases its invitations already cover.", { n: Math.abs(days).toFixed(1) })}</p>
          )}
        </div>
      </div>
      {w && <WorstCase w={w} f={data.sensitivity_worst_followup} />}
      <details className="card sources">
        <summary><h3 style={{ display: "inline" }}>{tr("Fairness: uptake by group, with 95% ranges")}</h3>
          <span className="small muted"> · {gaps.length ? tr("a real gap between groups is flagged") : tr("no clear gap between groups")}</span></summary>
        <div className="row" style={{ justifyContent: "space-between", margin: "10px 0" }}>
          <p className="small muted" style={{ margin: 0 }}>{tr("Uptake: people who completed at least one of their recommended checks, out of everyone with an open need.")}</p>
          <select value={group} onChange={(e) => setGroup(e.target.value)}>
            {Object.keys(data.fairness_new).map((g) => <option key={g} value={g}>{tr(groupLabel(g))}</option>)}
          </select>
        </div>
        <div dir="ltr">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={fair.filter((f) => f.uptake !== null)} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={C.line} />
              <XAxis dataKey="label" />
              <YAxis domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <Tooltip formatter={(v) => [pct(v), tr("Uptake")]} />
              <Bar dataKey="uptake" fill={NEW} isAnimationActive={false}><ErrorBar dataKey="err" width={6} stroke={C.ink} /></Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>{tr(groupLabel(group))}</th><th>{tr("People")}</th><th>{tr("Uptake")}</th><th>{tr("Range")}</th><th>{tr("Note")}</th></tr></thead>
            <tbody>{fair.map((f) => (
              <tr key={f.group}><td>{f.label}</td><td>{f.count}</td><td>{f.uptake === null ? tr("Too few to report") : pct(f.uptake)}</td>
                <td>{Array.isArray(f.range) ? tr("{a} to {b}", { a: pct(f.range[0]), b: pct(f.range[1]) }) : "-"}</td><td className="small">{tr(f.reliable)}</td></tr>
            ))}</tbody>
          </table>
        </div>
        <p className={gaps.length ? "warn small" : "small"}>{gaps.length
          ? tr("Real gaps (95% ranges do not overlap): {list}. All other groups overlap.", { list: gaps.map((g) => tr("{a} {p} is below {b} {q}", { a: g.lo.label, p: pct(g.lo.uptake), b: g.hi.label, q: pct(g.hi.uptake) })).join(tr("; ")) })
          : tr("All 95% ranges overlap: no clear gap between these groups.")}</p>
        <p className="small muted">{tr("Groups under 10 people are hidden, plus the next smallest when only one would be hidden; under 30 are marked not reliable.")}</p>
      </details>
      {data.safeguards_new && <Safeguards data={data} group={group} setGroup={setGroup} />}
      {data.followup_learning?.length > 0 && (
        <details className="card sources">
          <summary><h3 style={{ display: "inline" }}>{tr("What works when families do not reply")}</h3>
            <span className="small muted"> · {tr("the best follow-up depends on the language group")}</span></summary>
          <p className="small muted" style={{ marginTop: 10 }}>{tr("When a family does not reply, the system tries a reminder, another channel or a community health worker, and counts a success only when the person then attends the visit. It learns separately for each language, area and age group, and uses what it learns for the next follow-up.")}</p>
          <p className="small">{tr("The best action depends on the language group, not only the area: {list}.", { list: learnSummary(data.followup_learning, tr) })}</p>
          <div className="table-wrap"><table>
            <thead><tr><th>{tr("Language")}</th><th>{tr("Area")}</th><th>{tr("Child")}</th><th>{tr("Tries")}</th><th>{tr("Best action")}</th><th>{tr("Reminder")}</th><th>{tr("Switch channel")}</th><th>{tr("Health worker")}</th></tr></thead>
            <tbody>{data.followup_learning.map((r, i) => (
              <tr key={i}><td>{tr(r.language)}</td><td>{tr(r.area_type)}</td><td>{tr(r.child ? "yes" : "no")}</td><td>{r.tries}</td><td><b>{tr(ACTION_LABEL[r.best_action] || r.best_action)}</b>{closeCall(r) && <span className="small muted"> · {tr("near tie")}</span>}{r.tries < FEW_TRIES && <span className="small muted"> · {tr("few tries")}</span>}</td>
                <td>{pct(r.success_reminder_same_channel)}</td><td>{pct(r.success_switch_channel)}</td><td>{pct(r.success_community_health_worker)}</td></tr>
            ))}</tbody>
          </table></div>
        </details>
      )}
      {(nh || rm?.conditions || ns) && (
        <details className="card sources">
          <summary><h3 style={{ display: "inline" }}>{tr("Technical details: the models")}</h3>
            <span className="small muted"> · {techSummary}</span></summary>
          <div className="grid g3 tech-grid">
          {nh && nh.scores && (
            <div className="card">
              <h3>{tr("Real-data check of the diabetes score")}</h3>
              <p className="small">{tr("{n} real NHANES adults (2015-2023, never told they have diabetes, {c} with undiagnosed diabetes). Nothing is trained: these are the published scores, checked on people they were not tuned to.",
                { n: nh.adults.toLocaleString(), c: nh.cases })}</p>
              <table className="small"><thead><tr><th>{tr("Score")}</th><th>AUC</th><th>{tr("Cases found")}</th></tr></thead>
                <tbody>{nh.scores.map((s) => (
                  <tr key={s.name}><td>{tr(s.name)}{s.source && <div className="muted" title={s.source}>{s.source.split(".")[0]}</div>}</td>
                    <td>{s.auc}{s.auc_low != null && <span className="muted"> ({s.auc_low}-{s.auc_high})</span>}</td>
                    <td>{pct(s.cases_found_share)} {tr("of cases by inviting {p}", { p: pct(s.invited_share) })}</td></tr>
                ))}</tbody></table>
              <p className="small">{(nh.by_cycle || []).map((c) => `${c.cycle}: AUC ${c.auc}`).join(" · ")}</p>
              <p className="small muted">{tr(nh.note)}</p>
            </div>
          )}
          {rm && rm.conditions && (
            <div className="card">
              <h3>{tr("Risk score")}</h3>
              <p className="small">{tr(rm.target)}. {tr(rm.method)}.</p>
              {Object.entries(rm.conditions).map(([k, c]) => (
                <div key={k} className="pathway" style={{ marginBottom: 8 }}>
                  <div className="pathway-top"><b>{tr(condName(k))}</b>
                    <span className="small muted">{tr(c.score)}</span></div>
                  {c.items_used && (
                    <div className="pathway-meta">
                      {tr("Items used: {l}.", { l: c.items_used.map((x) => tr(x)).join(tr(", ")) })}{" "}
                      {tr("Not in the records: {l}.", { l: c.items_not_in_the_records.map((x) => tr(x)).join(tr(", ")) })}{" "}
                      {tr("The range is the spread those items could add.")}{" "}
                      {tr("Local level: {a} (WHO UAE prevalence {b}).", { a: pct(c.local_calibration?.undiagnosed_rate_target, 1), b: pct(c.local_calibration?.who_prevalence, 1) })}
                    </div>
                  )}
                </div>
              ))}
              <p className="small">{tr("High = top 15% of local scores (from {p}).", { p: pct(rm.threshold_high, 1) })}</p>
              <p className="small muted">{tr("Not used as inputs: {a}. Not allowed: {b}.", { a: rm.not_used.map((x) => tr(x)).join(tr(", ")), b: rm.not_allowed_uses.map((x) => tr(x)).join(tr(", ")) })}</p>
            </div>
          )}
        </div>
        </details>
      )}
      {data.sources && <Sources rows={data.sources} check={data.calibration_check} />}
      <SourceNote source={{ kind: data.source || "simulation", runs: ss ? ss.seeds.length : 1 }} />
    </>
  );
}
