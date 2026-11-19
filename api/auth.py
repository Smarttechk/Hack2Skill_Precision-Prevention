"""
Sign-in, roles, accounts and the access log, enforced on the server (not only in the browser).

Three modes, chosen at start-up:
  1. config/users.json exists (the hospital admin creates accounts on the Accounts page, or with
     python scripts/add_user.py NAME ROLE [--region R] [--person ID]): everyone signs in and sees only their role's
     pages and their own people:
       admin    (hospital admin)  every page and person, accounts, the access log, reloading results
       doctor / outreach          their role's pages; with a region, only people living in that region
       manager                    results, map and communities: totals only, no individual patient records
       patient                    only their own record and the children they are guardian of
  2. PP_PASSWORD is set: one shared admin account (user PP_USER, default "clinic"); for a quick demo.
  3. Neither: open, for your own computer only (the server listens on 127.0.0.1 unless told otherwise).

Everyone can sign up (one form per role), but an account only opens after the hospital admin has checked the
details: a doctor's licence, a staff number, or a patient's Emirates ID and date of birth, and linked the patient to
their own record. The only exception is the very first hospital admin, who is active at once.
Passwords are stored as PBKDF2-SHA256 hashes with a random salt. A sign-in gives a random session token in an
HttpOnly cookie (page scripts cannot read it); only its SHA-256 is stored (outputs/sessions.json), so sign-ins survive
a restart. Wrong passwords lock that user name on that computer for a while, and a computer trying many names is
locked too, so nobody can lock a doctor out from somewhere else. Sign-ins, failures, sign-outs, account changes
and every opened patient record are written to outputs/access_log.csv.
"""
import base64
import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys  # noqa: E402
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import utils  # noqa: E402
with open(os.path.join(ROOT, "config", "roles.json"), encoding="utf-8") as _fh:
    _R = {k: v["value"] for k, v in json.load(_fh)["values"].items()}
ITERATIONS = _R["password_hash_iterations"]
ROLE_PAGES = utils.role_pages()         # which pages each role can open, demo tools only with PP_DEMO=1 (the app reads /api/roles)
PAGE_API = _R["page_api"]              # which API calls each page uses
EVERYONE = _R["everyone_api"]
PUBLIC = _R["public_api"]              # allowed before signing in
ADMIN_ONLY = _R["admin_only_api"]
ADMIN = _R["admin_role"]
SCOPE = _R["account_scope"]
SESSION_SECONDS = _R["session_hours"] * 3600
MAX_FAILURES, MAX_FAILURES_PER_COMPUTER = _R["login_max_failures"], _R["login_max_failures_per_computer"]
LOCKOUT_SECONDS = _R["login_lockout_minutes"] * 60
MIN_PASSWORD = _R["min_password_length"]
COOKIE = _R["session_cookie"]

# each can be moved with an environment variable (PP_SESSIONS_FILE, PP_LIMITS_FILE, PP_ACCESS_LOG), so a test or demo server never
# writes into the real sessions, lock counts or access log
SESSIONS_FILE = os.environ.get("PP_SESSIONS_FILE") or os.path.join(ROOT, "outputs", "sessions.json")
LIMITS_FILE = os.environ.get("PP_LIMITS_FILE") or os.path.join(ROOT, "outputs", "auth_limits.json")   # wrong-password and sign-up counts, kept across restarts
ACCESS_LOG = os.environ.get("PP_ACCESS_LOG") or os.path.join(ROOT, "outputs", "access_log.csv")
LOG_FIELDS = ["time", "user", "role", "computer", "event", "detail", "chain"]
_VERIFIED = {}                         # sha256 of a checked Basic header -> account, so the slow hash runs once
_FAILURES = {}                         # (user, computer) or ("*", computer) -> (failed tries, first failure time)
_LOCK = threading.Lock()
_NO_ACCOUNT_SALT = secrets.token_hex(16)


