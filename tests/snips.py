# Code snippets added to the Implementation Guide. Each is executed by test_snips.py.
S = {}

S["context"] = '''patients = patients.rename(columns={"Id": "id"})
patients = patients[patients.DEATHDATE.isna()].reset_index(drop=True)   # never contact deceased people
n = len(patients)
today = pd.Timestamp("2026-11-01")
patients["age"] = ((today - pd.to_datetime(patients.BIRTHDATE)).dt.days // 365.25).astype(int)
patients["age_group"] = pd.cut(patients.age, [-1, 5, 17, 39, 64, 200],
                               labels=["0-5", "6-17", "18-39", "40-64", "65+"])
patients["nationality"] = rng.choice(["Emirati", "Other Arab", "South Asian", "Filipino", "Western", "Other"],
                                     n, p=[.20, .15, .40, .10, .05, .10])
patients["region"] = patients.area_type.map({"city": "Abu Dhabi City", "rural": "Al Ain outskirts",
                                             "remote": "Al Dhafra"})          # simulated mapping
patients["insurance"] = np.where(patients.nationality == "Emirati", "Thiqa",
                                 rng.choice(["Basic", "Enhanced"], n, p=[.6, .4]))
patients["smoker"] = (patients.age >= 15) & (rng.random(n) < 0.15)
patients["active"] = rng.random(n) < 0.45                  # about 150 minutes of exercise a week
patients["work_shift"] = np.where(patients.age >= 18,
                                  rng.choice(["day", "evening", "none"], n, p=[.5, .15, .35]), "none")
patients["clinician_gender_pref"] = rng.choice(["any", "female", "male"], n, p=[.6, .3, .1])
patients["family_history_recorded"] = rng.random(n) < 0.75 # False means family history is unknown'''

S["households"] = '''is_child = patients.age < 18
adults = patients[~is_child].sample(frac=1, random_state=42)
n_households = max(len(adults) // 2, 1)                     # about 2 adults per household
patients.loc[adults.index, "household_id"] = np.arange(len(adults)) % n_households
patients.loc[is_child, "household_id"] = rng.integers(0, n_households, is_child.sum())
guardian = patients[~is_child].groupby("household_id").id.first()   # the adult who gets child messages
patients["guardian_id"] = np.where(is_child, patients.household_id.map(guardian), None)
by_id = patients.set_index("id")
for col in ["area", "area_type", "region", "travel_min", "language", "nationality", "insurance"]:
    patients.loc[is_child, col] = patients.loc[is_child, "guardian_id"].map(by_id[col]).values
for marker in GENETIC_MARKERS:                              # a child of a carrier has a 50% chance
    col = f"gene_{marker}"
    parent_has = patients.guardian_id.map(by_id[col]).fillna(False).astype(bool)
    patients.loc[parent_has & (rng.random(n) < 0.5), col] = True'''

S["interventions"] = '''INTERVENTIONS = [   # (preventive intervention, who it is offered to)
    ("Prediabetes programme",      lambda p: 5.7 <= p.hba1c < 6.5 and not p.has_diabetes),
    ("Weight management support",  lambda p: p.age >= 18 and p.bmi >= 30),
    ("Stop-smoking service",       lambda p: p.smoker),
    ("Physical activity advice",   lambda p: p.age >= 18 and not p.active),
    ("Family nutrition programme", lambda p: p.age < 18 and p.bmi_percentile >= 95),
]
def interventions(p):
    return [{"check": name, "pathway": "Preventive intervention", "reason": "Lifestyle or lab finding"}
            for name, offer in INTERVENTIONS if offer(p)]'''

S["consent"] = '''consents = []          # one row per decision; the newest decision for a purpose wins
def record_consent(person_id, purpose, granted, given_by, day, version="v1"):
    consents.append({"person_id": person_id, "purpose": purpose, "granted": granted,
                     "given_by": given_by, "date": day, "version": version})
    audit_log.append(("consent", person_id, purpose, granted, given_by, day))
def has_consent(p, purpose):
    rows = [c for c in consents if c["person_id"] == p.id and c["purpose"] == purpose]
    if not rows:
        return False                                 # no record means no consent
    last = rows[-1]
    if p.age < 18 and last["given_by"] != p.guardian_id:
        return False                                 # for a child, only the guardian can agree
    return last["granted"]'''

