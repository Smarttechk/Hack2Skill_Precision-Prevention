"""FHIR R4 output (guide Step 13.1b).

  * open pathway cards as FHIR Tasks for clinic work lists (write_bundle). A Task never contains a genetic result: the
    reason says only "Genetic pathway" when that is the source.
  * patient records as FHIR resources (patient, encounter, condition, observations, immunization, procedure, medication
    request, allergy, care plan): one row of the hospital records database (records.py) in, one resource out. Used by
    the FHIR API (api/app.py, /api/fhir), by the sample export in scripts/fhir_import.py and by the bulk export
    (scripts/fhir_bulk_export.py), so records leave the system in the same form they arrive in.
  * outreach messages as FHIR Communications (communication), written by the bulk export next to the Tasks.
  * family history (FamilyMemberHistory), genetic results (Observation, LOINC 69548-6) and consent decisions
    (Consent), from the three tables of scripts/clinical_records.py. The FHIR API serves family history and consent;
    genetic results are built only for the export a hospital system sends, never served by the API.
"""
import json
import math

SYSTEM_URL = {"SNOMED-CT": "http://snomed.info/sct", "ICD-10": "http://hl7.org/fhir/sid/icd-10",
              "ICD-10-CM": "http://hl7.org/fhir/sid/icd-10-cm", "LOINC": "http://loinc.org", "CVX": "http://hl7.org/fhir/sid/cvx",
              "RxNorm": "http://www.nlm.nih.gov/research/umls/rxnorm"}
ALLERGY_CLINICAL = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
PARTICIPATION_MODE = "http://terminology.hl7.org/CodeSystem/v3-ParticipationMode"
# how a message reached the person, as FHIR Communication.medium (an SMS, a written chat message, a call, a visit)
MEDIUM = {"SMS": ("SMSWRIT", "SMS"), "WhatsApp": ("WRITTEN", "WhatsApp"), "reply": ("WRITTEN", "Written reply"),
          "Voice call": ("PHONE", "Phone call"), "Staff call": ("PHONE", "Phone call"),
          "Community health worker visit": ("FACE", "Visit by a community health worker")}
ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
CLASS_CODE = {"emergency": "EMER", "inpatient": "IMP", "snf": "IMP", "home": "HH", "hospice": "HH", "virtual": "VR"}  # else AMB
MRN_SYSTEM = "urn:abu-dhabi:hospital:mrn"
LANGUAGE_CODE = {"Arabic": "ar", "English": "en", "Urdu": "ur", "Hindi": "hi", "Tagalog": "tl"}
BP_PANEL, BP_PARTS = "85354-9", ("8480-6", "8462-4")
ROLE_CODE = "http://terminology.hl7.org/CodeSystem/v3-RoleCode"
# a genetic result: LOINC "Genetic variant assessment" with the gene studied and the clinical significance
GENETIC_CODE, GENE_STUDIED, SIGNIFICANCE_CODE = "69548-6", "48018-6", "53037-8"
SIGNIFICANCE = {"pathogenic": ("LA6668-3", "Pathogenic"), "likely_pathogenic": ("LA26332-9", "Likely pathogenic"),
                "VUS": ("LA26333-7", "Uncertain significance"), "likely_benign": ("LA26334-5", "Likely benign"),
                "benign": ("LA6675-8", "Benign")}
CONSENT_PURPOSE = "urn:abu-dhabi:prevention:consent-purpose"
CONSENT_TEXT = {"contact": "Contact about preventive care", "use_genetic": "Use of genetic results in the prevention plan"}


def _v(row, key):
    """A field of a row (dict or named tuple) as text; missing, None and NaN become ''."""
    x = row.get(key) if isinstance(row, dict) else getattr(row, key, None)
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    return str(x)


def cc(system, code, display):
    return {"coding": [{"system": SYSTEM_URL.get(system, system), "code": code, "display": display}], "text": display}


def ref(kind, i):
    return {"reference": f"{kind}/{i}"} if i else None


