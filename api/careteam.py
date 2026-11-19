"""
Care teams: which doctor is responsible for which patients, who may open someone outside their own patients, and a family's
sharing of a record with another hospital.

* A hospital is a region (Abu Dhabi, Al Ain, Al Dhafra). A doctor's profile holds the hospital, one or more specialties,
  the languages spoken, a panel limit, a backup doctor and whether they are the hospital's duty doctor.
* A patient can have one doctor per specialty (a care team). The system proposes assignments from rules (hospital,
  specialty, language, siblings together, panel limit, urgent first) and the hospital admin approves them.
* A doctor with a specialty sees only: their own panel, the panel of a colleague on leave who named them as backup,
  (on duty) people in their hospital with an Urgent card and nobody assigned, patients opened with a logged
  "break glass" reason for a few hours, and patients a family has shared with their hospital. A doctor account without a
  specialty keeps the older rule (everyone in their region).
* Everything is kept in outputs/care_team.json (PP_CARETEAM_FILE for tests); doctor profiles of real accounts live in the
  account record (config/users.json).
"""
import json
import os
import threading
from collections import Counter
from datetime import datetime, timedelta

import utils
from api import auth

CFG = utils.config("care_team")
SPECIALTIES = CFG["specialties"]
ORDER = list(SPECIALTIES)                                    # narrow specialties first is a matter of the config order
RANK = {"Urgent": 0, "High": 1, "Routine": 2}
_LOCK = threading.RLock()
_CACHE = {"stamp": None, "path": None, "data": None}
EMPTY = {"assignments": {}, "leave": {}, "grants": [], "break_glass": [], "demo_profiles": {}}


def now():
    return datetime.now().isoformat(timespec="seconds")


def later(hours=0, days=0):
    return (datetime.now() + timedelta(hours=hours, days=days)).isoformat(timespec="seconds")


# ------------------------------------------------------------------ the store
def store_path():
    return os.environ.get("PP_CARETEAM_FILE") or os.path.join(utils.OUTPUTS, "care_team.json")


def load():
    """The care-team state; read again only when the file has changed."""
    path = store_path()
    stamp = os.path.getmtime(path) if os.path.exists(path) else None
    with _LOCK:
        if _CACHE["path"] == path and _CACHE["stamp"] == stamp and _CACHE["data"] is not None:
            return _CACHE["data"]
        data = {k: type(v)() for k, v in EMPTY.items()}
        if stamp is not None:
            with open(path, encoding="utf-8") as fh:
                data.update(json.load(fh))
        _CACHE.update(path=path, stamp=stamp, data=data)
        return data


def save(data):
    path = store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with _LOCK:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
        _CACHE.update(path=path, stamp=os.path.getmtime(path), data=data)


def reset():
    save({k: type(v)() for k, v in EMPTY.items()})


# ------------------------------------------------------------------ doctors and their profiles
def match_specialty(text):
    """One of the configured specialties for what a doctor typed or was set to (a sign-up form has free text), else None."""
    t = str(text or "").strip().lower()
    if not t:
        return None
    for name, spec in SPECIALTIES.items():
        if t == name.lower() or any(a in t for a in spec["aliases"]):
            return name
    return None


def _norm(user, rec):
    raw = rec.get("specialty") or []
    raw = [raw] if isinstance(raw, str) else raw
    specs = [s for s in dict.fromkeys(match_specialty(x) for x in raw) if s]
    langs = rec.get("languages") or []
    langs = [x.strip() for x in langs.split(",")] if isinstance(langs, str) else list(langs)
    return {"user": user, "name": rec.get("full_name") or rec.get("name") or user, "hospital": rec.get("region") or rec.get("hospital") or None,
            "specialty": specs, "languages": [x for x in langs if x], "duty": bool(rec.get("duty")), "backup": rec.get("backup") or None,
            "panel_limit": int(rec.get("panel_limit") or CFG["default_panel_limit"])}


def open_mode():
    return auth.load_users() is None and not os.environ.get("PP_PASSWORD")


def doctors():
    """Every doctor that has a care-team profile: real accounts with a specialty, or (open mode) the made-up demo doctors."""
    users = auth.load_users()
    if users is None:
        over = load()["demo_profiles"]
        return [_norm(d["user"], {**d, **over.get(d["user"], {})}) for d in CFG["demo_doctors"]]
    return [_norm(u, r) for u, r in sorted(users.items()) if r.get("role") == "doctor" and _norm(u, r)["specialty"]]


def profile(user):
    return next((d for d in doctors() if d["user"] == user), None)


def on_leave(user):
    return bool(load()["leave"].get(user))


def hospitals(people):
    return sorted(people.region.dropna().unique().tolist())


def preview_account(value, people):
    """Open mode only: look at the app as one doctor, one outreach staff member, or the admin of one hospital (the X-Preview header)."""
    kind, _, who = str(value).partition(":")
    if kind == "doctor":
        d = profile(who)
        if d:
            return {"user": d["user"], "role": "doctor", "name": d["name"], "region": d["hospital"], "person": None}
    if kind == "outreach":
        s = next((x for x in CFG["demo_outreach"] if x["user"] == who), None)
        if s:
            return {"user": s["user"], "role": "outreach", "name": s["name"], "region": s["hospital"], "person": None}
    if kind == "hospital-admin" and who in hospitals(people):
        return {"user": f"hospital-admin-{who}", "role": auth.ADMIN, "name": f"Hospital admin, {who}", "region": who, "person": None}
    return None


