// Shown at the top of the doctor's page for a doctor who belongs to a care team: how many patients are theirs, who they cover, and
// "break glass" to open one patient outside the doctor's own patients in an emergency (a reason is required, it is logged, the admin sees it).
import React, { useState } from "react";
import { api, useApi } from "./api.js";
import { useTr } from "./i18n.js";

export default function CareTeamBanner({ onOpened }) {
  const tr = useTr();
  const { data: me, reload } = useApi("/careteam/me");
  const [open, setOpen] = useState(false);
  const [rec, setRec] = useState("");
  const [person, setPerson] = useState(null);
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState(null);
  if (!me?.managed) return null;
  const find = async () => {
    setMsg(null);
    try { setPerson(await api.get(`/careteam/lookup?record=${encodeURIComponent(rec.trim())}`)); } catch (e) { setPerson(null); setMsg({ ok: false, text: tr(String(e.message || e)) }); }
  };
  const openIt = async () => {
    setMsg(null);
    try {
      const g = await api.post("/careteam/break_glass", { person: person.id, reason });
      setMsg({ ok: true, text: g.already ? tr("This person is already on your list.") : tr("Open until {t}. It is in the access log.", { t: g.expires.replace("T", " ").slice(0, 16) }) });
      onOpened && onOpened(person.id);
      setPerson(null); setRec(""); setReason(""); reload();
    } catch (e) { setMsg({ ok: false, text: tr(String(e.message || e)) }); }
  };
  return (
    <div className="card careteam-banner">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <b>{me.name}</b> <span className="small muted">· {tr(me.hospital)} · {me.specialty.map((s) => tr(s)).join(", ")}{me.languages.length ? ` · ${me.languages.map((l) => tr(l)).join(", ")}` : ""}</span>
        </div>
        <div className="row">
          <span className="chip">{tr("Your patients: {a} of {b} allowed", { a: me.panel, b: me.panel_limit })}</span>
          {me.duty && <span className="chip">{tr("On duty")}: {tr("{n} urgent without a doctor", { n: me.duty_urgent })}</span>}
          {me.on_leave && <span className="chip warn-chip">{tr("On leave")}</span>}
          {me.covering.length > 0 && <span className="chip">{tr("Covering {n}", { n: me.covering.join(", ") })}</span>}
          {me.shared_with_us > 0 && <span className="chip">{tr("{n} shared by families", { n: me.shared_with_us })}</span>}
        </div>
      </div>
      <p className="small muted" style={{ margin: "6px 0" }}>{tr("You see your own patients, the ones you cover, and anyone a family has shared with this hospital. Nobody else's.")}</p>
      {me.break_glass.length > 0 && (
        <p className="small">{tr("Opened outside your own patients:")} {me.break_glass.map((g) => `${g.mrn} (${tr("until")} ${g.expires.replace("T", " ").slice(11, 16)})`).join(", ")}</p>
      )}
      <button className="btn" onClick={() => setOpen(!open)}>{tr("Emergency: open a patient who is not on my list")}</button>
      {open && (
        <div className="break-glass">
          <p className="small muted">{tr("Use this only when you must see this person now. You give a reason, the opening lasts a few hours, and the hospital admin sees it.")}</p>
          <div className="row">
            <input value={rec} onChange={(e) => setRec(e.target.value)} placeholder="ADH-1234567" aria-label={tr("Record number")} onKeyDown={(e) => e.key === "Enter" && find()} />
            <button className="btn" onClick={find} disabled={rec.trim().length < 4}>{tr("Find")}</button>
          </div>
          {person && !person.found && <p className="small" style={{ color: "var(--urgent)" }}>{tr("No record with that number.")}</p>}
          {person?.found && (
            <>
              <p className="small"><b>{person.mrn}</b> · {person.age} y · {tr(person.sex)} · {tr(person.hospital)}{person.on_my_list ? ` · ${tr("already on your list")}` : ""}</p>
              {!person.on_my_list && (
                <>
                  <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={tr("Why do you need to open this record?")} style={{ width: "100%" }} />
                  <button className="btn bad" onClick={openIt} disabled={reason.trim().length < 12}>{tr("Open for a few hours")}</button>
                </>
              )}
            </>
          )}
        </div>
      )}
      {msg && <div className={msg.ok ? "info small" : "error small"} style={{ marginTop: 8 }}>{msg.text}</div>}
    </div>
  );
}