def quantity(value, unit):
    """A reading as a FHIR Quantity, the number written as it was (120 stays 120, 80.0 stays 80.0)."""
    try:
        x = float(value)
    except ValueError:
        return None
    return {"value": int(x) if "." not in value and "e" not in value.lower() and x.is_integer() else x, "unit": unit}


def patient(row):
    pid = _v(row, "Id")
    mrn = _v(row, "MRN") or f"MRN-{pid[:8].upper()}"
    given = [x for x in (_v(row, "GIVEN_NAME") or _v(row, "FIRST"), _v(row, "MIDDLE")) if x]
    res = {"resourceType": "Patient", "id": pid,
           "identifier": [{"type": {"coding": [{"code": "MR"}]}, "system": MRN_SYSTEM, "value": mrn}],
           "name": [{"use": "official", "family": _v(row, "FAMILY_NAME") or _v(row, "LAST"), "given": given}],
           "gender": {"M": "male", "F": "female"}.get(_v(row, "GENDER"), "unknown"), "birthDate": _v(row, "BIRTHDATE")}
    if _v(row, "DEATHDATE"):
        res["deceasedDateTime"] = _v(row, "DEATHDATE")
    if _v(row, "home_place") or _v(row, "region"):
        res["address"] = [{"use": "home", "city": _v(row, "home_place"), "district": _v(row, "region"),
                           "state": "Abu Dhabi", "country": "AE"}]
    if _v(row, "language"):
        lang = _v(row, "language")
        res["communication"] = [{"language": {"coding": [{"system": "urn:ietf:bcp:47", "code": LANGUAGE_CODE.get(lang, ""),
                                                          "display": lang}], "text": lang}, "preferred": True}]
    if _v(row, "nationality"):
        res["extension"] = [{"url": "http://hl7.org/fhir/StructureDefinition/patient-nationality",
                             "extension": [{"url": "code", "valueCodeableConcept": {"text": _v(row, "nationality")}}]}]
    return res


def encounter(row):
    res = {"resourceType": "Encounter", "id": _v(row, "Id"), "status": "finished",
           "class": {"system": ACT_CODE, "code": CLASS_CODE.get(_v(row, "ENCOUNTERCLASS"), "AMB")},
           "type": [cc("SNOMED-CT", _v(row, "CODE"), _v(row, "DESCRIPTION"))], "subject": ref("Patient", _v(row, "PATIENT")),
           "period": {"start": _v(row, "START"), "end": _v(row, "STOP")}}
    if _v(row, "REASONCODE"):
        res["reasonCode"] = [cc("SNOMED-CT", _v(row, "REASONCODE"), _v(row, "REASONDESCRIPTION"))]
    return res


def condition(row, rid):
    res = {"resourceType": "Condition", "id": rid, "subject": ref("Patient", _v(row, "PATIENT")),
           "encounter": ref("Encounter", _v(row, "ENCOUNTER")),
           "code": cc(_v(row, "SYSTEM") or "SNOMED-CT", _v(row, "CODE"), _v(row, "DESCRIPTION")),
           "onsetDateTime": _v(row, "START")}
    if _v(row, "STOP"):
        res["abatementDateTime"] = _v(row, "STOP")
    return {k: v for k, v in res.items() if v is not None}


def _observation(row, rid, extra):
    res = {"resourceType": "Observation", "id": rid, "status": "final", "category": [{"coding": [{"code": _v(row, "CATEGORY")}]}],
           "subject": ref("Patient", _v(row, "PATIENT")), "effectiveDateTime": _v(row, "DATE"), **extra}
    if _v(row, "ENCOUNTER"):
        res["encounter"] = ref("Encounter", _v(row, "ENCOUNTER"))
    return res