# ------------------------------------------------------------------ who a specialty looks after
def eligible(p, rule):
    """Which people a specialty may look after (a True/False for every row of the people table)."""
    if rule == "children":
        return p.is_child == 1
    if rule == "adults":
        return p.is_child == 0
    if rule == "adults_long_term":
        return (p.is_child == 0) & (p.known_diabetes.fillna(0).astype(bool) | p.known_hypertension.fillna(0).astype(bool))
    if rule == "genetic":
        wanted = set(CFG["genetic_checks"])
        return p.open_cards.map(lambda cs: any(c["check"] in wanted for c in cs)) & p.consent_genetic.fillna(0).astype(bool)
    raise ValueError(f"Unknown rule {rule!r}")


def panel_size(user, assignments=None):
    a = assignments if assignments is not None else load()["assignments"]
    return sum(1 for team in a.values() if user in team.values())


def duty_ids(prof, p, assigned, leave):
    """What the hospital's duty doctor covers on top of their panel: people with an Urgent card and no doctor at all."""
    if not (prof["duty"] and prof["hospital"]) or leave.get(prof["user"]):
        return set()
    here = p[p.region == prof["hospital"]]
    free = here[~here.Id.isin(set(assigned))]
    return set(free[(free.urgency == "Urgent") & (free.open_cards.map(len) > 0)].Id)


def visible_ids(user, p):
    """The people a care-team doctor may open now (see the module notes)."""
    prof = profile(user)
    if prof is None:
        return set()
    d, stamp = load(), now()
    assigned = d["assignments"]
    ids = {pid for pid, team in assigned.items() if user in team.values()}
    covering = [x["user"] for x in doctors() if d["leave"].get(x["user"]) and x["backup"] == user]
    if covering:
        ids |= {pid for pid, team in assigned.items() if any(v in covering for v in team.values())}
    ids |= duty_ids(prof, p, assigned, d["leave"])
    ids |= {g["person"] for g in d["break_glass"] if g["doctor"] == user and g["expires"] > stamp}
    if prof["hospital"]:
        ids |= {g["person"] for g in d["grants"] if g["hospital"] == prof["hospital"] and not g.get("revoked") and g["expires"] > stamp}
    return ids


# ------------------------------------------------------------------ proposing and applying assignments
def propose(p, docs=None, only=None, hospital=None):
    """Who the rules would assign, without saving anything. Returns {"items": [(person, specialty, doctor, why)], "left": ...}.

    For each specialty (narrow first) and hospital: the people it may look after who have no doctor of that specialty yet,
    urgent first, children of one parent together; each goes to the doctor of that hospital and specialty with the lowest
    share of their panel filled, preferring one who speaks the person's language; nobody goes past a panel limit and
    nobody to a doctor on leave."""
    docs = docs if docs is not None else doctors()
    d = load()
    assigned, leave = d["assignments"], d["leave"]
    load_now = Counter({x["user"]: panel_size(x["user"], assigned) for x in docs})
    items, left = [], []
    for spec in ORDER:
        rule = SPECIALTIES[spec]["who"]
        mask = eligible(p, rule)
        for place in hospitals(p):
            if hospital and place != hospital:
                continue
            team = [x for x in docs if spec in x["specialty"] and x["hospital"] == place and not leave.get(x["user"])
                    and (not only or x["user"] in only)]
            cand = p[mask & (p.region == place)]
            cand = cand[~cand.Id.map(lambda i: spec in assigned.get(i, {}))]
            if cand.empty:
                continue
            if not team:
                left.append({"hospital": place, "specialty": spec, "people": int(len(cand)), "why": "no doctor"})
                continue
            cand = cand.assign(rank_=cand.urgency.map(RANK).fillna(3)).sort_values(["rank_", "Id"])
            # children of one parent are placed together, in the order of their most urgent child
            units = []
            if rule == "children":
                for _, g in cand.groupby(cand.guardian_id.fillna(cand.Id), sort=False):
                    units.append(list(g.itertuples()))
            else:
                units = [[r] for r in cand.itertuples()]
            units.sort(key=lambda u: min(r.rank_ for r in u))
            unplaced = 0
            for unit in units:
                room = [x for x in team if load_now[x["user"]] + len(unit) <= x["panel_limit"]]
                if not room:
                    unplaced += len(unit)
                    continue
                language = unit[0].language
                speaks = [x for x in room if language in x["languages"]]
                pick = min(speaks or room, key=lambda x: (load_now[x["user"]] / x["panel_limit"], x["user"]))
                for r in unit:
                    why = {"specialty": spec, "language": language, "language_match": bool(speaks), "together": len(unit) > 1}
                    items.append((r.Id, spec, pick["user"], why))
                load_now[pick["user"]] += len(unit)
            if unplaced:
                left.append({"hospital": place, "specialty": spec, "people": unplaced, "why": "patient lists full"})
    return {"items": items, "left": left}


