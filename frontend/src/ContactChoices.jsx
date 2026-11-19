// The two choices a person (or a parent, for a child) can change: reminders, and use of genetic results. Shared by
// "Why was I contacted?" and "My care". `consent` is {contact, use_genetic} when the page already has it.
import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import { useTr } from "./i18n.js";

export function Switch({ checked, onChange }) {
  return <label className="switch"><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /><span className="slider" /></label>;
}

export default function ContactChoices({ pid, child, consent }) {
  const tr = useTr();
  const [contact, setContact] = useState(true);
  const [genetic, setGenetic] = useState(false);
  const [saved, setSaved] = useState(null);
  useEffect(() => { if (consent) { setContact(consent.contact); setGenetic(consent.use_genetic); setSaved(null); } }, [consent]);
  const save = async () => {
    const r = await api.post(`/why/${pid}/consent`, { contact, use_genetic: genetic });
    setSaved(r.given_by === "guardian" ? "guardian" : "self");
  };
  return (
    <>
      <p className="small muted">{child ? `${tr("As the parent or guardian, you decide for your child.")} ` : ""}{tr("You can change your mind at any time.")}</p>
      <div className="row" style={{ justifyContent: "space-between", margin: "10px 0" }}>
        <span>{tr("You may contact me with reminders")}</span><Switch checked={contact} onChange={setContact} />
      </div>
      <div className="row" style={{ justifyContent: "space-between", margin: "10px 0" }}>
        <span>{tr("Genetic results may be used to plan check-ups")}</span><Switch checked={genetic} onChange={setGenetic} />
      </div>
      <button className="btn primary" onClick={save}>{tr("Save my choices")}</button>
      {saved && <div className="ok">{tr(saved === "guardian" ? "Saved with your decision as the parent or guardian." : "Saved.")} {tr("Every change is recorded with the date in the audit log.")}</div>}
    </>
  );
}
