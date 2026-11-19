"""
Support for people who may miss a booked visit (guide Step 8.5), planned by a plain rule: extra help for people with a known access
barrier or a missed visit before. It only ever adds help (a reminder, a health-worker call); it never lowers priority or blocks a
booking. There is no learned model here: the rule reads two things the system already knows.
"""
import simulation

DESIGN = simulation.DESIGN
SUPPORT = DESIGN["support_levels"]          # least to most, in config/real_values.json


def option_features(option, support):
    """What kind of visit and how much support, as numbers (used by the follow-up learner's key)."""
    option = option or ""
    return {"remote_visit": int(option.startswith(("Telehealth", "Mobile clinic"))),
            "transport": int(option.startswith("Clinic visit with transport")),
            "support_level": SUPPORT.index(support) if support in SUPPORT else 0}


def rule_support(barrier, past_no_shows):
    """Extra help for people with known barriers or a missed visit."""
    r = DESIGN["support_rule"]
    if barrier >= r["health_worker_call_barrier"] or past_no_shows >= r["health_worker_call_no_shows"]:
        return SUPPORT[2]
    if barrier >= r["extra_reminder_barrier"] or past_no_shows >= r["extra_reminder_no_shows"]:
        return SUPPORT[1]
    return SUPPORT[0]