S["pathway"] = '''NEEDS_CLINICIAN = {"genetic_counselling", "carrier_counselling", "results_review", "basic_health_check"}
def pathway_card(p, gap, guidelines, today):
    g = guidelines.get(gap["check"])                 # None if the check is not in the approved table
    card = {
        "pathway":       g["pathway"] if g else gap.get("pathway", "Clinician review"),
        "check":         gap["check"],
        "invite_by":     today + timedelta(days=DEADLINE_DAYS.get(p.urgency, 30)),
        "recall_months": g["every_months"] if g else None,
        "guideline":     f'{g["source"]} ({g["version"]})' if g else "Not in approved guidelines",
        "reason":        gap.get("reason", "Overdue by schedule"),
    }
    clinical = g is None or gap["check"] in NEEDS_CLINICIAN or p.urgency == "Urgent"
    card["decision"] = "Clinician decides" if clinical else "Outreach invite (population priority)"
    return card'''

S["routing"] = '''def recipient(p, people):
    """Messages about a child go to the parent or guardian, in the guardian's language."""
    return people.loc[p.guardian_id] if p.age < 18 else p
TEMPLATES = {   # written and approved by the clinical team, translated once and checked by a native speaker
    ("well_child_visit", "guardian", "English"):
        "Hello. {child} is due for a routine child health check. It helps make sure {child} "
        "is growing and developing well. {option}. Reply BOOK, INFO, LATER or STOP.",
    ("breast_screening", "adult", "English"):
        "Hello. You are due for a routine health check. {option}. Reply BOOK, INFO, LATER or STOP.",
}
BLOCKED = ["gene", "genetic", "brca", "cancer", "risk score", "%", "carrier"]
def safe_message(p, gap, option, people):
    to = recipient(p, people)
    audience = "guardian" if p.age < 18 else "adult"
    template = (TEMPLATES.get((gap["check"], audience, to.language))
                or TEMPLATES.get((gap["check"], audience, "English")))
    if template is None:
        return None, "No approved template: send to staff for review"
    text = template.format(child="your child", option=option)
    if any(w in text.lower() for w in BLOCKED) or len(text) > 320:
        return None, "Message failed safety check: send to staff for review"
    return text, to.id'''

S["timing"] = '''def contact_window(p, day, ramadan_days):
    if p.preferred_time:                         return p.preferred_time  # the person's choice wins
    if day in ramadan_days:                      return "20:30-22:30"     # after iftar
    if day.weekday() == 4:                       return "16:00-20:00"     # Friday: after midday prayer
    if p.work_shift == "day":                    return "18:00-21:00"     # after work
    return "10:00-13:00"'''

S["llmsafe"] = '''ALLOWED_TO_SEND = {"language", "age_group", "check", "care_option", "reasons", "question"}
def llm_safe(template, **fields):
    blocked = set(fields) - ALLOWED_TO_SEND
    if blocked:
        raise ValueError(f"Not allowed to send out: {sorted(blocked)}")
    return llm(template.format(**fields))'''

S["fair"] = '''THRESHOLD = 0.6                                   # same cut-off as High urgency in Step 5.1
def wilson(k, n, z=1.96):
    """95% range for a rate; wide ranges warn that a group is small."""
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))
def calibration_by_group(df, group):              # df columns: risk (predicted), outcome (0 or 1)
    t = df.groupby(group, observed=True).agg(count=("outcome", "size"),
                                             predicted=("risk", "mean"), observed=("outcome", "mean"))
    t["brier"] = df.assign(se=(df.risk - df.outcome) ** 2).groupby(group, observed=True).se.mean()
    t["gap"] = t.predicted - t.observed           # above 0.05 either way: check the model for this group
    return t
def suppress(t, min_size=10):
    """Hide small groups, and one more group if a single hidden one could be worked out from the total."""
    small = t["count"] < min_size
    if small.sum() == 1 and len(t) > 1:
        small[t.loc[~small, "count"].idxmin()] = True
    out = t.astype(object)
    out.loc[small, :] = "Too few to report"
    return out'''

S["outcomes"] = '''def timely_attendance(invites, window_days=30):
    """Share of invites attended within the window. Not attended counts as not on time."""
    days = (invites.attended_on - invites.invited_on).dt.days
    return float((days <= window_days).mean())
def followup_completion(results, window_days=30):
    abnormal = results[results.abnormal]
    days = (abnormal.followup_on - abnormal.result_on).dt.days
    return float((days <= window_days).mean()) if len(abnormal) else float("nan")
def calibration_summary(y, p):
    from sklearn.metrics import brier_score_loss
    from sklearn.linear_model import LogisticRegression
    p = np.clip(p, 1e-6, 1 - 1e-6)
    logit = np.log(p / (1 - p)).reshape(-1, 1)
    lr = LogisticRegression(C=1e6).fit(logit, y)
    return {"brier": brier_score_loss(y, p),        # lower is better
            "slope": lr.coef_[0][0],               # close to 1 is good
            "intercept": lr.intercept_[0]}         # close to 0 is good
patients["arm"] = np.where(rng.random(len(patients)) < 0.10, "usual_care", "new_system")'''
