"""
REST API for the React frontend. It wraps the existing Python system (agents, models, chat, consent)
and the outputs written by orchestrator.py and evaluate.py.

    python server.py        serves this API at /api and the built React app at /
"""
import contextvars
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

import consent
import messaging
import fhir
import summaries
import records as records_db   # the hospital records database (records.py)
import simulation
import utils
from agents import chat, outreach
from api import auth, careteam, clinic_answers, family
from api import outreach as contact_log        # who contacted whom (agents.outreach below is the message composer)
from agents.priority import DEADLINE_DAYS
from models.risk_scores import band as risk_band

app = FastAPI(title="Precision Prevention API", version="1.0")


ACCOUNT = contextvars.ContextVar("account", default=None)   # the signed-in account of the request being handled
NEEDS_SIGN_IN = ("/api/", "/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def sign_in(request, call_next):
    """Sign-in and role check for every API call (see api/auth.py for the three modes). The app's own files hold no
    patient data, so they load without signing in and show the sign-in page."""
    from fastapi.responses import JSONResponse
    path = request.url.path
    request.state.computer = computer(request)
    current = lambda: auth.who(request.cookies.get(auth.COOKIE), request.headers.get("authorization", ""),  # noqa: E731
                               request.state.computer)
    if not path.startswith(NEEDS_SIGN_IN) or path in auth.PUBLIC:
        token = ACCOUNT.set(current() if path in auth.PUBLIC else None)
        try:
            return await call_next(request)
        finally:
            ACCOUNT.reset(token)
    acc = current()
    if acc is not None and not auth.sign_in_required() and request.headers.get("x-preview") and ready():
        # open mode only (no accounts, your own computer): look at the app as one doctor or as one hospital's admin
        acc = careteam.preview_account(request.headers["x-preview"], people()) or acc
    if acc is None:                                    # no browser pop-up: the app shows its own sign-in page
        return JSONResponse({"detail": "Please sign in"}, status_code=401)
    if not auth.allowed(acc["role"], path):
        auth.log("refused", acc["user"], acc["role"], request.state.computer, path)
        return JSONResponse({"detail": f"Your role ({acc['role']}) cannot open this"}, status_code=403)
    request.state.user, request.state.role = acc["user"], acc["role"]
    token = ACCOUNT.set(acc)
    try:
        response = await call_next(request)
    finally:
        ACCOUNT.reset(token)
    if response.status_code == 200 and path.startswith(tuple(LOGGED_PATHS)):   # who opened which record
        # the search part too (?patient=...), or a search for one person's results would not say whose record was opened
        auth.log("opened_record", acc["user"], acc["role"], request.state.computer,
                 f"{request.method} {path}" + (f"?{request.url.query}" if request.url.query else ""))
    return response


LOGGED_PATHS = utils.config("roles")["logged_person_paths"]
MAX_BODY_BYTES = 1_000_000              # the largest request the API accepts (its forms are a few hundred bytes)
# what a page from this server may load: its own files, the two web fonts and the map's background pictures
# (OpenStreetMap tiles); never another site's scripts or frames
CONTENT_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                  "font-src 'self' https://fonts.gstatic.com data:; "
                  "img-src 'self' data: blob: https://*.tile.openstreetmap.org; connect-src 'self'; "
                  "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def foreign_origin(request):
    """True when the request says it comes from a page of a different site than the one being asked."""
    from urllib.parse import urlsplit
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return True
    origin = request.headers.get("origin")
    if not origin:
        return False                                    # not a browser page (a script, a test): nothing to compare
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip().lower()
    return urlsplit(origin).netloc.lower() != host


@app.middleware("http")
async def harden(request, call_next):
    """Runs around everything else: refuses oversized requests and sets the security headers on every response.
    API answers hold patient data, so browsers and proxies are told never to store them."""
    from fastapi.responses import JSONResponse
    size = request.headers.get("content-length", "")
    if size.isdigit() and int(size) > MAX_BODY_BYTES:
        response = JSONResponse({"detail": "Request too large"}, status_code=413)
    elif request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") and foreign_origin(request):
        # a page from another site asking this server to change something (the browser adds Origin to such requests); the
        # sign-in cookie is SameSite=Strict already, this is a second layer. Tools without an Origin (scripts, tests) pass.
        response = JSONResponse({"detail": "Requests from another site are not accepted"}, status_code=403)
    else:
        response = await call_next(request)
    path = request.url.path
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if not path.startswith(("/docs", "/redoc")):       # the API reference page loads its viewer from a CDN
        h.setdefault("Content-Security-Policy", CONTENT_POLICY)
    if path.startswith("/api/"):
        h["Cache-Control"] = "no-store"
    if request.url.scheme == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.get("/api/health")
def health():
    """For monitoring: is the server up and does it have what it needs? No patient data. 503 when something is missing."""
    from fastapi.responses import JSONResponse
    checks = {"records_database": records_db.exists(),
              "people": os.path.exists(os.path.join(utils.OUTPUTS, "new", "people_final.csv")),
              "results": os.path.exists(os.path.join(utils.OUTPUTS, "results", "results.json")),
              "sign_in_required": auth.sign_in_required()}
    ready = all(v for k, v in checks.items() if k != "sign_in_required")
    return JSONResponse({"status": "ok" if ready else "not ready", "checks": checks}, status_code=200 if ready else 503)


def computer(request):
    return request.client.host if request.client else ""


def by(request):
    """Who did it, for the decision and consent logs."""
    return getattr(request.state, "user", "local")


def mine(p):
    """Only the people the signed-in account may see: a doctor with a care-team profile sees their panel (api/careteam.py),
    a doctor, outreach or admin account with a region sees that region (a hospital), a patient account sees their own
    record and the children they are guardian of; everyone else sees all."""
    acc = ACCOUNT.get()
    if not acc:
        return p
    if acc.get("role") in auth.SCOPE["person"]:
        # a patient sees their own family; a new patient with no record yet sees nobody (never the fall-through to everyone)
        return family_of(p, acc["person"]) if acc.get("person") else p.iloc[0:0]
    if acc.get("role") == "doctor" and careteam.profile(acc["user"]):
        # a care-team doctor: their own panel, a colleague's on leave, the duty list, break-glass and shared records
        return p[p.Id.isin(careteam.visible_ids(acc["user"], p))]
    if acc.get("region"):
        return p[p.region == acc["region"]]
    return p


def check_mine(pid):
    """A person outside the account's limits looks the same as one that does not exist (no hint either way)."""
    if mine(people())[lambda d: d.Id == pid].empty:
        raise HTTPException(404, "Person not found")
LOCK = threading.Lock()
FEEDBACK = os.path.join(utils.OUTPUTS, "doctor_feedback.csv")
CONSENT_LOG = os.path.join(utils.OUTPUTS, "consent_changes.csv")
EMERGENCY_LOG = os.path.join(utils.OUTPUTS, "emergency_alerts.csv")
PHONE_LOG = os.path.join(utils.OUTPUTS, "phone_log.csv")      # every phone-demo start and reply, replayed after a restart
RANK = {"Urgent": 0, "High": 1, "Routine": 2}
PAGE = simulation.DESIGN["page_sizes"]
PLAIN = utils.config("language")["patient_check_names"]   # what a check is called to a patient: never a disease, gene or risk score


def emergencies():
    """People who sent emergency words in the live chat and still need the nurse call-back."""
    if not os.path.exists(EMERGENCY_LOG):
        return pd.DataFrame(columns=["time", "person", "text"])
    e = pd.read_csv(EMERGENCY_LOG, dtype={"person": str})
    return e[e.status == "open"] if "status" in e.columns else e


# ------------------------------------------------------------------ helpers
def clean(x):
    """Make anything JSON-safe: NaN and inf become None, numpy types become Python types."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if (math.isnan(x) or math.isinf(x)) else float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.isoformat()
    if x is pd.NaT:
        return None
    return x


def records(df):
    return clean(df.replace({np.nan: None}).to_dict("records"))


def ready():
    return os.path.exists(os.path.join(utils.OUTPUTS, "new", "people_final.csv"))


@lru_cache(maxsize=2)
def people(mode="new"):
    p = pd.read_csv(os.path.join(utils.OUTPUTS, mode, "people_final.csv"),
                    dtype={"Id": str, "household_id": str, "guardian_id": str})
    for c in ("cards", "done", "detected", "diagnosed", "risk_reasons", "warning_signs", "data_used"):
        p[c] = p[c].map(json.loads)
    p["risks"] = p.risks.map(lambda s: json.loads(s) if isinstance(s, str) else {}) if "risks" in p.columns else [{}] * len(p)
    # the child model's score (outputs from before it existed have no such columns)
    p["child_reasons"] = p.child_reasons.map(lambda s: json.loads(s) if isinstance(s, str) else []) if "child_reasons" in p.columns else [[]] * len(p)
    for c in ("child_risk", "child_risk_low", "child_risk_high"):
        if c not in p.columns:
            p[c] = np.nan
    p["open_cards"] = p.cards.map(lambda cs: [c for c in cs if c["status"] in ("ready", "agreed", "awaiting_doctor")])
    p["rank"] = p.urgency.map(RANK).fillna(3)
    return p


@lru_cache(maxsize=2)
def events(mode="new"):
    return pd.read_csv(os.path.join(utils.OUTPUTS, mode, "events.csv"), dtype={"person": str})


@lru_cache(maxsize=2)
def messages(mode="new"):
    return pd.read_csv(os.path.join(utils.OUTPUTS, mode, "messages.csv"), dtype={"person": str})


@lru_cache(maxsize=1)
def results():
    return utils.load_json(os.path.join(utils.OUTPUTS, "results", "results.json"), {})


@lru_cache(maxsize=1)
def live():
    """A live copy of the system for the chat and consent (the same agents the simulation uses)."""
    import orchestrator
    import simulation
    ctx = orchestrator.build_context("new", simulation.DESIGN["simulation"]["live_demo_seed"], dict(simulation.DEFAULT_PARAMS), utils.run_date())
    # after a restart, bring back what was decided in the app: consent changes from the Why page
    if os.path.exists(CONSENT_LOG):
        for r in pd.read_csv(CONSENT_LOG, dtype={"person": str, "given_by": str}).itertuples():
            per = ctx.people.get(r.person)
            if per is None:
                continue
            consent.record(ctx, 0, r.person, r.purpose, str(r.granted).lower() == "true", r.given_by)
            if r.purpose == "contact":
                CONSENT_CHANGES[r.person] = consent.has(ctx, per, "contact") and consent.has(ctx, ctx.guardian(per), "contact")
    replay_phone(ctx)                                  # and the phone demo, with its bookings and STOP replies
    return ctx


def latest_feedback():
    """The doctor's newest decision per person, from the Doctor page."""
    if not os.path.exists(FEEDBACK):
        return {}
    f = pd.read_csv(FEEDBACK, dtype={"person": str})
    return f.groupby("person").decision.last().to_dict()


NEVER_HELD = set(utils.config("clinical_rules")["never_held_checks"])   # an abnormal result or an emergency is always followed up


def apply_decision(cards, decision):
    """Disagree puts the open cards on hold, except a results review or an emergency call-back, which a doctor
    cannot switch off from this list; Agree releases the cards waiting for a doctor to outreach."""
    if decision == "Disagree":
        return [dict(c, status="on_hold") if c["status"] in ("ready", "agreed", "awaiting_doctor") and c["check"] not in NEVER_HELD
                else c for c in cards]
    return [dict(c, status="agreed") if c["status"] == "awaiting_doctor" else c for c in cards]


def overlay(p):
    """Show what has changed since the last simulation run, everywhere: the doctor's Agree / Disagree decisions,
    consent changes on the Why page, and people touched in the live phone demo (bookings, opt-outs, cards)."""
    fb = latest_feedback()
    if not CONSENT_CHANGES and os.path.exists(CONSENT_LOG):
        live()                                         # first request after a restart: replay saved consent changes
    if not SESSIONS and not fb and not CONSENT_CHANGES:
        return p
    p = p.copy()
    if "live_demo" not in p.columns:
        p["live_demo"] = 0
    for pid, decision in fb.items():
        idx = p.index[p.Id == pid]
        if idx.empty:
            continue
        i = idx[0]
        cards = apply_decision(p.at[i, "cards"], decision)
        p.at[i, "cards"] = cards
        p.at[i, "open_cards"] = [c for c in cards if c["status"] in ("ready", "agreed", "awaiting_doctor")]
        p.at[i, "doctor_feedback"] = decision
        if decision == "Disagree" and not p.at[i, "open_cards"]:
            p.at[i, "state"] = "On hold"
    for pid, may in CONSENT_CHANGES.items():
        idx = p.index[p.Id == pid]
        if not idx.empty:
            p.at[idx[0], "may_contact"] = int(may)
    if not SESSIONS:
        return p
    ctx = live()
    if "live_demo" not in p.columns:
        p["live_demo"] = 0
    for pid in list(SESSIONS):
        per, idx = ctx.people.get(pid), p.index[p.Id == pid]
        if per is None or idx.empty:
            continue
        i = idx[0]
        p.at[i, "state"] = per.state
        p.at[i, "may_contact"] = int(consent.has(ctx, per, "contact") and consent.has(ctx, ctx.guardian(per), "contact"))
        p.at[i, "open_cards"] = [c for c in per.cards if c["status"] in ("ready", "agreed", "awaiting_doctor")]
        p.at[i, "live_demo"] = 1
    return p


def person_row(pid):
    p = mine(overlay(people()))                       # only people this account may see
    hit = p[p.Id == pid]
    if hit.empty:
        raise HTTPException(404, "Person not found")
    return hit.iloc[0]


def risk_text(r):
    if r.data_sufficient != 1 or pd.isna(r.risk):
        return "Not enough data to judge. Basic check-up suggested." if r.age >= utils.config("clinical_rules")["adult_age"] else "No risk score for children (rules only)."
    return f"Risk {r.risk:.0%} (range {r.risk_low:.0%} to {r.risk_high:.0%})"


HIDDEN_FROM_OUTREACH = "Clinical details are for doctors only."


def summary_row(r):
    """One person as the lists show them. Outreach staff get who and what is due, but no risk score and no clinical reasons."""
    d = _summary_row(r)
    if (ACCOUNT.get() or {}).get("role") == "outreach":
        d.update(risk=None, risk_low=None, risk_high=None, risk_band=None, risk_text=HIDDEN_FROM_OUTREACH, reasons=[])
    return d


def _summary_row(r):
    return {"id": r.Id, "short": mrn(r.Id), "age": int(r.age), "sex": r.sex, "region": r.region, "nationality": r.nationality,
            "language": r.language, "urgency": r.urgency, "priority": r.priority, "state": r.state, "is_child": bool(r.is_child),
            "risk": r.risk, "risk_low": r.risk_low, "risk_high": r.risk_high, "risk_text": risk_text(r),
            "risk_band": risk_band(r.risk) if not pd.isna(r.risk) else None,   # the list shows the band, not a combined %
            # warning signs first: they decide Urgent and High, so the doctor sees why before the risk reasons
            "reasons": list(r.warning_signs) + list(r.risk_reasons),
            "checks": list(dict.fromkeys(c["check"] for c in r.open_cards)),
            "may_contact": bool(getattr(r, "may_contact", r.consent)), "live_demo": bool(getattr(r, "live_demo", 0) or 0),
            "awaiting_doctor": any(c["status"] == "awaiting_doctor" for c in r.open_cards)}


def may_contact(p):
    """Current contact consent: after STOP replies and Why-page changes (falls back to the starting consent)."""
    return p.may_contact if "may_contact" in p.columns else p.consent


def append_csv(path, row):
    pd.DataFrame([row]).to_csv(path, mode="a", header=not os.path.exists(path), index=False)


# ------------------------------------------------------------------ overview
@app.get("/api/roles")
def roles():
    """The pages each role can open (config/roles.json): the app builds its menu from this, so roles are defined once."""
    return {"roles": auth.ROLE_PAGES}


def me_info(acc):
    if acc is None:
        return {"signed_in": False, "sign_in_required": True}
    label = None
    if acc.get("person"):
        r = people().set_index("Id")
        if acc["person"] in r.index:
            x = r.loc[acc["person"]]
            label = f"{'Child' if x.is_child else 'Adult'}, {x.age} y, {x.region}"
    pages = auth.ROLE_PAGES.get(acc["role"], [])
    if acc.get("awaiting_record"):
        pages = [x for x in pages if x == "mycare"]            # nothing else to open until the record is linked
    return {"signed_in": auth.sign_in_required(), "sign_in_required": auth.sign_in_required(), "awaiting_record": bool(acc.get("awaiting_record")),
            "user": acc["user"], "role": acc["role"], "pages": pages,
            # the name shown at the top: the person's full name from sign-up, else their user name
            # read from the accounts file on every request, so a name the admin changes shows at once
            "name": ((auth.load_users() or {}).get(acc["user"], {}).get("full_name")) or acc.get("name") or acc["user"],
            "region": acc.get("region"), "person": acc.get("person"), "person_label": label,
            # the hospital admin sees how many sign-ups are waiting (a badge on Accounts)
            "pending_requests": len(visible_requests(acc.get("region"))) if acc["role"] == auth.ADMIN else 0}


@app.get("/api/me")
def me():
    """Who is signed in, their role, pages and limits. Before signing in: sign_in_required tells the app to show the
    sign-in page; in open mode (no accounts) the app shows a role switch instead."""
    return me_info(ACCOUNT.get())


class Login(BaseModel):
    user: str
    password: str


@app.post("/api/login")
def login(body: Login, request: Request, response: Response):
    """Sign in: the session token goes into an HttpOnly, SameSite=Strict cookie that page scripts cannot read."""
    if not auth.sign_in_required():
        return me_info(ACCOUNT.get() or auth.who())
    user, where = body.user.strip(), computer(request)
    token, acc = auth.login(user, body.password, where)
    if token is None:
        asked = auth.pending_for(user, body.password)
        if asked:
            raise HTTPException(403, f"Your sign-up request (sent {asked[:10]}) is waiting for the hospital admin to check "
                                     "your details. You can sign in as soon as it is approved.")
        if auth.is_locked(user, where):
            raise HTTPException(429, f"Too many wrong passwords: try again in {auth.LOCKOUT_SECONDS // 60} minutes, "
                                     "or ask the hospital admin to unlock the account")
        raise HTTPException(401, "Wrong user name or password")
    # no max_age: the cookie ends when the browser closes, so the next person on this computer starts at sign-in
    # (the server still ends every session after one shift)
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/")
    return me_info(acc)


@app.post("/api/logout")
def logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.COOKIE) or request.headers.get("authorization", "")[7:], computer(request))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"signed_out": True}