# ------------------------------------------------------------------ accounts file
def users_file():
    return os.environ.get("PP_USERS_FILE", os.path.join(ROOT, "config", "users.json"))


def load_users():
    path = users_file()
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("users", {})


def save_users(users):
    path = users_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"users": users}, fh, indent=2)
    os.replace(tmp, path)


def hash_password(password, salt=None, iterations=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations or ITERATIONS).hex()
    return salt, digest


def check_password(record, password):
    _, digest = hash_password(password, record["salt"], record.get("iterations"))
    return secrets.compare_digest(digest, record["hash"])


def account(user, rec):
    """What the server needs to know about a signed-in person: role and limits (region, own record)."""
    role = rec["role"]
    return {"user": user, "role": role, "name": rec.get("full_name"),
            "region": rec.get("region") if role in SCOPE["region"] else None,
            "person": rec.get("person") if role in SCOPE["person"] else None,
            # a new patient whose hospital record does not exist yet: the account is open but sees no record until it is linked
            "awaiting_record": bool(role in SCOPE["person"] and not rec.get("person") and rec.get("awaiting_record"))}


def _people():
    import pandas as pd
    path = os.path.join(ROOT, "outputs", "new", "people_final.csv")
    return pd.read_csv(path, dtype={"Id": str, "guardian_id": str}, usecols=["Id", "region", "guardian_id", "is_child"]) \
        if os.path.exists(path) else None


def account_record(role, password, region=None, person=None, allow_no_record=False):
    """A checked account record, or ValueError with the reason (used by the Accounts page and the script). A patient account
    normally needs a record; allow_no_record is for a new patient the hospital has not registered yet (see decide_request)."""
    if role not in ROLE_PAGES:
        raise ValueError(f"Unknown role {role!r}; use one of: {', '.join(ROLE_PAGES)}")
    check_new_password(password)
    region, person = region or None, person or None
    if region and role not in SCOPE["region"]:
        raise ValueError(f"A region applies only to: {', '.join(SCOPE['region'])}")
    if person and role not in SCOPE["person"]:
        raise ValueError(f"A patient record applies only to: {', '.join(SCOPE['person'])}")
    if role in SCOPE["person"] and not person and not allow_no_record:
        raise ValueError("A patient account must be linked to the patient's own record (after checking who they are).")
    if region or person:
        p = _people()
        if p is None:
            raise ValueError("Run python orchestrator.py first: regions and records are checked against the people.")
        if region and region not in set(p.region):
            raise ValueError(f"Unknown region {region!r}; use one of: {', '.join(sorted(p.region.dropna().unique()))}")
        if person and person not in set(p.Id):
            raise ValueError(f"No person with Id {person!r}")
        if person and int(p.loc[p.Id == person, "is_child"].iloc[0]) == 1:
            raise ValueError("That record belongs to a child. Children come with a parent's or guardian's account: "
                             "link the account to the parent's own record.")
    salt, digest = hash_password(password)
    rec = {"role": role, "salt": salt, "hash": digest, "iterations": ITERATIONS}
    if region:
        rec["region"] = region
    if person:
        rec["person"] = person
    return rec


def check_new_password(password):
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters.")


def save_account(name, rec, by="script"):
    users = load_users() or {}
    new = name not in users
    users[name] = rec
    save_users(users)
    end_sessions(name)                                # a changed role or password takes effect at once
    log("account_created" if new else "account_changed", by, ADMIN, "", f"{name}: {rec['role']}"
        + (f", {rec['region']}" if rec.get("region") else "") + (", patient record" if rec.get("person") else ""))


PROFILE_FIELDS = ("specialty", "languages", "panel_limit", "duty", "backup")