def observations(rows, first_id=0, prefix="obs"):
    """Observation resources for a list of rows: a systolic and diastolic reading taken together become one blood
    pressure panel with two components, as hospital systems send them."""
    out, i, panels = [], first_id, {}
    for r in rows:
        if _v(r, "CODE") in BP_PARTS:
            panels.setdefault((_v(r, "DATE"), _v(r, "PATIENT"), _v(r, "ENCOUNTER")), []).append(r)
            continue
        q = quantity(_v(r, "VALUE"), _v(r, "UNITS")) if _v(r, "TYPE") == "numeric" else None
        val = {"valueQuantity": q} if q else {"valueCodeableConcept": {"text": _v(r, "VALUE")}}
        out.append(_observation(r, f"{prefix}-{i}", {"code": cc("LOINC", _v(r, "CODE"), _v(r, "DESCRIPTION")), **val}))
        i += 1
    for group in panels.values():
        comps = [{"code": cc("LOINC", _v(r, "CODE"), _v(r, "DESCRIPTION")), "valueQuantity": quantity(_v(r, "VALUE"), _v(r, "UNITS"))}
                 for r in group]
        out.append(_observation(group[0], f"{prefix}-{i}", {"code": cc("LOINC", BP_PANEL, "Blood pressure panel"), "component": comps}))
        i += 1
    return out


def immunization(row, rid):
    res = {"resourceType": "Immunization", "id": rid, "status": "completed", "patient": ref("Patient", _v(row, "PATIENT")),
           "vaccineCode": cc("CVX", _v(row, "CODE"), _v(row, "DESCRIPTION")), "occurrenceDateTime": _v(row, "DATE")}
    if _v(row, "ENCOUNTER"):
        res["encounter"] = ref("Encounter", _v(row, "ENCOUNTER"))
    return res


def procedure(row, rid):
    res = {"resourceType": "Procedure", "id": rid, "status": "completed", "subject": ref("Patient", _v(row, "PATIENT")),
           "code": cc(_v(row, "SYSTEM") or "SNOMED-CT", _v(row, "CODE"), _v(row, "DESCRIPTION")),
           "performedPeriod": {"start": _v(row, "START"), "end": _v(row, "STOP")}}
    if _v(row, "ENCOUNTER"):
        res["encounter"] = ref("Encounter", _v(row, "ENCOUNTER"))
    if _v(row, "REASONCODE"):
        res["reasonCode"] = [cc("SNOMED-CT", _v(row, "REASONCODE"), _v(row, "REASONDESCRIPTION"))]
    return res


def medication_request(row, rid):
    """A medicine prescribed: RxNorm code, the dates, and the reason when there is one. No prices or payer."""
    res = {"resourceType": "MedicationRequest", "id": rid, "status": "completed" if _v(row, "STOP") else "active",
           "intent": "order", "medicationCodeableConcept": cc("RxNorm", _v(row, "CODE"), _v(row, "DESCRIPTION")),
           "subject": ref("Patient", _v(row, "PATIENT")), "authoredOn": _v(row, "START")}
    if _v(row, "ENCOUNTER"):
        res["encounter"] = ref("Encounter", _v(row, "ENCOUNTER"))
    if _v(row, "REASONCODE"):
        res["reasonCode"] = [cc("SNOMED-CT", _v(row, "REASONCODE"), _v(row, "REASONDESCRIPTION"))]
    if _v(row, "STOP"):
        res["dispenseRequest"] = {"validityPeriod": {"start": _v(row, "START"), "end": _v(row, "STOP")}}
    return res


