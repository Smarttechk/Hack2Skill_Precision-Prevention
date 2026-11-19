"""
Outreach contacts: who contacted whom, how, and what came of it.

* Marking a person done records an outcome, the staff member, the channel and a short note, and removes the person from every
  staff member's list at once.
* An outcome that ends the contact (agreed to book, declined, needs a doctor first) closes the person. "Asked to be called
  later" brings them back after a few days and "no answer" after a few hours; after three no-answers, or a wrong number, the
  person leaves the daily list and shows as escalated (another route: a health worker visit, or the doctor).
* "Being contacted": a staff member who starts a contact holds the person for 30 minutes, so two people do not call the same
  family. It ends when the outcome is saved, when they release it, or after 30 minutes.
* Everything is in plain files next to the other outputs (outreach_contacts.csv, outreach_claims.json); PP_OUTREACH_DIR moves them
  (the tests do). The older one-tick log (outreach_log.csv) still counts as "done".
"""
import json
import os
import threading
from datetime import datetime, timedelta

import pandas as pd

import utils

LOCK = threading.RLock()
CLAIM_MINUTES = 30
MAX_TRIES = 3
FIELDS = ["time", "person", "staff", "name", "channel", "outcome", "note", "next_try"]
CHANNELS = ["WhatsApp", "SMS", "Phone call", "Visit by a health worker", "In person at the clinic"]
OUTCOMES = {
    "agreed": {"label": "Reached: agreed to book", "ends": True},
    "later": {"label": "Reached: asked to be called later", "retry": timedelta(days=3)},
    "declined": {"label": "Reached: declined", "ends": True},
    "needs_doctor": {"label": "Needs a doctor first", "ends": True},
    "no_answer": {"label": "No answer", "retry": timedelta(hours=4)},
    "wrong_number": {"label": "Wrong number or cannot be reached", "escalate": True},
    "done": {"label": "Marked done", "ends": True},          # the older one-click tick
    # what happened to a booked visit (recorded by staff, no channel): attended closes the person; a missed visit brings them back to
    # the list the next day to rebook, and after three missed visits they are escalated; a moved visit returns after a week
    "attended": {"label": "Visit attended", "ends": True, "visit": True},
    "no_show": {"label": "Missed the visit", "retry": timedelta(days=1), "visit": True},
    "rescheduled": {"label": "Visit moved to another day", "retry": timedelta(days=7), "visit": True},
}
VISIT_OUTCOMES = {k for k, v in OUTCOMES.items() if v.get("visit")}


class Conflict(Exception):
    """Someone else is contacting this person right now."""


def now():
    return datetime.now().isoformat(timespec="seconds")


def _paths():
    d = os.environ.get("PP_OUTREACH_DIR") or utils.OUTPUTS
    return (os.path.join(d, "outreach_contacts.csv"), os.path.join(d, "outreach_claims.json"), os.path.join(d, "outreach_log.csv"))


def contacts():
    """Every contact so far, oldest first (the older one-tick log is included as outcome "done")."""
    new, _, old = _paths()
    frames = []
    if os.path.exists(old):
        o = pd.read_csv(old, dtype=str).fillna("")
        frames.append(pd.DataFrame({"time": o.time, "person": o.Id, "staff": "", "name": "", "channel": "", "outcome": "done", "note": "", "next_try": ""}))
    if os.path.exists(new):
        frames.append(pd.read_csv(new, dtype=str).fillna("").reindex(columns=FIELDS, fill_value=""))
    if not frames:
        return pd.DataFrame(columns=FIELDS)
    return pd.concat(frames, ignore_index=True).sort_values("time", kind="stable").reset_index(drop=True)


def states(df=None, stamp=None):
    """Where each contacted person stands: closed, waiting (back later), escalated (another route needed) or open (back in the list)."""
    df, stamp = (contacts() if df is None else df), stamp or now()
    out = {}
    for person, g in df.groupby("person", sort=False):
        last = g.iloc[-1]
        spec = OUTCOMES.get(last.outcome, OUTCOMES["done"])
        tries = int((g.outcome == "no_answer").sum())
        missed = int((g.outcome == "no_show").sum())
        if spec.get("ends"):
            status = "closed"
        elif spec.get("escalate") or tries >= MAX_TRIES or missed >= MAX_TRIES:
            status = "escalated"
        elif last.next_try and last.next_try > stamp:
            status = "waiting"
        else:
            status = "open"
        out[person] = {"status": status, "tries": tries, "missed_visits": missed, "next_try": last.next_try or None, "last": last.to_dict()}
    return out


def hidden_ids(stamp=None):
    """People that leave the daily list: closed, waiting for their retry time, or escalated."""
    return {p for p, s in states(stamp=stamp).items() if s["status"] != "open"}


# ------------------------------------------------------------------ being contacted
def _claims():
    _, path, _ = _paths()
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save_claims(c):
    _, path, _ = _paths()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(c, fh)
    os.replace(tmp, path)


def active_claims(stamp=None):
    stamp = stamp or now()
    return {p: c for p, c in _claims().items() if c["until"] > stamp}


def claim(person, user, name):
    stamp = now()
    with LOCK:
        c = _claims()
        cur = c.get(person)
        if cur and cur["until"] > stamp and cur["by"] != user:
            raise Conflict(f"Being contacted by {cur['name']} until {cur['until'][11:16]}")
        c[person] = {"by": user, "name": name, "since": stamp, "until": (datetime.now() + timedelta(minutes=CLAIM_MINUTES)).isoformat(timespec="seconds")}
        _save_claims({p: v for p, v in c.items() if v["until"] > stamp})
        return c[person]


def release(person, user):
    with LOCK:
        c = _claims()
        if c.get(person, {}).get("by") == user:
            c.pop(person)
            _save_claims(c)
            return True
        return False


# ------------------------------------------------------------------ recording a contact
def record(person, user, name, channel, outcome, note=""):
    """Save one contact. Raises ValueError for a bad outcome or channel and Conflict when someone else holds the person."""
    if outcome not in OUTCOMES:
        raise ValueError("Choose what happened")
    if outcome != "done" and outcome not in VISIT_OUTCOMES and channel not in CHANNELS:
        raise ValueError("Choose how you contacted them")
    stamp = now()
    with LOCK:
        cur = _claims().get(person)
        if cur and cur["until"] > stamp and cur["by"] != user:
            raise Conflict(f"Being contacted by {cur['name']} until {cur['until'][11:16]}")
        retry = OUTCOMES[outcome].get("retry")
        row = {"time": stamp, "person": person, "staff": user, "name": name, "channel": channel if outcome != "done" and outcome not in VISIT_OUTCOMES else "", "outcome": outcome,
               "note": str(note or "").strip()[:200], "next_try": (datetime.now() + retry).isoformat(timespec="seconds") if retry else ""}
        new, _, _ = _paths()
        os.makedirs(os.path.dirname(new), exist_ok=True)
        pd.DataFrame([row], columns=FIELDS).to_csv(new, mode="a", header=not os.path.exists(new), index=False)
        release(person, user)
    return row