def summarise(proposal, p, docs):
    """The proposal as the admin reads it: per doctor how many, how many urgent, language matches, siblings kept together."""
    by_id = p.set_index("Id")
    out = {x["user"]: {"user": x["user"], "name": x["name"], "hospital": x["hospital"], "new": 0, "urgent": 0, "language_match": 0,
                       "together": 0, "children": 0, "sample": []} for x in docs}
    for pid, spec, doc, why in proposal["items"]:
        row = by_id.loc[pid]
        o = out[doc]
        o["new"] += 1
        o["urgent"] += int(row.urgency == "Urgent")
        o["language_match"] += int(why["language_match"])
        o["together"] += int(why["together"])
        o["children"] += int(row.is_child == 1)
        if len(o["sample"]) < 5:
            o["sample"].append({"id": pid, "age": int(row.age), "language": row.language, "urgency": row.urgency, "specialty": spec})
    return [v for v in out.values() if v["new"]], proposal["left"]


def apply(proposal, by="admin"):
    d = load()
    for pid, spec, doc, why in proposal["items"]:
        d["assignments"].setdefault(pid, {})[spec] = doc
    save(d)
    return len(proposal["items"])


def assign(person, specialty, doctor, p):
    """One assignment by hand (doctor None = remove it). Raises ValueError with the reason when the rules do not allow it."""
    if specialty not in SPECIALTIES:
        raise ValueError(f"Unknown specialty {specialty!r}")
    row = p[p.Id == person]
    if row.empty:
        raise ValueError("Person not found")
    d = load()
    if doctor is None:
        d["assignments"].get(person, {}).pop(specialty, None)
        if not d["assignments"].get(person):
            d["assignments"].pop(person, None)
        save(d)
        return
    prof = profile(doctor)
    if prof is None:
        raise ValueError(f"{doctor} has no care-team profile")
    if specialty not in prof["specialty"]:
        raise ValueError(f"{prof['name']} does not work in {specialty}")
    r = row.iloc[0]
    if prof["hospital"] and r.region != prof["hospital"]:
        raise ValueError(f"{prof['name']} works at {prof['hospital']}, this person lives in {r.region}: share the record instead")
    if not bool(eligible(row, SPECIALTIES[specialty]["who"]).iloc[0]):
        raise ValueError(f"{specialty} looks after: {SPECIALTIES[specialty]['text'].lower()}")
    if d["assignments"].get(person, {}).get(specialty) != doctor and panel_size(doctor, d["assignments"]) >= prof["panel_limit"]:
        raise ValueError(f"{prof['name']}'s patient list is full ({prof['panel_limit']})")
    d["assignments"].setdefault(person, {})[specialty] = doctor
    save(d)


def set_profile(user, fields):
    """Changes that live in the care-team file: leave, and (open mode) the demo doctors' own profile."""
    d = load()
    if "on_leave" in fields:
        d["leave"][user] = bool(fields["on_leave"])
    if open_mode():
        keep = {k: v for k, v in fields.items() if k in ("specialty", "languages", "panel_limit", "duty", "backup")}
        if keep:
            d["demo_profiles"].setdefault(user, {}).update(keep)
    save(d)


# ------------------------------------------------------------------ break glass and sharing
def break_glass(user, person, reason, p):
    prof = profile(user)
    if prof is None:
        raise ValueError("Only care-team doctors use this")
    reason = str(reason or "").strip()
    if len(reason) < CFG["break_glass_min_reason"]:
        raise ValueError(f"Say why in at least {CFG['break_glass_min_reason']} characters (for example: patient in the emergency room)")
    if p[p.Id == person].empty:
        raise ValueError("Person not found")
    if person in visible_ids(user, p):
        return {"already": True}
    grant = {"doctor": user, "person": person, "reason": reason[:300], "time": now(), "expires": later(hours=CFG["break_glass_hours"])}
    d = load()
    d["break_glass"].append(grant)
    save(d)
    return {"already": False, **grant}


def share(person, hospital, days, by):
    days = max(1, min(int(days or CFG["sharing_default_days"]), CFG["sharing_max_days"]))
    d = load()
    stamp = now()
    for g in d["grants"]:
        if g["person"] == person and g["hospital"] == hospital and not g.get("revoked") and g["expires"] > stamp:
            g["expires"] = later(days=days)          # sharing again extends it
            save(d)
            return g
    g = {"person": person, "hospital": hospital, "by": by, "time": stamp, "expires": later(days=days),
         "purpose": "Care at another hospital"}
    d["grants"].append(g)
    save(d)
    return g


def revoke(person, hospital):
    d, n = load(), 0
    for g in d["grants"]:
        if g["person"] == person and g["hospital"] == hospital and not g.get("revoked"):
            g["revoked"] = now()
            n += 1
    save(d)
    return n


def active_grants(person):
    stamp = now()
    return [g for g in load()["grants"] if g["person"] == person and not g.get("revoked") and g["expires"] > stamp]
