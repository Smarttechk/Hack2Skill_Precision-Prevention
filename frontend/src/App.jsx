import React, { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { LABELS, LangContext } from "./i18n.js";
import { api, setPreviewAs, whenSignedOut } from "./api.js";
import Accounts from "./pages/Accounts.jsx";
import CareTeam from "./pages/CareTeam.jsx";
import Today from "./pages/Today.jsx";
import Audit from "./pages/Audit.jsx";
import MyCare from "./pages/MyCare.jsx";
import Home from "./pages/Home.jsx";
import Doctor from "./pages/Doctor.jsx";
import Queue from "./pages/Queue.jsx";
import Family from "./pages/Family.jsx";
import Journey from "./pages/Journey.jsx";
import Phone from "./pages/Phone.jsx";
import Why from "./pages/Why.jsx";
import MapPage from "./pages/MapPage.jsx";
import Communities from "./pages/Communities.jsx";
import Results from "./pages/Results.jsx";
import Landing from "./public/Landing.jsx";
import SignIn from "./public/SignIn.jsx";
import SignUp from "./public/SignUp.jsx";
import Roles from "./public/Roles.jsx";
import About from "./public/About.jsx";
import "./public/public.css";

import Icon from "./icons.jsx";

const PAGE_ICON = { home: "sparkles", today: "bell", audit: "shield", accounts: "id", careteam: "users", doctor: "stethoscope", queue: "megaphone", family: "users", journey: "calendar",
  phone: "message", why: "shield", map: "map", communities: "building", results: "chart", mycare: "heart" };
const PUBLIC = /^\/(welcome|signin|signup|roles|about)(\/|$)/;   // landing, sign-in, sign-up, "who sees what" and "how this meets each requirement": open to everyone

const RETRY_MS = 5000;   // how often to try the server again when it cannot be reached
const PAGES = { home: Home, doctor: Doctor, queue: Queue, family: Family, journey: Journey, phone: Phone, why: Why,
  map: MapPage, communities: Communities, results: Results, accounts: Accounts, careteam: CareTeam, mycare: MyCare, today: Today, audit: Audit };
const SIGNED_OUT = { signed_in: false, sign_in_required: true };

// Which pages each role can open comes from the server (config/roles.json via /api/roles), so roles are defined once.
// With accounts set up, the server decides the role and which people an account may see; the menu only mirrors it.

function readSaved(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage blocked: fine */ }
}

function ChangePassword({ t, onChanged, onCancel }) {
  const [oldPw, setOld] = useState("");
  const [newPw, setNew] = useState("");
  const [msg, setMsg] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    try {
      await api.post("/me/password", { old_password: oldPw, new_password: newPw });
      onChanged();                                    // the server ends every session: sign in with the new password
    } catch (err) { setMsg(String(err.message || err)); }
  };
  return (
    <form className="pw-form" onSubmit={submit}>
      <input type="password" autoComplete="current-password" placeholder={t.currentPassword} value={oldPw} onChange={(e) => setOld(e.target.value)} required />
      <input type="password" autoComplete="new-password" placeholder={t.newPassword} value={newPw} onChange={(e) => setNew(e.target.value)} required />
      {msg && <div className="small" style={{ color: "var(--urgent)" }}>{msg}</div>}
      <div className="row"><button className="btn primary" type="submit">{t.save}</button><button className="btn" type="button" onClick={onCancel}>{t.cancel}</button></div>
    </form>
  );
}

