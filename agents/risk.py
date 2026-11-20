"""
Risk agent (guide Step 4): a score with its range, top 3 reasons and the list of data used. The diabetes score is the published
FINDRISC questionnaire (models/risk_scores.py); nothing is trained there, and this score alone sets the priority. A trained second
opinion (models/ml_risk.py) is shown to doctors as information and never changes anyone's priority. People without enough records get
"Not enough data to judge" instead of a guess.
"""
import pandas as pd

from agents.base import Agent
from models import risk_scores as risk_model
import utils

# children get no risk score: their invitation comes from the check-up and vaccine schedule and growth records (rules only)
CHILD_DATA_USED = ["Age", "Well-child visit and vaccine records", "Clinic and emergency visits",
                   "Travel time and clinics near home", "Household (children, guardian's work shift, phone, insurance)"]


def data_used(r):
    """What the risk score uses (the FINDRISC items the records have). Vaccines, area, travel time and genetic findings are used
    by the care-gap rules, pathways and the access barrier, never by the risk score."""
    used = ["Age", "BMI", "Diagnoses (prediabetes, diabetes, high blood pressure)"]
    if r.family_history_recorded:
        used.append("Family history of diabetes")
    return used


class RiskAgent(Agent):
    name = "risk"

    def __init__(self, bundle=None):
        self.bundle = bundle

    def run(self, ctx, day):
        if ctx.mode != "new":                          # usual care uses no risk
            return
        if day != 0:
            self.update_from_visits(ctx, day)
            return
        if self.bundle is None:
            self.bundle = risk_model.load(ctx.features)
        ctx.risk_bundle = self.bundle
        f = ctx.features
        adults = f[(f.age >= utils.config("clinical_rules")["adult_age"]) & (f.data_sufficient == 1)]
        # the scores depend only on the records and the model, not on the run's behaviour settings, so they are
        # computed once and reused by every run in the same process (sensitivity analysis, seed spread)
        key = (id(self.bundle), tuple(adults.Id))
        if getattr(self, "_cache_key", None) != key:
            self._cache_key, self._cache = key, risk_model.score(self.bundle, adults)
        scores = self._cache
        for i, r in adults.iterrows():
            p = ctx.people[r.Id]
            s = scores.loc[i]
            if pd.isna(s.risk):                        # diabetes already diagnosed: nothing to predict
                continue
            p.risk, p.risk_low, p.risk_high, p.risk_reasons = float(s.risk), float(s.risk_low), float(s.risk_high), list(s.risk_reasons)
            # each condition separately: chance, range and its own main factors (None when already diagnosed)
            p.risks = {c: (None if pd.isna(s[f"{c}_risk"]) else
                           {"risk": float(s[f"{c}_risk"]), "low": float(s[f"{c}_low"]), "high": float(s[f"{c}_high"]),
                            "reasons": list(s[f"{c}_reasons"])})
                       for c in risk_model.CONDITIONS}
        for p in ctx.people.values():
            p.data_used = data_used(p.row) if not p.is_child else CHILD_DATA_USED
            p.risk_found = False
            ctx.audit.append({"day": day, "agent": self.name, "person": p.pid, "data": "; ".join(p.data_used),
                              "why": "risk score" if p.risk is not None else "care gaps"})

    @staticmethod
    def update_from_visits(ctx, day):
        """After a visit, the result replaces the prediction: a finding (diabetes or high blood pressure) puts the
        person at the top, a normal blood pressure and blood sugar puts the risk part of their priority at the bottom
        until the next check is due. The finding is shown as a result, never as a risk factor (person.detected keeps
        it); the prediction for that condition stops being shown. A real system would also rescore from new readings."""
        for p in ctx.people.values():
            if p.risk_normal_day is not None and not p.risk_found and not getattr(p, "_normal_logged", False):
                p._normal_logged = True
                ctx.audit.append({"day": day, "agent": "risk", "person": p.pid, "data": "visit result",
                                  "why": "normal result lowers the priority of the risk prediction"})
            if p.detected and not getattr(p, "risk_found", False):
                p.risk_found = True
                ctx.audit.append({"day": day, "agent": "risk", "person": p.pid, "data": "visit result",
                                  "why": "finding replaces the risk prediction"})