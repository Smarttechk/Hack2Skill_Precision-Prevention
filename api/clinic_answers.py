"""
Answers a clinic can add about a patient to narrow the diabetes score's range: waist circumference and two lifestyle questions
(daily exercise, daily vegetables). The records do not hold them, so without them the score shows a wide range; with them it is
re-worked for that person. Stored in outputs/clinic_answers.json (PP_ANSWERS_FILE moves it). They inform the doctor only: they do not
change anyone's priority or the simulation.
"""
import json
import os
import threading
from datetime import datetime

import utils

LOCK = threading.RLock()
WAIST_CM = (40, 200)


def path():
    return os.environ.get("PP_ANSWERS_FILE") or os.path.join(utils.OUTPUTS, "clinic_answers.json")


def load():
    p = path()
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def get(person):
    return load().get(person)


def save(person, by, waist_cm=None, exercise_daily=None, veg_daily=None):
    """Keep what was given (anything left out keeps its earlier answer). Raises ValueError with a plain reason."""
    if waist_cm is not None:
        try:
            waist_cm = float(waist_cm)
        except (TypeError, ValueError):
            raise ValueError("Waist circumference must be a number in centimetres")
        if not WAIST_CM[0] <= waist_cm <= WAIST_CM[1]:
            raise ValueError(f"Waist circumference should be between {WAIST_CM[0]} and {WAIST_CM[1]} cm")
    if waist_cm is None and exercise_daily is None and veg_daily is None:
        raise ValueError("Give at least one answer")
    with LOCK:
        d = load()
        cur = d.get(person, {})
        new = {"waist_cm": waist_cm if waist_cm is not None else cur.get("waist_cm"),
               "exercise_daily": exercise_daily if exercise_daily is not None else cur.get("exercise_daily"),
               "veg_daily": veg_daily if veg_daily is not None else cur.get("veg_daily"),
               "by": by, "time": datetime.now().isoformat(timespec="seconds")}
        d[person] = new
        p = path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.replace(tmp, p)
    return new