def update_profile(name, fields, by):
    """Change a doctor's care-team profile (specialty, languages, panel limit, duty, backup). Unlike save_account this does
    not end their sessions: a doctor in the middle of a shift stays signed in."""
    users = load_users() or {}
    if name not in users or users[name].get("role") != "doctor":
        raise ValueError(f"{name!r} is not a doctor account")
    for k, v in fields.items():
        if k in PROFILE_FIELDS:
            if v in (None, "", []):
                users[name].pop(k, None)
            else:
                users[name][k] = v
    save_users(users)
    log("profile_changed", by, ADMIN, "", f"{name}: " + ", ".join(f"{k}" for k in fields if k in PROFILE_FIELDS))


def set_password(name, password, by):
    users = load_users() or {}
    if name not in users:
        raise ValueError(f"No account {name!r}")
    check_new_password(password)
    users[name]["salt"], users[name]["hash"] = hash_password(password)
    users[name]["iterations"] = ITERATIONS
    save_users(users)
    end_sessions(name)
    unlock(name)
    log("password_changed", by, "", "", name)


def remove_account(name, by):
    users = load_users() or {}
    if users.pop(name, None) is None:
        raise ValueError(f"No account {name!r}")
    save_users(users)
    end_sessions(name)
    log("account_removed", by, ADMIN, "", name)


# ------------------------------------------------------------------ wrong passwords
def credentials(header):
    if not header or not header.lower().startswith("basic "):
        return None, None
    try:
        user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        return user, pw
    except ValueError:
        return None, None


def _save_limits():
    """Write the wrong-password and sign-up counts (call while holding _LOCK), so a restart does not reset them."""
    now = time.time()
    data = {"failures": {f"{k[0]}\t{k[1]}": [n, s] for k, (n, s) in _FAILURES.items() if now - s < LOCKOUT_SECONDS},
            "signups": {c: [t for t in ts if now - t < 3600] for c, ts in _SIGNUPS.items()}}
    try:
        os.makedirs(os.path.dirname(LIMITS_FILE), exist_ok=True)
        tmp = LIMITS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, LIMITS_FILE)
    except OSError:
        pass                                           # limits still work in memory


def _load_limits():
    if not os.path.exists(LIMITS_FILE):
        return
    try:
        with open(LIMITS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    now = time.time()
    for k, (n, s) in data.get("failures", {}).items():
        if now - s < LOCKOUT_SECONDS:
            user, _, comp = k.partition("\t")
            _FAILURES[(user, comp)] = (n, s)
    for c, ts in data.get("signups", {}).items():
        _SIGNUPS[c] = [t for t in ts if now - t < 3600]


def _too_many(key, limit):
    tries, since = _FAILURES.get(key, (0, 0))
    if tries and time.time() - since >= LOCKOUT_SECONDS:
        _FAILURES.pop(key, None)
        return False
    return tries >= limit


def _failed(key):
    tries, since = _FAILURES.get(key, (0, time.time()))
    _FAILURES[key] = (tries + 1, since)


def is_locked(user, computer=""):
    """Locked: this user name on this computer, or this computer after trying many names."""
    with _LOCK:
        return _too_many((user, computer), MAX_FAILURES) or _too_many(("*", computer), MAX_FAILURES_PER_COMPUTER)


def unlock(name):
    with _LOCK:
        for k in [k for k in _FAILURES if k[0] == name]:
            _FAILURES.pop(k, None)
        _save_limits()


def locked_users():
    with _LOCK:
        return sorted({k[0] for k, (n, since) in _FAILURES.items()
                       if k[0] != "*" and n >= MAX_FAILURES and time.time() - since < LOCKOUT_SECONDS})


def verify(user, password, computer=""):
    """The account for this user name and password, or None."""
    users = load_users()
    if users is None:
        expected = os.environ.get("PP_PASSWORD")
        if expected and user is not None and secrets.compare_digest(user, os.environ.get("PP_USER", "clinic")) \
                and secrets.compare_digest(password or "", expected):
            return {"user": user, "role": ADMIN, "region": None, "person": None}
        return None
    if is_locked(user or "", computer):
        return None
    rec = users.get(user or "")
    if rec is None:
        hash_password(password or "", _NO_ACCOUNT_SALT)   # the same work as a real check: timing does not tell
                                                           # an outsider which user names exist
    if rec and password is not None and check_password(rec, password):
        with _LOCK:
            if _FAILURES.pop((user, computer), None):
                _save_limits()
        return account(user, rec)
    with _LOCK:
        _failed((user or "", computer))
        _failed(("*", computer))
        _save_limits()
    return None


# ------------------------------------------------------------------ sessions (only token hashes are stored)
def _digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read_sessions():
    if not os.path.exists(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_sessions(s):
    os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)
    now = time.time()
    s = {k: v for k, v in s.items() if v["expires"] > now}
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh)
    os.replace(tmp, SESSIONS_FILE)


