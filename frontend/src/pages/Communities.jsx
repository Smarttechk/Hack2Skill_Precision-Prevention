import React, { useMemo, useState } from "react";
import { pct, useApi } from "../api.js";
import { ErrorBox, Loading, useMore } from "../components.jsx";
import { useTr } from "../i18n.js";
import { checkLabel } from "../labels.js";

// Places (10 or more residents) and what the team could do there. Totals only: no person is named.
export default function Communities() {
  const tr = useTr();
  const { data, error, loading } = useApi("/communities");
  const [region, setRegion] = useState("");
  const [withActions, setWithActions] = useState(true);
  const rows = useMemo(() => (data || []).filter((c) => (!region || c.region === region) && (!withActions || c.actions.length)), [data, region, withActions]);
  const [shown, more] = useMore(rows, 12);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const regions = [...new Set(data.map((c) => c.region))];
  const small = rows.filter((c) => !c.reliable).length;
  const start = rows.slice(0, 5);
  return (
    <>
      <div className="card">
        <h3>{tr("Where to run group activities, and what to do there")}</h3>
        <p className="muted small" style={{ margin: "0 0 12px" }}>{tr("Places are listed with the most people needing care first. Each card gives the numbers and one or two actions the team could take. Totals only: no person is named.")}</p>
        {start.length > 0 && (
          <>
            <div className="small muted" style={{ marginBottom: 6 }}>{tr("Start here")}</div>
            <ol className="start-here">
              {start.map((c) => (
                <li key={c.place}>
                  <b>{c.place}</b> <span className="muted">{tr(c.region)}</span>
                  <span className="start-n">{tr("{n} need care now", { n: c.open_now })}</span>
                  <span className="start-do">{c.actions[0] ? tr(c.actions[0]) : tr("No action suggested")}</span>
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
      <div className="card row">
        <span className="small muted">{tr("Region")}</span>
        <select value={region} onChange={(e) => setRegion(e.target.value)}><option value="">{tr("All")}</option>{regions.map((r) => <option key={r} value={r}>{tr(r)}</option>)}</select>
        <label className="toggle"><input type="checkbox" checked={withActions} onChange={(e) => setWithActions(e.target.checked)} /> {tr("Only places with a suggested action")}</label>
        <span className="small muted" style={{ marginInlineStart: "auto" }}>{tr("{a} of {b} places · most people needing care first", { a: rows.length, b: data.length })}</span>
      </div>
      {small > 0 && <p className="small muted" style={{ margin: "0 4px 14px" }}>{tr("{n} of these places have fewer than 30 residents: read their rates as rough guides.", { n: small })}</p>}
      <div className="grid g2">
        {shown.map((c) => (
          <div className="card place" key={c.place}>
            <div className="place-head">
              <h3>{c.place}</h3>
              <span className="small muted">{tr(c.region)} · {tr(c.area_type)}{!c.reliable && <span className="tag-small" title={tr("Fewer than 30 residents: read the rates as a rough guide.")}>{tr("small number")}</span>}</span>
            </div>
            <div className="place-stats">
              <div><span>{c.people}</span>{tr("residents")}</div>
              <div><span>{c.open_now} <small>({pct(c.share_open_now)})</small></span>{tr("need care now")}</div>
              <div><span>{pct(c.uptake)}</span>{tr("took up care")}</div>
              <div><span>{tr("{n} min", { n: Math.round(c.travel_min_typical ?? c.avg_travel_min) })}</span>{tr("typical trip to a clinic")}</div>
            </div>
            <p className="small place-gaps">
              {tr("Most common: {list}.", { list: c.top_gaps.map((g) => tr(checkLabel(g))).join(tr(", ")) })}{" "}
              {tr("Main language {l}.", { l: tr(c.main_language) })}
              {c.children_overdue > 0 && <> {c.children_overdue === 1 ? tr("1 child overdue a check-up.") : tr("{n} children overdue a check-up.", { n: c.children_overdue })}</>}
            </p>
            {c.actions.length > 0 && <ul className="place-actions">{c.actions.map((a, i) => <li key={i}>{tr(a)}</li>)}</ul>}
          </div>
        ))}
      </div>
      {more}
    </>
  );
}
