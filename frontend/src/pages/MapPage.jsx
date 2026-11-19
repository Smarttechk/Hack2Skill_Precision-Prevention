import React from "react";
import { CircleMarker, MapContainer, TileLayer, Tooltip } from "react-leaflet";
import { pct, useApi } from "../api.js";
import { ErrorBox, Loading } from "../components.jsx";
import { useTr } from "../i18n.js";
import { C, gapColour as colour } from "../theme.js";   // sage (few gaps) to terracotta (many gaps)

export default function MapPage() {
  const tr = useTr();
  const { data, error, loading } = useApi("/map");
  if (loading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  // most people needing care first (40 of 60 matters more for planning than 10 of 10)
  const need = (p) => Math.round(p.people * p.gap_rate);
  const top = [...data.places].sort((a, b) => need(b) - need(a) || b.gap_rate - a.gap_rate).slice(0, 12);
  return (
    <div className="split" style={{ gridTemplateColumns: "minmax(0,1.6fr) minmax(0,1fr)" }}>
      <div className="card">
        <div className="map" dir="ltr">
          <MapContainer center={[24.0, 54.6]} zoom={7} style={{ height: "100%" }} scrollWheelZoom>
            {/* the app sends no referrer anywhere else; OpenStreetMap refuses tiles that arrive without one, so this layer sends just the site's address */}
            <TileLayer referrerPolicy="origin" attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
            {data.facilities.map((f, i) => (
              <CircleMarker key={`f${i}`} center={[f.lat, f.lon]} radius={3} pathOptions={{ color: C.ink, fillOpacity: 0.9, weight: 1 }}>
                <Tooltip>{f.name || f.kind} ({tr(f.kind)})</Tooltip>
              </CircleMarker>
            ))}
            {data.places.map((p, i) => (
              <CircleMarker key={`p${i}`} center={[p.lat, p.lon]} radius={5 + Math.sqrt(p.people)}
                pathOptions={{ color: colour(p.gap_rate), fillColor: colour(p.gap_rate), fillOpacity: 0.55, weight: 1 }}>
                <Tooltip><b>{p.home_place}</b><br />{tr("{n} residents", { n: p.people })} &middot; {tr("{p} need care now", { p: pct(p.gap_rate) })}<br />{tr(p.region)}, {tr(p.area_type)}</Tooltip>
              </CircleMarker>
            ))}
          </MapContainer>
        </div>
        <div className="legend">
          <span><i style={{ background: colour(0.1) }} />{tr("few open gaps")}</span>
          <span><i style={{ background: colour(0.5) }} />{tr("some")}</span>
          <span><i style={{ background: colour(0.9) }} />{tr("many")}</span>
          <span><i style={{ background: C.ink }} />{tr("health facility")}</span>
          <span className="muted">{tr("Map data © OpenStreetMap contributors. Circle size = residents.")}</span>
        </div>
      </div>
      <div className="card">
        <h3>{tr("Where most people need care now")}</h3>
        <ol className="top-places">
          {top.map((p) => (
            <li key={p.home_place}>
              <span className="tp-dot" style={{ background: colour(p.gap_rate) }} />
              <span className="tp-name"><b>{p.home_place}</b><span className="small muted">{tr(p.region)} · {tr("{n} residents", { n: p.people })}</span></span>
              <span className="tp-val"><b>{need(p)}</b><span className="small muted">{pct(p.gap_rate)}</span></span>
            </li>
          ))}
        </ol>
        <p className="small muted">{tr("People with an open pathway card. Only places with 10 or more residents are shown (small-group rule).")}</p>
      </div>
    </div>
  );
}
