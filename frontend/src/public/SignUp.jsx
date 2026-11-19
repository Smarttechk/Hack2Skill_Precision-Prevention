// One sign-up form per role (fields come from config/roles.json via /api/public/info). The request waits for the
// hospital admin, except the very first hospital admin.
import React, { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import Icon from "../icons.jsx";
import { useTr } from "../i18n.js";
import { LangSwitch } from "../components.jsx";
import { FIELD_INFO, ROLE_INFO, ROLE_ORDER } from "../roleInfo.js";

export default function SignUp({ current, onSignOut }) {
  const { role = "patient" } = useParams();
  const navigate = useNavigate();
  const tr = useTr();
  const [info, setInfo] = useState(null);
  const [details, setDetails] = useState({});
  const [user, setUser] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [show, setShow] = useState(false);
  const [show2, setShow2] = useState(false);
  const [agree, setAgree] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get("/public/info").then(setInfo).catch((e) => setError(String(e.message || e))); }, []);
  useEffect(() => { setDetails({}); setError(""); setDone(null); window.scrollTo(0, 0); }, [role]);
  const r = ROLE_INFO[role];
  if (!r) return <div className="pub auth-page"><div className="auth-card">Unknown role. <Link to="/welcome">Back to home</Link></div></div>;

  const set = (k, v) => setDetails((d) => ({ ...d, [k]: v }));
  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (pw !== pw2) { setError("The two passwords are not the same."); return; }
    if (info && pw.length < info.min_password) { setError(`Use a password of at least ${info.min_password} characters.`); return; }
    if (!agree) { setError("Please confirm that your details are correct."); return; }
    setBusy(true);
    try {
      setDone(await api.post("/signup", { role, user, password: pw, details }));
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <div className="pub auth-page">
        <div className="orb o1" /><div className="orb o2" /><div className="grid-bg" />
        <div className="auth-wrap">
          {done.status === "active" ? (
            <div className="auth-card center">
              <span className="done-ico" style={{ color: "#2f6a55" }}><Icon name="check" size={36} /></span>
              <h2>{tr("Your account is ready")}</h2>
              <p className="muted">{done.message}</p>
              <div className="auth-ok">{tr("Your user name:")} <code>{done.user}</code></div>
              <button className="pub-btn primary full" onClick={() => navigate("/signin")}>{tr("Sign in now")} <Icon name="arrowRight" size={16} /></button>
            </div>
          ) : (
            <div className="auth-card">
              <div className="center"><span className="done-ico" style={{ color: "#3e8a9a" }}><Icon name="clock" size={36} /></span></div>
              <h2 className="center">{tr("Thanks, {name}", { name: details.full_name?.split(" ")[0] || done.user })}</h2>
              <p className="muted center">{tr("Your {role} account is almost ready. Here is what happens next:", { role: tr(r.title).toLowerCase() })}</p>
              <ol className="steps">
                <li className="done"><b>{tr("Sign-up received")}</b><span>{tr("Your details and password are saved securely.")}</span></li>
                <li className="now"><b>{tr("The hospital admin checks your details")}</b><span>{tr(r.approval)}</span></li>
                <li><b>{tr("Your account opens")}</b><span>{tr("Then sign in with your user name {user} and the password you chose.", { user: done.user })}</span></li>
              </ol>
              <div className="pub-info"><Icon name="shield" size={16} /><span>{tr("Until then, signing in will tell you your request is still waiting. Questions? Contact your hospital's admin team.")}</span></div>
              <button className="pub-btn primary full" onClick={() => navigate("/welcome")}>{tr("Back to home")}</button>
              <button className="pub-btn ghost full" onClick={() => navigate("/signin")}>{tr("Go to sign in")}</button>
            </div>
          )}
        </div>
      </div>
    );
  }

  const fields = [...(info?.fields?.[role] || []), ...(info?.optional?.[role] || [])];
  const required = new Set(info?.fields?.[role] || []);
  return (
    <div className="pub signup-page">
      <div className="signup-card">
        <div className="signup-top">
          <div className="signup-head">
            <span className="pub-ico" style={{ color: "#fff", background: r.color, border: "none" }}><Icon name={r.icon} size={24} /></span>
            <div>
              <h1>{tr("{role} sign-up", { role: tr(r.title) })}</h1>
              <p className="muted">{tr(r.who)}</p>
            </div>
          </div>
          <div className="row">
            <button className="back-home" onClick={() => navigate("/welcome")}><Icon name="arrowLeft" size={16} /> {tr("Back to home")}</button>
            <LangSwitch />
          </div>
        </div>
        <div className="role-tabs">
          {ROLE_ORDER.map((k) => (
            <button key={k} type="button" className={k === role ? "on" : ""} onClick={() => navigate(`/signup/${k}`)}>{tr(ROLE_INFO[k].title)}</button>
          ))}
        </div>
        {current && (
          <div className="signed-note">
            <span>{tr("You are signed in as {user} on this browser. Sign out before making a new account.", { user: current.name || current.user })}</span>
            <button className="pub-btn ghost sm" type="button" onClick={onSignOut}>{tr("Sign out")}</button>
          </div>
        )}
        <div className="pub-info"><Icon name="shield" size={16} /><span><b>{tr("What you will see:")}</b> {tr(r.sees)}. {tr(r.approval)} <Link className="more-link" to={`/roles/${role}`}>{tr("Full details")}</Link></span></div>
        {error && <div className="auth-error">{error}</div>}
        {!info ? <p className="muted center">Loading…</p> : (
          <form onSubmit={submit}>
            <div className="sec-label">{tr("Your details")}</div>
            <div className="two-col">
              {fields.map((f) => {
                const fi = FIELD_INFO[f] || { label: f, icon: "id" };
                const val = details[f] || "";
                return (
                  <div className="field" key={f}>
                    <label htmlFor={`f-${f}`}>{tr(fi.label)}{required.has(f) ? " *" : ""}</label>
                    {fi.options || fi.regions ? (
                      <select id={`f-${f}`} value={val} onChange={(e) => set(f, e.target.value)} required={required.has(f)}>
                        <option value="">{fi.regions ? tr("All regions / not sure") : tr("Choose…")}</option>
                        {(fi.regions ? info.regions : fi.options).map((o) => <option key={o}>{o}</option>)}
                      </select>
                    ) : (
                      <div className="in-wrap"><Icon name={fi.icon} size={16} />
                        <input id={`f-${f}`} type={fi.type || "text"} placeholder={fi.placeholder || ""} value={val}
                          onChange={(e) => set(f, e.target.value)} required={required.has(f)} />
                      </div>
                    )}
                    {fi.note && <span className="hint">{tr(fi.note)}</span>}
                  </div>
                );
              })}
            </div>
            <div className="sec-label">{tr("Account")}</div>
            <div className="two-col">
              <div className="field">
                <label htmlFor="f-user">{tr("User name")} *</label>
                <div className="in-wrap"><Icon name="user" size={16} /><input id="f-user" autoComplete="username" placeholder="e.g. amina.k" value={user} onChange={(e) => setUser(e.target.value)} required /></div>
              </div>
              <div className="field">
                <label htmlFor="f-pw">{tr("Password")} * <span className="hint">{tr("(at least {n} characters)", { n: info.min_password })}</span></label>
                <div className="in-wrap"><Icon name="lock" size={16} />
                  <input id="f-pw" type={show ? "text" : "password"} autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required />
                  <button type="button" className="eye" onClick={() => setShow((v) => !v)} aria-label={show ? "Hide password" : "Show password"}><Icon name={show ? "eyeOff" : "eye"} size={16} /></button>
                </div>
              </div>
              <div className="field">
                <label htmlFor="f-pw2">{tr("Repeat password")} *</label>
                <div className="in-wrap"><Icon name="lock" size={16} />
                  <input id="f-pw2" type={show2 ? "text" : "password"} autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} required />
                  <button type="button" className="eye" onClick={() => setShow2((v) => !v)} aria-label={show2 ? "Hide password" : "Show password"}><Icon name={show2 ? "eyeOff" : "eye"} size={16} /></button>
                </div>
                {pw2 && <span className="hint" style={{ color: pw2 === pw ? "#2f6a55" : "#9a4a36" }}>{pw2 === pw ? tr("Passwords match") : tr("Passwords do not match yet")}</span>}
              </div>
            </div>
            <label className="agree">
              <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
              <span>{tr("I confirm my details are correct. I understand the hospital will check them before my account opens, and that every sign-in is logged.")}</span>
            </label>
            <button className="pub-btn full" type="submit" disabled={busy} style={{ background: r.color, borderColor: r.color, color: "#fff" }}>
              {busy ? tr("Sending…") : tr("Sign up as {role}", { role: tr(r.title).toLowerCase() })} <Icon name="arrowRight" size={16} />
            </button>
          </form>
        )}
        <p className="center small muted" style={{ margin: "10px 0 0" }}>{tr("Already have an account?")} <Link to="/signin">{tr("Sign in")}</Link></p>
      </div>
    </div>
  );
}