def allergy(row, rid):
    """An allergy or intolerance with its reactions. Medicines are coded in RxNorm, foods and the rest in SNOMED CT."""
    category = _v(row, "CATEGORY")
    res = {"resourceType": "AllergyIntolerance", "id": rid,
           "clinicalStatus": {"coding": [{"system": ALLERGY_CLINICAL, "code": "resolved" if _v(row, "STOP") else "active"}]},
           "type": _v(row, "TYPE") if _v(row, "TYPE") in ("allergy", "intolerance") else "allergy",
           "code": cc("RxNorm" if category == "medication" else "SNOMED-CT", _v(row, "CODE"), _v(row, "DESCRIPTION")),
           "patient": ref("Patient", _v(row, "PATIENT")), "onsetDateTime": _v(row, "START")}
    if category in ("food", "medication", "environment", "biologic"):
        res["category"] = [category]
    if _v(row, "ENCOUNTER"):
        res["encounter"] = ref("Encounter", _v(row, "ENCOUNTER"))
    reactions = []
    for n in ("1", "2"):
        if _v(row, f"REACTION{n}"):
            r = {"manifestation": [cc("SNOMED-CT", _v(row, f"REACTION{n}"), _v(row, f"DESCRIPTION{n}"))]}
            if _v(row, f"SEVERITY{n}").lower() in ("mild", "moderate", "severe"):
                r["severity"] = _v(row, f"SEVERITY{n}").lower()
            reactions.append(r)
    if reactions:
        res["reaction"] = reactions
    return res


def care_plan(row, rid):
    """A care plan: what was planned (SNOMED CT), for which period, and why."""
    period = {"start": _v(row, "START")}
    if _v(row, "STOP"):
        period["end"] = _v(row, "STOP")
    res = {"resourceType": "CarePlan", "id": _v(row, "Id") or rid, "status": "completed" if _v(row, "STOP") else "active",
           "intent": "plan", "title": _v(row, "DESCRIPTION"), "category": [cc("SNOMED-CT", _v(row, "CODE"), _v(row, "DESCRIPTION"))],
           "subject": ref("Patient", _v(row, "PATIENT")), "period": period}
    if _v(row, "ENCOUNTER"):
        res["encounter"] = ref("Encounter", _v(row, "ENCOUNTER"))
    if _v(row, "REASONDESCRIPTION"):
        res["description"] = f"Reason: {_v(row, 'REASONDESCRIPTION')}"
    return res


def communication(row, rid, sent):
    """One outreach message (or the person's reply) from the simulation's message log: who it was for, how it reached
    them, the text and the date it was sent. A reply is the person's own message; the system's answer is kept as a note."""
    channel = _v(row, "channel")
    code, label = MEDIUM.get(channel, ("WRITTEN", channel or "Message"))
    person, recipient = _v(row, "person"), _v(row, "recipient") or _v(row, "person")
    text, said = _v(row, "text"), _v(row, "text")
    note = []
    if channel == "reply":
        said, _, answer = text.partition(" -> ")
        if answer:
            note = [{"text": f"Answer: {answer}"}]
    res = {"resourceType": "Communication", "id": rid, "status": "completed",
           "category": [{"text": "Preventive care outreach"}],
           "medium": [{"coding": [{"system": PARTICIPATION_MODE, "code": code, "display": label}], "text": label}],
           "subject": ref("Patient", person), "sent": sent, "payload": [{"contentString": said}]}
    if channel == "reply":
        res["sender"], res["recipient"] = ref("Patient", recipient), [{"display": "Clinic"}]
    else:
        res["sender"], res["recipient"] = {"display": "Clinic"}, [ref("Patient", recipient)]
    if LANGUAGE_CODE.get(_v(row, "language")):
        res["language"] = LANGUAGE_CODE[_v(row, "language")]
    if note:
        res["note"] = note
    return res


def family_history(row, rid):
    """What a person reported about one relative's condition; without a code, the history was taken and nothing
    relevant was reported."""
    res = {"resourceType": "FamilyMemberHistory", "id": rid, "status": "completed", "patient": ref("Patient", _v(row, "PATIENT")),
           "date": _v(row, "DATE"), "relationship": {"coding": [{"system": ROLE_CODE, "code": _v(row, "RELATIONSHIP") or "FAMMEMB"}]}}
    if _v(row, "CODE"):
        res["condition"] = [{"code": cc(_v(row, "SYSTEM") or "SNOMED-CT", _v(row, "CODE"), _v(row, "DESCRIPTION"))}]
    else:
        res["note"] = [{"text": _v(row, "DESCRIPTION") or "No relevant family history reported"}]
    return res