def login(user, password, computer=""):
    """Check the password and start a session: returns (token, account) or (None, None)."""
    acc = verify(user, password, computer)
    if acc is None:
        log("sign_in_failed", user, "", computer, "locked" if is_locked(user, computer) else "wrong user name or password")
        return None, None
    token = secrets.token_urlsafe(32)
    with _LOCK:
        s = _read_sessions()
        s[_digest(token)] = {"account": acc, "expires": time.time() + SESSION_SECONDS}
        _write_sessions(s)
    log("sign_in", acc["user"], acc["role"], computer, "")
    return token, acc


def session_account(token):
    if not token:
        return None
    with _LOCK:
        hit = _read_sessions().get(_digest(token))
    if not hit or hit["expires"] < time.time():
        return None
    acc = hit["account"]
    users = load_users()
    if users is not None and acc["user"] not in users:
        return None                                    # the account was removed: its sessions end too
    return acc


def logout(token, computer=""):
    if not token:
        return
    with _LOCK:
        s = _read_sessions()
        hit = s.pop(_digest(token), None)
        _write_sessions(s)
    if hit:
        log("sign_out", hit["account"]["user"], hit["account"]["role"], computer, "")


def end_sessions(name):
    with _LOCK:
        s = _read_sessions()
        _write_sessions({k: v for k, v in s.items() if v["account"]["user"] != name})
    for k in [k for k, v in _VERIFIED.items() if v and v["user"] == name]:
        _VERIFIED.pop(k, None)


def who(token=None, header="", computer=""):
    """The signed-in account for this request, or None when sign-in is required and missing or wrong.
    The app uses the session cookie; scripts may send a Bearer token or a Basic user name and password."""
    if not sign_in_required():
        return {"user": "local", "role": ADMIN, "region": None, "person": None}
    if header and header.lower().startswith("bearer "):
        token = header[7:].strip()
    if token:
        return session_account(token)
    user, pw = credentials(header)
    if user is None:
        return None
    path = users_file()
    stamp = os.path.getmtime(path) if os.path.exists(path) else 0
    # the cache key includes the file's change time, so a changed or removed account is checked again
    key = hashlib.sha256(f"{path}|{stamp}|{header}".encode("utf-8")).hexdigest()
    if key in _VERIFIED:
        return _VERIFIED[key]
    acc = verify(user, pw, computer)
    if acc is None:
        log("sign_in_failed", user, "", computer, "script sign-in")
    elif load_users() is not None:
        _VERIFIED[key] = acc
    return acc


def allowed(role, path):
    """May this role call this API path? The app's pages and files carry no patient data and are open to load."""
    if not path.startswith("/api/") or any(path == p for p in PUBLIC):
        return True
    if any(path.startswith(p) for p in ADMIN_ONLY):
        return role == ADMIN
    if any(path.startswith(p) for p in EVERYONE):
        return True
    if not sign_in_required() and path.startswith("/api/mycare"):
        return True          # open mode (no accounts, your own computer): "Preview as patient" shows My care for a sample family
    return any(path.startswith(api) for page in ROLE_PAGES.get(role, []) for api in PAGE_API.get(page, []))


def sign_in_required():
    return load_users() is not None or bool(os.environ.get("PP_PASSWORD"))


