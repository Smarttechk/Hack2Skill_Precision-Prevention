"""
Sending SMS and WhatsApp messages to families.

Only approved texts go out, and only to people who allow contact. A child's messages go to the guardian's number. The number is
whatever the family typed in My care; we never take one from the hospital records.

Delivery goes through Twilio's REST API (standard library only). It stays in practice mode until these are set:

    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_SMS_FROM and/or TWILIO_WHATSAPP_FROM

In practice mode, or with PP_MESSAGING=dry, nothing is sent and the attempt is only logged. WhatsApp business messages also need the
provider's own approved templates; getting that approval is the hospital's job.

Every attempt, sent or not, goes to outputs/outbox.csv with the last four digits of the number and the length of the text, never the text.
"""
import base64
import csv
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import utils

LOCK = threading.RLock()
NUMBER = re.compile(r"^\+[1-9]\d{7,14}$")
CHANNELS = ("SMS", "WhatsApp")
FIELDS = ["time", "staff", "record", "channel", "status", "to_last4", "characters", "detail"]


def contact_path():
    return os.environ.get("PP_CONTACT_FILE") or os.path.join(utils.OUTPUTS, "contact_details.json")


def outbox_path():
    return os.environ.get("PP_OUTBOX_FILE") or os.path.join(utils.OUTPUTS, "outbox.csv")


def clean_number(raw):
    """A mobile number in international form (+ and 8 to 15 digits), or ValueError with a plain reason."""
    n = re.sub(r"[\s\-().]", "", str(raw or ""))
    if n.startswith("00"):
        n = "+" + n[2:]
    if not NUMBER.match(n):
        raise ValueError("Type the mobile number with the country code, for example +971501234567")
    return n


def _load():
    p = contact_path()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def get_number(person):
    return (_load().get(str(person)) or {}).get("number")


def set_number(person, number, by=""):
    n = clean_number(number)
    with LOCK:
        d = _load()
        d[str(person)] = {"number": n, "set_by": by, "time": datetime.now().isoformat(timespec="seconds")}
        p = contact_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.replace(tmp, p)
    return n


def clear_number(person):
    with LOCK:
        d = _load()
        d.pop(str(person), None)
        p = contact_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(d, fh)


def masked(person):
    n = get_number(person)
    return None if not n else "*" * (len(n) - 4) + n[-4:]


def live_ready():
    """True when the provider's details are all set and dry-run is not forced."""
    if os.environ.get("PP_MESSAGING", "").lower() == "dry":
        return False
    return bool(os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN")
                and (os.environ.get("TWILIO_SMS_FROM") or os.environ.get("TWILIO_WHATSAPP_FROM")))


def mode():
    return "live" if live_ready() else "dry run"


def _log(staff, record, channel, status, to, text, detail=""):
    p = outbox_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    new = not os.path.exists(p)
    with LOCK, open(p, "a", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({"time": datetime.now().isoformat(timespec="seconds"), "staff": staff, "record": record, "channel": channel, "status": status,
                    "to_last4": str(to)[-4:], "characters": len(text), "detail": str(detail)[:120]})


def outbox(limit=50):
    p = outbox_path()
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    return rows[::-1][:limit]


def send(channel, to, text, staff="", record=""):
    """Send one text. Returns {"status": "sent" | "dry run" | "failed", "detail": ...}; never raises for a delivery problem."""
    if channel not in CHANNELS:
        raise ValueError("Messages go by SMS or WhatsApp")
    to = clean_number(to)
    sender = os.environ.get("TWILIO_WHATSAPP_FROM" if channel == "WhatsApp" else "TWILIO_SMS_FROM", "")
    if not live_ready() or not sender:
        why = "dry run: nothing was sent" if not live_ready() else f"no {channel} sender is set"
        _log(staff, record, channel, "dry run", to, text, why)
        return {"status": "dry run", "detail": why}
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    wa = lambda n: "whatsapp:" + (n if n.startswith("+") else "+" + n)  # noqa: E731
    form = {"To": wa(to) if channel == "WhatsApp" else to, "From": wa(sender) if channel == "WhatsApp" else sender, "Body": text}
    req = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=urllib.parse.urlencode(form).encode(),
                                 headers={"Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode("utf-8"))
        _log(staff, record, channel, "sent", to, text, body.get("sid", ""))
        return {"status": "sent", "detail": body.get("status", "queued")}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            msg = ""
        _log(staff, record, channel, "failed", to, text, f"{e.code} {msg}")
        return {"status": "failed", "detail": f"The provider refused it ({e.code}). {msg}".strip()}
    except Exception as e:                               # network down, timeout
        _log(staff, record, channel, "failed", to, text, type(e).__name__)
        return {"status": "failed", "detail": "The provider could not be reached. Nothing was sent."}