def genetic_result(row, rid):
    """A variant found, as hospital laboratories report it (HL7 genomics reporting): LOINC 69548-6 with the gene
    studied and the clinical significance as components."""
    gene = _v(row, "GENE") or _v(row, "MARKER")
    code, text = SIGNIFICANCE.get(_v(row, "SIGNIFICANCE"), ("", _v(row, "SIGNIFICANCE")))
    note = {"note": [{"text": _v(row, "DESCRIPTION")}]} if _v(row, "DESCRIPTION") else {}
    return {"resourceType": "Observation", "id": rid, "status": "final", **note,
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
            "code": cc("LOINC", GENETIC_CODE, "Genetic variant assessment"), "subject": ref("Patient", _v(row, "PATIENT")),
            "effectiveDateTime": _v(row, "DATE"),
            "valueCodeableConcept": {"coding": [{"system": SYSTEM_URL["LOINC"], "code": "LA9633-4", "display": "Present"}], "text": "Present"},
            "component": [{"code": cc("LOINC", GENE_STUDIED, "Gene studied [ID]"),
                           "valueCodeableConcept": {"coding": [{"system": "http://www.genenames.org", "display": gene}], "text": gene}},
                          {"code": cc("LOINC", SIGNIFICANCE_CODE, "Genetic variation clinical significance [Imp]"),
                           "valueCodeableConcept": {"coding": [{"system": SYSTEM_URL["LOINC"], "code": code, "display": text}], "text": text}}]}


def consent(row, rid):
    """One consent decision: the purpose, permit or deny, and who gave it (the guardian for a child)."""
    purpose = _v(row, "PURPOSE")
    return {"resourceType": "Consent", "id": rid, "status": "active",
            "scope": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/consentscope", "code": "patient-privacy"}]},
            "category": [{"coding": [{"system": CONSENT_PURPOSE, "code": purpose}], "text": CONSENT_TEXT.get(purpose, purpose)}],
            "patient": ref("Patient", _v(row, "PATIENT")), "dateTime": _v(row, "DATE"),
            "performer": [ref("Patient", _v(row, "GIVEN_BY") or _v(row, "PATIENT"))],
            "policyRule": {"text": "Hospital consent form"},
            "provision": {"type": "deny" if _v(row, "DECISION") == "deny" else "permit"}}


def bundle(resources, kind="searchset", base="https://hospital.example/fhir"):
    return {"resourceType": "Bundle", "type": kind, "total": len(resources),
            "entry": [{"fullUrl": f"{base}/{r['resourceType']}/{r['id']}", "resource": r,
                       **({"search": {"mode": "match"}} if kind == "searchset" else {})} for r in resources]}


def to_task(person_id, card, ctx):
    reason = "Genetic pathway" if str(card.get("reason", "")).startswith("Genetic marker") else card.get("reason", "")
    return {
        "resourceType": "Task", "status": "requested",
        "intent": "proposal" if card["decision"] == "Clinician decides" else "plan",
        "description": f"{card['pathway']}: {card['check']}",
        "for": {"reference": f"Patient/{person_id}"},
        "authoredOn": ctx.date(len(ctx.daily) - 1).date().isoformat(),
        "restriction": {"period": {"end": ctx.date(card["invite_by_day"]).date().isoformat()}},
        "reasonCode": {"text": reason},
        "note": [{"text": f"Guideline: {card['guideline']}. Outreach priority, not a diagnosis."}],
    }


def write_bundle(ctx, path):
    tasks = [to_task(p.pid, c, ctx) for p in ctx.people.values() for c in p.cards
             if c["status"] in ("ready", "agreed", "awaiting_doctor")]
    bundle = {"resourceType": "Bundle", "type": "collection", "total": len(tasks),
              "entry": [{"resource": t} for t in tasks]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=1)
    return len(tasks)
