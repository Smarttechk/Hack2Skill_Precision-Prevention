// Small shared pieces used across the pages.
import React from "react";
import { pct } from "./api.js";
import { useT, useTr } from "./i18n.js";
import { checkLabel } from "./labels.js";

// colour coding: coral = Urgent, honey = High, green = Routine (soft tint + dot, see .badge in styles.css)
const tone = (name) => ({ "--b": `var(--${name})`, "--b-soft": `var(--${name}-soft)`, "--b-dot": `var(--${name}-dot)` });
const URG = { Urgent: tone("urgent"), High: tone("high"), Routine: tone("routine") };
const GREY = { "--b": "var(--ink-2)", "--b-soft": "var(--line-2)" };

export function Badge({ urgency }) {
  const tr = useTr();
  if (!urgency) return <span className="chip">-</span>;
  return <span className="badge" style={URG[urgency] || GREY}>{tr(urgency)}</span>;
}

export function DecisionBadge({ decision, status }) {
  const tr = useTr();
  const clinical = decision === "Clinician decides";
  const style = status === "done" || status === "on_hold" ? GREY
    : clinical ? { "--b": "var(--slate-dark)", "--b-soft": "var(--slate-soft)" } : { "--b": "var(--primary-dark)", "--b-soft": "var(--primary-soft)" };
  const text = status === "done" ? "Done" : status === "on_hold" ? "On hold" : clinical ? (status === "agreed" ? "Doctor agreed" : "Clinician decides") : "Outreach invite";
  return <span className="badge" style={style}>{tr(text)}</span>;
}

export function RiskBar({ risk, low, high, bare }) {
  const tr = useTr();
  if (risk === null || risk === undefined) return <span className="muted small">{tr("no score")}</span>;
  if (bare) {      // just the bar, scaled to the top risk band so small risks are still visible
    const top = Math.max(0.1, high * 1.5);
    return (
      <span className="riskbar wide" title={`Risk ${pct(risk)} (range ${pct(low)} to ${pct(high)})`}>
        <span className="range" style={{ left: `${(low / top) * 100}%`, width: `${Math.max(((high - low) / top) * 100, 3)}%` }} />
        <span className="dot" style={{ left: `calc(${(risk / top) * 100}% - 2px)` }} />
      </span>
    );
  }
  return (
    <span title={`Risk ${pct(risk)} (range ${pct(low)} to ${pct(high)})`}>
      <span className="riskbar">
        <span className="range" style={{ left: `${low * 100}%`, width: `${Math.max((high - low) * 100, 2)}%` }} />
        <span className="dot" style={{ left: `calc(${risk * 100}% - 2px)` }} />
      </span>{" "}
      <b>{pct(risk)}</b> <span className="muted small">({pct(low)} to {pct(high)})</span>
    </span>
  );
}

// English / Arabic switch for pages without the sidebar (landing, sign-in, sign-up)
export function LangSwitch() {
  const { lang, setLang } = useT();
  return (
    <span className="lang-switch">
      <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>EN</button>
      <button className={lang === "ar" ? "on" : ""} onClick={() => setLang("ar")}>عربي</button>
    </span>
  );
}

// where the result numbers come from, as a small line under them (production dashboards name their data source)
export function SourceNote({ source }) {
  const tr = useTr();
  if (!source) return null;
  const text = source.kind === "live" ? tr("Source: live hospital data.")
    : source.runs > 1 ? tr("Source: comparison with usual care on the project's dataset, average of {n} runs; same staff and clinic capacity both ways.", { n: source.runs })
      : tr("Source: comparison with usual care on the project's dataset; same staff and clinic capacity both ways.");
  return <p className="source-note">{text}</p>;
}

export function Kpi({ label, value, delta, negative }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {delta && <div className={`delta ${negative ? "neg" : ""}`}>{delta}</div>}
    </div>
  );
}

export function Loading() {
  const { t } = useT();
  return <div className="card muted">{t.loading}</div>;
}

export function ErrorBox({ error }) {
  const tr = useTr();
  const text = String(error?.message || error);
  // only a network failure means the server is down; any other message is the server's own answer
  const down = /failed to fetch|networkerror|load failed/i.test(text);
  return <div className="error">{tr("Could not load: {t}.", { t: tr(text) })}{down && ` ${tr("Is the server running (python server.py)?")}`}</div>;
}

// the intended-use statement of a decision-support tool: one quiet line at the foot of the clinical pages
export function Banner() {
  const { t } = useT();
  return <p className="intended-use">{t.banner}</p>;
}

export function Chips({ items }) {
  const tr = useTr();
  if (!items || !items.length) return <span className="muted small">{tr("none")}</span>;
  return <>{items.map((x, i) => <span key={i} className="chip">{x}</span>)}</>;
}

// pathway cards as a stacked list: easy to read in a narrow panel (no sideways scrolling)
export function CardsTable({ cards }) {
  const tr = useTr();
  if (!cards || !cards.length) return <p className="muted small">{tr("No pathway cards.")}</p>;
  return (
    <div className="pathways">
      {cards.map((c, i) => (
        <div className={`pathway ${c.status === "done" || c.status === "on_hold" ? "quiet" : ""}`} key={i}>
          <div className="pathway-top"><b>{tr(checkLabel(c.check))}</b><DecisionBadge decision={c.decision} status={c.status} /></div>
          {c.reason && <div className="pathway-reason">{tr(c.reason)}</div>}
          <div className="pathway-meta">
            {[tr(c.pathway), c.guideline, c.recall_months ? tr("recall every {n} months", { n: c.recall_months }) : null].filter(Boolean).join(" · ")}
          </div>
        </div>
      ))}
    </div>
  );
}

// a few check names as soft chips, then "+n more"
export function CheckChips({ checks, max = 2 }) {
  const tr = useTr();
  const list = [...new Set(checks || [])];
  if (!list.length) return null;
  return (
    <span className="check-chips">
      {list.slice(0, max).map((c) => <span key={c} className="chip">{tr(checkLabel(c))}</span>)}
      {list.length > max && <span className="chip more" title={list.slice(max).map((x) => tr(checkLabel(x))).join(tr(", "))}>+{list.length - max}</span>}
    </span>
  );
}

// show a long list a page at a time instead of a scroll box inside the page
export function useMore(items, step = 25) {
  const tr = useTr();
  const [n, setN] = React.useState(step);
  React.useEffect(() => setN(step), [items, step]);
  const shown = (items || []).slice(0, n);
  const more = items && items.length > n
    ? <button className="btn more-btn" onClick={() => setN(n + step)}>{tr("Show {a} more ({b} left)", { a: Math.min(step, items.length - n), b: items.length - n })}</button>
    : null;
  return [shown, more];
}