# ------------------------------------------------------------------ sign-up requests
SIGNUP_FIELDS, SIGNUP_OPTIONAL = _R["signup_fields"], _R["signup_optional_fields"]
SIGNUP_LIMIT = _R["signup_max_per_computer_per_hour"]
EMIRATES_ID = re.compile(_R["emirates_id_pattern"])
USER_NAME = re.compile(_R["user_name_pattern"])
_SIGNUPS = {}                          # computer -> times of recent requests
_load_limits()                         # counts from before a restart (both dictionaries exist now)


def requests_file():
    return os.environ.get("PP_SIGNUP_FILE", os.path.join(ROOT, "config", "signup_requests.json"))


def load_requests():
    path = requests_file()
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_requests(reqs):
    path = requests_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(reqs, fh, indent=2)
    os.replace(tmp, path)


def signup(role, user, password, details, computer=""):
    """A sign-up request. The password is hashed at once; an Emirates ID is kept only as its last 4 digits (to compare
    with the card) and a salted hash. Returns ("active" | "pending", message). Raises ValueError with the reason."""
    user = (user or "").strip()
    if role not in SIGNUP_FIELDS:
        raise ValueError(f"Unknown role {role!r}")
    if not USER_NAME.match(user):
        raise ValueError("User name: 3 to 40 letters, numbers, dots, dashes or underscores")
    check_new_password(password)
    users, reqs = load_users() or {}, load_requests()
    if user in users or any(r["user"] == user and r["status"] == "pending" for r in reqs.values()):
        raise ValueError("That user name is taken; please choose another")
    clean = {}
    for f in SIGNUP_FIELDS[role]:
        val = str(details.get(f, "")).strip()
        if not val:
            raise ValueError(f"Please fill in: {f.replace('_', ' ')}")
        clean[f] = val[:200]
    for f in SIGNUP_OPTIONAL.get(role, []):
        if str(details.get(f, "")).strip():
            clean[f] = str(details[f]).strip()[:200]
    if "emirates_id" in clean:
        eid = clean.pop("emirates_id").replace(" ", "")
        if not EMIRATES_ID.match(eid):
            raise ValueError("Emirates ID must look like 784-1990-1234567-1")
        digits = eid.replace("-", "")
        salt = secrets.token_hex(8)
        clean["emirates_id_last4"] = digits[-4:]
        clean["emirates_id_hash"] = salt + ":" + hashlib.sha256((salt + digits).encode()).hexdigest()
    with _LOCK:
        recent = [t for t in _SIGNUPS.get(computer, []) if time.time() - t < 3600]
        if len(recent) >= SIGNUP_LIMIT:
            raise ValueError("Too many sign-up requests from this computer; please try again later")
        _SIGNUPS[computer] = recent + [time.time()]
        _save_limits()
    salt, digest = hash_password(password)
    if role == ADMIN and not users and _R["signup_auto_approve_first_admin"]:
        save_account(user, {"role": ADMIN, "salt": salt, "hash": digest, "iterations": ITERATIONS,
                            "full_name": clean["full_name"], "email": clean.get("email")}, by="first admin sign-up")
        log("signup_first_admin", user, ADMIN, computer, clean.get("hospital", ""))
        return "active", "You are the first hospital admin: your account is active. Sign in to approve everyone else."
    rid = secrets.token_hex(6)
    reqs[rid] = {"id": rid, "user": user, "role": role, "status": "pending", "requested": datetime.now().isoformat(timespec="seconds"),
                 "salt": salt, "hash": digest, "iterations": ITERATIONS, "details": clean}
    _save_requests(reqs)
    log("signup_requested", user, role, computer, clean.get("full_name", ""))
    return "pending", "Thank you. The hospital admin will check your details and activate your account; then you can sign in."


def pending_for(user, password):
    """When sign-in fails: is this a sign-up still waiting for approval? Only told when the password matches the
    request, so nobody can probe which user names have asked for an account."""
    r = next((r for r in load_requests().values() if r["user"] == user and r["status"] == "pending"), None)
    if r and check_password(r, password or ""):
        unlock(user)                                   # not a wrong password, so it does not count towards a lock
        return r["requested"]
    return None


