// Sign-in card (GenoRisk style): back to home, logo, user name and password with show / hide, link to sign up.
import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import Icon from "../icons.jsx";
import { useTr } from "../i18n.js";
import { LangSwitch } from "../components.jsx";

export default function SignIn({ onDone, current, onSignOut }) {
  const navigate = useNavigate();
  const tr = useTr();
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { window.scrollTo(0, 0); }, []);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      if (current) await onSignOut();                  // end the other account's session before starting this one
      const me = await api.post("/login", { user, password });
      setPassword("");
      onDone(me);
      navigate("/", { replace: true });
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="pub auth-page">
      <div className="orb o1" /><div className="orb o2" /><div className="grid-bg" />
      <div className="auth-wrap">
        <div className="auth-top"><button className="back-home" onClick={() => navigate("/welcome")}><Icon name="arrowLeft" size={16} /> {tr("Back to home")}</button><LangSwitch /></div>
        <div className="auth-card">
          <div className="auth-logo"><span className="pub-logo big"><Icon name="logo" size={24} /></span><b>Precision Prevention</b></div>
          <h2>{tr("Welcome back")}</h2>
          <p className="muted">{tr("Sign in to open your pages. You will see only what your role needs.")}</p>
          {current && (
            <div className="signed-note">
              <span>{tr("Already signed in as {user}.", { user: current.name || current.user })}</span>
              <span className="row">
                <button className="pub-btn primary sm" type="button" onClick={() => navigate("/")}>{tr("Continue")}</button>
                <button className="pub-btn ghost sm" type="button" onClick={onSignOut}>{tr("Sign out")}</button>
              </span>
            </div>
          )}
          {error && <div className={/waiting/.test(error) ? "pub-info" : "auth-error"}>{/waiting/.test(error) && <Icon name="clock" size={16} />}<span>{error}</span></div>}
          <form onSubmit={submit}>
            <label className="auth-input"><Icon name="user" size={18} />
              <input aria-label={tr("User name")} placeholder={tr("User name")} autoComplete="username" value={user} onChange={(e) => setUser(e.target.value)} required />
            </label>
            <label className="auth-input"><Icon name="lock" size={18} />
              <input aria-label={tr("Password")} placeholder={tr("Password")} type={show ? "text" : "password"} autoComplete="current-password"
                value={password} onChange={(e) => setPassword(e.target.value)} required />
              <button type="button" className="eye" onClick={() => setShow((v) => !v)} aria-label={show ? "Hide password" : "Show password"}>
                <Icon name={show ? "eyeOff" : "eye"} size={16} />
              </button>
            </label>
            <button className="pub-btn primary full" type="submit" disabled={busy}>{busy ? tr("Signing in…") : tr("Sign in")}</button>
          </form>
          <div className="auth-foot">{tr("Don't have an account?")} <Link to="/signup/patient">{tr("Sign up")}</Link></div>
          <div className="auth-foot small">{tr("Forgot your password? Ask your hospital admin to reset it.")}</div>
        </div>
      </div>
    </div>
  );
}
