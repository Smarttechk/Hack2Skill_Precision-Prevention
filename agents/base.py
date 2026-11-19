"""
Shared pieces for every agent (guide Step 2): the Agent shape, the per-person state and the context.

Every agent reads and writes ctx.people (one Person per living resident) and appends to ctx.events,
so each decision builds on the one before it and can be traced afterwards.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import utils

ADULT_AGE = utils.config("clinical_rules")["adult_age"]

STATES = ["None", "Flagged", "Contacted", "Booked", "Attended", "Done", "On hold", "Opted out"]


class Agent:
    name = "agent"

    def run(self, ctx, day):
        """Read the shared state, decide, act, and record what happened."""
        raise NotImplementedError


@dataclass
class Person:
    pid: str
    row: object                                    # the person's row from patient_features.csv (read-only)
    gaps: list = field(default_factory=list)
    warning_signs: list = field(default_factory=list)
    cards: list = field(default_factory=list)      # pathway cards (Step 5.4)
    risk: float = None
    risk_low: float = None
    risk_high: float = None
    risk_reasons: list = field(default_factory=list)
    risks: dict = field(default_factory=dict)      # per condition: {"risk", "low", "high", "reasons"} or None if diagnosed
    child_risk: float = None                       # child model: chance of missing the next scheduled Well-Child Visit
    child_risk_low: float = None
    child_risk_high: float = None
    child_reasons: list = field(default_factory=list)
    data_used: list = field(default_factory=list)
    urgency: str = None
    priority: float = 0.0
    state: str = "None"
    flagged_on: int = None
    contacted_on: int = None
    first_invite_day: int = None
    reminders: int = 0
    paused_until: int = -1
    channel: str = None
    preferred_channel: str = None
    preferred_time: str = None
    booked_day: int = None
    booked_on: int = None                          # day the booking was made (lead time = booked_day - booked_on)
    booking_checks: list = field(default_factory=list)
    booking_option: str = None
    past_no_shows: int = 0
    support: str = None
    done: dict = field(default_factory=dict)       # check -> day completed during the simulation
    doctor_feedback: str = None
    awaiting_reply: bool = False
    last_action: str = None
    followup_action_key: tuple = None
    detected: dict = field(default_factory=dict)   # condition -> day an abnormal result was found
    diagnosed: dict = field(default_factory=dict)  # condition -> day confirmed at follow-up
    needs_results_review: list = field(default_factory=list)
    risk_found: bool = False                       # a visit found the condition the risk score predicts
    risk_normal_day: int = None                    # day a visit measured normal blood pressure and blood sugar
    rebooks: int = 0                               # rebookings since the last attended visit

    @property
    def is_child(self):
        return self.row.age < ADULT_AGE


@dataclass
class Context:
    mode: str                                      # "new" (this system) or "old" (age-and-gender invitations)
    start: pd.Timestamp
    features: pd.DataFrame
    people: dict
    guidelines: pd.DataFrame
    templates: dict
    reply_words: dict
    calendar: pd.DataFrame
    truth: pd.DataFrame
    rng: np.random.Generator
    params: dict
    events: list = field(default_factory=list)
    pending_replies: list = field(default_factory=list)   # (person id, reply word, day)
    messages: list = field(default_factory=list)
    invites: list = field(default_factory=list)    # one row per booking, for the no-show model
    audit: list = field(default_factory=list)
    consents: list = field(default_factory=list)
    daily: list = field(default_factory=list)
    mobile_clinics: dict = field(default_factory=dict)
    noshow_model: object = None
    bandit: object = None
    channel_learner: object = None                 # learns the first-contact channel from replies (agents/outreach.py)
    risk_bundle: object = None
    child_bundle: object = None                    # unused: children have no learned score (kept so older code still reads the field)
    _families: dict = None
    slots: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)
    completion_behind: dict = field(default_factory=dict)   # groups behind on completed care, set daily by outreach     # warnings the evaluation agent raises for the team      # ("clinic" or "mobile", day) -> visits booked

    def date(self, day):
        return self.start + pd.Timedelta(days=day)

    def log(self, day, pid, event, detail=""):
        self.events.append({"day": day, "date": self.date(day).date().isoformat(), "person": pid,
                            "event": event, "detail": detail})

    def guardian(self, person):
        """The adult who receives a child's messages; the person themselves for an adult."""
        if person.is_child and isinstance(person.row.guardian_id, str) and person.row.guardian_id in self.people:
            return self.people[person.row.guardian_id]
        return person

    def family(self, recipient):
        """Everyone whose messages go to this recipient: the recipient and the children they look after."""
        if self._families is None:
            self._families = {}
            for p in self.people.values():
                self._families.setdefault(self.guardian(p).pid, []).append(p)
        return self._families.get(recipient.pid, [recipient])