def pending_requests():
    return [{k: v for k, v in r.items() if k not in ("salt", "hash", "iterations")}
            for r in sorted(load_requests().values(), key=lambda r: r["requested"]) if r["status"] == "pending"]


def decide_request(rid, approve, by, region=None, person=None, without_record=False):
    """Approve (creates the account with the password the person chose) or reject a sign-up request.

    A new patient has no hospital record yet, so the admin cannot link one: without_record opens the account anyway. It sees no
    record at all (not even by accident) and a page that says the hospital is setting up their record, until an admin links the
    record they were registered under (link_record)."""
    reqs = load_requests()
    r = reqs.get(rid)
    if not r or r["status"] != "pending":
        raise ValueError("No such pending request")
    if approve:
        patient = r["role"] in SCOPE["person"]
        hint = (region or r["details"].get("home_region") or None) if patient else None     # which hospital will register them
        region = None if patient else (region or r["details"].get("region") or None)
        person = person or None
        awaiting = bool(patient and not person and without_record)
        # the same checks as an account the admin creates (role, region, a patient's own record)
        account_record(r["role"], "x" * MIN_PASSWORD, region, person, allow_no_record=awaiting)
        if r["user"] in (load_users() or {}):
            raise ValueError("That user name was taken in the meantime; reject and ask for a new request")
        rec = {"role": r["role"], "salt": r["salt"], "hash": r["hash"], "iterations": r["iterations"],
               "full_name": r["details"].get("full_name"), "email": r["details"].get("email")}
        if r["role"] == "doctor" and r["details"].get("specialty"):
            rec["specialty"] = r["details"]["specialty"]          # as typed; the care team matches it to a configured specialty
        if region and r["role"] in SCOPE["region"]:
            rec["region"] = region
        if person and r["role"] in SCOPE["person"]:
            rec["person"] = person
        if awaiting:
            rec["awaiting_record"] = True
            rec["region"] = hint                         # the hospital that registers them; account() never uses it for a patient
            rec["signup_dob"] = r["details"].get("date_of_birth")
            rec["signup_eid_last4"] = r["details"].get("emirates_id_last4")
        save_account(r["user"], rec, by=by)
    r["status"] = "approved" if approve else "rejected"
    r["decided_by"], r["decided"] = by, datetime.now().isoformat(timespec="seconds")
    r.pop("salt", None), r.pop("hash", None)        # the password hash now lives only in the account (or nowhere)
    _save_requests(reqs)
    log("signup_approved" if approve else "signup_rejected", by, ADMIN, "", f"{r['user']} ({r['role']})")
    return r["user"]


def link_record(name, person, by):
    """Link a patient account that was opened without a record to the record the hospital has since created or found."""
    users = load_users() or {}
    rec = users.get(name)
    if rec is None or rec.get("role") not in SCOPE["person"]:
        raise ValueError("No such patient account")
    if rec.get("person"):
        raise ValueError("This account is already linked to a record")
    check = account_record(rec["role"], "x" * MIN_PASSWORD, None, person)       # an adult's own record, and it exists
    rec["person"] = check["person"]
    for k in ("awaiting_record", "region", "signup_dob", "signup_eid_last4"):
        rec.pop(k, None)
    save_account(name, rec, by=by)
    log("record_linked", by, ADMIN, "", name)


