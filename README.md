# Precision Prevention and Screening

A prototype for the Future Health Digital Innovation Challenge (Abu Dhabi), built by Smart Tech LLC.

Too many families miss check-ups, and some carry health risks nobody has noticed. This project finds the children and adults who most need a Well-Child Visit or a screening, helps staff reach them in their own language, and keeps track of whether they actually came. A doctor decides every clinical step. The list it produces is a priority list for outreach, not a diagnosis.

Everything runs on **generated patients**, not real people. The people are made to look like Abu Dhabi using published UAE figures. The one risk score we rely on is a published questionnaire, and we checked it on real public survey data. **Nothing is trained in this project.**

## Try it quickly

You need Python 3.10 or newer. Node.js is only needed if you want to rebuild the web app.

```
python -m pip install -r requirements.txt
python scripts/demo_pack.py load
python server.py
```

Then open http://127.0.0.1:8000 (use the number address, not `localhost`, which can be slow on Windows).

`demo_pack.py load` unpacks a small bundle: the generated people, the results, and the already-built web app. It leaves out the large hospital records database, so the FHIR API, patient names and record download need the full build below.

With no accounts set up the app opens in **open mode**: a "Preview as" switch in the left menu lets you look at each role without a password.

## The five roles

| Role | What they do |
|---|---|
| Patient or family | See what is due, find the nearest hospital if it is urgent, choose how to be contacted, add a child, download the record |
| Doctor | See only their own patients, with the risk range, the reasons and the data used; agree or disagree with each flag |
| Outreach staff | Work through today's contact list, record each call and visit, send the approved SMS or WhatsApp reminder |
| Hospital admin | Daily action list, accounts, care teams, audit log, sign-up approval |
| Programme manager | Totals only: results against usual care, fairness, the map. No patient records |

The server enforces this, not just the menus. A doctor asking for the outreach list gets a refusal, and a record outside someone's own patients looks like it does not exist.

English and Arabic are both supported (the whole page flips right to left). The Arabic is a draft: `docs/arabic_review.csv` lists every text with its Arabic so a native speaker can correct it, and `scripts/arabic_apply_review.py` puts the corrections back.

## How it works

1. **Data.** About 5,000 generated residents with visits, results, vaccines, family history, genetics and consent, adjusted to published UAE figures. Records are kept in FHIR-shaped tables and can be imported or read as FHIR R4.
2. **Risk scores.** Diabetes uses the published FINDRISC questionnaire, scaled to UAE levels. Heart risk uses the published 2013 ACC/AHA equations and is shown for information only. Blood pressure is shown as the measured reading with its guideline category. Missing answers widen the range instead of being guessed.
3. **Seven agents.** Care gaps, risk, priority, outreach, chat, follow-up and evaluation. They turn the scores into who to contact, how urgently, and by which channel.
4. **A comparison with usual care.** The same people and the same staff capacity are run both ways, then compared on completed check-ups, timely attendance, follow-up, hidden cases found, cost and fairness by group.
5. **The app.** A FastAPI server and a React web app, served from one port.

## Real-data check of the diabetes score

On 12,925 US adults from NHANES (2015 to 2023) with no diabetes diagnosis, the score used here reaches an AUC of 0.743 (95% range 0.724 to 0.763). Today's broad rule (age 45+ or BMI 25+) reaches 0.566. Details are in `outputs/models/nhanes_validation.json`. Run it yourself with `python -m models.validate_scores` after the data step.

We found no published check of FINDRISC on UAE patients, so a pilot would need to test it locally.

## Run the full build

```
python -m pip install -r requirements.txt
python run_all.py
python server.py
```

`run_all.py` builds the data, checks the scores on NHANES, runs the comparison, runs the evaluation, runs the tests and builds the web app. It takes several hours the first time. The data step downloads public files (Synthea output, NHANES, OpenStreetMap facilities) and skips anything already downloaded.

To rebuild only the web app: `cd frontend`, `npm install`, `npm run build`.

## Accounts for real use

```
python scripts/add_user.py NAME admin
python server.py --host 0.0.0.0 --certfile cert.pem --keyfile key.pem
```

- Passwords are stored as salted PBKDF2 hashes. Five wrong tries lock a user name for 15 minutes.
- The server refuses to start on a network address when nobody would have to sign in. Use HTTPS.
- Every sign-in and every record opened is written to `outputs/access_log.csv`. The log is chained (each line carries a keyed hash of the one before), so an edited, removed or inserted line is detected. `python scripts/audit_head.py` checks it.
- Keep `config/users.json`, `outputs/sessions.json` and `outputs/access_log.csv` private and off the repository.

For a demo, `python scripts/seed_demo_data.py` adds made-up doctors, staff and ten patient or family accounts (random passwords are saved in your user folder, never in the project). `--remove` takes them out again.

## Sending messages

Staff can send the approved reminder by SMS or WhatsApp from the outreach queue. It only goes to people who allow contact, and a child's messages go to the guardian's number. It stays a practice run, and nothing leaves the computer, until you set up Twilio:

```
$env:TWILIO_ACCOUNT_SID = "AC..."
$env:TWILIO_AUTH_TOKEN = "..."
$env:TWILIO_SMS_FROM = "+1..."
$env:TWILIO_WHATSAPP_FROM = "+1..."
```

WhatsApp business messages also need the provider's own approved templates, which the hospital has to obtain. Every attempt is logged in `outputs/outbox.csv` without the message text.

## Tests

```
python run_all.py
```

runs all 17 test files (575 checks) as one of its steps. You can also run one file, for example `python tests/test_family.py`. `python scripts/audit_doctor_view.py` recomputes what a doctor sees for a sample of people and reports anything that disagrees.

## Where things are

| Folder | Contents |
|---|---|
| `api/` | The server: sign-in, roles, care teams, outreach log, family requests, FHIR API |
| `agents/` | The seven agents |
| `models/` | The published risk scores and the real-data check |
| `frontend/` | The React web app, plus an older Streamlit dashboard |
| `config/` | Settings, rules, message templates and the source of every number |
| `scripts/` | Data building, FHIR import and export, demo data, review tools |
| `tests/` | The test files |
| `docs/` | Architecture diagrams (draw.io) and the Arabic review sheet |
| `demo_pack/` | A small bundle so the app runs without the full build |

Every number the comparison uses is listed in `config/real_values.json` with its source and whether it is published, tuned to a published figure, a design choice or an assumption.

## Limits to keep in mind

- All patients are generated, and the comparison with usual care is on that data. It is not a clinical trial.
- The heart equations are information only and could not be validated here, because NHANES has no follow-up.
- Hypertension shows the measured reading only. We do not claim a hypertension score.
- Booking is not a full system yet. A family replies BOOK and staff record whether the visit happened. A complete booking system is planned.
- A clinician should review every translation and clinical rule before real use.