// top right: the person's name; click for role, limits, change password and sign out
function UserMenu({ t, me, roleLabel, limits, onSignOut, onChanged }) {
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) { setOpen(false); setPw(false); } };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const name = me.name || me.user;
  const initials = name.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  return (
    <div className="user-menu" ref={ref}>
      <button className="user-chip" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-haspopup="menu">
        <span className="avatar">{initials}</span><span className="user-text"><span className="user-name">{name}</span><span className="user-role">{roleLabel}</span></span><span className="caret">▾</span>
      </button>
      {open && (
        <div className="user-pop" role="menu">
          <div className="user-head">
            <span className="avatar big">{initials}</span>
            <div><b>{name}</b><div className="small muted">{roleLabel}{me.name && me.name !== me.user ? ` · ${me.user}` : ""}</div></div>
          </div>
          {limits && <div className="small muted user-limits">{limits}</div>}
          {pw ? <ChangePassword t={t} onChanged={onChanged} onCancel={() => setPw(false)} /> : (
            <>
              <button className="menu-item" role="menuitem" onClick={() => setPw(true)}><Icon name="lock" size={16} />{t.changePassword}</button>
              <button className="menu-item" role="menuitem" onClick={onSignOut}><Icon name="arrowRight" size={16} />{t.signOut}</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const [lang, setLang] = useState(params.get("lang") || readSaved("pp-lang", "en"));
  const [preview, setPreview] = useState(params.get("role") || readSaved("pp-role", "admin"));   // open mode only
  const [persona, setPersona] = useState(readSaved("pp-persona", ""));   // open mode: which doctor, or which hospital's admin
  const [demo, setDemo] = useState(null);
  const [me, setMe] = useState(null);
  const [ROLES, setRoles] = useState(null);
  const [offline, setOffline] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => { setMenuOpen(false); }, [location.pathname]);        // a page was chosen: fold the phone menu again
  useEffect(() => {
    whenSignedOut(() => setMe(SIGNED_OUT));
    // if the server is not reachable, say so and try again until it is (no blank menu, no "/undefined" page)
    let timer;
    const load = () => Promise.all([api.get("/roles"), api.get("/me")])
      .then(([r, m]) => { setRoles(r.roles); setMe(m); setOffline(false); })
      .catch((e) => {
        if (String(e.message).includes("sign in")) return;    // handled by whenSignedOut
        setOffline(true); timer = setTimeout(load, RETRY_MS);
      });
    load();
    return () => clearTimeout(timer);
  }, []);
  const t = LABELS[lang] || LABELS.en;
  const needsSignIn = !!me?.sign_in_required && !me?.signed_in;
  const accounts = !!me?.sign_in_required;                   // accounts set up: the server decides the role
  const role = accounts ? me?.role : (ROLES && ROLES[preview] ? preview : "admin");
  const pages = (!needsSignIn && ROLES && role && ROLES[role]) || [];
  const current = location.pathname.replace("/", "") || pages[0];

  useEffect(() => { save("pp-lang", lang); document.documentElement.dir = lang === "ar" ? "rtl" : "ltr"; document.documentElement.lang = lang; }, [lang]);
  useEffect(() => { if (!accounts) save("pp-role", preview); }, [preview, accounts]);
  // open mode: the made-up doctors and the hospitals, to look at the app as one of them
  useEffect(() => { if (me && !me.sign_in_required) api.get("/careteam/demo").then(setDemo).catch(() => setDemo(null)); }, [!!me, me?.sign_in_required]); // eslint-disable-line
  const personaKind = preview === "doctor" ? "doctor:" : preview === "admin" ? "hospital-admin:" : preview === "outreach" ? "outreach:" : "";
  const previewHeader = !accounts && me && personaKind && persona.startsWith(personaKind) ? persona : "";
  setPreviewAs(previewHeader);            // before the pages ask for their data
  useEffect(() => { save("pp-persona", persona); }, [persona]);
  useEffect(() => { if (persona && !persona.startsWith(personaKind || "\u0000")) setPersona(""); }, [preview]); // eslint-disable-line
  // keep the user on a page their role allows
  useEffect(() => {
    if (pages.length && !pages.includes(current) && !PUBLIC.test(location.pathname)) navigate(`/${pages[0]}`, { replace: true });
  }, [role, ROLES, me]); // eslint-disable-line

  const signOut = async () => {
    try { await api.post("/logout"); } catch { /* already signed out */ }
    setMe(SIGNED_OUT);
    navigate("/signin", { replace: true });
  };
  // someone already signed in on this browser who opens sign-in or sign-up: say so, never switch accounts silently
  const signedIn = me && !needsSignIn && accounts ? me : null;
  const signOutHere = async () => {
    try { await api.post("/logout"); } catch { /* already signed out */ }
    setMe(SIGNED_OUT);
  };
  const limits = me?.person ? `${t.ownRecord}${me.person_label ? ` (${me.person_label})` : ""}` : me?.region ? `${t.onlyRegion} ${me.region}` : null;

  const ctx = useMemo(() => ({ lang, t, setLang }), [lang, t]);
  // public pages (landing, sign-in, sign-up) have their own full-page layout; signed out, every other page leads there
  if (PUBLIC.test(location.pathname) || (me && needsSignIn)) {
    if (!PUBLIC.test(location.pathname)) return <Navigate to="/welcome" replace />;
    return (
      <LangContext.Provider value={ctx}>
        <Routes>
          <Route path="/welcome" element={<Landing signedIn={!!me && !needsSignIn && accounts} />} />
          <Route path="/signin" element={<SignIn onDone={(m) => setMe(m)} current={signedIn} onSignOut={signOutHere} />} />
          <Route path="/signup" element={<Navigate to="/signup/patient" replace />} />
          <Route path="/signup/:role" element={<SignUp current={signedIn} onSignOut={signOutHere} />} />
          <Route path="/roles" element={<Navigate to="/roles/patient" replace />} />
          <Route path="/roles/:role" element={<Roles signedIn={!!signedIn} />} />
          <Route path="/about" element={<About />} />
          <Route path="*" element={<Navigate to="/welcome" replace />} />
        </Routes>
      </LangContext.Provider>
    );
  }
  let body;
  if (!ROLES || !me) body = offline ? <div className="error">{t.offline}</div> : <div className="muted">{t.loading}</div>;
  else body = (
    <Routes key={`${me.user || preview}|${previewHeader}`}>
      <Route path="/" element={<Navigate to={`/${pages[0]}`} replace />} />
      {pages.map((p) => { const C = PAGES[p]; return <Route key={p} path={`/${p}`} element={<C pages={pages} />} />; })}
      <Route path="*" element={<Navigate to={`/${pages[0]}`} replace />} />
    </Routes>
  );
  return (
    <LangContext.Provider value={ctx}>
      <div className="shell">
        <aside className="side">
          <div className="logo">{t.appTitle}</div>
          <div className="sub">{t.challenge}</div>
          {/* on a phone the menu is folded away so the page itself starts at the top */}
          <button type="button" className="menu-toggle" aria-expanded={menuOpen} onClick={() => setMenuOpen((v) => !v)}>
            {menuOpen ? (lang === "ar" ? "إغلاق القائمة" : "Close menu") : (lang === "ar" ? "القائمة واللغة" : "Menu and language")}
          </button>
          <div className={`side-body ${menuOpen ? "open" : ""}`}>
          <label>Language / اللغة</label>
          <div className="lang">
            <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>English</button>
            <button className={lang === "ar" ? "on" : ""} onClick={() => setLang("ar")}>العربية</button>
          </div>
          {!accounts && me && (
            <>
              <label>{t.previewRole}</label>
              <select value={role} onChange={(e) => setPreview(e.target.value)}>
                {Object.keys(ROLES || {}).map((r) => <option key={r} value={r}>{t.roles[r] || r}</option>)}
              </select>
              {demo?.open_mode && preview === "doctor" && (
                <>
                  <label>{t.previewDoctor}</label>
                  <select value={persona} onChange={(e) => setPersona(e.target.value)}>
                    <option value="">{t.allPatients}</option>
                    {demo.doctors.map((d) => <option key={d.user} value={`doctor:${d.user}`}>{d.name} · {d.hospital} · {d.specialty.join(", ")}</option>)}
                  </select>
                </>
              )}
              {demo?.open_mode && preview === "outreach" && (
                <>
                  <label>{t.previewStaff}</label>
                  <select value={persona} onChange={(e) => setPersona(e.target.value)}>
                    <option value="">{t.anyStaff}</option>
                    {demo.outreach.map((s) => <option key={s.user} value={`outreach:${s.user}`}>{s.name} · {s.hospital}</option>)}
                  </select>
                </>
              )}
              {demo?.open_mode && preview === "admin" && (
                <>
                  <label>{t.previewHospital}</label>
                  <select value={persona} onChange={(e) => setPersona(e.target.value)}>
                    <option value="">{t.centralAdmin}</option>
                    {demo.hospitals.map((h) => <option key={h} value={`hospital-admin:${h}`}>{h}</option>)}
                  </select>
                </>
              )}
            </>
          )}
          {pages.length > 0 && <label>{t.pagesLabel}</label>}
          <nav>
            {pages.map((p) => (
              <NavLink key={p} to={`/${p}`} className={({ isActive }) => (isActive ? "active" : "")}>
                <Icon name={PAGE_ICON[p] || "logo"} size={18} />{t.pages[p]}
                {p === "accounts" && me?.pending_requests > 0 && <span className="nav-count" title="Sign-up requests waiting">{me.pending_requests}</span>}
              </NavLink>
            ))}
          </nav>
          {!accounts && me && <div className="demo">{t.openMode}</div>}
          <NavLink to="/about" className="about-link">{t.about}</NavLink>
          </div>
        </aside>
        <main className="main">
          <div className="topbar">
            <h1>{needsSignIn ? t.signIn : (t.pages[current] || t.appTitle)}</h1>
            {accounts && me?.signed_in && (
              <UserMenu t={t} me={me} roleLabel={t.roles[role] || role} limits={limits} onSignOut={signOut} onChanged={() => setMe(SIGNED_OUT)} />
            )}
          </div>
          {body}
        </main>
      </div>
    </LangContext.Provider>
  );
}
