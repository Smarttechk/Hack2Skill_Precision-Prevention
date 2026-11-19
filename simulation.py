"""
The simulated world: how people reply to messages and whether they attend (guide Steps 8.3 and 8.5).
Every number comes from config/real_values.json, with its source and status (published, calibrated,
design or assumption). Calibrated values come from config/calibrated.json, written by scripts/calibrate.py.
"""
import json
import os

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
REAL = json.load(open(os.path.join(_HERE, "config", "real_values.json"), encoding="utf-8"))
_CAL_PATH = os.path.join(_HERE, "config", "calibrated.json")
CALIBRATED = json.load(open(_CAL_PATH, encoding="utf-8")) if os.path.exists(_CAL_PATH) else {}
B = {k: v["value"] for k, v in REAL["behaviour"].items()}
DESIGN = {k: v["value"] for k, v in REAL["design"].items()}

_followed_up = 1 - (B["results_not_followed_up_low"] + B["results_not_followed_up_high"]) / 2   # middle of the published range
DEFAULT_PARAMS = {
    "capacity": B["capacity_per_day"],
    "base_attendance_logit": CALIBRATED.get("base_attendance_logit", {}).get("value", DESIGN["uncalibrated_fallbacks"]["base_attendance_logit"]),
    "telehealth_boost": B["telehealth_boost"],
    "reply_mult": 1.0,
    "doctor_agree": B["doctor_agree"],
    "old_followup_rate": round(_followed_up, 3),
    "stop_share": CALIBRATED.get("stop_share", {}).get("value", DESIGN["uncalibrated_fallbacks"]["stop_share"]),
    "clinic_slots_per_day": DESIGN["clinic_slots_per_day"],
}
_phone = B["reply_sms"] * B["phone_vs_sms_ratio"]
CHANNEL_REPLY = {"WhatsApp": B["reply_whatsapp"], "SMS": B["reply_sms"], "Voice call": _phone, "Staff call": _phone,
                 "Community health worker visit": _phone}
with open(os.path.join(_HERE, "config", "clinical_rules.json"), encoding="utf-8") as _fh:
    DM_CHECKS = set(json.load(_fh)["values"]["blood_sugar_checks"]["value"])


def reply_types(stop_share):
    """BOOK / INFO / LATER keep their assumed proportions; STOP is the calibrated share."""
    split = B["reply_split"]
    total = sum(split.values())
    words = list(split) + ["STOP"]
    return words, [split[w] / total * (1 - stop_share) for w in split] + [stop_share]


def reply_probability(ctx, recipient, channel, in_own_language, barrier):
    r = recipient.row
    if channel in ("SMS", "WhatsApp") and r.phone_type == "none":
        return 0.0
    p = CHANNEL_REPLY.get(channel, B["reply_sms"])
    if channel == "SMS" and not r.can_read:
        p *= B["cannot_read_sms_factor"]
    if not in_own_language:                     # published odds ratio for language-matched outreach, applied in reverse
        odds = p / (1 - p) / B["language_match_odds_ratio"]
        p = odds / (1 + odds)
    p *= (1 - B["barrier_reply_effect"] * barrier) * ctx.params["reply_mult"]
    return float(np.clip(p, 0, DESIGN["max_reply_probability"]))


_rs = B["results_reply_split"]
RESULTS_REPLY_TYPES = (list(_rs), list(_rs.values()))   # a nurse calling about a result


def respond(ctx, recipient, channel, in_own_language, barrier, kind="invite"):
    """None (no reply) or one of BOOK / INFO / LATER / STOP."""
    if ctx.rng.random() >= reply_probability(ctx, recipient, channel, in_own_language, barrier):
        return None
    types = RESULTS_REPLY_TYPES if kind == "results" else reply_types(ctx.params.get("stop_share", DEFAULT_PARAMS["stop_share"]))
    return str(ctx.rng.choice(types[0], p=types[1]))


def attend_probability(ctx, barrier, past_no_shows, days_notice, is_ramadan, evening_slot, option, support):
    """Chance of attending a booked visit. days_notice = days between booking and the visit (lead time).
    Every effect is in config/real_values.json with its source; published odds ratios for a no-show are applied
    to the attendance log-odds with the opposite sign."""
    z = (ctx.params["base_attendance_logit"] + B["attendance_barrier_effect"] * barrier
         - np.log(B["prior_no_show_odds_ratio"]) * min(past_no_shows, B["prior_no_show_count_cap"])
         - np.log(B["lead_time_no_show_odds_ratio_per_day"]) * days_notice
         + B["ramadan_attendance_effect"] * bool(is_ramadan and not evening_slot)
         + B["evening_slot_effect"] * bool(evening_slot))
    if option and option.startswith(("Telehealth", "Mobile clinic")):
        z += ctx.params["telehealth_boost"]
    if option and option.startswith("Clinic visit with transport"):
        z += B["transport_help_effect"]
    p = 1 / (1 + np.exp(-z))
    levels = DESIGN["support_levels"]             # least to most: standard, extra reminder, staff call
    cut = {levels[2]: B["reminder_no_show_reduction_call"], levels[1]: B["reminder_no_show_reduction_automated"]}.get(support, 0.0)
    return float(1 - (1 - p) * (1 - cut))          # the reminder cuts the chance of a no-show by the published share

def screening_findings(ctx, person, checks, day):
    """What a completed visit reveals, from the hidden truth file (never shown to any model)."""
    found = []
    if person.pid not in ctx.truth.index or person.is_child:
        return found
    t = ctx.truth.loc[person.pid]
    when = ctx.date(day)
    if t.hidden_hypertension and t.hypertension_onset <= when and "hypertension" not in person.detected:
        found.append("hypertension")            # blood pressure is measured at every adult visit
    if t.hidden_diabetes and t.diabetes_onset <= when and "diabetes" not in person.detected and set(checks) & DM_CHECKS:
        found.append("diabetes")
    return found