class NewPassword(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/me/password")
def change_my_password(body: NewPassword, request: Request):
    """Everyone can change their own password (the old one is checked first)."""
    acc = ACCOUNT.get()
    users = auth.load_users()
    if not users or acc["user"] not in users:
        raise HTTPException(400, "This sign-in has no account to change (open mode or shared password)")
    # checked like a sign-in, so wrong guesses count towards the lock (a stolen session cannot guess the password)
    if auth.verify(acc["user"], body.old_password, computer(request)) is None:
        auth.log("password_change_failed", acc["user"], acc["role"], computer(request), "wrong old password")
        if auth.is_locked(acc["user"], computer(request)):
            raise HTTPException(429, f"Too many wrong passwords: try again in {auth.LOCKOUT_SECONDS // 60} minutes")
        raise HTTPException(400, "The current password is wrong")
    try:
        auth.set_password(acc["user"], body.new_password, by=acc["user"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"changed": True, "note": "Signed out everywhere: please sign in again with the new password."}


# ------------------------------------------------------------------ accounts (hospital admin only)
class AccountIn(BaseModel):
    name: str
    role: str
    password: str
    full_name: str = ""
    region: str = ""
    person: str = ""
    specialty: list[str] = []          # a doctor's care-team profile
    languages: list[str] = []
    panel_limit: int = 0


class PasswordIn(BaseModel):
    password: str


# A hospital admin (an admin account with a region) works inside that hospital only; the central admin (no region) sees all.
def admin_region():
    acc = ACCOUNT.get() or {}
    return acc.get("region") if acc.get("role") == auth.ADMIN else None


def account_region(rec):
    """The hospital an account belongs to: its region, or for a patient the region where their record lives."""
    if rec.get("region"):
        return rec["region"]
    if rec.get("person"):
        row = people()[lambda d: d.Id == rec["person"]]
        return row.region.iloc[0] if len(row) else None
    return None


def may_manage(name, rec):
    here = admin_region()
    return here is None or account_region(rec) == here or name == (ACCOUNT.get() or {}).get("user")


def managed_account(name):
    """The account the admin wants to change, or 404 when it is another hospital's (it looks like it does not exist)."""
    rec = (auth.load_users() or {}).get(name)
    if rec is None or not may_manage(name, rec):
        raise HTTPException(404, "No such account")
    return rec


def central_only():
    if admin_region() is not None:
        raise HTTPException(403, "Only the central admin can do this")


@app.get("/api/admin/users")
def admin_users():
    users = auth.load_users()
    locked = set(auth.locked_users())
    regions = sorted(people().region.dropna().unique().tolist()) if ready() else []
    here = admin_region()
    if here:
        regions = [here]
    return {"accounts_on": users is not None, "roles": list(auth.ROLE_PAGES), "regions": regions, "hospital": here,
            "specialties": {k: v["text"] for k, v in careteam.SPECIALTIES.items()},
            "scope": auth.SCOPE, "min_password": auth.MIN_PASSWORD,
            "users": [{"name": n, "role": r["role"], "region": r.get("region"), "person": r.get("person"),
                       "awaiting_record": bool(r.get("awaiting_record") and not r.get("person")), "signup_dob": r.get("signup_dob"),
                       "full_name": r.get("full_name"),
                       "specialty": careteam.profile(n)["specialty"] if r["role"] == "doctor" and careteam.profile(n) else [],
                       "locked": n in locked} for n, r in sorted((users or {}).items()) if may_manage(n, r)]}


@app.post("/api/admin/users")
def admin_create(body: AccountIn, request: Request):
    name = body.name.strip()
    if not name or not name.replace("_", "").replace(".", "").replace("-", "").isalnum():
        raise HTTPException(400, "Use letters, numbers, dots, dashes or underscores for the user name")
    region, person, here = body.region.strip(), body.person.strip(), admin_region()
    if here:
        # a hospital admin creates accounts for their own hospital only
        if body.role in auth.SCOPE["region"]:
            region = here
        elif body.role in auth.SCOPE["person"]:
            row = people()[lambda d: d.Id == person]
            if row.empty or row.region.iloc[0] != here:
                raise HTTPException(400, f"That record is not in {here}")
        else:
            raise HTTPException(403, "Only the central admin creates this kind of account")
    try:
        rec = auth.account_record(body.role, body.password, region, person)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.full_name.strip():
        rec["full_name"] = body.full_name.strip()[:120]      # shown at the top of the app instead of the user name
    if body.role == "doctor":                                 # the care-team profile
        spec = [careteam.match_specialty(s) for s in body.specialty]
        if any(s is None for s in spec):
            raise HTTPException(400, "Choose a specialty from the list")
        if spec:
            rec["specialty"] = spec
        if body.languages:
            rec["languages"] = [x.strip() for x in body.languages if x.strip()]
        if body.panel_limit:
            rec["panel_limit"] = max(1, min(int(body.panel_limit), 5000))
    auth.save_account(name, rec, by=by(request))
    return {"saved": name}


class LinkIn(BaseModel):
    person: str


@app.post("/api/admin/users/{name}/link")
def admin_link_record(name: str, body: LinkIn, request: Request):
    """A new patient's account was opened before the hospital had their record: link it now."""
    managed_account(name)
    here = admin_region()
    row = people()[lambda d: d.Id == body.person.strip()]
    if here and (row.empty or row.region.iloc[0] != here):
        raise HTTPException(400, f"That record is not in {here}")
    try:
        auth.link_record(name, body.person.strip(), by(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"linked": name}


@app.post("/api/admin/users/{name}/password")
def admin_reset(name: str, body: PasswordIn, request: Request):
    managed_account(name)
    try:
        auth.set_password(name, body.password, by=by(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"reset": name}


@app.post("/api/admin/users/{name}/unlock")
def admin_unlock(name: str, request: Request):
    managed_account(name)
    auth.unlock(name)
    auth.log("account_unlocked", by(request), auth.ADMIN, computer(request), name)
    return {"unlocked": name}


@app.post("/api/admin/users/{name}/remove")
def admin_remove(name: str, request: Request):
    managed_account(name)
    if name == by(request):
        raise HTTPException(400, "You cannot remove your own account while signed in with it")
    try:
        auth.remove_account(name, by=by(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"removed": name}


@app.get("/api/admin/access_log")
def admin_access_log(user: str = "", event: str = ""):
    if not os.path.exists(auth.ACCESS_LOG):
        return []
    a = pd.read_csv(auth.ACCESS_LOG, dtype=str).fillna("")
    a = a.drop(columns=["chain"], errors="ignore")      # the chain is for the Audit page's integrity check, not for the table
    if admin_region():                                  # a hospital admin reads their own hospital's accounts only
        a = a[a.user.isin({n for n, r in (auth.load_users() or {}).items() if may_manage(n, r)})]
    if user:
        a = a[a.user == user]
    if event:
        a = a[a.event == event]
    return records(a.iloc[::-1].head(utils.config("roles")["access_log_page_rows"]))


# ------------------------------------------------------------------ care teams: doctors, panels, break glass, sharing
class DoctorProfileIn(BaseModel):
    user: str
    specialty: list[str] | None = None
    languages: list[str] | None = None
    panel_limit: int | None = None
    duty: bool | None = None
    on_leave: bool | None = None
    backup: str | None = None            # "" clears it


class AssignIn(BaseModel):
    person: str
    specialty: str
    doctor: str | None = None            # None removes the assignment


class ApplyIn(BaseModel):
    doctors: list[str] = []              # empty = every doctor of the admin's hospital(s)


class BreakGlassIn(BaseModel):
    person: str
    reason: str


class ShareIn(BaseModel):
    person: str
    hospital: str
    days: int = 0


def scoped_doctors():
    here = admin_region()
    return [d for d in careteam.doctors() if not here or d["hospital"] == here]


def first_name(pid):
    return (str(identity().name.get(pid) or "").split() or [""])[0]


def unmanaged_doctors():
    """Doctor accounts without a care-team specialty: they still see their whole region until the admin gives them one."""
    here = admin_region()
    users = auth.load_users() or {}
    managed = {d["user"] for d in careteam.doctors()}
    out = []
    for n, r in sorted(users.items()):
        if r.get("role") == "doctor" and n not in managed and (not here or r.get("region") == here):
            out.append({"user": n, "name": r.get("full_name") or n, "hospital": r.get("region"), "specialty": [], "languages": r.get("languages") or [],
                        "panel_limit": int(r.get("panel_limit") or careteam.CFG["default_panel_limit"]), "duty": False, "backup": None,
                        "panel_size": 0, "on_leave": False})
    return out


@app.get("/api/admin/careteam")
def admin_careteam():
    p, here = people(), admin_region()
    assigned = careteam.load()["assignments"]
    docs = scoped_doctors()
    rows = []
    for h in [h for h in careteam.hospitals(p) if not here or h == here]:
        pp = p[p.region == h]
        has = pp.Id.isin(set(assigned))
        urgent = (pp.urgency == "Urgent") & (pp.open_cards.map(len) > 0)
        rows.append({"hospital": h, "people": int(len(pp)), "with_a_doctor": int(has.sum()), "urgent_without_doctor": int((urgent & ~has).sum()),
                     "doctors": sum(1 for d in docs if d["hospital"] == h)})
    return {"open_mode": careteam.open_mode(), "hospital": here, "hospitals": rows,
            "doctors": [{**d, "panel_size": careteam.panel_size(d["user"], assigned), "on_leave": careteam.on_leave(d["user"])} for d in docs],
            "not_in_a_team": unmanaged_doctors(),
            "specialties": {k: v["text"] for k, v in careteam.SPECIALTIES.items()},
            "languages": sorted(p.language.dropna().unique().tolist())}


@app.post("/api/admin/careteam/proposal")
def admin_careteam_proposal():
    """What the rules would assign now, nothing saved: per doctor and what is left over (and why)."""
    p, docs = people(), scoped_doctors()
    prop = careteam.propose(p, docs, hospital=admin_region())
    per_doctor, left = careteam.summarise(prop, p, docs)
    for d in per_doctor:
        for s in d["sample"]:
            s["mrn"] = mrn(s["id"])
    return clean({"total": len(prop["items"]), "per_doctor": per_doctor, "left": left})


@app.post("/api/admin/careteam/apply")
def admin_careteam_apply(body: ApplyIn, request: Request):
    prop = careteam.propose(people(), scoped_doctors(), only=set(body.doctors) or None, hospital=admin_region())
    n = careteam.apply(prop)
    auth.log("assignments_applied", by(request), auth.ADMIN, computer(request),
             f"{n} assignments" + (f" for {', '.join(body.doctors)}" if body.doctors else ""))
    return {"applied": n}


@app.get("/api/admin/careteam/find")
def admin_careteam_find(record: str = ""):
    """People (children too) by record number, for assigning one by hand: only what is needed to pick the right person."""
    v = record.strip().lower()
    if len(v) < 3:
        raise HTTPException(400, "Type at least 3 characters of the record number")
    p, here = people(), admin_region()
    p = p[p.region == here] if here else p
    ids = identity()
    p = p[p.Id.map(ids.MRN).fillna("").str.lower().str.contains(v, regex=False)].head(8)
    assigned = careteam.load()["assignments"]
    return clean([{"id": r.Id, "mrn": mrn(r.Id), "age": int(r.age), "sex": r.sex, "language": r.language, "hospital": r.region, "urgency": r.urgency,
                   "child": bool(r.is_child), "team": assigned.get(r.Id, {})} for r in p.itertuples()])


@app.post("/api/admin/careteam/assign")
def admin_careteam_assign(body: AssignIn, request: Request):
    p, here = people(), admin_region()
    row = p[p.Id == body.person]
    if row.empty or (here and row.region.iloc[0] != here):
        raise HTTPException(404, "Person not found")
    try:
        careteam.assign(body.person, body.specialty, body.doctor, p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.log("assignment_changed", by(request), auth.ADMIN, computer(request),
             f"{mrn(body.person)}: {body.specialty} -> {body.doctor or 'no doctor'}")
    return {"ok": True}


@app.post("/api/admin/careteam/doctor")
def admin_careteam_doctor(body: DoctorProfileIn, request: Request):
    docs = scoped_doctors()
    doc = next((d for d in docs + unmanaged_doctors() if d["user"] == body.user), None)
    if doc is None:
        raise HTTPException(404, "No such doctor")
    fields = {k: v for k, v in body.model_dump().items() if k != "user" and v is not None}
    if "specialty" in fields:
        spec = [careteam.match_specialty(s) for s in fields["specialty"]]
        if not spec or any(s is None for s in spec):
            raise HTTPException(400, "Choose at least one specialty from the list")
        fields["specialty"] = list(dict.fromkeys(spec))
    if "panel_limit" in fields:
        fields["panel_limit"] = max(1, min(int(fields["panel_limit"]), 5000))
    if fields.get("backup"):
        other = next((d for d in docs if d["user"] == fields["backup"]), None)
        if other is None or other["user"] == body.user or other["hospital"] != doc["hospital"]:
            raise HTTPException(400, "The backup must be another doctor of the same hospital")
    profile_fields = {k: v for k, v in fields.items() if k in auth.PROFILE_FIELDS}
    if profile_fields and not careteam.open_mode():
        try:
            auth.update_profile(body.user, profile_fields, by(request))
        except ValueError as e:
            raise HTTPException(400, str(e))
    careteam.set_profile(body.user, fields)
    if "on_leave" in fields or careteam.open_mode():
        auth.log("profile_changed", by(request), auth.ADMIN, computer(request), f"{body.user}: " + ", ".join(f"{k}={v}" for k, v in fields.items()))
    return {"ok": True}


@app.post("/api/admin/careteam/reset")
def admin_careteam_reset(request: Request):
    """Start the care-team demo over: no assignments, no leave, no break-glass or sharing (central admin only)."""
    central_only()
    careteam.reset()
    auth.log("assignments_applied", by(request), auth.ADMIN, computer(request), "care-team data cleared")
    return {"reset": True}


@app.get("/api/admin/careteam/panel")
def admin_careteam_panel(doctor: str, limit: int = 40):
    """Who is on one doctor's panel (a sample), so two doctors' panels can be compared side by side."""
    if next((d for d in scoped_doctors() if d["user"] == doctor), None) is None:
        raise HTTPException(404, "No such doctor")
    assigned = careteam.load()["assignments"]
    ids = [pid for pid, team in assigned.items() if doctor in team.values()]
    p = people()
    rows = p[p.Id.isin(ids)].assign(rank_=lambda d: d.urgency.map(careteam.RANK).fillna(3)).sort_values(["rank_", "Id"]).head(max(1, min(limit, 200)))
    return clean({"doctor": doctor, "total": len(ids),
                  "people": [{"id": r.Id, "mrn": mrn(r.Id), "age": int(r.age), "sex": r.sex, "language": r.language, "urgency": r.urgency,
                              "open": int(len(r.open_cards)), "specialty": [s for s, d in assigned[r.Id].items() if d == doctor]}
                             for r in rows.itertuples()]})


@app.get("/api/admin/careteam/activity")
def admin_careteam_activity():
    """Break-glass openings and families' sharing with other hospitals, newest first (inside the admin's hospital)."""
    d, here, stamp = careteam.load(), admin_region(), careteam.now()
    p = people().set_index("Id")
    doctors_here = {x["user"] for x in scoped_doctors()}
    home = lambda pid: p.region.get(pid)  # noqa: E731
    glass = [{"time": g["time"], "doctor": g["doctor"], "mrn": mrn(g["person"]), "home": home(g["person"]), "reason": g["reason"],
              "expires": g["expires"], "active": g["expires"] > stamp}
             for g in d["break_glass"] if not here or g["doctor"] in doctors_here or home(g["person"]) == here]
    shares = [{"time": g["time"], "by": g["by"], "mrn": mrn(g["person"]), "home": home(g["person"]), "hospital": g["hospital"],
               "expires": g["expires"], "active": (not g.get("revoked")) and g["expires"] > stamp, "revoked": g.get("revoked")}
              for g in d["grants"] if not here or g["hospital"] == here or home(g["person"]) == here]
    return {"break_glass": glass[::-1][:30], "shares": shares[::-1][:30]}


# ------------------------------------------------------------------ hospital admin: today, and the audit page
@app.get("/api/admin/today")
def admin_today():
    """What needs the hospital admin today, in one place: requests to decide, urgent people with no doctor, people who could not be
    reached, booked visits to confirm, break-glass openings that are open now, panels nearly full and sign-in trouble. A hospital admin
    sees their own hospital; the central admin sees all."""
    here, stamp = admin_region(), careteam.now()
    p = people()
    if here:
        p = p[p.region == here]
    ids = set(p.Id)
    d = careteam.load()
    assigned = set(d["assignments"])
    open_ = p.open_cards.map(len) > 0
    urgent = (p.urgency == "Urgent") & open_
    df = contact_log.contacts()
    df = df[df.person.isin(ids)]
    states = contact_log.states(df)
    row = p.set_index("Id")
    no_doc = p[urgent & ~p.Id.isin(assigned)].sort_values("priority", ascending=False)
    esc = [(pid, s) for pid, s in states.items() if s["status"] == "escalated" and pid in ids]
    waiting_urgent = int((urgent & p.state.isin(["Flagged", "Contacted"]) & (may_contact(p) == 1)
                          & ~p.Id.isin({k for k, s in states.items() if s["status"] != "open"})).sum())
    vt = visit_totals(df, p)
    missed_back = sum(1 for pid, s in states.items() if pid in ids and s["status"] != "closed" and s["last"].get("outcome") == "no_show")
    glass = [g for g in d["break_glass"] if g["expires"] > stamp and (not here or g["person"] in ids)]
    soon = (pd.Timestamp(stamp) + pd.Timedelta(minutes=60)).isoformat(timespec="seconds")
    docs = scoped_doctors()
    full = [{"name": x["name"], "hospital": x["hospital"], "panel": careteam.panel_size(x["user"], d["assignments"]), "limit": x["panel_limit"]}
            for x in docs if careteam.panel_size(x["user"], d["assignments"]) >= 0.9 * x["panel_limit"]]
    away = [x["name"] for x in docs if careteam.on_leave(x["user"])]
    e = emergencies()
    users = {n for n, r in (auth.load_users() or {}).items() if may_manage(n, r)}
    log = pd.read_csv(auth.ACCESS_LOG, dtype=str).fillna("") if os.path.exists(auth.ACCESS_LOG) else pd.DataFrame(columns=["time", "user", "event"])
    day_ago = (pd.Timestamp(stamp) - pd.Timedelta(hours=24)).isoformat(timespec="seconds")
    recent = log[log.time >= day_ago]
    if here:
        recent = recent[recent.user.isin(users)]
    failed = int((recent.event == "sign_in_failed").sum())
    locked = [n for n in auth.locked_users() if not here or n in users]
    n_requests, n_family = len(visible_requests(here)), len(family.pending(here))
    n_awaiting = sum(1 for n, r in (auth.load_users() or {}).items() if r.get("awaiting_record") and not r.get("person") and may_manage(n, r))
    work = [
        {"key": "emergencies", "count": int(e.person.isin(ids).sum()), "to": "doctor"},
        {"key": "urgent_no_doctor", "count": int(len(no_doc)), "to": "careteam"},
        {"key": "break_glass_open", "count": len(glass), "to": "audit"},
        {"key": "escalated", "count": len(esc), "to": "queue"},
        {"key": "visits_to_confirm", "count": vt["to_confirm"], "to": "queue"},
        {"key": "missed_visits", "count": missed_back, "to": "queue"},
        {"key": "signup_requests", "count": n_requests, "to": "accounts"},
        {"key": "family_requests", "count": n_family, "to": "accounts"},
        {"key": "awaiting_records", "count": n_awaiting, "to": "accounts"},
        {"key": "urgent_waiting", "count": waiting_urgent, "to": "queue"},
        {"key": "panels_full", "count": len(full), "to": "careteam"},
        {"key": "locked_accounts", "count": len(locked), "to": "accounts"},
        {"key": "audit_log_altered", "count": 0 if auth.verify_log()["ok"] else 1, "to": "audit"},
    ]
    return clean({
        "scope": here or "All hospitals", "time": stamp, "needs_action": sum(w["count"] for w in work if w["key"] not in ("urgent_waiting", "panels_full")),
        "work": work,
        "urgent_no_doctor": [{"mrn": mrn(r.Id), "age": int(r.age), "hospital": r.region, "language": r.language} for r in no_doc.head(8).itertuples()],
        "escalated": [{"mrn": mrn(pid), "age": int(row.loc[pid].age), "language": row.loc[pid].language,
                       "why": contact_log.OUTCOMES.get(s["last"].get("outcome"), contact_log.OUTCOMES["done"])["label"], "time": s["last"].get("time")}
                      for pid, s in esc[:8]],
        "break_glass": [{"doctor": g["doctor"], "mrn": mrn(g["person"]), "reason": g["reason"], "expires": g["expires"], "ending_soon": g["expires"] <= soon}
                        for g in sorted(glass, key=lambda g: g["expires"])[:10]],
        "panels": full, "on_leave": away,
        "visits": vt,
        "security": {"failed_sign_ins_24h": failed, "locked_accounts": locked},
    })


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
OPENED_KIND = (("/api/person/", "Clinical view"), ("/api/fhir/", "FHIR record"), ("/api/why/", "Why page"), ("/api/mycare", "My care"),
               ("/api/household", "Family view"), ("/api/journey", "Journey"))
SENSITIVE_EVENTS = {"account_created", "account_changed", "account_removed", "password_changed", "account_unlocked", "assignments_applied",
                    "assignment_changed", "profile_changed", "record_linked", "family_approved", "family_rejected", "signup_approved",
                    "signup_rejected", "break_glass", "share_record", "share_revoked", "audit_export"}


def audit_log(days=30):
    """The access log as the admin may read it (a hospital admin: their own hospital's accounts), newest first, from the last days."""
    if not os.path.exists(auth.ACCESS_LOG):
        return pd.DataFrame(columns=["time", "user", "role", "computer", "event", "detail"])
    a = pd.read_csv(auth.ACCESS_LOG, dtype=str).fillna("")
    if admin_region():
        a = a[a.user.isin({n for n, r in (auth.load_users() or {}).items() if may_manage(n, r)})]
    since = (pd.Timestamp(careteam.now()) - pd.Timedelta(days=days)).isoformat(timespec="seconds")
    return a[a.time >= since].iloc[::-1]


def readable(detail):
    """A log detail with record Ids turned into record numbers, so the page never shows a raw Id."""
    return UUID.sub(lambda m: mrn(m.group(0)), str(detail))


@app.get("/api/admin/audit")
def admin_audit():
    """For the hospital admin (and to show a reviewer): who opened which record and why, break-glass openings, family sharing, account
    changes and sign-in trouble. Counts, the latest rows and the top openers; the full log can be downloaded."""
    a = audit_log(30)
    stamp = pd.Timestamp(careteam.now())
    t = lambda **k: (stamp - pd.Timedelta(**k)).isoformat(timespec="seconds")  # noqa: E731
    opened = a[a.event == "opened_record"]
    o7 = opened[opened.time >= t(days=7)]
    days = [(stamp - pd.Timedelta(days=i)).date().isoformat() for i in range(6, -1, -1)]
    by_day = [{"day": dd, "opened": int((opened.time.str[:10] == dd).sum())} for dd in days]
    top = o7.groupby(["user", "role"]).size().sort_values(ascending=False).head(8)
    act = admin_careteam_activity()
    recent_opened = []
    for r in o7.head(15).itertuples():
        kind = next((lbl for pref, lbl in OPENED_KIND if f" {pref}" in f" {r.detail.split(' ', 1)[-1]}"), "Record")
        recent_opened.append({"time": r.time, "user": r.user, "role": r.role, "what": kind, "record": readable(UUID.search(r.detail).group(0)) if UUID.search(r.detail) else "-"})
    changes = [{"time": r.time, "user": r.user, "event": r.event, "detail": readable(r.detail)} for r in a[a.event.isin(SENSITIVE_EVENTS)].head(20).itertuples()]
    return clean({
        "scope": admin_region() or "All hospitals", "time": stamp.isoformat(timespec="seconds"),
        "kpis": {"opened_24h": int((opened.time >= t(hours=24)).sum()), "opened_7d": int(len(o7)), "people_opening_7d": int(o7.user.nunique()),
                 "break_glass_30d": len(act["break_glass"]), "shares_active": sum(1 for s in act["shares"] if s["active"]),
                 "failed_sign_ins_7d": int(((a.event == "sign_in_failed") & (a.time >= t(days=7))).sum()),
                 "refused_7d": int(((a.event == "refused") & (a.time >= t(days=7))).sum()),
                 "account_changes_30d": int(a.event.isin({"account_created", "account_changed", "account_removed", "password_changed"}).sum())},
        "by_day": by_day, "top_openers": [{"user": u, "role": r, "opened": int(n)} for (u, r), n in top.items()],
        "recent_opened": recent_opened, "break_glass": act["break_glass"][:15], "shares": act["shares"][:15], "changes": changes,
        "holds": ["Who signed in, from which computer, and failed sign-ins and lockouts", "Every patient record a person opened (which record, which view)",
                  "Every break-glass opening with the reason the doctor gave", "Every family sharing with another hospital, and its withdrawal",
                  "Account, password, role, care-team and sign-up decisions"],
        "never_holds": ["Passwords or sign-in tokens", "The text of notes, messages or clinical findings", "A person's name (record numbers only)"],
        "integrity_note": "No page or API in this app edits or deletes the log. Each line also carries a keyed hash of the line before it, so a "
                          "line that is edited, removed or inserted breaks every hash after it, and without the key nobody can rebuild them. "
                          "Keep the key (PP_AUDIT_KEY) outside this computer in a real deployment, and write down the head hash from time to time: "
                          "the removal of the newest lines is only visible against a head hash noted earlier.",
        "integrity": auth.verify_log(),
    })


@app.get("/api/admin/audit/export")
def admin_audit_export(request: Request, days: int = 30):
    """The log as a CSV file for a reviewer. Record Ids become record numbers; the export itself is logged."""
    from fastapi.responses import Response
    a = audit_log(max(1, min(days, 365)))
    a = a.assign(detail=a.detail.map(readable))[["time", "user", "role", "computer", "event", "detail"]]
    auth.log("audit_export", by(request), auth.ADMIN, computer(request), f"{len(a)} rows, last {days} days")
    return Response(a.to_csv(index=False), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=access_log.csv"})


@app.get("/api/careteam/demo")
def careteam_demo():
    """Open mode only: the made-up doctors and the hospitals, so the preview can switch between them."""
    if not careteam.open_mode() or not ready():
        return {"open_mode": False}
    return {"open_mode": True, "hospitals": careteam.hospitals(people()),
            "doctors": [{"user": d["user"], "name": d["name"], "hospital": d["hospital"], "specialty": d["specialty"]} for d in careteam.doctors()],
            "outreach": careteam.CFG["demo_outreach"]}


def my_profile():
    acc = ACCOUNT.get() or {}
    return acc, (careteam.profile(acc.get("user")) if acc.get("role") == "doctor" else None)


@app.get("/api/careteam/me")
def careteam_me():
    """A care-team doctor's own panel, who they cover, and what they opened outside it (a doctor without a profile: not managed)."""
    acc, prof = my_profile()
    if prof is None:
        return {"managed": False}
    d, p, stamp = careteam.load(), people(), careteam.now()
    ids = careteam.visible_ids(acc["user"], p)
    return {"managed": True, "name": prof["name"], "hospital": prof["hospital"], "specialty": prof["specialty"], "languages": prof["languages"],
            "duty": prof["duty"], "panel_limit": prof["panel_limit"], "panel": careteam.panel_size(acc["user"], d["assignments"]),
            "on_leave": careteam.on_leave(acc["user"]), "can_open": len(ids),
            "covering": [x["name"] for x in careteam.doctors() if d["leave"].get(x["user"]) and x["backup"] == acc["user"]],
            "duty_urgent": len(careteam.duty_ids(prof, p, d["assignments"], d["leave"])),
            "shared_with_us": len({g["person"] for g in d["grants"] if g["hospital"] == prof["hospital"] and not g.get("revoked") and g["expires"] > stamp}),
            "break_glass": [{"mrn": mrn(g["person"]), "reason": g["reason"], "expires": g["expires"]}
                            for g in d["break_glass"] if g["doctor"] == acc["user"] and g["expires"] > stamp]}


@app.get("/api/careteam/lookup")
def careteam_lookup(record: str = ""):
    """Find one person by their exact record number, with nothing clinical: age, sex and hospital, and whether they are
    already on the doctor's list. The first step of opening someone outside the doctor's own patients (break glass)."""
    acc, prof = my_profile()
    if prof is None:
        raise HTTPException(403, "Only care-team doctors use this")
    v = record.strip().lower()
    ids = identity()
    hit = ids[ids.MRN.str.lower() == v] if v else ids.iloc[0:0]
    p = people()
    row = p[p.Id.isin(hit.index)]
    if row.empty:
        return {"found": False}
    r = row.iloc[0]
    return clean({"found": True, "id": r.Id, "mrn": mrn(r.Id), "age": int(r.age), "sex": r.sex, "hospital": r.region,
                  "on_my_list": r.Id in careteam.visible_ids(acc["user"], p)})


@app.post("/api/careteam/break_glass")
def careteam_break_glass(body: BreakGlassIn, request: Request):
    """Open one patient outside the doctor's own patients for a few hours, after giving a reason. It is written to the access log and the
    hospital admin sees it."""
    acc, prof = my_profile()
    if prof is None:
        raise HTTPException(403, "Only care-team doctors use this")
    try:
        g = careteam.break_glass(acc["user"], body.person, body.reason, people())
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not g["already"]:
        auth.log("break_glass", acc["user"], acc["role"], computer(request), f"{mrn(body.person)} until {g['expires']}: {g['reason']}")
    return g


@app.get("/api/mycare/sharing")
def mycare_sharing():
    """For each person in the family: who they have shared their record with (another hospital, for how long) and the hospitals
    they could share with."""
    person = family_person()
    if not person:
        raise HTTPException(400, "Sharing is for patient accounts (linked to their own record)")
    p = people()
    places = careteam.hospitals(p)
    return {"default_days": careteam.CFG["sharing_default_days"], "max_days": careteam.CFG["sharing_max_days"],
            "people": [{"id": r.Id, "self": r.Id == person, "age": int(r.age), "first_name": first_name(r.Id), "home": r.region,
                        "shared": [{"hospital": g["hospital"], "until": g["expires"][:10]} for g in careteam.active_grants(r.Id)],
                        "options": [h for h in places if h != r.region]}
                       for r in family_of(p, person).sort_values("is_child").itertuples()]}


def sharing_target(body):
    person = family_person()
    p = people()
    row = family_of(p, person)[lambda d: d.Id == body.person] if person else p.iloc[0:0]
    if row.empty:
        raise HTTPException(404, "Person not found")
    if body.hospital not in careteam.hospitals(p) or body.hospital == row.region.iloc[0]:
        raise HTTPException(400, "Choose another hospital")
    return row.iloc[0]


@app.post("/api/mycare/sharing")
def mycare_share(body: ShareIn, request: Request):
    """The family lets another hospital's doctors open this person's record for a number of days. It can be taken back."""
    r = sharing_target(body)
    g = careteam.share(r.Id, body.hospital, body.days, by(request))
    auth.log("share_record", by(request), (ACCOUNT.get() or {}).get("role", ""), computer(request), f"{mrn(r.Id)} with {body.hospital} until {g['expires']}")
    return {"shared": body.hospital, "until": g["expires"][:10]}


@app.post("/api/mycare/sharing/revoke")
def mycare_unshare(body: ShareIn, request: Request):
    r = sharing_target(body)
    n = careteam.revoke(r.Id, body.hospital)
    auth.log("share_revoked", by(request), (ACCOUNT.get() or {}).get("role", ""), computer(request), f"{mrn(r.Id)} with {body.hospital}")
    return {"revoked": n}


# ------------------------------------------------------------------ adding a child to a family account
class FamilyIn(BaseModel):
    first_name: str
    dob: str
    relation: str
    record_number: str = ""
    note: str = ""
    family_name: str = ""
    sex: str = ""
    language: str = ""


@app.get("/api/mycare/family")
def mycare_family():
    """The parent's side of adding a child: how they can be related, and where their earlier requests stand."""
    person = family_person()
    if not person:
        raise HTTPException(400, "This is for patient accounts")
    return {"relations": family.RELATIONS, "sexes": family.SEXES, "languages": family.LANGUAGES, "requests": family.mine(person),
            "pending_limit": family.MAX_PENDING}


@app.post("/api/mycare/family/request")
def mycare_family_request(body: FamilyIn, request: Request):
    person = family_person()
    p = people()
    row = p[p.Id == person] if person else p.iloc[0:0]
    if row.empty or row.iloc[0].is_child == 1:
        raise HTTPException(400, "Only an adult's account can add a child")
    try:
        r = family.request(person, by(request), row.iloc[0].region, body.first_name, body.dob, body.relation, body.record_number, body.note,
                           body.family_name, body.sex, body.language)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.log("family_request", by(request), (ACCOUNT.get() or {}).get("role", ""), computer(request), f"{mrn(person)} asks to add a child ({r['relation']})")
    return {"saved": True, "id": r["id"], "message": "Sent to the hospital. They will check it against the child's record and tell you here."}


@app.get("/api/admin/family/requests")
def admin_family_requests():
    p, ids = people().set_index("Id"), identity()
    out = []
    for r in family.pending(admin_region()):
        who = p.loc[r["person"]] if r["person"] in p.index else None
        out.append({**{k: r.get(k, "") for k in ("id", "first_name", "family_name", "sex", "language", "dob", "relation", "record_number", "note", "time", "region")},
                    "parent": mrn(r["person"]), "parent_name": str(ids.name.get(r["person"]) or ""), "parent_age": int(who.age) if who is not None else None})
    return out


@app.get("/api/admin/family/find")
def admin_family_find(dob: str = "", record: str = ""):
    """Children of the admin's hospital by date of birth, record number or name, with who they are registered to."""
    dob, record = dob.strip(), record.strip().lower()
    if not dob and not record:
        raise HTTPException(400, "Give a date of birth, a record number or a name")
    p = people()
    if admin_region():
        p = p[p.region == admin_region()]
    ids = identity()
    p = p[p.is_child == 1].assign(born=p.Id.map(ids.BIRTHDATE).fillna(""), mrn=p.Id.map(ids.MRN).fillna(""), name=p.Id.map(ids.name).fillna(""))
    hit = p[p.mrn.str.lower().str.contains(record, regex=False) | p.name.str.lower().str.contains(record, regex=False)] if record else p[p.born == dob]
    return {"total": int(len(hit)), "children": [
        {"id": r.Id, "short": r.mrn or r.Id[:8], "name": r.name, "born": r.born, "age": int(r.age), "sex": r.sex, "language": r.language,
         "region": r.region, "registered_to": mrn(r.guardian_id) if isinstance(r.guardian_id, str) else None,
         "extra_guardians": len(family.co_guardians(r.Id))} for r in hit.head(8).itertuples()]}


class FamilyDecision(BaseModel):
    child: str = ""
    reason: str = ""


@app.post("/api/admin/family/{rid}/approve")
def admin_family_approve(rid: str, body: FamilyDecision, request: Request):
    p = people()
    kid = p[p.Id == body.child]
    if admin_region() and (kid.empty or kid.iloc[0].region != admin_region()):
        raise HTTPException(400, f"That record is not in {admin_region()}")
    try:
        r = family.decide(rid, True, by(request), child=body.child, region=admin_region(), child_region=None if kid.empty else kid.iloc[0].region,
                          child_is_child=bool((not kid.empty) and kid.iloc[0].is_child == 1),
                          registered_guardian=None if kid.empty else kid.iloc[0].guardian_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.log("family_approved", by(request), auth.ADMIN, computer(request), f"{mrn(r['person'])} may look after {mrn(body.child)}")
    return {"approved": True}


@app.post("/api/admin/family/{rid}/reject")
def admin_family_reject(rid: str, body: FamilyDecision, request: Request):
    try:
        r = family.decide(rid, False, by(request), reason=body.reason, region=admin_region())
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.log("family_rejected", by(request), auth.ADMIN, computer(request), f"{mrn(r['person'])}: {r['reason']}")
    return {"rejected": True}


# ------------------------------------------------------------------ sign-up (public) and approval (hospital admin)
class SignupIn(BaseModel):
    role: str
    user: str
    password: str
    details: dict = {}


@app.post("/api/signup")
def signup(body: SignupIn, request: Request):
    """Anyone can ask for an account; the hospital admin checks the details and approves it (the very first hospital
    admin is active at once)."""
    try:
        status, message = auth.signup(body.role, body.user, body.password, body.details, computer(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": status, "message": message, "user": body.user.strip()}


def results_source(r):
    """Where the result numbers come from: the 60-day pilot simulation and how many runs it averages, or live hospital
    data once results.json says "source": "live". The pages show it as a small source line."""
    seeds = (r.get("seed_spread") or {}).get("seeds") or []
    return {"kind": r.get("source", "simulation"), "runs": len(seeds) or 1}


@app.get("/api/public/info")
def public_info():
    """What the landing page may show before signing in: the sign-up form fields, regions and headline totals only
    (no person-level data)."""
    r = results() if ready() else {}
    head = None
    if r:
        n, o, cv = r.get("new", {}), r.get("old", {}), r.get("clinical_value", {})
        head = {"found_new": cv.get("found_new"), "found_old": cv.get("found_old"),
                "followup_new": n.get("followup_within_30_days"), "followup_old": o.get("followup_within_30_days"),
                "well_child_new": (n.get("well_child") or {}).get("people_rate"), "well_child_old": (o.get("well_child") or {}).get("people_rate"),
                "adult_new": (n.get("adult_screening") or {}).get("people_rate"), "adult_old": (o.get("adult_screening") or {}).get("people_rate")}
        sm = (r.get("seed_spread") or {}).get("summary") or {}
        if sm:                                          # the same averages as the Overview and Results pages
            avg = lambda k: (sm.get(k) or {}).get("mean")  # noqa: E731
            head.update({"found_new": round(avg("found_new")), "found_old": round(avg("found_old")),
                         "followup_new": avg("followup_new"), "followup_old": avg("followup_old"),
                         "well_child_new": avg("well_child_new"), "well_child_old": avg("well_child_old"),
                         "adult_new": avg("adult_screening_new"), "adult_old": avg("adult_screening_old")})
        head["source"] = results_source(r)
    return clean({"fields": auth.SIGNUP_FIELDS, "optional": auth.SIGNUP_OPTIONAL, "min_password": auth.MIN_PASSWORD,
                  "regions": sorted(people().region.dropna().unique().tolist()) if ready() else [],
                  "accounts_exist": bool(auth.load_users()), "headline": head,
                  "emergency": utils.config("clinical_rules")["emergency_number"]})


class Decision(BaseModel):
    region: str = ""
    person: str = ""
    without_record: bool = False         # a new patient: open the account now, link the hospital record when it exists


def visible_requests(here):
    """The sign-up requests an admin may decide: all for the central admin; for a hospital admin the patient requests
    (linked to a record of their hospital when approved) and staff who asked for their hospital or named none."""
    if here is None:
        return auth.pending_requests()
    return [r for r in auth.pending_requests()
            if r["role"] in auth.SCOPE["person"] or ((r["details"].get("region") or here) == here and r["role"] in auth.SCOPE["region"])]


@app.get("/api/admin/requests")
def admin_requests():
    return visible_requests(admin_region())


@app.post("/api/admin/requests/{rid}/approve")
def admin_approve(rid: str, body: Decision, request: Request):
    here, region, person = admin_region(), body.region.strip() or None, body.person.strip() or None
    if here:
        wanted = next((r for r in auth.pending_requests() if r["id"] == rid), None)
        if wanted is None:
            raise HTTPException(400, "No such pending request")
        if wanted["role"] in auth.SCOPE["region"]:
            region = here                                      # their staff belong to their hospital
        elif wanted["role"] in auth.SCOPE["person"]:
            if person or not body.without_record:
                row = people()[lambda d: d.Id == person]
                if row.empty or row.region.iloc[0] != here:
                    raise HTTPException(400, f"That record is not in {here}")
            else:
                region = here                                   # a new patient registers at this hospital
        else:
            raise HTTPException(403, "Only the central admin approves this kind of account")
    try:
        user = auth.decide_request(rid, True, by(request), region, person, without_record=body.without_record)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"approved": user}


@app.post("/api/admin/requests/{rid}/reject")
def admin_reject(rid: str, request: Request):
    try:
        user = auth.decide_request(rid, False, by(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"rejected": user}


@lru_cache(maxsize=1)
def identity():
    """Record number (MRN), name and date of birth of every person, from the hospital records database (records.py).
    Only the hospital admin's record search shows names; every other page shows the record number."""
    if records_db.exists():
        d = records_db.read("patient", ["Id", "MRN", "GIVEN_NAME", "FAMILY_NAME", "BIRTHDATE"]).fillna("")
        d["name"] = (d.GIVEN_NAME + " " + d.FAMILY_NAME).str.strip()
        return d.set_index("Id")[["MRN", "name", "BIRTHDATE"]]
    f = os.path.join(utils.ADJUSTED, "patients.csv")               # database not built yet: the CSV copy, no names
    d = pd.read_csv(f, usecols=["Id", "BIRTHDATE"], dtype=str) if os.path.exists(f) else pd.DataFrame(columns=["Id", "BIRTHDATE"])
    return d.assign(MRN=d.Id.str[:8], name="").set_index("Id")[["MRN", "name", "BIRTHDATE"]]


def mrn(pid):
    """The record number shown on screen (the first 8 characters of the internal Id when there is no database)."""
    ids = identity()
    return ids.MRN.get(pid) or str(pid)[:8]


@app.get("/api/admin/find_person")
def admin_find_person(dob: str = "", record: str = ""):
    """Helps the hospital admin link a patient sign-up to the right record: adults born on that date (or, when
    nobody is, in that year), or a record number or name. Adults only: a parent's children come with their account."""
    dob, record = dob.strip(), record.strip().lower()
    if not dob and not record:
        raise HTTPException(400, "Give a date of birth, a record number or a name")
    p = people()
    if admin_region():
        p = p[p.region == admin_region()]               # a hospital admin links patients of their own hospital
    ids = identity()
    p = p[p.is_child == 0].assign(born=p.Id.map(ids.BIRTHDATE).fillna(""), mrn=p.Id.map(ids.MRN).fillna(""),
                                  name=p.Id.map(ids.name).fillna(""))
    exact = True
    if record:
        hit = p[p.mrn.str.lower().str.contains(record, regex=False) | p.name.str.lower().str.contains(record, regex=False)
                | p.Id.str.lower().str.startswith(record)]
    else:
        hit = p[p.born == dob]
        if hit.empty and len(dob) >= 4:
            hit, exact = p[p.born.str[:4] == dob[:4]].sort_values("born"), False
    kids = people().guardian_id.value_counts()
    rows = [{"id": r.Id, "short": r.mrn or r.Id[:8], "name": r.name, "born": r.born, "age": int(r.age), "sex": r.sex,
             "nationality": r.nationality, "language": r.language, "region": r.region, "children": int(kids.get(r.Id, 0))}
            for r in hit.head(8).itertuples()]
    return {"exact": exact, "total": int(len(hit)), "people": rows}


# ------------------------------------------------------------------ my care (patient or family account)
def patient_messages(m):
    """The conversation as the family saw it: clinic messages, their own replies and our answers; staff notes such as
    "[staff call: ...]" become a plain line, never internal wording."""
    out = []
    for r in m.itertuples():
        text = "" if pd.isna(r.text) else str(r.text)
        if r.channel == "reply":
            said, _, answer = text.partition(" -> ")
            out.append({"from": "you", "channel": "", "text": said})
            if answer:
                out.append({"from": "clinic", "channel": "", "text": answer})
        elif text.startswith("["):
            out.append({"from": "clinic", "channel": r.channel, "text": f"{r.channel} from the care team"})
        else:
            out.append({"from": "clinic", "channel": r.channel, "text": text})
    return out

@lru_cache(maxsize=1)
def facility_table():
    f = utils.load_facilities()
    return f.assign(shown=f.name.fillna(f.kind))


def nearest_clinic(r):
    """The clinic the system calls nearest to this person's home: its name (English when the map data has one, else
    Arabic), the usual trip, and a link that opens that clinic on a map. The link holds the clinic's position only,
    never the person's home. Several clinics can share a name, so the one closest to the home is taken."""
    if pd.isna(r.nearest_facility) or pd.isna(r.travel_min):
        return None
    out = {"name": str(r.nearest_facility), "name_ar": None, "minutes": int(round(r.travel_min)), "map_url": None}
    f = facility_table()
    same = f[(f.shown == r.nearest_facility) & f.lat.notna() & f.lon.notna()]
    if same.empty or pd.isna(r.home_lat) or pd.isna(r.home_lon):
        return out
    dx = (same.lon - r.home_lon) * math.cos(math.radians(r.home_lat))
    best = same.loc[(dx ** 2 + (same.lat - r.home_lat) ** 2).idxmin()]
    out["name"] = str(best.name_en) if pd.notna(best.name_en) else out["name"]
    out["name_ar"] = str(best.name_ar) if pd.notna(best.name_ar) else None
    out["map_url"] = f"https://www.google.com/maps/search/?api=1&query={best.lat:.5f},{best.lon:.5f}"
    return out


VISIT_KIND = {"wellness": "Check-up", "ambulatory": "Clinic visit", "outpatient": "Clinic visit", "urgentcare": "Urgent care visit",
              "emergency": "Emergency visit", "inpatient": "Hospital stay", "snf": "Hospital stay",
              "home": "Home or remote visit", "hospice": "Home or remote visit", "virtual": "Home or remote visit"}


def family_person():
    """Whose family My care shows: the signed-in patient's own record. In open mode (no accounts, your own computer) the
    largest family stands in, so "Preview as patient" has something to show. None for any other account."""
    acc = ACCOUNT.get() or {}
    if acc.get("person"):
        return acc["person"]
    if auth.sign_in_required():
        return None
    p = people()
    kids = p[(p.is_child == 1) & p.guardian_id.notna()].guardian_id.value_counts()
    return kids.index[0] if len(kids) else None


def family_of(p, person):
    """That person and the children they are guardian of: the ones registered to them and the ones the hospital approved them
    to add (api/family.py). A guardian covers children only: once a young person is an adult their record is theirs alone."""
    return p[(p.Id == person) | (((p.guardian_id == person) | p.Id.isin(family.extra_children(person))) & (p.is_child == 1))]


def hospitals_near(lat, lon, n=3):
    """The nearest named hospitals to a home, by straight-line distance. The map link holds the hospital's position, never the home.
    It is a straight line, not a route, so no drive time is promised."""
    f = facility_table()
    h = f[(f.kind == "hospital") & f.lat.notna() & f.lon.notna() & (f.name_en.notna() | f.name.notna() | f.name_ar.notna())].copy()
    if h.empty or pd.isna(lat) or pd.isna(lon):
        return []
    dx = (h.lon - lon) * math.cos(math.radians(lat)) * 111.32
    dy = (h.lat - lat) * 110.57
    h["km"] = (dx ** 2 + dy ** 2) ** 0.5
    out, seen = [], set()
    for r in h.sort_values("km").itertuples():
        name = str(r.name_en) if pd.notna(r.name_en) else (str(r.name) if pd.notna(r.name) else str(r.name_ar))
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "name_ar": str(r.name_ar) if pd.notna(r.name_ar) else None, "km": round(float(r.km), 1),
                    "minutes": max(3, int(round(float(r.km) / 45 * 60))),
                    "map_url": f"https://www.google.com/maps/search/?api=1&query={r.lat:.5f},{r.lon:.5f}"})
        if len(out) == n:
            break
    return out


@app.get("/api/mycare/urgent")
def mycare_urgent(pid: str = ""):
    """What a family sees when someone needs a hospital now: the emergency number, then the nearest hospitals to that person's home.
    It is not a booking and not a diagnosis."""
    person = family_person()
    if not person:
        raise HTTPException(400, "This is for patient accounts (linked to their own record)")
    p = family_of(people(), person)
    who = pid or person
    hit = p[p.Id == who]
    if hit.empty:                                                   # someone outside the family looks like no one
        raise HTTPException(404, "Person not found")
    r = hit.iloc[0]
    em = utils.config("clinical_rules")["emergency_number"]
    return clean({"emergency": em, "hospitals": hospitals_near(r.home_lat, r.home_lon),
                  "advice": [f"If it is life-threatening, call {em} or go to the nearest hospital emergency department now.",
                             "Take the person's Emirates ID and any medicines they use.",
                             "For a child, an adult stays with them the whole time."]})


@app.get("/api/mycare/{pid}/history")
def mycare_history(pid: str):
    """Past visits and vaccines of one person in the signed-in patient's family, read-only and in plain words: the date and
    the kind of visit (never the reason or a diagnosis), and each vaccine with its dates."""
    person = family_person()
    if not person:
        raise HTTPException(400, "History is for patient accounts (linked to their own record)")
    if family_of(people(), person)[lambda d: d.Id == pid].empty:        # someone outside the family looks like no one
        raise HTTPException(404, "Person not found")
    if not records_db.exists():
        return {"available": False, "visits": [], "vaccines": []}
    enc = records_db.read("encounters", ["START", "ENCOUNTERCLASS"], patient=pid)
    imm = records_db.read("immunizations", ["DATE", "DESCRIPTION"], patient=pid)
    enc = enc.assign(day=enc.START.astype(str).str[:10]).sort_values("day", ascending=False).head(12)
    visits = [{"date": r.day, "kind": VISIT_KIND.get(str(r.ENCOUNTERCLASS), "Clinic visit")} for r in enc.itertuples()]
    vaccines = []
    for name, g in imm.assign(day=imm.DATE.astype(str).str[:10]).groupby("DESCRIPTION", sort=False):
        days = sorted(set(g.day))
        vaccines.append({"name": re.sub(r"\s*\((?:procedure|finding|situation)\)$", "", str(name)), "dates": days})
    vaccines.sort(key=lambda v: v["dates"][0])
    return {"available": True, "visits": visits, "vaccines": vaccines}


@app.get("/api/mycare")
def mycare():
    """What a signed-in patient sees: their own record and their children's, in plain words: what is due, any
    booked visit, the messages the clinic sent them, and their consent. Never risk scores, reasons or genetics."""
    if (ACCOUNT.get() or {}).get("awaiting_record"):
        em = utils.config("clinical_rules")["emergency_number"]
        return {"awaiting_record": True, "people": [], "emergency": em, "help": f"Emergency: call {em}"}
    person = family_person()
    if not person:
        raise HTTPException(400, "My care is for patient accounts (linked to their own record)")
    p = family_of(overlay(people()), person)
    ctx, m, ev, ids = live(), messages(), events(), identity()
    seen = recorded_visits(contact_log.contacts())          # what staff recorded about a visit: attended, missed or moved
    out = []
    for r in p.sort_values("is_child").itertuples():
        mine_ev = ev[ev.person == r.Id]
        last = mine_ev[mine_ev.event.isin(["booked", "attended", "missed_visit"])].tail(1)
        visit = None
        if len(last) and last.event.iloc[0] == "booked":     # a booking not yet attended or missed
            when, _, how = last.detail.iloc[0].split(";")[0].partition(",")
            try:
                visit = {"date": (utils.run_date() + pd.Timedelta(days=int(when.split()[-1]))).date().isoformat(),
                         "how": how.strip()}
            except ValueError:
                visit = None
        per = ctx.people.get(r.Id)
        out.append({"id": r.Id, "who": "You" if r.Id == person else f"Your child ({r.age} y)",
                    "self": r.Id == person, "age": int(r.age),
                    # the first name, so a parent can tell their children apart (the account's own family only)
                    "first_name": (str(ids.name.get(r.Id) or "").split() or [""])[0],
                    "due": list(dict.fromkeys(PLAIN.get(c["check"], c["check"]) for c in r.open_cards
                                              if c["check"] in PLAIN)),
                    "done": list(dict.fromkeys(PLAIN.get(c, c) for c in r.done if c in PLAIN)),
                    "booked": visit,
                    "last_visit": ({"outcome": seen[r.Id][0], "date": seen[r.Id][1][:10]} if r.Id in seen else None),
                    # the clinic nearest their home, the usual trip there and a map link (the same for the whole household)
                    "clinic": nearest_clinic(r),
                    "messages": patient_messages(m[m.person == r.Id].tail(20)),
                    "may_contact": bool(consent.has(ctx, per, "contact")) if per is not None else bool(r.consent)})
    return clean({"people": out, "help": f"Emergency: call {utils.config('clinical_rules')['emergency_number']}",
                  "emergency": utils.config("clinical_rules")["emergency_number"]})


@app.get("/api/status")
def status():
    return {"ready": ready(), "today": utils.run_date().date().isoformat()}


@app.get("/api/overview")
def overview():
    if not ready():
        raise HTTPException(503, "Run python orchestrator.py and python evaluate.py first")
    p, r = mine(people()), results()
    acc = ACCOUNT.get() or {}
    e = emergencies()
    return clean({
        "role": acc.get("role"),
        # what needs a person today, shown at the top of the overview
        "emergencies": int(e.person.isin(p.Id).sum()),
        "pending_requests": len(auth.pending_requests()) if acc.get("role") == auth.ADMIN else None,
        "pending_family": len(family.pending(admin_region())) if acc.get("role") == auth.ADMIN else None,
        "awaiting_records": sum(1 for n, r in (auth.load_users() or {}).items() if r.get("awaiting_record") and not r.get("person") and may_manage(n, r))
        if acc.get("role") == auth.ADMIN else None,
        "people": len(p), "children": int(p.is_child.sum()),
        "open": int((p.open_cards.map(len) > 0).sum()),
        "urgent": int(((p.urgency == "Urgent") & (p.open_cards.map(len) > 0)).sum()),
        "awaiting_doctor": int(p.open_cards.map(lambda cs: any(c["status"] == "awaiting_doctor" for c in cs)).sum()),
        "regions": sorted(p.region.dropna().unique().tolist()),
        "headline": {k: r.get(k) for k in ("new", "old", "clinical_value")} if r else None,
        "seed_spread": {k: (r.get("seed_spread") or {}).get(k) for k in ("seeds", "summary")} if r and r.get("seed_spread") else None,
        "source": results_source(r) if r else None,
    })


# ------------------------------------------------------------------ doctor view
@app.get("/api/doctor/list")
def doctor_list(urgency: str = "Urgent,High", region: str = "", awaiting_only: bool = False, limit: int = None):
    limit = limit or PAGE["doctor_list"]
    p = mine(overlay(people())).copy()
    urgent_now = set(emergencies().person)           # emergency words in the live chat: Urgent, at the very top
    if urgent_now:
        hit = p.Id.isin(urgent_now)
        p.loc[hit, "urgency"], p.loc[hit, "rank"] = "Urgent", -1
        p.loc[hit, "open_cards"] = p.loc[hit, "open_cards"].map(
            lambda cs: [{"check": "emergency_callback", "status": "agreed"}] + list(cs))
    q = p[p.open_cards.map(len) > 0]
    q = q[q.urgency.isin(urgency.split(","))] if urgency else q
    if region:
        q = q[q.region.isin(region.split(","))]
    if awaiting_only:
        q = q[q.open_cards.map(lambda cs: any(c["status"] == "awaiting_doctor" for c in cs))]
    q = q.sort_values(["rank", "priority"], ascending=[True, False])
    return {"total": int(len(q)), "items": [clean(summary_row(r)) for r in q.head(limit).itertuples()]}


@app.get("/api/person/{pid}")
def person(pid: str):
    r = person_row(pid)
    band = risk_band(r.risk)
    fb = []
    if os.path.exists(FEEDBACK):
        f = pd.read_csv(FEEDBACK, dtype={"person": str})
        fb = records(f[f.person == pid])
    return clean({**summary_row(r), "data_used": r.data_used, "warning_signs": r.warning_signs, "risk_reasons": r.risk_reasons,
                  "cards": r.cards, "household_id": r.household_id, "guardian_id": r.guardian_id,
                  "travel_min": r.travel_min, "nearest_facility": r.nearest_facility, "insurance": r.insurance,
                  "area_type": r.area_type, "detected": r.detected, "diagnosed": r.diagnosed, "done": r.done,
                  "summary": summaries.clinician_summary(band, list(r.risk_reasons), list(r.warning_signs),
                                                     r.urgency if isinstance(r.urgency, str) else None),
                  "conditions": condition_status(r), "normal_result_day": r.risk_normal_day,
                  "clinic_answers": clinic_answers.get(pid), "child_model": None, "feedback": fb})        # children have no risk score: their invitations come from the rules


def condition_status(r):
    """Per condition, what the doctor should see: a finding at a visit replaces the prediction, a known diagnosis is
    not predicted, otherwise the chance of having it undiagnosed now, with its range, band and own main factors."""
    out = []
    risks = r.risks if isinstance(r.risks, dict) else {}
    for c in risk_model_conditions():
        name = {"diabetes": "Diabetes", "hypertension": "High blood pressure"}.get(c, c)
        if c in (r.detected or {}):
            out.append({"condition": c, "name": name, "status": "found", "day": r.detected[c]})
        elif int(getattr(r, f"known_{c}", 0) or 0):
            out.append({"condition": c, "name": name, "status": "known"})
        elif risks.get(c):
            s = risks[c]
            extra = clinic_score(r.Id) if c == "diabetes" else None          # re-worked with waist and lifestyle answers, when the clinic added them
            if extra:
                s = {**s, **extra}
            out.append({"condition": c, "name": name, "status": "score", "risk": s["risk"], "low": s["low"], "high": s["high"],
                        "reasons": s["reasons"], "band": risk_band_for(s["risk"], c),
                        **({"note": "Worked out again with the waist and lifestyle answers the clinic recorded."} if extra else {})})
        elif c == "hypertension" and bp_reading(r.Id):
            out.append({"condition": c, "name": name, "status": "reading", **bp_reading(r.Id)})
        else:
            out.append({"condition": c, "name": name, "status": "none"})
    if any(o["condition"] == "diabetes" and o["status"] == "score" for o in out):
        second = ml_opinion(r.Id)               # beside the published score, never instead of it
        if second:
            out.append(second)
    heart = heart_risk(r.Id)
    if heart:
        out.append(heart)
    return out


@lru_cache(maxsize=1)
def feature_rows():
    return utils.load_features().set_index("Id")


@lru_cache(maxsize=1)
def risk_bundle():
    from models import risk_scores
    return risk_scores.load(utils.load_features())


def clinic_score(pid):
    """The diabetes chance, range and reasons worked out again for one person with the answers a clinic recorded (waist, daily exercise,
    daily vegetables); None when there are none. The same published score and the same UAE scaling, only fewer unknown items."""
    ans = clinic_answers.get(pid)
    f = feature_rows()
    if not ans or pid not in f.index:
        return None
    from models import risk_scores
    num = lambda v: np.nan if v is None else float(v)  # noqa: E731
    row = f.loc[[pid]].assign(waist=num(ans.get("waist_cm")), exercise_daily=num(ans.get("exercise_daily")), veg_daily=num(ans.get("veg_daily")))
    s = risk_scores.score(risk_bundle(), row).iloc[0]
    if pd.isna(s.risk):
        return None
    return {"risk": float(s.risk), "low": float(s.risk_low), "high": float(s.risk_high), "reasons": list(s.risk_reasons)}


class AnswersIn(BaseModel):
    waist_cm: float | None = None
    exercise_daily: bool | None = None
    veg_daily: bool | None = None


@app.post("/api/person/{pid}/answers")
def person_answers(pid: str, body: AnswersIn, request: Request):
    """A doctor (or the admin) records waist circumference and two lifestyle answers for a patient they may open; the diabetes score is
    then worked out again for that person with a narrower range."""
    r = person_row(pid)
    if r.is_child == 1:
        raise HTTPException(400, "These answers are for adults")
    try:
        clinic_answers.save(pid, by(request), body.waist_cm, body.exercise_daily, body.veg_daily)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.log("answers_saved", by(request), (ACCOUNT.get() or {}).get("role", ""), computer(request), f"{mrn(pid)}: waist and lifestyle answers")
    return {"saved": True, "diabetes": clinic_score(pid)}


def bp_reading(pid):
    """The adult's last recorded blood pressure and its guideline category. There is no predicted score for high blood pressure:
    blood pressure is measured, so the reading and its date are what the doctor sees (and how old it is)."""
    from models import risk_scores
    f = feature_rows()
    if pid not in f.index:
        return None
    x = f.loc[pid]
    if x.age < utils.config("clinical_rules")["adult_age"] or pd.isna(x.sbp) or pd.isna(x.dbp):
        return None
    when = str(x.get("sbp_date") or "")[:10]
    return {"sbp": int(round(x.sbp)), "dbp": int(round(x.dbp)), "date": when or None, "category": risk_scores.bp_category(x.sbp, x.dbp),
            "months_ago": None if pd.isna(x.get("months_since_sbp")) else int(round(x.months_since_sbp)),
            "note": "Guideline category of the last reading (ACC/AHA 2017), not a prediction."}


def heart_risk(pid):
    """The 10-year heart-disease chance from the published pooled cohort equations (ages 40 to 79 with the measurements), as
    information for the doctor: not scaled to the UAE and never used for anyone's priority."""
    from models import risk_scores
    f = feature_rows()
    if pid not in f.index:
        return None
    s = risk_scores.ascvd(f.loc[[pid]]).iloc[0]
    if s["risk"] is None or pd.isna(s["risk"]):
        return None
    return {"condition": "cardiovascular", "name": "Heart disease, next 10 years", "status": "score", "risk": float(s["risk"]), "low": float(s["risk"]),
            "high": float(s["risk"]), "reasons": s["reasons"], "band": s["band"],
            "note": "Published formula (ACC/AHA 2013), information only: not scaled to the UAE and it does not change anyone's priority."}


def ml_opinion(pid):
    """A second opinion on undiagnosed diabetes from a model trained on real NHANES adults (models/ml_risk.py), shown next to the
    published score as information for the doctor: scaled to the UAE level, switched by config/models.json, and never used for
    anyone's priority."""
    from models import ml_risk
    f = feature_rows()
    if pid not in f.index:
        return None
    s = ml_risk.second_opinion(f.loc[[pid]])
    if not s:
        return None
    return {"condition": "diabetes_ml", "name": "Diabetes, machine-learning second opinion", "status": "score", "risk": s["risk"], "low": s["risk"],
            "high": s["risk"], "reasons": s["reasons"], "band": s["band"], "inputs_recorded": s["measured"], "inputs_total": s["of"],
            "note": "Trained on real survey data (NHANES) and scaled to the UAE level. Information only: it does not change anyone's priority."}


def risk_model_conditions():
    """The conditions a doctor sees a row for: diabetes has a published-score chance; high blood pressure is a measured finding."""
    from models.risk_scores import SHOWN
    return SHOWN


def risk_band_for(risk, condition):
    from models.risk_scores import band
    return band(risk, condition)


class Feedback(BaseModel):
    decision: str
    note: str = ""


@app.post("/api/person/{pid}/feedback")
def feedback(pid: str, body: Feedback, request: Request):
    person_row(pid)
    if body.decision not in ("Agree", "Disagree"):
        raise HTTPException(400, "decision must be Agree or Disagree")
    if body.decision == "Disagree" and len((body.note or "").strip()) < 3:
        raise HTTPException(400, "Please say why you disagree (a few words): it helps improve the flags.")
    with LOCK:
        append_csv(FEEDBACK, {"time": datetime.now().isoformat(timespec="seconds"), "person": pid,
                              "decision": body.decision, "note": body.note, "by": by(request)})
        per = live().people.get(pid)                  # the live system acts on it too (outreach, phone demo)
        if per is not None:
            per.cards = apply_decision(per.cards, body.decision)
            per.doctor_feedback = body.decision
            open_left = [c for c in per.cards if c["status"] in ("ready", "agreed", "awaiting_doctor")]
            if body.decision == "Disagree" and not open_left:
                per.state, per.awaiting_reply = "On hold", False
            elif per.state in ("None", "Done", "On hold") and any(c["status"] in ("agreed", "ready") for c in per.cards):
                per.state = "Flagged"
        kept = [c["check"] for c in apply_decision(person_row(pid).cards, body.decision)
                if c["status"] in ("ready", "agreed", "awaiting_doctor")]
    if body.decision == "Agree":
        effect = "Case released to outreach."
    elif kept:
        effect = f"Other checks put on hold; still followed up: {', '.join(kept)} (a result review or emergency cannot be held)."
    else:
        effect = "Person put on hold; contact stopped."
    return {"saved": True, "effect": effect}


# ------------------------------------------------------------------ outreach queue
def staff_identity():
    """Who is working the list: the account's user name and the name shown for them."""
    acc = ACCOUNT.get() or {}
    user = acc.get("user") or "local"
    return user, (auth.load_users() or {}).get(user, {}).get("full_name") or acc.get("name") or ("Staff on this computer" if user == "local" else user)


def staff_channel(channel):
    return {"Voice call": "Phone call", "Staff call": "Phone call", "Community health worker visit": "Visit by a health worker"}.get(channel, channel)


@app.get("/api/queue")
def queue(n: int = None):
    """Today's contact list: people with an open care gap, most urgent first, without anyone already contacted (closed, waiting
    for a retry, or escalated). A person someone else is contacting right now is shown with their name."""
    n = n or PAGE["queue"]
    ctx, p = live(), mine(overlay(people()))
    df = contact_log.contacts()
    states = contact_log.states(df)
    hide = {pid for pid, s in states.items() if s["status"] != "open"}
    # a person whose visit was missed comes back to rebook (once the day's wait is over), whatever their state was
    missed = {pid for pid, s in states.items() if s["status"] == "open" and s["last"].get("outcome") == "no_show"}
    wanted = ((p.open_cards.map(len) > 0) & p.state.isin(["Flagged", "Contacted"])) | p.Id.isin(missed)
    q = p[wanted & (may_contact(p) == 1) & ~p.Id.isin(hide)]
    q = q.sort_values(["rank", "priority"], ascending=[True, False])
    claims, me = contact_log.active_claims(), staff_identity()[0]
    items = []
    for r in q.head(n).itertuples():
        per = ctx.people[r.Id]
        rec = ctx.guardian(per)
        card = sorted(r.open_cards, key=lambda c: c["invite_by_day"])[0] if len(r.open_cards) else \
            {"check": "missed_visit", "invite_by_day": 0, "decision": "Outreach invite"}
        channel = outreach.channel_for(rec)
        held = claims.get(r.Id)
        items.append({"id": r.Id, "short": mrn(r.Id), "urgency": r.urgency, "contact": "Guardian of child" if rec is not per else "Person",
                      "channel": channel, "planned_channel": staff_channel(channel), "language": rec.row.language,
                      "best_time": outreach.contact_window(ctx, rec, 0),
                      "about": "Rebook the missed visit" if card["check"] == "missed_visit" else PLAIN.get(card["check"], card["check"]),
                      "care_option": outreach.care_option(ctx, per, card["check"]),
                      "deadline_days": DEADLINE_DAYS.get(r.urgency, DEADLINE_DAYS["Routine"]), "needs_doctor": card["decision"] == "Clinician decides",
                      "staff_call": channel in ("Voice call", "Community health worker visit"),
                      "tries": states.get(r.Id, {}).get("tries", 0),
                      # a visit that was missed: the person is back on the list to rebook
                      "missed_visit": states.get(r.Id, {}).get("last", {}).get("outcome") == "no_show",
                      "claimed_by": held["name"] if held and held["by"] != me else None, "claimed_until": held["until"][11:16] if held else None,
                      "claimed_by_me": bool(held and held["by"] == me)})
    mine_ids = set(p.Id)
    today = contact_log.now()[:10]
    calls = df[~df.outcome.isin(contact_log.VISIT_OUTCOMES)]                      # a recorded visit is not a contact
    return clean({"waiting": int(len(q)), "urgent": int((q.urgency == "Urgent").sum()), "items": items,
                  "contacted_today": int(((calls.time.str[:10] == today) & calls.person.isin(mine_ids)).sum()),
                  "escalated": sum(1 for pid, s in states.items() if s["status"] == "escalated" and pid in mine_ids),
                  "channels": contact_log.CHANNELS,
                  "outcomes": {k: v["label"] for k, v in contact_log.OUTCOMES.items() if k != "done" and k not in contact_log.VISIT_OUTCOMES}})


def _pct(a, b):
    return round(100 * a / b, 1) if b else None


_PROPOSALS = {}


def proposal_cache(here, p, docs):
    """What the care-team rules would assign now (who is left out and why), kept for a minute and until the care-team file or the
    doctors change: working it out takes most of a second and the manager's panel asks for it on every refresh."""
    path = careteam.store_path()
    key = (here, os.path.getmtime(path) if os.path.exists(path) else None, tuple(sorted((d["user"], d["panel_limit"], tuple(d["specialty"])) for d in docs)), len(p))
    hit = _PROPOSALS.get(here)
    if hit and hit[0] == key and time.time() - hit[1] < 60:
        return hit[2]
    prop = careteam.propose(p, docs, hospital=here)
    _PROPOSALS[here] = (key, time.time(), prop)
    return prop


@app.on_event("startup")
def warm_caches():
    """Load the big tables once, in the background, so the first person to open a page does not wait for them."""
    def run():
        try:
            if ready():
                people(), events(), messages(), identity(), results(), feature_rows(), journey_index()
                live()                                       # the live copy of the system (consent, chat): the slowest to build
        except Exception:                                  # warming is a convenience: never stop the server over it
            pass
    threading.Thread(target=run, daemon=True).start()


@app.get("/api/operations")
def operations():
    """For the programme manager (and admins): how the outreach and the care teams are doing, as totals only. No person, no
    record number and no staff name appears here; a hospital admin sees their own hospital."""
    p, here = people(), admin_region()
    if here:
        p = p[p.region == here]
    ids = set(p.Id)
    df = contact_log.contacts()
    df = df[df.person.isin(ids)].copy()
    visits = visit_totals(df, p)
    df = df[~df.outcome.isin(contact_log.VISIT_OUTCOMES)].copy()                  # contacts only; recorded visits are counted apart
    states = {pid: s for pid, s in contact_log.states(df).items()}
    OUT = {k: v for k, v in contact_log.OUTCOMES.items() if k not in contact_log.VISIT_OUTCOMES}
    reached_set = {"agreed", "later", "declined", "needs_doctor", "done"}
    stamp = contact_log.now()
    today, week = stamp[:10], (pd.Timestamp(stamp) - pd.Timedelta(days=7)).isoformat()
    n = len(df)
    reached = int(df.outcome.isin(reached_set).sum())
    mix = [{"outcome": k, "label": OUT[k]["label"], "count": int((df.outcome == k).sum())} for k in OUT if k != "done" or (df.outcome == "done").any()]
    chan = []
    for c in contact_log.CHANNELS:
        g = df[df.channel == c]
        chan.append({"channel": c, "attempts": int(len(g)), "reached": int(g.outcome.isin(reached_set).sum()),
                     "reached_pct": _pct(int(g.outcome.isin(reached_set).sum()), len(g))})
    # time from the first attempt to the first time someone was reached (the moment a person was flagged is not recorded)
    hours = []
    for _, g in df.groupby("person"):
        t = pd.to_datetime(g.time)
        hit = g[g.outcome.isin(reached_set)]
        if not hit.empty:
            hours.append((pd.to_datetime(hit.time.iloc[0]) - t.iloc[0]).total_seconds() / 3600)
    people_contacted = len(states)
    escalated = sum(1 for s in states.values() if s["status"] == "escalated")
    agreed = int((df.outcome == "agreed").sum())
    outreach_out = {"contacts": n, "people": people_contacted, "today": int((df.time.str[:10] == today).sum()), "this_week": int((df.time >= week).sum()),
                    "reached_pct": _pct(reached, n), "no_answer_pct": _pct(int((df.outcome == "no_answer").sum()), n),
                    "escalated": escalated, "escalated_pct": _pct(escalated, people_contacted),
                    "agreed_pct": _pct(agreed, reached), "mix": mix, "channels": chan,
                    "hours_to_reach_median": round(float(pd.Series(hours).median()), 1) if hours else None, "reached_people": len(hours),
                    "waiting_now": int(((p.open_cards.map(len) > 0) & ~p.Id.isin({k for k, s in states.items() if s["status"] != "open"})).sum())}
    # care-team coverage
    d = careteam.load()
    assigned = d["assignments"]
    docs = scoped_doctors()
    rows, near_full = [], 0
    for h in careteam.hospitals(p):
        pp = p[p.region == h]
        has = pp.Id.isin(set(assigned))
        urgent = (pp.urgency == "Urgent") & (pp.open_cards.map(len) > 0)
        rows.append({"hospital": h, "people": int(len(pp)), "with_a_doctor": int(has.sum()), "with_a_doctor_pct": _pct(int(has.sum()), len(pp)),
                     "urgent_without_doctor": int((urgent & ~has).sum()), "doctors": sum(1 for x in docs if x["hospital"] == h)})
    for x in docs:
        if careteam.panel_size(x["user"], assigned) >= 0.9 * x["panel_limit"]:
            near_full += 1
    prop = proposal_cache(here, p, docs)
    left = [{"hospital": x["hospital"], "specialty": x["specialty"], "people": x["people"], "why": x["why"]} for x in prop["left"]]
    coverage = {"hospitals": rows, "doctors": len(docs), "panels_near_full": near_full, "left_out": left,
                "left_out_people": sum(x["people"] for x in left), "with_a_doctor": sum(r["with_a_doctor"] for r in rows),
                "urgent_without_doctor": sum(r["urgent_without_doctor"] for r in rows)}
    # safeguards: counts only
    mine_ids = ids
    since = (pd.Timestamp(stamp) - pd.Timedelta(days=30)).isoformat()
    bg = [g for g in d["break_glass"] if g["person"] in mine_ids]
    sh = [g for g in d["grants"] if g["person"] in mine_ids]
    safeguards = {"break_glass_30d": sum(1 for g in bg if g["time"] >= since), "break_glass_total": len(bg),
                  "shares_active": sum(1 for g in sh if not g.get("revoked") and g["expires"] > stamp),
                  "shares_30d": sum(1 for g in sh if g["time"] >= since), "shares_revoked": sum(1 for g in sh if g.get("revoked"))}
    cfg = careteam.CFG
    policy = [{"name": "Default maximum patients per doctor", "value": cfg["default_panel_limit"], "unit": "people"},
              {"name": "Break glass lasts", "value": cfg["break_glass_hours"], "unit": "hours"},
              {"name": "Reason needed for break glass", "value": cfg["break_glass_min_reason"], "unit": "characters at least"},
              {"name": "Family sharing lasts by default", "value": cfg["sharing_default_days"], "unit": "days"},
              {"name": "Longest family sharing", "value": cfg["sharing_max_days"], "unit": "days"},
              {"name": "Person held while contacted", "value": contact_log.CLAIM_MINUTES, "unit": "minutes"},
              {"name": "No answer: try again after", "value": int(OUT["no_answer"]["retry"].total_seconds() // 3600), "unit": "hours"},
              {"name": "Asked to be called later: try again after", "value": OUT["later"]["retry"].days, "unit": "days"},
              {"name": "Tries without an answer before escalating", "value": contact_log.MAX_TRIES, "unit": "tries"}]
    policy.append({"name": "Missed visit: back on the list after", "value": contact_log.OUTCOMES["no_show"]["retry"].days, "unit": "days"})
    # how much of the missing waist and lifestyle information clinics have collected (counts only)
    scored = int(p.risk.notna().sum())
    got = len(set(clinic_answers.load()) & set(p[p.risk.notna()].Id))
    collection = {"scored_adults": scored, "answers_recorded": got, "answers_pct": _pct(got, scored)}
    return clean({"scope": here or "All hospitals", "outreach": outreach_out, "visits": visits, "collection": collection, "coverage": coverage,
                  "safeguards": safeguards, "policy": policy})


# ------------------------------------------------------------------ booked visits: attended or missed
CONFIRM_WITHIN_DAYS = 7          # staff confirm what happened to a visit dated up to a week ahead (or already past)
REMINDER_WITHIN_DAYS = 2         # a visit in the next two days is due a reminder


def sim_today():
    """The system's own 'today': the last day of the run (a real deployment reads the clock)."""
    return utils.run_date() + pd.Timedelta(days=int(events().day.max()))


def booked_visits(p):
    """The latest booked visit of each person (people of p) that the simulation has not yet closed, as rows with a date."""
    ev = events()
    ev = ev[ev.person.isin(set(p.Id)) & ev.event.isin(["booked", "attended", "missed_visit"])].sort_values(["person", "day"], kind="stable")
    last = ev.groupby("person").tail(1)
    rows = []
    for r in last.itertuples():
        if r.event != "booked":
            continue
        when, _, rest = str(r.detail).split(";")[0].partition(",")
        try:
            day = int(when.split()[-1])
        except ValueError:
            continue
        rows.append({"id": r.person, "date": (utils.run_date() + pd.Timedelta(days=day)).date().isoformat(),
                     "how": rest.split(",")[0].strip() or "Clinic visit"})
    return pd.DataFrame(rows, columns=["id", "date", "how"])


def recorded_visits(df):
    """Each person's latest visit outcome recorded by staff: {person: (outcome, time)}."""
    v = df[df.outcome.isin(contact_log.VISIT_OUTCOMES)]
    return {r.person: (r.outcome, r.time) for r in v.sort_values("time", kind="stable").groupby("person").tail(1).itertuples()}


def visit_totals(df, p):
    """Totals for the manager: visits staff have recorded and how many booked visits are waiting to be confirmed (no names)."""
    done = recorded_visits(df)
    attended = sum(1 for o, _ in done.values() if o == "attended")
    missed = sum(1 for o, _ in done.values() if o == "no_show")
    moved = sum(1 for o, _ in done.values() if o == "rescheduled")
    b = booked_visits(p)
    horizon = (sim_today() + pd.Timedelta(days=CONFIRM_WITHIN_DAYS)).date().isoformat()
    to_confirm = int(((b.date <= horizon) & ~b.id.isin(set(done))).sum()) if len(b) else 0
    return {"recorded": len(done), "attended": attended, "no_show": missed, "rescheduled": moved,
            "attendance_pct": _pct(attended, attended + missed), "to_confirm": to_confirm}


@app.get("/api/visits")
def visits_list(status: str = "to_confirm", n: int = 150):
    """Booked visits of the people this account may see, with what happened. status: to_confirm (dated within the next week or
    already past, and not yet recorded), upcoming, attended, no_show, rescheduled, all. Staff record the outcome; a missed visit
    returns the person to the contact list. No clinical detail: who (adult or child, age, language), the date, how, and what is recorded."""
    p = mine(overlay(people()))
    df = contact_log.contacts()
    done = recorded_visits(df)
    me = p.set_index("Id")
    today = sim_today().date()
    items = []
    for r in booked_visits(p).itertuples():
        x = me.loc[r.id]
        out, when = done.get(r.id, (None, None))
        days = (pd.Timestamp(r.date).date() - today).days
        status_now = out or ("to_confirm" if days <= CONFIRM_WITHIN_DAYS else "upcoming")
        items.append({"id": r.id, "short": mrn(r.id), "who": "Child" if x.is_child else "Adult", "age": int(x.age), "language": x.language,
                      "date": r.date, "how": r.how, "status": status_now, "recorded_at": when,
                      "reminder_due": out is None and days <= REMINDER_WITHIN_DAYS})
    for pid, (out, when) in done.items():                                       # outcomes recorded for people without a booking row
        if pid in me.index and pid not in {i["id"] for i in items}:
            x = me.loc[pid]
            items.append({"id": pid, "short": mrn(pid), "who": "Child" if x.is_child else "Adult", "age": int(x.age), "language": x.language,
                          "date": when[:10], "how": "", "status": out, "recorded_at": when, "reminder_due": False})
    counts = {k: sum(1 for i in items if i["status"] == k) for k in ("to_confirm", "upcoming", "attended", "no_show", "rescheduled")}
    want = {"all": None, "missed": "no_show", "moved": "rescheduled"}.get(status, status)
    shown = [i for i in items if want is None or i["status"] == want]
    shown.sort(key=lambda i: (i["status"] != "to_confirm", i["date"]))
    attended, missed = counts["attended"], counts["no_show"]
    return clean({"counts": counts, "attendance_pct": _pct(attended, attended + missed), "items": shown[:max(1, min(n, 500))], "total": len(shown),
                  "outcomes": {k: contact_log.OUTCOMES[k]["label"] for k in ("attended", "no_show", "rescheduled")}})


class VisitIn(BaseModel):
    id: str
    outcome: str
    note: str = ""


@app.post("/api/visits/outcome")
def visits_outcome(body: VisitIn, request: Request):
    """Record what happened to a booked visit. A missed visit brings the person back to the contact list the next day to rebook; after
    three missed visits they are escalated. Attended closes the person."""
    check_mine(body.id)
    if body.outcome not in contact_log.VISIT_OUTCOMES:
        raise HTTPException(400, "Choose attended, missed or moved")
    user, name = staff_identity()
    try:
        row = contact_log.record(body.id, user, name, "", body.outcome, body.note)
    except contact_log.Conflict as e:
        raise HTTPException(409, str(e))
    auth.log("visit_outcome", user, (ACCOUNT.get() or {}).get("role", ""), computer(request), f"{mrn(body.id)}: {body.outcome}")
    st = contact_log.states()[body.id]
    return {"saved": True, "outcome": contact_log.OUTCOMES[body.outcome]["label"], "back_at": row["next_try"] or None,
            "escalated": st["status"] == "escalated"}


class QueueDone(BaseModel):
    ids: list[str]


class QueueId(BaseModel):
    id: str


class ContactIn(BaseModel):
    id: str
    channel: str = ""
    outcome: str
    note: str = ""


@app.post("/api/queue/claim")
def queue_claim(body: QueueId):
    """Start contacting this person: nobody else can record a contact for them for 30 minutes."""
    check_mine(body.id)
    user, name = staff_identity()
    try:
        c = contact_log.claim(body.id, user, name)
    except contact_log.Conflict as e:
        raise HTTPException(409, str(e))
    return {"until": c["until"][11:16]}


@app.post("/api/queue/release")
def queue_release(body: QueueId):
    check_mine(body.id)
    return {"released": contact_log.release(body.id, staff_identity()[0])}


@app.post("/api/queue/contact")
def queue_contact(body: ContactIn, request: Request):
    """Save the result of one contact: how, what happened, a short note. The person leaves everyone's list, or comes back
    later (asked to be called later, no answer), or is escalated (a wrong number, three no-answers)."""
    check_mine(body.id)
    user, name = staff_identity()
    try:
        row = contact_log.record(body.id, user, name, body.channel, body.outcome, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except contact_log.Conflict as e:
        raise HTTPException(409, str(e))
    auth.log("outreach_contact", user, (ACCOUNT.get() or {}).get("role", ""), computer(request), f"{mrn(body.id)} {row['channel'] or '-'}: {body.outcome}")
    st = contact_log.states()[body.id]
    esc = st["status"] == "escalated"
    # an escalated person is not tried again by the list, so there is no "back at" time to report
    return {"saved": True, "outcome": contact_log.OUTCOMES[body.outcome]["label"], "back_at": None if esc else (row["next_try"] or None), "escalated": esc}


class SendIn(BaseModel):
    id: str
    channel: str


@app.post("/api/queue/send")
def queue_send(body: SendIn, request: Request):
    """Send the approved message for this person's open check by SMS or WhatsApp, to the number the family gave (the guardian's for a
    child). It is refused without consent, a number or an approved text, and it is only a practice until the provider is set up."""
    check_mine(body.id)
    ctx = live()
    per = ctx.people[body.id]
    rec = ctx.guardian(per)
    if not chat.may_contact(ctx, per):
        raise HTTPException(400, "This person does not allow messages. Call them only if they asked for a call.")
    number = messaging.get_number(rec.pid)
    if not number:
        raise HTTPException(400, f"There is no mobile number for {mrn(body.id)}. Ask them to add it in My care, or call them instead.")
    row = person_row(body.id)                                           # the same cards the queue shows
    card = sorted(row.open_cards, key=lambda c: c["invite_by_day"])[0] if len(row.open_cards) else {"check": "missed_visit"}
    audience = "guardian" if per.is_child else "adult"
    text, own = outreach.safe_message(ctx, card["check"], audience, rec.row.language, outreach.care_option(ctx, per, card["check"]))
    if not text:
        raise HTTPException(400, "There is no approved message for this check. Please call instead.")
    user, name = staff_identity()
    try:
        res = messaging.send(body.channel, number, text, staff=user, record=mrn(body.id))
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.log("message_sent", user, (ACCOUNT.get() or {}).get("role", ""), computer(request), f"{mrn(body.id)} {body.channel}: {res['status']}")
    return {**res, "mode": messaging.mode(), "language": rec.row.language, "in_own_language": bool(own), "characters": len(text)}


@app.get("/api/admin/outbox")
def admin_outbox(limit: int = 50):
    """Messages sent (or practised) from the queue: who, which record, which channel, the result and the last four digits. No text."""
    return {"mode": messaging.mode(), "rows": messaging.outbox(max(1, min(limit, 500)))}


@app.get("/api/mycare/phone")
def mycare_phone():
    person = family_person()
    if not person:
        raise HTTPException(400, "This is for patient accounts")
    return {"masked": messaging.masked(person)}


class PhoneIn(BaseModel):
    number: str


@app.post("/api/mycare/phone")
def mycare_phone_set(body: PhoneIn, request: Request):
    person = family_person()
    if not person:
        raise HTTPException(400, "This is for patient accounts")
    try:
        messaging.set_number(person, body.number, by(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.log("phone_saved", by(request), (ACCOUNT.get() or {}).get("role", ""), computer(request), f"{mrn(person)}: mobile number saved")
    return {"saved": True, "masked": messaging.masked(person)}


@app.post("/api/mycare/phone/clear")
def mycare_phone_clear(request: Request):
    person = family_person()
    if not person:
        raise HTTPException(400, "This is for patient accounts")
    messaging.clear_number(person)
    auth.log("phone_removed", by(request), (ACCOUNT.get() or {}).get("role", ""), computer(request), f"{mrn(person)}: mobile number removed")
    return {"saved": True, "masked": None}


@app.post("/api/queue/done")
def queue_done(body: QueueDone):
    """The older one-tick way to close people; kept for scripts. It records the contact as "Marked done"."""
    user, name = staff_identity()
    for pid in body.ids:
        check_mine(pid)
    for pid in body.ids:
        contact_log.record(pid, user, name, "", "done")
    return {"saved": len(body.ids)}


@app.get("/api/queue/contacts")
def queue_contacts(staff: str = "", outcome: str = "", days: int = 0, limit: int = 200, mine_only: str = ""):
    """Who has been contacted: when, by whom, how, what happened, plus the people who need another way to be reached.
    Outreach staff start on their own contacts and can switch to the team; the hospital admin sees everyone and a line per staff member."""
    p = mine(people()).set_index("Id")
    df = contact_log.contacts()
    df = df[df.person.isin(p.index)]
    everyone = sorted({s for s in df.staff.map(lambda s: s) if s})
    names = {r.staff: r.name for r in df.itertuples() if r.staff}
    role = (ACCOUNT.get() or {}).get("role", "")
    me_user, me_name = staff_identity()
    own = (role == "outreach") if mine_only == "" else mine_only in ("1", "true")
    if days:
        df = df[df.time >= (datetime.now() - timedelta(days=int(days))).isoformat(timespec="seconds")]
    # a line per staff member for the period (admins only; outreach staff are not shown colleagues side by side)
    by_staff = []
    if role != "outreach":
        for u, g in df[df.staff != ""].groupby("staff"):
            calls = g[~g.outcome.isin(contact_log.VISIT_OUTCOMES)]
            by_staff.append({"user": u, "name": names.get(u) or u, "contacts": int(len(calls)),
                             "reached": int(calls.outcome.isin(["agreed", "later", "declined", "needs_doctor"]).sum()),
                             "agreed": int((calls.outcome == "agreed").sum()),
                             "no_answer": int((calls.outcome == "no_answer").sum()),
                             "wrong_number": int((calls.outcome == "wrong_number").sum()),
                             "visits_recorded": int(g.outcome.isin(contact_log.VISIT_OUTCOMES).sum()),
                             "last": str(g.time.max())})
        by_staff.sort(key=lambda r: -r["contacts"])
    if own:
        df = df[df.staff == me_user]
    if staff:
        df = df[df.staff == staff]
    if outcome:
        df = df[df.outcome == outcome]
    label = lambda o: contact_log.OUTCOMES.get(o, contact_log.OUTCOMES["done"])["label"]  # noqa: E731
    rows = [{"time": r.time, "staff": r.name or r.staff or "Earlier tick", "mrn": mrn(r.person), "age": int(p.loc[r.person].age), "sex": p.loc[r.person].sex,
             "language": p.loc[r.person].language, "child": bool(p.loc[r.person].is_child), "channel": r.channel, "outcome": r.outcome,
             "outcome_label": label(r.outcome), "note": r.note} for r in df.iloc[::-1].head(max(1, min(limit, 1000))).itertuples()]
    counts = df.outcome.value_counts().to_dict()
    esc = [{"mrn": mrn(pid), "age": int(p.loc[pid].age), "language": p.loc[pid].language, "tries": s["tries"], "time": s["last"]["time"],
            "staff": s["last"]["name"] or s["last"]["staff"], "why": "wrong number" if s["last"]["outcome"] == "wrong_number" else "no answer after several tries"}
           for pid, s in contact_log.states().items() if s["status"] == "escalated" and pid in p.index]
    return clean({"rows": rows, "total": int(len(df)), "counts": {label(k): v for k, v in counts.items()},
                  "staff": [{"user": u, "name": names.get(u) or u} for u in everyone], "outcomes": {k: v["label"] for k, v in contact_log.OUTCOMES.items()},
                  "escalated": esc, "role": role, "own": bool(own), "me": {"user": me_user, "name": me_name}, "by_staff": by_staff})


# ------------------------------------------------------------------ families and journeys
@app.get("/api/households")
def households(limit: int = None):
    limit = limit or PAGE["households"]
    p = mine(people())
    open_ = p.open_cards.map(len) > 0
    g = p.assign(open=open_).groupby("household_id").agg(size=("Id", "size"), open=("open", "sum"),
                                                           children=("is_child", "sum"), language=("language", "first"),
                                                           region=("region", "first"))
    g = g[(g["size"] > 1) & (g.open > 0)].sort_values(["open", "size"], ascending=False).head(limit)
    out = records(g.reset_index())
    # name each household by its guardian's record number and the children's ages, so the rows can be told apart
    by_hh = {h: d for h, d in p[p.household_id.isin(g.index)].groupby("household_id")}
    for row in out:
        d = by_hh.get(row["household_id"])
        if d is None:
            continue
        guardian = d[d.is_child == 0]
        row["guardian"] = mrn(guardian.Id.iloc[0]) if len(guardian) else None
        row["children_ages"] = sorted(int(a) for a in d[d.is_child == 1].age)
        row["adults"] = int((d.is_child == 0).sum())
    return out


@app.get("/api/household/{hh}")
def household(hh: str):
    p = mine(people())
    d = p[p.household_id == hh]
    if d.empty:
        raise HTTPException(404, "Household not found")
    members = []
    guardian = d[d.is_child == 0]
    gid = guardian.Id.iloc[0] if len(guardian) else None
    for r in d.sort_values("is_child").itertuples():
        members.append({**summary_row(r), "cards": r.cards, "consent": bool(r.consent),
                        "relation": "Child" if r.is_child else ("Guardian" if r.Id == gid else "Adult")})
    return clean({"id": hh, "language": d.language.iloc[0], "region": d.region.iloc[0],
                  "guardian": gid, "guardian_record": mrn(gid) if gid else None, "members": members,
                  "how": "A household is a guardian and the children whose hospital record names that guardian. Other adults, such as a "
                         "husband or wife, are separate people with their own record and their own consent."})


@app.get("/api/journeys")
def journeys(limit: int = None):
    limit = limit or PAGE["journeys"]
    order, diagnosed = journey_index()
    p = mine(people()).set_index("Id")
    ids = [i for i in order if i in p.index][:limit]
    return [clean({"id": i, "label": f"Record {mrn(i)}, {'Child' if p.loc[i].is_child else 'Adult'}, {p.loc[i].age} y, "
                                     f"{p.loc[i].language}, {p.loc[i].region}", "diagnosed": i in diagnosed})
            for i in ids]


@lru_cache(maxsize=1)
def journey_index():
    """People who were diagnosed or attended (in the order it happened) and the set who were diagnosed, worked out once from the events
    (it used to scan every event for every person on every call)."""
    ev = events()
    return list(dict.fromkeys(ev[ev.event.isin(["diagnosed", "attended"])].person)), set(ev[ev.event == "diagnosed"].person)


@app.get("/api/journey/{pid}")
def journey(pid: str):
    r = person_row(pid)
    ev, m = events(), messages()
    return clean({"person": summary_row(r), "travel_min": r.travel_min, "nearest_facility": r.nearest_facility,
                  "events": records(ev[ev.person == pid][["day", "date", "event", "detail"]]),
                  "messages": records(m[m.person == pid][["day", "channel", "language", "window", "text"]])})


# ------------------------------------------------------------------ patient phone (live chat)
SESSIONS = {}
CONSENT_CHANGES = {}   # person -> may be contacted, after a change on the Why page (the live system has the full record)


def phone_candidates(children_first=True):
    p = mine(overlay(people()))
    if ACCOUNT.get() and ACCOUNT.get().get("person"):
        return p.sort_values("is_child")                  # a patient sees their own record and their children, nothing else
    c = p[(p.open_cards.map(len) > 0) & (may_contact(p) == 1)]
    return c.sort_values(["is_child", "priority"], ascending=[not children_first, False]).head(simulation.DESIGN["phone_demo_people"])


@app.get("/api/phone/people")
def phone_people():
    def label(r):
        parts = [f"Record {mrn(r.Id)}", f"{'Child' if r.is_child else 'Adult'}, {r.age} y", f"speaks {r.language}", r.region,
                 ", ".join(PLAIN.get(c["check"], c["check"]) for c in r.open_cards[:2])]   # plain names, never codes
        return " | ".join(x for x in parts if x)            # no stray "|" when nothing is due
    return [{"id": r.Id, "label": label(r)} for r in phone_candidates().itertuples()]


def phone_status(ctx, per):
    g = ctx.guardian(per)
    return {"state": per.state, "booked_for": ctx.date(per.booked_day).date().isoformat() if per.booked_day else None,
            "care_option": per.booking_option, "may_contact": consent.has(ctx, per, "contact"),
            "channel_preference": g.preferred_channel, "time_preference": g.preferred_time}


def _start(ctx, pid, cards):
    """Start (or restart) a phone conversation in the live system. Used by the endpoint and by the replay after a
    restart, so it never calls live() or person_row()."""
    per = ctx.people[pid]
    per.cards = [dict(c, status="agreed" if c["status"] == "awaiting_doctor" else c["status"])
                 for c in cards if c["status"] in ("ready", "agreed", "awaiting_doctor")]
    per.state, per.contacted_on, per.awaiting_reply, per.booked_day, per.paused_until = "Contacted", 0, True, None, -1
    per.booking_option = outreach.care_option(ctx, per, per.cards[0]["check"]) if per.cards else "Clinic visit"
    if not chat.may_contact(ctx, per):
        per.state = "Opted out"                       # consent history is kept: an opt-out stays an opt-out
    rec = ctx.guardian(per)
    rec.preferred_channel = rec.preferred_time = None
    convo = []
    if per.state == "Opted out":
        convo.append({"from": "system", "text": "This person has opted out: no message is sent (they can reply START)"})
    elif per.cards:
        card = per.cards[0]
        text, own = outreach.safe_message(ctx, card["check"], "guardian" if per.is_child else "adult",
                                          rec.row.language, per.booking_option)
        window = outreach.contact_window(ctx, rec, 0)
        if text is None:
            convo.append({"from": "system", "text": f"No approved message in {rec.row.language}: a nurse will call instead"})
        else:
            note = "" if own else f" (in English: no approved {rec.row.language} template yet)"
            convo.append({"from": "system", "text": f"Sent by {outreach.channel_for(rec)} at {window.split('-')[0]}{note}"})
            convo.append({"from": "clinic", "text": text, "time": window.split("-")[0]})
    else:
        convo.append({"from": "system", "text": "Nothing to send for this person right now"})
    SESSIONS[pid] = convo
    return per, rec, convo


def _reply(ctx, pid, text, now):
    per = ctx.people[pid]
    convo = SESSIONS.setdefault(pid, [])
    convo.append({"from": "family", "text": text, "time": now})
    if chat.reply_allowed(ctx, per, text):            # an opt-out stops our messages, never the emergency answer
        convo.append({"from": "clinic", "text": chat.handle_reply(ctx, per, text, 0), "time": now})
    else:
        convo.append({"from": "system", "text": "No reply: this person has opted out (reply START to join again)"})
    return per, convo


def replay_phone(ctx):
    """After a restart, bring the phone demo back exactly as it was: every start and reply is in phone_log.csv."""
    if not os.path.exists(PHONE_LOG):
        return
    saved = people().set_index("Id")
    fb = latest_feedback()
    for r in pd.read_csv(PHONE_LOG, dtype={"person": str, "text": str}).itertuples():
        if r.person not in ctx.people or r.person not in saved.index:
            continue
        if r.action == "start":
            cards = saved.at[r.person, "cards"]
            _start(ctx, r.person, apply_decision(cards, fb[r.person]) if r.person in fb else cards)
        elif r.action == "reply":
            _reply(ctx, r.person, "" if pd.isna(r.text) else r.text, str(r.time)[11:16])


@app.post("/api/phone/{pid}/start")
def phone_start(pid: str):
    r = person_row(pid)
    ctx = live()
    with LOCK:
        per, rec, convo = _start(ctx, pid, r.cards)
        append_csv(PHONE_LOG, {"time": datetime.now().isoformat(timespec="seconds"), "person": pid, "action": "start", "text": ""})
    return clean({"recipient": "parent / guardian" if per.is_child else "patient", "language": rec.row.language,
                  "channel": outreach.channel_for(rec), "conversation": convo, "status": phone_status(ctx, per)})


class Reply(BaseModel):
    text: str


@app.post("/api/phone/{pid}/reply")
def phone_reply(pid: str, body: Reply):
    check_mine(pid)
    if pid not in SESSIONS:
        phone_start(pid)
    ctx = live()
    with LOCK:
        per, convo = _reply(ctx, pid, body.text, datetime.now().strftime("%H:%M"))
        append_csv(PHONE_LOG, {"time": datetime.now().isoformat(timespec="seconds"), "person": pid, "action": "reply",
                               "text": body.text})
        if any(c["check"] == "emergency_callback" and c["status"] != "done" for c in per.cards) \
                and pid not in set(emergencies().person):
            append_csv(EMERGENCY_LOG, {"time": datetime.now().isoformat(timespec="seconds"), "person": pid,
                                       "text": body.text, "status": "open"})
    return clean({"conversation": convo, "status": phone_status(ctx, per)})

# ------------------------------------------------------------------ why was I contacted + consent
WHY_DATA_CHILD = ["Age and sex", "Check-up and vaccine records", "Growth measurements (height and weight)", "Family records"]


@app.get("/api/why/people")
def why_people():
    """Whose "Why was I contacted?" page can be shown: a patient gets themselves and their children; staff get the
    phone-demo people (both within the account's limits)."""
    acc = ACCOUNT.get() or {}
    if acc.get("person"):
        p = mine(overlay(people())).sort_values("is_child")
        ids = identity()
        return [{"id": r.Id, "label": "You" if r.Id == acc["person"] else f"Your child ({r.age} y)",
                 "self": r.Id == acc["person"], "age": int(r.age), "first_name": (str(ids.name.get(r.Id) or "").split() or [""])[0]}
                for r in p.itertuples()]
    return phone_people()


@app.get("/api/why/{pid}")
def why(pid: str):
    r = person_row(pid)
    ctx = live()
    per = ctx.people[pid]
    signs = [s for s in r.warning_signs if not any(w in s.lower() for w in ("genetic", "abnormal", "risk"))]
    # a child is never given a risk score: their invitation comes from the check-up schedule and growth records
    used = WHY_DATA_CHILD if r.is_child else [d for d in r.data_used if "Genetic" not in d]
    return clean({"is_child": bool(r.is_child), "checks": [PLAIN.get(c["check"], c["check"]) for c in r.open_cards],
                  "noticed": signs, "data_used": used,
                  "genetic_used": bool(r.consent_genetic),
                  "consent": {"contact": consent.has(ctx, per, "contact"), "use_genetic": consent.has(ctx, per, "use_genetic")}})


class Consent(BaseModel):
    contact: bool
    use_genetic: bool


@app.post("/api/why/{pid}/consent")
def set_consent(pid: str, body: Consent, request: Request):
    r = person_row(pid)
    ctx = live()
    given_by = r.guardian_id if r.is_child and isinstance(r.guardian_id, str) else pid
    with LOCK:
        for purpose, val in (("contact", body.contact), ("use_genetic", body.use_genetic)):
            consent.record(ctx, 0, pid, purpose, val, given_by)
            if purpose == "contact":
                per = ctx.people[pid]
                CONSENT_CHANGES[pid] = consent.has(ctx, per, "contact") and consent.has(ctx, ctx.guardian(per), "contact")
            append_csv(CONSENT_LOG, {"time": datetime.now().isoformat(timespec="seconds"), "person": pid,
                                     "purpose": purpose, "granted": val, "given_by": given_by, "recorded_by": by(request)})
    return {"saved": True, "given_by": "guardian" if given_by != pid else "person"}


# ------------------------------------------------------------------ map and communities
@app.get("/api/map")
def map_data():
    p = mine(people())
    pl = p.assign(gap=p.open_cards.map(len) > 0).groupby("home_place").agg(
        lat=("home_lat", "mean"), lon=("home_lon", "mean"), people=("Id", "size"), gap_rate=("gap", "mean"),
        region=("region", "first"), area_type=("area_type", "first")).reset_index()
    pl = pl[pl.people >= simulation.DESIGN["map_min_people"]]
    fac = utils.load_facilities()
    fac = fac[["name", "name_ar", "kind", "lat", "lon"]]
    return {"places": records(pl), "facilities": records(fac),
            "attribution": "Map data © OpenStreetMap contributors"}


def community_actions(c):
    a = []
    ca = simulation.DESIGN["community_actions"]
    if c["children_overdue"] >= ca["children_overdue_for_school_day"]:
        a.append("School or nursery screening day for check-ups, vaccines and development screens")
    if c["area_type"] == "remote" and c["share_with_gaps"] > ca["remote_gap_share_for_mobile_clinic"]:
        a.append("Mobile clinic visit for the whole community")
    if any(g in ("diabetes_screen", "bp_check") for g in c["top_gaps"]):
        a.append("Community health day with blood sugar and blood pressure checks")
    if c["uptake"] < ca["low_uptake_for_health_talk"]:
        a.append(f"Health talk in {c['main_language']} with a trusted local leader")
    if any(g in ("carrier_counselling", "premarital_screening") for g in c["top_gaps"]):
        a.append("Group information session on carrier screening before marriage")
    return a


@app.get("/api/communities")
def communities():
    p = mine(people())
    rows = []
    for place, d in p.groupby("home_place"):
        if len(d) < simulation.DESIGN["community_actions"]["min_people"]:
            continue
        gaps = pd.Series([c["check"] for cs in d.cards for c in cs]).value_counts()
        rec = d[d.cards.map(len) > 0]
        c = {"place": place, "region": d.region.iloc[0], "area_type": d.area_type.iloc[0], "people": int(len(d)),
             "share_with_gaps": round(float((d.n_gaps_start.fillna(0) > 0).mean()), 2), "top_gaps": list(gaps.index[:3]),
             # who still needs care now (an open pathway card), the number a planner acts on
             "share_open_now": round(float((d.open_cards.map(len) > 0).mean()), 2),
             "open_now": int((d.open_cards.map(len) > 0).sum()),
             # typical (median) trip: a few very long trips should not make a whole place look far away
             "travel_min_typical": round(float(d.travel_min.median()), 0),
             "children_overdue": int(((d.is_child == 1) & d.cards.map(lambda cs: any(x["check"] == "well_child_visit" for x in cs))).sum()),
             "uptake": round(float((rec.done.map(len) > 0).mean()), 2) if len(rec) else 0.0,
             "avg_travel_min": round(float(d.travel_min.mean()), 0), "main_language": d.language.mode().iloc[0],
             "reliable": len(d) >= simulation.DESIGN["community_actions"]["reliable_people"]}
        c["actions"] = community_actions(c)
        rows.append(c)
    # most people needing care first: 40 of 60 matters more for planning than 10 of 10
    rows.sort(key=lambda c: (-c["open_now"], -c["share_open_now"]))
    return clean(rows)


# ------------------------------------------------------------------ results
@app.get("/api/results")
def results_api():
    r = results()
    if not r:
        raise HTTPException(503, "Run python evaluate.py first")
    daily = []
    for mode, label in (("new", "This system"), ("old", "Usual care")):
        d = pd.read_csv(os.path.join(utils.OUTPUTS, mode, "daily.csv"))
        for row in d.itertuples():
            daily.append({"day": int(row.day), "way": label, "completed": int(row.people_with_completed_care),
                          "found": int(row.abnormal_found), "contacted": int(row.ever_contacted)})
    fl = os.path.join(utils.OUTPUTS, "new", "followup_learning.csv")
    learning = records(pd.read_csv(fl).sort_values("tries", ascending=False).head(PAGE["learning_rows"])) if os.path.exists(fl) else []
    cl = os.path.join(utils.OUTPUTS, "new", "channel_learning.csv")
    channels = records(pd.read_csv(cl)) if os.path.exists(cl) else []
    ml = utils.load_json(os.path.join(utils.MODELS, "ml_comparison.json"), None)             # trained models against the published score
    return clean({**r, "daily": daily, "followup_learning": learning, "channel_learning": channels, "nhanes_ml": ml})


@app.get("/api/alerts")
def alerts():
    """Live emergency call-backs, then the evaluation agent's alerts from the last run (newest first)."""
    path = os.path.join(utils.OUTPUTS, "new", "alerts.csv")
    run = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
    run = run.sort_values("day", ascending=False).head(PAGE["alerts"]) if not run.empty else run
    e = emergencies()
    return {"emergencies": records(e[e.person.isin(mine(people()).Id)]), "alerts": records(run)}


@app.post("/api/alerts/emergency/{pid}/done")
def emergency_done(pid: str):
    """The nurse has called the person back: close the emergency, here and in the live chat copy."""
    check_mine(pid)
    with LOCK:
        if not os.path.exists(EMERGENCY_LOG):
            raise HTTPException(404, "No emergency for this person")
        e = pd.read_csv(EMERGENCY_LOG, dtype={"person": str})
        hit = (e.person == pid) & (e.status == "open")
        if not hit.any():
            raise HTTPException(404, "No open emergency for this person")
        e.loc[hit, "status"] = "called back"
        e.loc[hit, "closed"] = datetime.now().isoformat(timespec="seconds")
        e.to_csv(EMERGENCY_LOG, index=False)
        per = live().people.get(pid)
        if per is not None:
            for c in per.cards:
                if c["check"] == "emergency_callback":
                    c["status"] = "done"
            live().log(0, pid, "emergency_callback", "nurse called back (marked on the Doctor page)")
    return {"closed": True, "open_left": int(len(emergencies()))}


# ------------------------------------------------------------------ FHIR R4 API: patient records as standard FHIR
FHIR_TYPES = {"Encounter": "encounters", "Condition": "conditions", "Observation": "observations",
              "Immunization": "immunizations", "Procedure": "procedures",
              "MedicationRequest": "medications", "AllergyIntolerance": "allergies", "CarePlan": "careplans",
              # family history and consent decisions. Genetic results are deliberately not served here: they stay in
              # the records database and reach a doctor only as a pathway card, with the person's genetic consent
              "FamilyMemberHistory": "family_history", "Consent": "consents"}


def fhir_response(body, status=200):
    from fastapi.responses import JSONResponse
    return JSONResponse(clean(body), status_code=status, media_type="application/fhir+json")


def fhir_error(status, text):
    code = {404: "not-found", 400: "invalid", 503: "transient"}.get(status, "processing")
    return fhir_response({"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": code, "diagnostics": text}]},
                         status)


def fhir_patient_row(pid):
    """The person's row in the hospital records database, if the signed-in account may see them (else None:
    someone outside the account's limits looks the same as someone who does not exist)."""
    try:
        check_mine(pid)
    except HTTPException:
        return None
    d = records_db.read("patient", patient=pid)
    return None if d.empty else d.iloc[0].to_dict()


def fhir_resources(pid, rtype, code=""):
    """One person's resources of one type, built from the database with the same builders as the FHIR export."""
    code = code.split("|")[-1].strip()
    codes = list(fhir.BP_PARTS) if code == fhir.BP_PANEL else ([code] if code else None)
    if rtype == "Consent":
        codes = None                                           # consent decisions have no code to search by
    if not records_db.has(FHIR_TYPES[rtype]):                  # a database built before this table existed
        return []
    rows = list(records_db.read(FHIR_TYPES[rtype], codes=codes, patient=pid).itertuples(index=False))
    tag = pid[:8]
    if rtype == "FamilyMemberHistory":
        return [fhir.family_history(r, f"fmh-{tag}-{i}") for i, r in enumerate(rows)]
    if rtype == "Consent":
        return [fhir.consent(r, f"consent-{tag}-{i}") for i, r in enumerate(rows)]
    if rtype == "Encounter":
        return [fhir.encounter(r) for r in rows]
    if rtype == "Condition":
        return [fhir.condition(r, f"cond-{tag}-{i}") for i, r in enumerate(rows)]
    if rtype == "Observation":
        return fhir.observations(rows, prefix=f"obs-{tag}")
    if rtype == "Immunization":
        return [fhir.immunization(r, f"imm-{tag}-{i}") for i, r in enumerate(rows)]
    if rtype == "MedicationRequest":
        return [fhir.medication_request(r, f"med-{tag}-{i}") for i, r in enumerate(rows)]
    if rtype == "AllergyIntolerance":
        return [fhir.allergy(r, f"allergy-{tag}-{i}") for i, r in enumerate(rows)]
    if rtype == "CarePlan":
        return [fhir.care_plan(r, f"plan-{tag}-{i}") for i, r in enumerate(rows)]
    return [fhir.procedure(r, f"proc-{tag}-{i}") for i, r in enumerate(rows)]


@app.get("/api/fhir/metadata")
def fhir_metadata():
    """What this FHIR server offers (a FHIR CapabilityStatement)."""
    coded = ("Observation", "Condition", "Procedure", "Immunization", "FamilyMemberHistory", "MedicationRequest", "AllergyIntolerance")
    return fhir_response({
        "resourceType": "CapabilityStatement", "status": "active", "kind": "instance", "fhirVersion": "4.0.1",
        "format": ["application/fhir+json"], "date": datetime.now().date().isoformat(),
        "software": {"name": "Precision Prevention and Screening"},
        "rest": [{"mode": "server",
                  "security": {"description": "Sign-in required. Doctors and the hospital admin see the people their "
                               "account allows (a region, or everyone); a patient sees their own record and their children's. "
                               "Every record opened is written to the access log."},
                  "resource": [{"type": "Patient", "interaction": [{"code": "read"}, {"code": "search-type"}],
                                "searchParam": [{"name": "identifier", "type": "token"}],
                                "operation": [{"name": "everything",
                                               "definition": "http://hl7.org/fhir/OperationDefinition/Patient-everything"}]}]
                  + [{"type": t, "interaction": [{"code": "search-type"}],
                      "searchParam": [{"name": "patient", "type": "reference"}] + ([{"name": "code", "type": "token"}] if t in coded else [])}
                     for t in FHIR_TYPES]}]})


@app.get("/api/fhir/Patient/{pid}/$everything")
def fhir_everything(pid: str):
    """The whole record of one person: the Patient and every visit, diagnosis, result, vaccine, procedure, medicine,
    allergy and care plan, the family history they reported and their consent decisions. Genetic results are not
    included."""
    if not records_db.exists():
        return fhir_error(503, "The hospital records database has not been built (python scripts/get_all_data.py --only records)")
    row = fhir_patient_row(pid)
    if row is None:
        return fhir_error(404, f"Patient/{pid} not found")
    resources = [fhir.patient(row)]
    for rtype in FHIR_TYPES:
        resources += fhir_resources(pid, rtype)
    return fhir_response(fhir.bundle(resources))


@app.get("/api/fhir/Patient/{pid}")
def fhir_patient(pid: str):
    if not records_db.exists():
        return fhir_error(503, "The hospital records database has not been built")
    row = fhir_patient_row(pid)
    return fhir_response(fhir.patient(row)) if row else fhir_error(404, f"Patient/{pid} not found")


@app.get("/api/fhir/Patient")
def fhir_patient_search(identifier: str = ""):
    """Find a patient by hospital record number: identifier=ADH-1234567 (or system|value)."""
    value = identifier.split("|")[-1].strip()
    if not value:
        return fhir_error(400, "Search patients by identifier (their hospital record number)")
    if not records_db.exists():
        return fhir_error(503, "The hospital records database has not been built")
    ids = identity()
    found = [pid for pid in ids.index[ids.MRN == value] if fhir_patient_row(pid) is not None]
    return fhir_response(fhir.bundle([fhir.patient(fhir_patient_row(pid)) for pid in found]))


@app.get("/api/fhir/{rtype}")
def fhir_search(rtype: str, patient: str = "", code: str = ""):
    """One person's Encounters, Conditions, Observations, Immunizations, Procedures, MedicationRequests,
    AllergyIntolerances, CarePlans, FamilyMemberHistory or Consent: ?patient=Patient/<id>, and for coded types
    optionally &code=<code> (for example 4548-4, HbA1c; 85354-9, the blood pressure panel)."""
    if rtype not in FHIR_TYPES:
        return fhir_error(404, f"{rtype} is not offered here (see /api/fhir/metadata)")
    pid = patient.split("/")[-1].strip()
    if not pid:
        return fhir_error(400, "Search by patient: ?patient=Patient/<id>")
    if not records_db.exists():
        return fhir_error(503, "The hospital records database has not been built")
    if fhir_patient_row(pid) is None:
        return fhir_error(404, f"Patient/{pid} not found")
    return fhir_response(fhir.bundle(fhir_resources(pid, rtype, code)))


@app.post("/api/reload")
def reload_outputs():
    """Clear caches after a new simulation run."""
    central_only()
    for f in (people, events, messages, results, identity):
        f.cache_clear()
    from models import risk_scores
    risk_scores._CUTOFFS.clear()                    # re-read the risk cut-offs from the model card
    return {"reloaded": True}
