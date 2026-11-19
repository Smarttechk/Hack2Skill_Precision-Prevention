import React from "react";
import { Link } from "react-router-dom";
import { pct, useApi } from "../api.js";
import { ErrorBox, Kpi, Loading, SourceNote } from "../components.jsx";
import { useTr } from "../i18n.js";
import Operations from "./Operations.jsx";

// every shortcut card; only those the signed-in role can open are shown
const SHORTCUTS = [
  ["journey", "Follow one person", "From flag to message, reply, booking, visit and result."],
  ["phone", "See the family's phone", "Reply as a parent and watch the system answer live."],
  ["doctor", "Review as a doctor", "Risk with its range, reasons, data used and Agree or Disagree."],
  ["queue", "Work the outreach queue", "Who to contact today, in which language and by which channel."],
  ["map", "See where the gaps are", "Places with the most open care needs, region by region."],
  ["communities", "Plan community actions", "What the team could do in each place (totals only)."],
  ["results", "Check the results", "The comparison with usual care, with safeguards and sources."],
];

export default function Home({ pages = [] }) {
  const tr = useTr();
  const { data, error, loading } = useApi("/overview");
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const h = data.headline || {};
  const n = h.new || {}, o = h.old || {}, cv = h.clinical_value || {};
  const found = cv.share_of_hidden_cases_found_new || {}, foundOld = cv.share_of_hidden_cases_found_old || {};
  const endKey = Object.keys(found).find((k) => k.startsWith("found_by_day_") && k !== "found_by_day_30");
  // results: the average over the seed runs when available, the same numbers as the Results page
  const ss = data.seed_spread, sm = ss?.summary;
  const avg = (k, mode) => sm?.[`${k}_${mode}`]?.mean;
  const total = found.hidden_cases_at_start;
  const uc = (v) => tr("usual care {v}", { v });
  const kpis = sm ? [
    [tr("Well-Child care completed"), pct(avg("well_child", "new")), uc(pct(avg("well_child", "old")))],
    [tr("Adult screening completed"), pct(avg("adult_screening", "new")), uc(pct(avg("adult_screening", "old")))],
    [total ? tr("Hidden cases found (of {n})", { n: total }) : tr("Hidden cases found"), Math.round(avg("found", "new")).toLocaleString(), uc(Math.round(avg("found", "old")))],
    [tr("Results followed up in 30 days"), pct(avg("followup", "new")), uc(pct(avg("followup", "old")))],
  ] : n.well_child ? [
    [tr("Well-Child care completed"), pct(n.well_child.people_rate), uc(pct(o.well_child.people_rate))],
    [tr("Adult screening completed"), pct(n.adult_screening.people_rate), uc(pct(o.adult_screening.people_rate))],
    [tr("Hidden cases found"), pct(found[endKey]), uc(pct(foundOld[endKey]))],
    [tr("Results followed up in 30 days"), pct(n.followup_within_30_days), uc(pct(o.followup_within_30_days))],
  ] : null;
  const shortcuts = SHORTCUTS.filter(([p]) => pages.includes(p)).slice(0, 3);
  return (
    <>
      <div className="grid g4">
        <Kpi label={tr("People in the system")} value={data.people.toLocaleString()} delta={tr("{n} children", { n: data.children.toLocaleString() })} />
        <Kpi label={tr("With open pathway cards")} value={data.open.toLocaleString()} delta={tr("{n} urgent", { n: data.urgent })} negative />
        <Kpi label={tr("Waiting for a doctor")} value={data.awaiting_doctor.toLocaleString()} />
        <Kpi label={tr("Regions")} value={data.regions.length} delta={data.regions.map(tr).join(" · ")} />
      </div>
      {kpis && data.role === "manager" && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>{tr("Results against usual care")}</h3>
          <div className="grid g4">
            {kpis.map(([label, value, delta]) => <Kpi key={label} label={label} value={value} delta={delta} />)}
          </div>
          <p className="small muted" style={{ marginTop: 10 }}>
            {pages.includes("results") ? <Link to="/results">{tr("Full detail on the Results page.")}</Link> : null}
          </p>
          <SourceNote source={data.source} />
        </div>
      )}
      {(data.role === "manager" || data.role === "admin") && <Operations />}
      {shortcuts.length > 0 && (
        <div className="grid g3">
          {shortcuts.map(([to, title, text]) => (
            <Link key={to} to={`/${to}`} className="card" style={{ textDecoration: "none", color: "inherit" }}>
              <h3>{tr(title)} <span className="arrow">&rarr;</span></h3><p className="muted small">{tr(text)}</p>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
