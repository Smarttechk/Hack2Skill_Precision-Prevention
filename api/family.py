"""
Adding family members to a patient account.

* A signed-in adult can ask to add a child they look after: the child's first name, date of birth, how they are related, and
  the child's record number if they know it. Nothing opens until the hospital admin has checked it against the record.
* The hospital admin finds the child's record (inside their own hospital), compares, and approves or rejects with a reason.
  Approval adds the parent as an extra guardian of that child: they then see the child in My care (what is due, messages,
  history, consent) like the registered guardian does. The registered guardian is never replaced.
* Adults (a spouse, a parent) are not added under someone else's account: they are adults with their own records and consent,
  so they sign up for their own account. The form says so.
* Everything is kept in outputs/family_requests.json (PP_FAMILY_FILE moves it; the tests do).
"""
import json
import os
import secrets
import threading
from datetime import date, datetime

import utils

LOCK = threading.RLock()
RELATIONS = ["Mother", "Father", "Legal guardian", "Other family member with parental responsibility"]
MAX_PENDING = 3
MAX_CHILDREN = 12


def path():
    return os.environ.get("PP_FAMILY_FILE") or os.path.join(utils.OUTPUTS, "family_requests.json")


def load():
    p = path()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
    else:
        d = {}
    d.setdefault("requests", [])
    d.setdefault("links", {})            # child id -> [extra guardians' person ids]
    return d


def save(d):
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    os.replace(tmp, p)


def now():
    return datetime.now().isoformat(timespec="seconds")


def extra_children(person):
    """Children the person was approved to look after on top of the ones registered to them."""
    return {c for c, gs in load()["links"].items() if person in gs}


def co_guardians(child):
    return list(load()["links"].get(child, []))


SEXES = ["Girl", "Boy"]
LANGUAGES = ["English", "Arabic", "Hindi", "Urdu", "Tagalog", "Other"]


def request(person, user, region, first_name, dob, relation, record_number="", note="", family_name="", sex="", language=""):
    """A parent's request to add a child. Raises ValueError with a plain reason."""
    first_name = " ".join(str(first_name or "").split())
    if not (2 <= len(first_name) <= 40):
        raise ValueError("Give the child's first name (2 to 40 letters)")
    try:
        born = date.fromisoformat(str(dob).strip())
    except ValueError:
        raise ValueError("Give the child's date of birth as year-month-day, for example 2019-05-23")
    age = (date.today() - born).days / 365.25
    if born > date.today():
        raise ValueError("The date of birth is in the future")
    if age >= 18:
        raise ValueError("This is for children under 18. An adult family member signs up for their own account, so their record and "
                         "their consent stay theirs.")
    if relation not in RELATIONS:
        raise ValueError("Choose how you are related to the child")
    with LOCK:
        d = load()
        mine = [r for r in d["requests"] if r["person"] == person]
        if sum(1 for r in mine if r["status"] == "pending") >= MAX_PENDING:
            raise ValueError(f"You already have {MAX_PENDING} requests waiting for the hospital. Please wait for them to be checked.")
        if sum(1 for r in mine if r["status"] == "approved") >= MAX_CHILDREN:
            raise ValueError("That is the most children one account can add; ask the hospital if you need more")
        if any(r["status"] == "pending" and r["first_name"].lower() == first_name.lower() and r["dob"] == born.isoformat() for r in mine):
            raise ValueError("You have already asked to add this child")
        family_name = " ".join(str(family_name or "").split())[:40]
        sex = sex if sex in SEXES else ""
        language = language if language in LANGUAGES else ""
        row = {"id": secrets.token_hex(5), "person": person, "user": user, "region": region, "time": now(), "first_name": first_name,
               "family_name": family_name, "sex": sex, "language": language,
               "dob": born.isoformat(), "relation": relation, "record_number": str(record_number or "").strip()[:30],
               "note": str(note or "").strip()[:200], "status": "pending"}
        d["requests"].append(row)
        save(d)
    return row


def mine(person):
    """What the parent sees about their requests (no admin-only fields)."""
    out = []
    for r in load()["requests"]:
        if r["person"] == person:
            out.append({k: r.get(k) for k in ("id", "first_name", "dob", "relation", "time", "status", "reason", "decided")})
    return sorted(out, key=lambda r: r["time"], reverse=True)


def pending(region=None):
    return [r for r in load()["requests"] if r["status"] == "pending" and (not region or r.get("region") == region)]


def decide(rid, approve, by, child=None, reason="", region=None, child_region=None, child_is_child=True, registered_guardian=None):
    """Approve (the child must be a child of the same hospital) or reject. Returns the finished request."""
    with LOCK:
        d = load()
        r = next((x for x in d["requests"] if x["id"] == rid and x["status"] == "pending"), None)
        if r is None:
            raise ValueError("No such pending request")
        if region and r.get("region") != region:
            raise ValueError("No such pending request")
        if approve:
            if not child or not child_is_child:
                raise ValueError("Choose the child's record")
            if child_region != r.get("region"):
                raise ValueError("The child's record is at a different hospital from the parent")
            if registered_guardian == r["person"]:
                raise ValueError("This parent is already the registered guardian of that child")
            links = d["links"].setdefault(child, [])
            if r["person"] not in links:
                links.append(r["person"])
            r["child"] = child
        else:
            reason = str(reason or "").strip()
            if len(reason) < 5:
                raise ValueError("Say why (a few words): the parent sees it")
            r["reason"] = reason[:200]
        r.update(status="approved" if approve else "rejected", decided=now(), decided_by=by)
        save(d)
        return r
