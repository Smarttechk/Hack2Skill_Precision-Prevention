import React, { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useT, useTr } from "./i18n.js";

// The optional Care Assistant (api/assistant.py): questions about the one record that is open. It only reads. The approved message for a
// family is the approved template, shown in its own box, never text written by the model. The conversation lives on this screen only.
const TOOL_LABELS = { get_patient_overview: "Record overview", get_open_care_gaps: "Open care gaps", get_contact_history: "Contact history", get_approved_message: "Approved message" };
const SUGGEST = ["Why was this person flagged?", "What is due, and what has staff already done?", "Show the approved message for the family"];

export default function CareAssistant({ pid }) {
  const tr = useTr();
  const { lang } = useT();
  const [status, setStatus] = useState(null);
  const [turns, setTurns] = useState([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [copied, setCopied] = useState(null);
  const end = useRef(null);

  useEffect(() => {
    let on = true;
    api.get("/assistant/status").then((s) => on && setStatus(s)).catch(() => on && setStatus({ available: false, reason: "The Care Assistant is not available." }));
    return () => { on = false; };
  }, []);
  useEffect(() => { setTurns([]); setErr(null); setQ(""); setCopied(null); }, [pid]);
  useEffect(() => { if (turns.length) end.current?.scrollIntoView({ block: "nearest" }); }, [turns, busy]);

  const ask = async (text) => {
    const question = (text ?? q).trim();
    if (!question || busy) return;
    setBusy(true); setErr(null); setQ("");
    const history = turns.map((t) => ({ role: t.role, content: t.content }));
    setTurns((t) => [...t, { role: "user", content: question }]);
    try {
      const r = await api.post("/assistant/ask", { pid, question, history, lang });
      setTurns((t) => [...t, { role: "assistant", content: r.answer, tools: r.tools_used, approved: r.approved_messages }]);
    } catch (e) {
      setErr(String(e.message || e));
    }
    setBusy(false);
  };
  const copy = async (text, i) => {
    try { await navigator.clipboard.writeText(text); setCopied(i); } catch { /* the text can still be selected by hand */ }
  };

  if (!status) return null;
  return (
    <section className="pp-sec care-assistant">
      <h4>{tr("Care Assistant")} <span className="chip tag-teal">{tr("AI, read-only")}</span></h4>
      {!status.available ? (
        <p className="small muted">{tr(status.reason)}</p>
      ) : (
        <>
          {turns.length === 0 && (
            <div style={{ marginBottom: 8 }}>
              {SUGGEST.map((s) => <button key={s} type="button" className="chip more" style={{ cursor: "pointer" }} disabled={busy} onClick={() => ask(tr(s))}>{tr(s)}</button>)}
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className="cond" style={{ marginBottom: 8 }}>
              {t.role === "user" ? (
                <div className="cond-row"><b className="cond-name">{t.content}</b></div>
              ) : (
                <>
                  <div style={{ whiteSpace: "pre-wrap" }}>{tr(t.content)}</div>
                  {t.tools?.length > 0 && <div className="cond-note muted">{tr("Used: {l}", { l: [...new Set(t.tools)].map((x) => tr(TOOL_LABELS[x] || x)).join(tr(", ")) })}</div>}
                  {(t.approved || []).map((m, k) => (
                    <div key={k} className="pathway" style={{ marginTop: 6 }}>
                      <div className="pathway-top"><b>{tr("Approved message (not written by the AI). Staff send it themselves.")}</b></div>
                      <div style={{ whiteSpace: "pre-wrap", margin: "4px 0" }} dir="auto">{m.text}</div>
                      <button type="button" className="btn" onClick={() => copy(m.text, `${i}-${k}`)}>{copied === `${i}-${k}` ? tr("Copied") : tr("Copy")}</button>
                    </div>
                  ))}
                </>
              )}
            </div>
          ))}
          {busy && <p className="small muted">{tr("Asking...")}</p>}
          {err && <p className="small" style={{ color: "var(--urgent)" }}>{tr(err)}</p>}
          <form onSubmit={(e) => { e.preventDefault(); ask(); }} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={800} placeholder={tr("Ask a question about this record")} disabled={busy}
              style={{ flex: "1 1 220px", minWidth: 0 }} aria-label={tr("Ask a question about this record")} />
            <button type="submit" className="btn primary" disabled={busy || !q.trim()}>{tr("Ask")}</button>
            {turns.length > 0 && <button type="button" className="btn" disabled={busy} onClick={() => { setTurns([]); setErr(null); }}>{tr("Clear")}</button>}
          </form>
          <p className="small muted" style={{ marginTop: 6 }}>{tr("AI-generated: check it against the record. It cannot send or change anything, and a doctor decides every clinical step.")}</p>
          <div ref={end} />
        </>
      )}
    </section>
  );
}
