"""
The fixed texts shown instead of anything written by a language model.

The doctor's one-sentence summary is put together from the person's own reasons and warning signs, and a free question in the chat gets
one approved answer. The wording lives in config/fixed_text.json. Nothing here calls an outside service.
"""
import json
import os
from functools import lru_cache

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "config", "fixed_text.json"), encoding="utf-8") as _fh:
    TEXT = {k: v["value"] for k, v in json.load(_fh)["values"].items()}


def clinician_summary(risk_band, reasons, signs, urgency=None):
    """One sentence for the doctor. The urgency comes first with its real cause: a warning sign makes a person Urgent even when
    the risk score is low, so the text never reads "Urgent ... low risk" without saying why. Cached: the same inputs give the same text."""
    return _summary(risk_band, tuple(reasons), tuple(signs), urgency)


@lru_cache(maxsize=2048)
def _summary(risk_band, reasons, signs, urgency=None):
    reasons, signs = list(reasons), list(signs)
    why = ("because of " + "; ".join(s[0].lower() + s[1:] for s in signs)) if signs else \
          ("because of the risk score" if risk_band == "High" else "because a routine check is due")
    parts = [f"{urgency or 'Flagged'} {why}",
             f"risk score in the {risk_band.lower()} band" if risk_band else "no risk score",
             ("main factors: " + ", ".join(reasons)) if reasons else None]
    return TEXT["summary_fallback"].replace("{parts}", "; ".join(p for p in parts if p))


def general_answer(question, language, check):
    """The reply to a free question in the chat: one approved answer that points to the care team and the doctor."""
    return TEXT["answer_fallback"]
