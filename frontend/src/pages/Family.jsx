import React, { useEffect, useState } from "react";
import { pct, useApi } from "../api.js";
import { Badge, CardsTable, ErrorBox, Loading, useMore } from "../components.jsx";
import { useTr } from "../i18n.js";

export default function Family() {
  const tr = useTr();
  const list = useApi("/households?limit=150");
  const [hh, setHh] = useState(null);
  useEffect(() => { if (list.data?.length && !hh) setHh(list.data[0].household_id); }, [list.data]); // eslint-disable-line
  const [shown, more] = useMore(list.data || [], 20);
  if (list.loading) return <Loading />;
  if (list.error) return <ErrorBox error={list.error} />;
  return (
    <div className="split" style={{ gridTemplateColumns: "minmax(0,0.85fr) minmax(0,1.5fr)" }}>
      <div className="card list-card">
        <h3>{tr("Households with open cards")}</h3>
        <p className="small muted">{tr("{n} families; the ones with the most open cards first.", { n: list.data.length })}</p>
        {list.data.length === 0 && <p className="small muted">{tr("No families to show for your list. Families appear here when a household has open cards among the people you can see, for example children for a paediatrician.")}</p>}
        <div className="plist">
          {shown.map((h) => (
            <button key={h.household_id} className={`prow ${h.household_id === hh ? "sel" : ""}`} onClick={() => setHh(h.household_id)}>
              <div className="prow-top">
                <span className="prow-who">{h.guardian ? <>{tr("Guardian")} <bdi>{h.guardian}</bdi></> : tr("{n} people", { n: h.size })}</span>
                <span className="prow-risk"><b>{h.open}</b> <span className="muted small">{tr("open")}</span></span>
              </div>
              <div className="prow-why">
                {h.children ? (h.children === 1 ? tr("1 child") : tr("{n} children", { n: h.children })) : tr("{n} people", { n: h.size })}
                {h.children_ages?.length > 0 && <> ({tr("ages")} {h.children_ages.join(", ")})</>} · {tr(h.language)} · {tr(h.region)}
              </div>
            </button>
          ))}
        </div>
        {more}
      </div>
      {hh && <Household id={hh} />}
    </div>
  );
}

function Household({ id }) {
  const tr = useTr();
  const { data, error, loading } = useApi(`/household/${id}`);
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return (
    <div>
      <div className="card">
        <h3>{tr("One household, one conversation")}</h3>
        <p className="muted small">{tr("Messages for the children go to the guardian ({g}), in {l}. Consent for children is the guardian's decision. A parent's overdue screening and a child's missed visit can be booked together.",
          { g: data.guardian_record || tr("unknown"), l: tr(data.language) })}</p>
        <p className="muted small"><b>{tr("How a household is identified:")}</b> {tr(data.how)}</p>
      </div>
      {data.members.map((m) => (
        <div className="card" key={m.id}>
          <div className="pp-head">
            <div>
              <h3>{tr(m.relation)} <bdi>{m.short}</bdi>, {tr("{n} years", { n: m.age })}, {tr(m.sex)}</h3>
              <div className="small muted">{m.risk === null ? tr(m.risk_text) : tr("Risk {r} (likely {a} to {b})", { r: pct(m.risk), a: pct(m.risk_low), b: pct(m.risk_high) })}</div>
            </div>
            <div className="row"><Badge urgency={m.urgency} />{m.state && m.state !== "None" && <span className="chip">{tr(m.state)}</span>}</div>
          </div>
          <CardsTable cards={m.cards} />
        </div>
      ))}
    </div>
  );
}