# ------------------------------------------------------------------ access log
def log(event, user, role, computer, detail):
    """One line per sign-in, failure, sign-out, account change and opened patient record (who, when, from where)."""
    if not sign_in_required() and event not in ("account_created", "account_changed", "account_removed", "password_changed",
                                                "signup_requested", "signup_first_admin", "break_glass", "share_record",
                                                "share_revoked", "assignments_applied", "assignment_changed", "profile_changed", "outreach_contact",
                                                "family_request", "family_approved", "family_rejected", "record_linked", "visit_outcome", "audit_export", "answers_saved"):
        return                                          # open mode on your own computer: nobody to log
    os.makedirs(os.path.dirname(ACCESS_LOG), exist_ok=True)
    with _LOCK:
        _upgrade_log()
        new = not os.path.exists(ACCESS_LOG)
        row = {"time": datetime.now().isoformat(timespec="seconds"), "user": user or "", "role": role or "",
               "computer": computer or "", "event": event, "detail": detail or ""}
        row["chain"] = _chain(_head(), row)
        with open(ACCESS_LOG, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
        _HEAD.update(path=ACCESS_LOG, size=os.path.getsize(ACCESS_LOG), chain=row["chain"])


# ------------------------------------------------------------------ the log is chained: each line carries a keyed hash of the line before it
# A line that is edited, removed or inserted breaks every hash after it, and nobody without the key can rebuild them (verify_log tells
# which line). The key is PP_AUDIT_KEY, or a random one made once next to the log; in a real deployment keep it outside this computer.
# Lines written before the chain existed have an empty chain and are reported as "before the chain".
_HEAD = {}


def _audit_key():
    k = os.environ.get("PP_AUDIT_KEY")
    if k:
        return k.encode("utf-8")
    path = os.path.join(os.path.dirname(ACCESS_LOG), "audit_key")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_hex(32))
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip().encode("utf-8")


def _chain(prev, row):
    msg = "|".join([prev or ""] + [str(row.get(f, "")) for f in LOG_FIELDS if f != "chain"])
    return hmac.new(_audit_key(), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _rows():
    if not os.path.exists(ACCESS_LOG):
        return []
    with open(ACCESS_LOG, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _head():
    """The chain value of the last line (read once, then kept while the file has not changed under us)."""
    if _HEAD.get("path") == ACCESS_LOG and os.path.exists(ACCESS_LOG) and _HEAD.get("size") == os.path.getsize(ACCESS_LOG):
        return _HEAD["chain"]
    rows = _rows()
    return (rows[-1].get("chain") or "") if rows else ""


def key_source():
    """Where the log's key comes from: the PP_AUDIT_KEY environment variable (safer: it can live off this computer), or a file next to the
    log (anyone who can edit the log on this computer can read the key and rebuild the chain)."""
    return "environment" if os.environ.get("PP_AUDIT_KEY") else "file"


def chain_at(line):
    """The full chain value at one line (1 = the first data line), for a head written down elsewhere; None when there is no such line."""
    rows = _rows()
    return (rows[line - 1].get("chain") or None) if 1 <= line <= len(rows) else None


def _upgrade_log():
    """An older log has no chain column: add it (empty on the old lines) so new lines can be chained."""
    if not os.path.exists(ACCESS_LOG):
        return
    with open(ACCESS_LOG, newline="", encoding="utf-8") as fh:
        first = fh.readline()
    if "chain" in first:
        return
    rows = _rows()
    tmp = ACCESS_LOG + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**{f: r.get(f, "") for f in LOG_FIELDS}, "chain": ""})
    os.replace(tmp, ACCESS_LOG)
    _HEAD.clear()


def verify_log():
    """Check the chain from the first chained line to the end. Returns lines, chained lines, lines from before the chain, whether all
    hashes match, the first line (1 = first data line) that does not, and the head hash a reviewer can write down."""
    rows = _rows()
    legacy, chained, bad, prev, started = 0, 0, None, "", False
    for i, r in enumerate(rows, start=1):
        c = r.get("chain") or ""
        if not started and not c:
            legacy += 1
            continue
        started = True
        if not c or not hmac.compare_digest(c, _chain(prev, r)):
            bad = bad or i
        chained += 1
        prev = c or prev
    return {"lines": len(rows), "chained": chained, "before_chain": legacy, "ok": bad is None, "first_bad_line": bad,
            "head": (rows[-1].get("chain") or "")[:16] if rows else "", "key": key_source()}
