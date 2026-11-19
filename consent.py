"""Consent records (guide Step 4.8): one row per decision; the newest decision per purpose wins;
for a child only the guardian's decision counts. Every change goes to the audit log."""

PURPOSES = ("contact", "use_genetic", "use_family_history", "share_with_doctor")


def record(ctx, day, person_id, purpose, granted, given_by):
    ctx.consents.append({"day": day, "person": person_id, "purpose": purpose, "granted": bool(granted), "given_by": given_by})
    ctx.audit.append({"day": day, "agent": "consent", "person": person_id, "data": f"{purpose}={granted}", "why": f"given by {given_by}"})


def has(ctx, person, purpose):
    rows = [c for c in ctx.consents if c["person"] == person.pid and c["purpose"] == purpose]
    if not rows:
        if purpose == "contact":
            return bool(person.row.consent)
        if purpose == "use_genetic":
            return bool(person.row.consent_genetic)
        return False
    last = rows[-1]
    if person.is_child and last["given_by"] != person.row.guardian_id:
        return False
    return last["granted"]
