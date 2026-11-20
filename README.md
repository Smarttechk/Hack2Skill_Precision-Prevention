# Precision Prevention and Screening: prototype

Finds children, adults and families who are overdue for check-ups or carry hidden risk, and brings them to a completed
Well-Child Visit, adult screening or preventive programme, fairly for every group. Built for the Future Health
Challenge (Abu Dhabi). Everything runs on synthetic patients adjusted to the UAE; the risk method is also checked on
real public survey data (NHANES). The order in which people are invited comes from a published questionnaire score (FINDRISC), which is not trained. A model trained on real NHANES
data is shown to doctors as a second opinion on diabetes: information only, it never changes anyone's priority.

## See it in a few minutes (no data build)

```
python -m pip install -r requirements.txt
python scripts/demo_pack.py load     # unpacks the simulated data, results and the built app (5.7 MB zip, already in this repo)
python server.py                     # then open http://127.0.0.1:8000
```

With no accounts set up the app opens in "open mode": a **Preview as** switch at the top of the sidebar shows each role
(patient or family, doctor, outreach staff, programme manager, hospital admin), and the landing, sign-up and
"How this meets each requirement" pages are public. Everything in the pack is simulated. The pack leaves out the hospital
records database (842 MB), so the FHIR API, patient names, past visits and the record download need the full build below.

## Run it (the full build, several hours)

```
python -m pip install -r requirements.txt
python run_all.py                    # data -> models -> 60-day simulation -> evaluation -> tests -> React build
python server.py                     # API + React app at http://127.0.0.1:8000
```

`python run_all.py --sensitivity` also runs the worst-case analysis and the AI on/off comparison (over an hour extra; it was about 1.5 hours on one laptop).
The summaries and chat replies are fixed approved text. By default nothing calls an outside service; the only optional exception is the Care Assistant (see "Care Assistant" below), which stays off until an API key is set.
The React build needs Node.js 18 or newer; `run_all.py` builds it once into `frontend/dist`.

## The app (React + FastAPI)

`python server.py` serves the API under `/api` and the built React app on the same port. Everyone signs in on the
app's sign-in page and sees only what their role needs: the pages below, and only their own people. English and
Arabic (right-to-left) switch with one click.

| Role | Pages | People they see |
|---|---|---|
| Hospital admin | Every page, Accounts (create accounts, reset passwords, unlock, remove, access log), reloading results | Everyone |
| Doctor | Doctor view (risk with range, warning signs, reasons, data used, Agree / Disagree), Family view, Patient journey | Their region, if the account has one |
| Outreach staff | Outreach queue (today's calls, mark done), Family view, Patient journey | Their region, if the account has one |
| Programme manager | Overview, Results (charts, fairness with ranges), Map, Communities | Totals only, no individual patient records |
| Patient or family | My care (what is due, booked visits, the clinic's messages, in plain words), Why was I contacted? (consent choices) | Only their own record and the children they are guardian of |

Demo tools are hidden in production. The **Patient phone** page (a simulated family phone: reply BOOK, STOP, LATER or
emergency words and watch the system answer) is listed under `demo_pages` in `config/roles.json`; it shows for the
hospital admin and outreach staff only when the server runs with `PP_DEMO=1` (PowerShell: `$env:PP_DEMO = "1"`,
then `python server.py`), and while hidden its API is refused too. The chat engine behind it (bookings, STOP and
START, emergency words, answers in the family's language) is the real one and stays in place.

Signed out, the app opens a landing page (`/welcome`): what the project does, how it works, the seven agents, the five
roles, headline results and an FAQ. Each role has its own sign-up form (`/signup/doctor`, `/signup/patient`, ...)
asking for what the hospital needs to check: a DoH licence for doctors, a staff number for outreach staff, the
department for managers, and the Emirates ID (only the last 4 digits and a hash are kept) and date of birth for
patients. A request opens no record until the hospital admin approves it on the Accounts page, choosing the region
for staff and linking a patient to their own record. The very first hospital admin to sign up is active at once.
Requests are kept in `config/signup_requests.json`, with the password already hashed. Everyone can change their own
password from the sidebar. Accounts can also be made from the command line (passwords stored only as salted PBKDF2
hashes in `config/users.json`):

    python scripts/add_user.py hospital_admin admin                      asks for the password
    python scripts/add_user.py --demo                                    one account per role; passwords saved OUTSIDE the project
    python scripts/add_user.py dr_amina doctor --region "Abu Dhabi"
    python scripts/add_user.py --list          python scripts/add_user.py --remove dr_amina

The server enforces all of it, not just the menu: a doctor asking for the outreach queue gets "403"; a person outside
an account's region, or another family's record, answers "not found" even by direct link. The sign-in lives in an
HttpOnly, SameSite=Strict cookie that page scripts cannot read; only a hash of it is stored (`outputs/sessions.json`),
so sign-ins survive a restart and last one shift (8 hours). Five wrong passwords lock that user name on that computer
for 15 minutes (the real doctor on another computer can still sign in; the admin can unlock), and a computer that
fails 20 times with any names is blocked. Sign-ins, failures, sign-outs, refused pages, account changes and every
opened patient record are written to `outputs/access_log.csv`, shown on the Accounts page. The older Streamlit
dashboard uses the same accounts, limits and log. Leave `config/users.json`, `outputs/sessions.json` and
`outputs/access_log.csv` out when sharing the project (they are in `.gitignore`), and remove the demo accounts
before any real use. With no `config/users.json` the app is open on your own computer and shows a "Preview as" role
switch instead; deep links such as `http://127.0.0.1:8000/why?role=patient&lang=ar` then open a role and page directly.

To work on the frontend with live reload: run `python server.py`, then in a second terminal
`cd frontend`, `npm run dev`, and open http://localhost:5173 (it forwards `/api` to port 8000).

**Care teams: each doctor sees their own patients** (`api/careteam.py`, the admin's **Care teams** page, `config/care_team.json`).
A hospital is a region. A doctor account with a specialty (Paediatrics, Family medicine, Internal medicine, Genetic
counselling), languages and a panel limit belongs to a care team and sees only: the patients assigned to them, the panel of a
colleague on leave who named them as backup, (as duty doctor) people with an Urgent card whom nobody looks after, anyone
opened in an emergency with a logged reason ("break glass", a few hours), and records a family has shared with their
hospital from My care (for some days, taken back at any time). The system proposes assignments from rules (hospital,
specialty, language, urgent first, children of one parent together, panel limit, nobody to a doctor on leave) and the
admin approves them; a person can have one doctor per specialty. A doctor account without a specialty keeps the older
rule (everyone in their region). An admin account with a region is a **hospital admin**: they see and change that hospital's
accounts, doctors, patients, sign-up requests and access log only; an admin without a region is the central admin.
Assignments, leave, break-glass openings and sharing are kept in `outputs/care_team.json` (not committed). With no
accounts (open mode) made-up doctors are used and **Preview as** lets you look at the app as one doctor or one hospital's
admin.

**Outreach queue: who has been contacted** (`api/outreach.py`). Starting a contact holds the person for 30 minutes (a colleague
sees "Being contacted by Aisha"); saving it records how (WhatsApp, SMS, phone call, health-worker visit, in person), what
happened (agreed to book, asked to be called later, declined, needs a doctor first, no answer, wrong number) and a short note. The
person then leaves every colleague's list at once: for good after "agreed", "declined" or "needs a doctor", back after three days
for "later" and after four hours for "no answer", and escalated (another route: a health-worker visit or the doctor) after three
no-answers or a wrong number. The **Contacted so far** tab lists who contacted whom, when, how and with what result, filterable by
staff member, result and period. The lists refresh by themselves every 30 seconds. Contacts are kept in
`outputs/outreach_contacts.csv` and `outputs/outreach_claims.json`.

**The hospital admin's Today and Audit pages** (`GET /api/admin/today`, `GET /api/admin/audit`, `GET /api/admin/audit/export`; a hospital
admin sees their own hospital, the central admin all). **Today** puts everything that needs action in one place, linked to where it
is dealt with: emergency call-backs, urgent people with no doctor, break-glass openings open now, people who could not be reached,
booked visits to confirm, missed visits to rebook, sign-up and family requests, new patients waiting for a record, panels nearly full and
locked accounts, with short lists and no clinical detail. **Audit** is for the admin and for a reviewer: records opened per day and who
opened the most, every break-glass opening with the doctor's reason, family sharing with another hospital, account and access
decisions, what the log holds and never holds (no passwords, no note text, no names; record numbers only), and a CSV download of
the log (the download is itself logged). Nothing in the app edits or deletes the log, and the log is **chained**: each line carries a
keyed hash (HMAC-SHA256) of the line before it, so a line that is edited, removed or inserted breaks every hash after it, the Audit
page says at which line, and Today raises it; without the key nobody can rebuild the hashes. The key is `PP_AUDIT_KEY`, or a random one
made once next to the log (`outputs/audit_key`, never committed); in a real deployment keep it off the computer and write down the
head hash from time to time, because removing the newest lines is only visible against a head hash noted earlier. An older log is
upgraded in place (its old lines are reported as written before the chain). The log, sessions and lock counts can be moved with
`PP_ACCESS_LOG`, `PP_SESSIONS_FILE` and `PP_LIMITS_FILE`, so a test server never writes into the real ones.

**Clinic answers that narrow the diabetes range** (`POST /api/person/{id}/answers`, the doctor view's "Add waist and lifestyle answers").
The records have no waist, exercise or vegetable answers, so the FINDRISC range is wide (about 17 points). A doctor can record them in
two minutes; the score is then worked out again for that person (same published points, same UAE scaling, fewer unknown items) and the
range narrows. They inform the doctor only: they do not change anyone's priority or the simulation. Stored in
`outputs/clinic_answers.json`.

**Messages by SMS or WhatsApp** (`messaging.py`, `POST /api/queue/send`, `GET /api/admin/outbox`). In the outreach queue, a staff member who has
claimed a person can send the approved message for that person's open check by SMS or WhatsApp, in the family's language, to the mobile number
the family typed in My care (a child's messages go to the guardian's number). It is refused without consent, without a number or without an
approved text. It is a **practice (dry run)** until the hospital sets the provider's details, so nothing leaves the computer by accident:

```
$env:TWILIO_ACCOUNT_SID = "AC..."      $env:TWILIO_AUTH_TOKEN = "..."
$env:TWILIO_SMS_FROM = "+1..."         $env:TWILIO_WHATSAPP_FROM = "+1..."      # either or both
python server.py
```

Twilio's REST API is called with the Python standard library (nothing extra to install). WhatsApp business messages also need the provider's own
approved templates and sender: that approval is the hospital's to get. Every attempt is written to `outputs/outbox.csv` (who, which record, channel,
result, last 4 digits and text length; never the text or the full number) and the admin sees it under "Messages sent from the queue".
`PP_MESSAGING=dry` forces the practice mode even when the details are set.

**Urgent care** (`GET /api/mycare/urgent`). My care has a "Does ... need a hospital now?" card for each person in the family: the emergency number
first, then the three nearest named hospitals to that person's home with the straight-line distance and a map link (the link holds the hospital's
position only). It is not a booking and not a diagnosis. Booking itself stays what it was: the family replies BOOK to the clinic's message, and staff
record the visit as attended, missed or moved.

**Households.** A household is a guardian and the children whose hospital record names that guardian (`household_id` is the guardian's record).
Other adults are separate people with their own record and consent. The Family view lists each household by the guardian's record number and the
children's ages, and labels each member Guardian, Adult or Child.

**Outreach views.** Outreach staff open "Contacted so far" on **My contacts** (they can switch to the whole team so nobody is called twice, but
they are not shown colleagues compared). The hospital admin sees **Team activity** (contacts, people reached, agreed to book, no answer, wrong
number, visits recorded and last activity per staff member), **Who contacted whom** with filters, and the message log.

**Check the doctor view yourself** (`python scripts/audit_doctor_view.py --n 400`). It asks the API what a doctor sees for a sample of people,
recomputes each figure from the features and the published formulas, and reports anything that disagrees (ages, sexes, diabetes chance and range,
heart-disease chance, blood-pressure category, no score for children, no prediction for a known diagnosis, a reason for every Urgent).

**Demo data** (`python scripts/seed_demo_data.py`, `--remove` undoes it). 13 doctors (scripts/seed_demo_team.py), 3 hospital admins, 3 outreach
staff, a programme manager, and 10 patient or family accounts linked to 10 different records (four families, six adults of different kinds),
waist and lifestyle answers, about 30 contacts and 20 visit outcomes. Passwords are random and written to your user folder. Practice mobile numbers
are added only while messaging is not live. It asks first and names the files it will change.

**Demo care team** (`python scripts/seed_demo_team.py`, `--remove` undoes it). With only a few doctor accounts almost every patient is
"left without the right specialty" and every panel is full. The script creates 13 made-up doctors across the three hospitals with panel
limits that fit, gives every patient a doctor by the care-team rules and leaves a small honest gap (no genetic counselling outside Abu
Dhabi, no internal medicine in Al Dhafra). Passwords are random and written to your user folder, never to the project. The script names the accounts, care-team and log files it will
change and asks you to type "yes" first (`--yes` skips it).

**Arabic review sheet** (`python scripts/arabic_review_sheet.py` writes `docs/arabic_review.csv`): every English text with its Arabic draft,
the newest screens first, with columns for a native speaker's correction and status. When the reviewer returns the sheet,
`python scripts/arabic_apply_review.py SHEET.csv` shows what would change and `--apply` writes the corrections into the app (a row
marked OK, or with no correction, is left alone; texts no longer in the files are listed, not guessed).

**The audit key and a head kept elsewhere.** The Audit page says where the log's key lives: a file next to the log (a warning, because
anyone who can edit the log on that computer can read the key) or the `PP_AUDIT_KEY` environment variable (set it from a secret store
before a real deployment). `python scripts/audit_head.py` checks the chain from the command line; `--record FILE` adds the current head
to a file kept somewhere the computer cannot change, and `--check FILE` then shows what the chain alone cannot: that the newest lines
were removed, or the log rebuilt.

**Collecting the missing waist and lifestyle answers.** When a patient's diabetes range is wide and no answers are recorded, the
doctor view opens the "two quick answers" form with a prompt instead of hiding it, and the programme manager's Operations panel shows
how many adults with a diabetes score have answers recorded (a count), the number to raise.

**Booked visits: attended or missed** (the **Appointments** tab, `GET /api/visits`, `POST /api/visits/outcome`). Staff see the visits
booked for their hospital (dated within a week or already past, or later), with no clinical detail, and record what happened:
*attended* closes the person; *missed* puts them back on the contact list the next day as "Missed visit: rebook" (after three
missed visits they are escalated for another way to reach them); *moved* returns after a week. A visit within two days shows
"Reminder due". The family sees the result in My care ("recorded as attended" or "we missed you, we will contact you"). The
programme manager's Operations panel adds visit totals only (recorded, attended, missed, attendance rate, still to confirm).
Recorded visits are kept in the same log as contacts, are not counted as contacts, and each one is written to the access log.
In the prototype the system's "today" is the last simulated day; a real deployment reads the clock.

*Keeping the list itself current:* in this prototype the list is computed from the 60-day simulation outputs, so it is a snapshot.
A real deployment recomputes it from the hospital's records on a schedule (a nightly run of the agents) and straight away for
urgent events (emergency words in a chat, an abnormal result); nobody updates it by hand.

**A new patient with no record yet.** The sign-up form asks which hospital region they use. The hospital admin cannot link a
record that does not exist, so they choose **Approve as a new patient (no record yet)**: the account opens at once but sees nothing
except a My care page saying the record is being set up (it is scoped to nobody, never to everyone). When the clinic has
registered the patient, the admin finds the record on the Accounts page (**Link record**, searched by the date of birth from the
sign-up) and the account opens fully. Admins see a count of new patients waiting for a record.

**Adding a child to a family account** (`api/family.py`). On My care a signed-in parent can ask to add a child (first name, date of
birth, relation, record number if known). Nothing opens until the hospital admin finds the child's record in their own hospital,
compares the details and approves it on the Accounts page (or rejects it with a reason the parent sees). Approval makes the parent
an extra guardian: the child then shows in My care with their due checks, history, contact choices and sharing; the registered
guardian is not replaced. Adults (a husband, wife or parent) are never added under someone else's account: they sign up for their
own, so their record and consent stay theirs. Requests are kept in `outputs/family_requests.json`.

**The React app (`python server.py`) is the main frontend.** The older Streamlit dashboard
(`frontend/streamlit_app/`) still works with `python -m streamlit run frontend/streamlit_app/dashboard.py` as a fallback: it has the same sign-in, account
limits, access log, doctor view, results and Safeguards table, but not the landing page, sign-up, Accounts, My care
pages or the full Arabic text.

Arabic: the switch at the top of every page (sidebar inside the app; EN / عربي on the landing, sign-in and sign-up
pages) turns the whole interface right-to-left and translates the pages, menus, sign-up forms, the doctor view, My care
and the safeguards headings (`frontend/src/ar.js`). Data written by the system (reasons, check names on staff pages,
messages) stays as written. The Arabic is a draft that a native-speaking clinician must check before a pilot.

Sign-in protection: wrong passwords and sign-up requests are counted in `outputs/auth_limits.json`, so a restart does
not reset a lock or the sign-up limit.

## Where the numbers come from

Every number the simulation and the cost model use is in `config/real_values.json`, each with its source, link and
status: **published** (taken from a study, price list or official figure), **calibrated** (tuned by
`scripts/calibrate.py` until usual care matches a published UAE figure: 20.8% missed visits at Emirates Health Services,
2.5% opt-out from health texts), **design** (a choice of this system, such as staff numbers) or **assumption** (no
published value found; kept visible and varied in `python evaluate.py --sensitivity`). The Results page lists them all
under "Where every number comes from". To update a value, edit the file and run `python run_all.py`.

## Where AI is used, and where it is not

Rules from published guidelines decide what is due and how urgent it is; AI works inside those limits, and every
clinical decision stays with a doctor.

| AI step | What it does | Status |
|---|---|---|
| Diabetes risk score (`models/risk_scores.py`) | Chance of undiagnosed diabetes from the **published FINDRISC questionnaire**, with range and reasons. Nothing is trained: the points and bands are published, and one number moves them to the UAE level | On |
| Heart-disease risk (`models/risk_scores.py`, `ascvd`) | The 10-year chance of a heart attack, heart-disease death or stroke from the **published 2013 ACC/AHA pooled cohort equations** (ages 40 to 79), shown to the doctor as information only: not scaled to the UAE and never used for priority. The equations reproduce the guideline's worked examples (5.4% for a man, 2.1% for a woman, both 55 y). Not validated here: NHANES has no follow-up outcomes | Information only |
| Trained second opinion on diabetes (`models/ml_risk.py`) | A logistic regression trained on real NHANES adults, from nine inputs a record can hold (age, BMI, told of high blood sugar, high-blood-pressure diagnosis, family history, sex, systolic blood pressure, total and HDL cholesterol). Scaled to the UAE level by one number, shown in the Doctor view next to the published score, with its main factors. It never decides who is invited or in what order, and one switch turns it off (`ml_second_opinion` in `config/models.json`) | Information only |
| Real-data check (`models/validate_scores.py`) | Ranks real NHANES adults with FINDRISC, the ADA risk test and the broad age-or-BMI rule and reports AUC with ranges | Check only |
| Support for likely no-shows (`models/noshow.py`) | Extra help for people with a known access barrier or a missed visit, by a plain rule | On (rule) |
| Follow-up learner (`agents/followup.py`) | Learns which follow-up action gets each kind of family to attend (simple counts per group, no fitted model) | On |
| First-message channel learner (`agents/outreach.py`) | Learns WhatsApp or SMS from replies, starting from the rule | Off: no gain found |
| Care Assistant (`api/assistant.py`) | A language model (Claude API) that answers a doctor's, outreach worker's or admin's question about the one record that is open, using four read-only tools. It never decides, sends or changes anything | Off until an API key is set; **not tested against the live service** (see below) |

**Care Assistant** (optional, off by default). It is an agent in the narrow sense: the model chooses which of four read-only tools to call (record overview,
open care gaps, contact history, approved message) and answers from what they return. It is built as a manual tool-use loop on the Claude Messages API
(`claude-opus-5-5`, medium effort, at most 6 steps), not on a hosted agent service, because the tools must run inside this server under the signed-in user's own
access rules, and health data should not be kept on an outside service longer than needed.

- **Guardrails.** Read-only: no tool writes, books, sends or changes a priority. The tools take no person id, so the assistant can only see the record the user
  opened, under the same access rule as the rest of the app (a record outside the user's region or hospital is a 404). The system prompt forbids diagnosis,
  treatment advice and priority changes, and tells the assistant to stop at emergency words and point to the fixed emergency rules (998). Tool text is treated as data, not as instructions.
- **Family messages are never written by the model.** `get_approved_message` returns the approved template for an open check (`agents/outreach.py`, `safe_message`); the screen shows it
  in its own box, and staff send it themselves.
- **What leaves the server.** Only the opened record's age band, sex, risk band and reasons, open care gaps and contact outcomes. No name, phone number, Emirates ID, address or note is sent. Outreach staff get no
  risk or clinical detail at all. Each question is limited to 800 characters, 20 questions an hour per user, and the last 6 turns of text.
- **Audit.** Each use is written to the signed audit log (`assistant_used`: who, which record number, which tools), without the question or the answer.
- **One switch.** `care_assistant.enabled` in `config/models.json`, or leave `ANTHROPIC_API_KEY` unset. Nothing that decides who is contacted, or in what order, imports the assistant.
- **Enable it.** `python -m pip install -r requirements-assistant.txt`, set `ANTHROPIC_API_KEY` on the server, restart. The panel then appears under the record in the Doctor and Journey pages; without a key it says why it is off.
- **What is and is not tested.** `tests/test_assistant.py` (34 checks) runs the whole loop, the tools, the redaction, the limits and the endpoint against a scripted stand-in for the API. The real service
  was **not** called: the build PC's application control blocks the `anthropic` package, so a pilot must run a first live check on the server (a few real questions against synthetic records) before anyone relies on it. The answers are AI-generated and can be wrong.

**What the trained second opinion adds was measured** (`python -m models.ml_comparison`, `outputs/models/ml_comparison.json`; the Results page shows it).
Same 12,925 real NHANES adults and 456 undiagnosed cases as the real-data check, same label (HbA1c of 6.5% or more). Every trained model is
scored only on people it was not trained on (5-fold cross-validation, repeated 3 times). Race, nationality, income and education are not used,
and a missing value is replaced by the typical value, not flagged: which values a survey happened to collect says nothing about diabetes and
would not exist in hospital records (an earlier run that let models learn from it looked better than it should, and was corrected).

| Score | AUC (95% range) | Cases found by inviting 30% | AUC gain over FINDRISC |
|---|---|---|---|
| FINDRISC as used in this prototype (not trained) | 0.743 (0.724 to 0.764) | 64% | |
| Logistic regression, the same 5 inputs | 0.761 (0.740 to 0.779) | 66% | +0.018 |
| Gradient boosting, the same 5 inputs | 0.764 (0.747 to 0.783) | 68% | +0.021 |
| Logistic regression, + sex, blood pressure, cholesterol, HDL | 0.795 (0.778 to 0.812) | 73% | +0.052 |
| Gradient boosting, + the same record items | 0.790 (0.773 to 0.806) | 70% | +0.047 |
| **Logistic regression as used in the app** (nine inputs) | **0.795 (0.778 to 0.814)** | **72%** | **+0.052** |

What this shows, and what it does not:
- The trained model ranks better than the published score, and the 95% range of the gain is above zero. It holds for men and women, all three age bands and
  all six ethnic groups checked, and when a whole survey cycle is left out of training (AUC 0.79 to 0.80 against 0.74 to 0.75).
- **The gain comes from routine measurements, not from a more complex method.** With only the five questionnaire inputs it is small (+0.018); HDL and
  total cholesterol, blood pressure and sex add the rest. Gradient boosting is no better than the plain logistic regression, so the app uses the
  logistic regression, whose weights a doctor can read (the strongest are HDL, age, BMI and a past "high blood sugar").
- It is trained on US survey data and has not been validated on UAE patients. It is scaled to the UAE level the same way as FINDRISC (one intercept shift),
  and about a third of the adults in the simulated records have no cholesterol or HDL, so for them the typical value is used and the Doctor view says
  "n of 8 inputs on record". The simulation's hidden cases are generated by its own rules, so its results do not show this model's real-data gain.
- That is why it is only a second opinion. Using it to decide priority would change who is contacted, and every result, and should wait for a validation on
  the hospital's own patients.

**Shadow check on the simulated residents** (`python -m models.ml_shadow`, `outputs/models/ml_shadow.json`; the Results page shows it). The trained model
also scores the app's own 1,866 simulated adults without a diabetes diagnosis, next to FINDRISC, against the simulation's hidden truth
(283 hidden cases; the truth is only the answer key, never an input). It only watches: it changes nobody's priority and no result.

| | AUC (95% range) | Top 30% finds | High band holds |
|---|---|---|---|
| FINDRISC (published, sets the priority) | 0.686 (0.651 to 0.717) | 52% | 41% |
| Trained second opinion | 0.633 (0.597 to 0.666) | 45% | 25% |

Here the trained model does **worse** (AUC gain -0.053, range -0.080 to -0.025). The reason is in the simulation, not in the model: it
makes its hidden cases from age, BMI and family history, so HDL, cholesterol and blood pressure carry no signal in it. One input alone: HDL has an AUC of
0.645 in real adults and 0.508 in the simulation, systolic blood pressure 0.666 and 0.476.
Without those three measurements the same model scores 0.67 on the simulated adults. So this check cannot confirm the model: the real-data check above is the valid
test, and a pilot must repeat it on the hospital's own patients, with the second opinion switched off (`ml_second_opinion`) if the gain does not hold there.

**What the channel learner adds was measured, and it is off** (`python evaluate.py --ablation`,
`outputs/results/ai_ablation.json`; 5 seeds, same people and capacity):

| Setting | Well-Child care completed | Adult screening completed | Undiagnosed cases found |
|---|---|---|---|
| Rules only (the default) | 50% | 34% | 319 |
| + channel learner | 52% | 34% | 319 |

The difference in completed care is inside the run-to-run spread (single seeds range from 47% to 52%) and the number of cases
found is the same. The channel learner confirmed the rule: WhatsApp got 18 replies per 100 first messages, SMS 15. In this
simulation nearly everyone due is contacted within 60 days, so reordering has little to gain; a hospital with a longer queue
may see more, which is why it stays available as a switch (`ai_features` in `config/real_values.json`). Learning the best time of
day was not built: the simulation has no data in which timing changes replies, so it could not be tested.

## Running it for real users

```
python scripts/add_user.py NAME admin                     # the hospital admin, before the server is on a network
python server.py --host 0.0.0.0 --certfile cert.pem --keyfile key.pem
```

- The server refuses to start on a network address when nobody would have to sign in.
- Use HTTPS: the two files above, or a reverse proxy (nginx, IIS, a load balancer) that terminates it.
- Every response carries security headers (no framing, own scripts only, no content sniffing); API answers are
  never stored by browsers or proxies; requests over 1 MB are refused.
- `GET /api/health` answers without sign-in and without patient data (200 when ready, 503 when the records
  database or results are missing): point the monitoring at it.
- Run one server process. Sessions, sign-in limits and the access log are files under `outputs/`; several processes
  or machines need them moved to a shared database first, together with the records database (`records.connect()`).
- Keep `config/users.json`, `outputs/sessions.json` and `outputs/access_log.csv` on the server only, and back up the
  access log.

## Patient records: the hospital records database and FHIR import

Patient records live in a hospital records database, `data/records/hospital.db` (SQLite; `records.py`), with the six
tables a hospital record system keeps, shaped like the FHIR R4 resources they come from: `patient` (hospital record
number MRN, name, date of birth, sex, nationality, language, insurance, home area, guardian), `encounter` (visits),
`condition` (diagnoses, SNOMED CT), `observation` (results such as HbA1c and blood pressure, LOINC), `immunization`
(CVX) and `procedure` (screenings). The feature step and the app read patient records from it; names and record
numbers are shown only where a person must be identified (the hospital admin linking a patient account) and never
become model features. The simulation's hidden truth is not a hospital record and stays outside the database. The
prototype's patients are generated test records with names that fit their nationality; for a pilot the same tables
move to the hospital's PostgreSQL server in the UAE (only `records.connect()` changes).

Records arrive the way a hospital or Abu Dhabi's Malaffi exchange sends them, as HL7 FHIR R4:

```
python scripts/fhir_import.py hospital_export/ --out data/imported     # Bundles, single resources or bulk .ndjson
$env:PP_RAW_DIR = "data/imported"                                      # PowerShell; then rebuild from the import
python scripts/get_all_data.py --only adjust records features
python orchestrator.py
python evaluate.py
python scripts/fhir_import.py --sample 300                             # a sample FHIR bundle, for demos
```

The import keeps a hospital's own MRN, names, nationality and preferred language, stores only the last 4 digits of an
Emirates ID, splits a blood pressure panel into its two readings, skips other resource types (counted in
`import_report.json`) and reports which codes the care-gap rules recognise: a hospital that codes diagnoses in
ICD-10 needs those codes added to `data/reference/code_map.csv`. Exporting 300 people to FHIR and importing them
back gives the same rows in every table (`tests/test_records.py`), and the whole pipeline runs on an import.

**Family history, genetic results and consent are records too** (`scripts/clinical_records.py`). The database has
three more tables, and the feature step reads them, so what the care-gap, risk and outreach agents know about a
person's family history, genetics and consent is what the records say:

| Table | FHIR resource | Holds | Used for |
|---|---|---|---|
| `family_history` | FamilyMemberHistory | A relative's condition the person reported (SNOMED CT or ICD-10); a row without a code means the history was taken and nothing relevant reported | Family history of diabetes in the risk score and the warning signs |
| `genetic_result` | Observation, LOINC 69548-6 | Gene, the marker group it belongs to and the clinical significance | The genetic pathways (only pathogenic and likely pathogenic findings, only with genetic consent) |
| `consent` | Consent | Purpose (`contact`, `use_genetic`), permit or deny, date, who gave it | Who may be contacted and whose genetic results may change the plan |

A hospital's gene symbol reaches the right rule through `genetic_marker_genes` in `config/clinical_rules.json`
(BRCA1 and BRCA2 to the BRCA pathway, LDLR/APOB/PCSK9 to familial hypercholesterolaemia, and so on); a result for
any other gene, a benign finding or "no variant found" changes nothing. For consent the newest decision wins and for
a child only the guardian's decision counts. From a FHIR import these three are never generated: what the hospital
did not send is not there, and a person with no consent record gets `consent_when_not_recorded` (contact allowed
until they say no, genetic use only with a recorded yes; set contact to false where a recorded yes is required
before any outreach). For the generated test patients the rows are written at the end of the `adjust` step from the
same draws as before, so the population and every result are unchanged. Child development and behaviour concerns,
the polygenic score and marital status have no record table yet and are still generated.

### Reading records out: the FHIR R4 API

The same records can be read out as standard FHIR R4 (`application/fhir+json`), so a clinic system or the Malaffi
exchange can ask for a patient's record in the format it already speaks:

| Request | Returns |
|---|---|
| `GET /api/fhir/metadata` | CapabilityStatement: what the server offers |
| `GET /api/fhir/Patient/{id}` | The Patient: hospital record number, name, birth date, sex, language, nationality, home area |
| `GET /api/fhir/Patient?identifier=ADH-1234567` | Search by hospital record number (Bundle) |
| `GET /api/fhir/Patient/{id}/$everything` | The whole record: Patient, Encounters, Conditions, Observations, Immunizations, Procedures, FamilyMemberHistory, Consent |
| `GET /api/fhir/Observation?patient={id}&code=4548-4` | One kind of record for one patient, optionally one code (here HbA1c); also Encounter, Condition, Immunization, Procedure, FamilyMemberHistory, Consent |

Genetic results are not served by this API: they stay in the records database and reach a doctor only as a pathway
card, with the person's genetic consent.

A blood pressure reading goes out as one panel (LOINC 85354-9) with its systolic and diastolic values; errors come
back as a FHIR OperationOutcome. The resources are built by `fhir.py`, the same builders the sample export in
`scripts/fhir_import.py` uses, so records leave the system in the form they arrive in: importing a patient's
`$everything` back gives the same rows as the database (`tests/test_fhir_api.py`).

Access follows the same rules as the app: doctors and the hospital admin see the people their account allows (a
region, or everyone); a patient sees their own record and their children's; programme managers and outreach staff
cannot use it. Someone outside an account's limits looks the same as someone who does not exist, and every record
opened is written to the access log. Sign in first (the app's session cookie), or send the account with HTTP basic
authentication, for example `curl -u dr_abu_dhabi http://127.0.0.1:8000/api/fhir/metadata`.

## How it is built

| Part | File | Guide step |
|---|---|---|
| Data, UAE adjustment, preprocessing | `scripts/get_all_data.py`, `scripts/uae_adjust.py`, `scripts/preprocess.py` | 1 |
| Hospital records database and FHIR import | `records.py`, `scripts/build_records_db.py`, `scripts/fhir_import.py` | 1, 13.1b |
| Shared helpers and data loading | `utils.py` | 10.6, 10.7 |
| Risk score: published FINDRISC (range, reasons, model card) | `models/risk_scores.py` | 4 |
| Real-data check of the published scores | `models/validate_scores.py` | 4.9, 4.10 |
| No-show support (a plain rule, adds support only) | `models/noshow.py` | 8.5 |
| Agents: care gaps, risk, priority and pathway cards, outreach, chat, follow-up, evaluation | `agents/` | 3, 4, 5, 6, 7, 8, 10.1 |
| Consent records | `consent.py` | 4.8 |
| Fixed texts for the doctor summary and chat replies | `summaries.py` | 7.1 |
| Simulated world (replies, attendance, findings) | `simulation.py` | 8.3 |
| Orchestrator: this system and usual care, 60 days | `orchestrator.py` | 9 |
| Evaluation: outcomes, fairness, worst case, business case | `evaluate.py` | 10, 13.3 |
| FHIR: Tasks for clinic systems, patient records as FHIR resources | `fhir.py` | 13.1b |
| FHIR R4 API (records out, same access rules as the app) | `api/app.py` (`/api/fhir`) | 11, 13.1b |
| REST API for the frontend | `api/app.py`, `server.py` | 11 |
| React frontend (10 pages, roles, English and Arabic) | `frontend/src/` | 11 |
| Trained second opinion on diabetes (comparison, final model, checks) | `models/ml_comparison.py`, `models/ml_risk.py`, `tests/test_ml_risk.py` | 4 |
| Tests | `tests/` | 12 |

## Outputs

- `outputs/models/`: `risk_model_card.json`, `nhanes_validation.json`
- `outputs/new/` and `outputs/old/`: people at the end, events, messages, daily counts, invitations, audit log,
  consents; plus for this system the follow-up learning table and `fhir_tasks.json`
- `outputs/results/`: `results.json` (every result below), fairness tables, `sensitivity.csv`
- `outputs/doctor_feedback.csv`: Agree / Disagree saved from the Doctor view

## Results (60 days, 5,000 synthetic people, same capacity of 120 new contacts a day)

| Measure | This system | Usual care |
|---|---|---|
| Children due who completed Well-Child care (same people and checks) | 32% | 10% |
| Adults due who completed screening (same people and checks) | 32% | 10% |
| Hidden diabetes and hypertension cases found (of 931) | 292 (31%) | 70 (8%) |
| Abnormal results followed up within 30 days | 84% | 79% |
| Cost per person with completed care (outreach + visits) | 280 AED | 265 AED |
| Outreach cost per person with completed care | 21 AED | 45 AED |

Each extra hidden case found costs about 1,345 AED more than usual care (a year of diabetes care costs about 7,712 AED).
With the visit price anywhere from 120 to 450 AED, the cost per person stays close for both ways (162-550 against
165-496 AED). The run date is fixed (`run_date` in `config/real_values.json`), so these numbers are reproducible;
usual care has only about 40-70 results to follow up per run, so its follow-up rate is the least stable number.
No value is written into the code. Every number lives in a config file, each with its source and a status
(published, calibrated, design or assumption), and all of them appear in the app's "Where every number comes from"
table:
- `config/real_values.json`: prices, behaviour (reply rates, attendance effects, reminders, telehealth, transport)
  and design choices (deadlines, reminder days, slots, contact times, alerts, sensitivity settings, page sizes, the
  run date);
- `config/clinical_rules.json`: screening ages and intervals, thresholds, genetic pathways, warning signs, the
  emergency number;
- `config/synthetic_data.json`: how the synthetic patients are made to look like the UAE and how features are built;
- `config/models.json`: model settings and seeds;
- `config/language.json`: emergency words and preference phrases in the five languages (to be checked by native
  speakers);
- `config/fixed_text.json`: the fixed doctor-summary and chat-reply texts;
- `config/roles.json`: the pages each role can open, the API calls behind them, account limits (region, own record),
  session length, lockout and password rules (the server enforces it and the app builds its menu from it);
- `config/calibrated.json`: the two values tuned to published UAE figures.
The screening intervals have one source (`clinical_rules.json`); `data/reference/guidelines.csv` is written from it.
What stays in the code is only units and orderings (365 days a year, Urgent before High before Routine); the weekly
review, Friday's contact window and the caps on counts are in `real_values.json` too.
Moving them out of the code changed no result: the data, the risk model card and every simulated event are identical
to the run before.

Every behaviour number (reply rates, attendance effects, reminders, telehealth, transport) is in
`config/real_values.json` with its source and status: published, calibrated, design or assumption. Where no study
supports an effect (evening slots, Ramadan, transport help), it is set to zero, so this system gets no credit for it.

Worst case (sensitivity analysis, 64 settings x 2 seeds, including doctors agreeing only 60% of the time and a
quarter fewer clinic slots, run with the published diabetes score): this system still finds 163 more hidden cases and
completes care for 1,041 more people than usual care (worst setting: no telehealth effect, doctors agreeing 60% of the
time, 30 clinic slots a day).

Seed spread (`python evaluate.py --seeds`, 5 random seeds, both ways on the same people; Results page "Same results
over 5 random seeds"). One run's luck is not an effect, so the headline numbers are given as average and range:

| | This system | Usual care |
|---|---|---|
| Well-Child care completed | 50% (47% to 52%) | 10% (8% to 13%) |
| Adult screening completed | 34% (34% to 35%) | 10% (10% to 11%) |
| Hidden cases found (of 931) | 319 (302 to 335) | 60 (52 to 64) |
| Results followed up within 30 days | 83% (79% to 85%), about 170 results a run | 62% (52% to 74%), about 40 results a run |

Follow-up rests on few results per run (especially for usual care), so the Results page also gives its 95% range.

Guidelines and languages (`data/reference/`, written by `scripts/get_all_data.py`): every pathway card names its
guideline and edition (for example USPSTF 2021 for blood pressure and bowel screening, USPSTF 2024 for breast
screening, ADA Standards of Care in Diabetes 2025, Bright Futures 4th edition), including the programmes (diabetes
prevention, weight support, stop-smoking, family nutrition, genetic and carrier counselling); local clinical sign-off
is marked as pending. Messages and reply words exist in all five languages of the population (Arabic, English, Urdu,
Hindi, Tagalog) as drafts for native speakers and the clinical team to check. The care option inside an invitation
(clinic visit, transport help, telehealth, mobile clinic) and the chat assistant's answers (booking, STOP, START, LATER,
emergency) are written in the same language (`config/language.json`); usual care stays in English as before. Moving Urdu, Hindi and Tagalog speakers from an English message or a nurse call to a message in their own language was measured earlier and changed the headline numbers by less than the run-to-run spread.

Risk score (`models/risk_scores.py`): the diabetes score is the **published FINDRISC questionnaire** (Lindstrom and Tuomilehto,
Diabetes Care 2003), not a model trained here. Points come from age, BMI, waist, treatment for high blood pressure, a history
of high blood sugar, family history, daily exercise and daily vegetables; the published bands give the chance of developing
diabetes (1%, 4%, 17%, 33%, 50%), read as a smooth curve. The records have no waist, exercise or vegetable answers (and
sometimes no family history), so the score uses what is known, takes half of the points the missing items could add as its
central value and gives the full possible spread as its range: a person is never scored as if those answers were known. One
number moves the curve to the local level (an intercept shift on the log-odds scale, so nobody's rank changes): WHO UAE
prevalence minus the diagnosed share in the records, capped at the published undiagnosed share (10.8% for diabetes). The
reasons are the items that gave the points. **High blood pressure has no predicted score**: blood pressure is measured, so a
high reading is a finding (an abnormal-result card, shown as "Found at a visit: results review needed"), and diagnosed or
treated blood pressure is an input to the diabetes score. The doctor also sees the last recorded reading with its date and its 2017 ACC/AHA category (normal, elevated, stage 1, stage 2), and a note when it is a year old or more: a category of a measurement, not a prediction. A published hypertension risk score (Framingham, Parikh 2008) is the next step; its points table was not available to code. Nationality, language, insurance and region are never inputs.

How well the score ranks real people is checked in `models/validate_scores.py` on NHANES adults (2015-2016, 2017-2018 and
2021-2023, never told they have diabetes, undiagnosed = measured HbA1c 6.5% or more), against the ADA diabetes risk test and the
broad "age 45 or more, or BMI 25 or more" rule (Results page, "Real-data check of the diabetes score"):

| Score | AUC (95% range) | Cases found, share of people invited |
|---|---|---|
| FINDRISC, every item NHANES has (6 of 8) | 0.748 (0.728 to 0.767) | 65% of cases by inviting 30% |
| FINDRISC as used in this prototype (no waist) | 0.743 (0.724 to 0.763) | 64% of cases by inviting 30% |
| ADA diabetes risk test (5 of 7 items) | 0.737 (0.717 to 0.754) | 63% of cases by inviting 30% |
| Broad rule today (age 45+ or BMI 25+) | 0.566 | 99% of cases by inviting 87% |

12,925 adults, 456 with undiagnosed diabetes; by survey cycle the score used here reaches AUC 0.75 (2015-2016), 0.736 (2017-2018), 0.749 (2021-2023). The points are the published ones, filled in only where NHANES has the answer, so a full questionnaire would do a little better; none was built for the UAE.

People already diagnosed get no score. The priority uses the score: High is the top 15% of local scores, Moderate the top 40%
(`risk_model_card.json`). Risks under 10% are shown with one decimal, or as "about x%" when the range is narrower than that.

Safeguards by group (challenge requirement 4; Results page, "Safeguards"): checked against the hidden truth that
the system never sees, for nationality, region, language, insurance, sex and age band, overall and per condition. A
true case is diabetes present at the start and not diagnosed (the one condition with a predicted score). Among the 1,866 adults
scored, 20% are flagged High; 17% of non-cases are flagged (false positives) and 59% of true cases are not flagged (false
negatives). Flagged people are true cases 31 times in 100 against 15 in 100 if people were picked at random, so the score is
about twice as good as chance on the simulated population (AUC 0.69 against the hidden truth; 0.74 on real NHANES adults).
The score is one route in among several: care gaps, warning signs and family pathways invite people it does not flag, and the
visits find 33% of the 931 hidden cases against 7% for usual care. The
gap between groups is shown openly, with local calibration by group beside it. Traced back, the gap is not in who is
flagged or invited (about 85% of hidden cases get a card and 92% an invitation in every group) but in who completes a
blood-sugar or blood-pressure check afterwards. Moving lagging groups up the queue for these checks was tested over 4
seeds and did not reduce missed cases (the gap between groups also varies from 11 to 23 points between seeds, so much
of it is chance in small groups). It was therefore not kept as an action: the evaluation agent alerts the team weekly
when a group falls behind on these checks, and the honest next step is human (for example an Urdu-speaking community
health worker). Groups under 10 people are hidden, and no rate is shown from fewer than 10 people or cases.
`tests/test_system.py` recomputes the rates from the truth file independently.

Support for likely no-shows: it only ever adds help (an extra reminder, a health-worker call); it never lowers priority or blocks a booking. A plain rule decides: a known access barrier or a missed visit before. No model is trained for it: simulated patients differ little in their chance of attending, so a learned model would add nothing here.

Honest notes: these are simulated results on synthetic patients. For the few cases both ways found, usual care found
them about 7 days earlier, because this system reaches people with more barriers first. Telehealth books only checks
that can be done remotely; a STOP reply covers the whole family on that phone; people who never reply are invited again
after 30 days, and a doctor's hold is reviewed after 90 days. Emergency words in Arabic, English, Urdu, Hindi and
Tagalog give the emergency number at once and add an Urgent call-back card; a nurse calls back outside the daily
queue, on holidays too. The word lists must be checked by native-speaking clinicians before a pilot. All timing
rules (deadlines, reminder days, pauses, cut-offs) are in the "design" section of `config/real_values.json`.

Other safeguards in the agents:
- Care-gap: genetic-pathway checks count past tests in the records (a recent mammogram is not repeated); known high
  blood pressure is checked every 6 months; children under 2 with no vaccine for 6 months get a catch-up gap;
  warning signs are refreshed weekly.
- Outreach and booking: the daily limit counts messages actually sent; clinics have 40 check-up slots a day and a
  mobile clinic 25 per session (the same limits both ways), so busy days push bookings later.
- Follow-up: no reminders on public holidays (sent the next day); the follow-up chooser learns from attended visits,
  not bookings; after 2 rebookings a person rests 30 days instead of being rebooked forever.
- Chat: emergency words match whole words only (a negation such as "no chest pain" still raises the alarm on
  purpose); channel and time preferences are understood in all five languages; emergencies from the live chat appear
  at the top of the Doctor page.
- Evaluation agent: alerts the team about missed contact deadlines, Urgent people who cannot be messaged (no contact
  consent, so the clinician decides how to reach them), opt-out spikes, and a reach gap by language, nationality,
  insurance or region (`outputs/new/alerts.csv`, shown on the Doctor page). Emergencies from the live chat stay at
  the top of the Doctor page until a nurse presses "Nurse called back".
- Public holidays pause routine messages only; Urgent cases are still contacted.
- Reach floor: the priority score moves people with access barriers up, which at first left Emirati, Arabic-speaking
  and Thiqa families far behind (23% reached against 63% at one weekly check). Now any language, nationality,
  insurance or region group more than 10 points behind the best-reached group of its kind goes first within each
  urgency level. Gaps close within about a month, and by day 60 every group is at 90-94% reached. A group behind on
  completed care also goes first and gets the extra reminder. Children used to complete care less often than adults
  over 65; family visits (below) closed that gap.
- Contact deadlines restart when a person becomes eligible again (after LATER, a missed visit or no reply), and an
  open results review only closes when the review itself happens, so no Urgent result is left without a review.
- Similar alerts within 7 days are merged unless the number grows by half; people with no contact consent are marked
  on the Doctor page, and changes made in the live phone demo show there too.
- Usual-care attendance is calibrated over 6 runs (79.3% against the published 79.2%); a single run has only about
  450 usual-care visits, so it can land several points away.
- The doctor-agreement rate (an assumption) is part of the sensitivity analysis (60% or 85%).

How the comparison is kept fair:
- Completion rates use the same people and checks for both ways: what usual care's own rules say is due. This
  system's extra checks (development, lipids, wellbeing...) are reported separately (`*_all_checks` in results.json).
- Costs include the visits themselves (a typical Abu Dhabi GP consultation, AED 220, price-directory estimate) as well
  as outreach. This system books many more visits, so it costs more in total; per person with completed care the cost
  is about the same, and outreach per person is cheaper.
- The diabetes score reads only what a clinic can see (age, BMI, diagnoses, family history). In the simulation, family history is linked to true diabetes at the strength found in real NHANES data (odds ratio about 5).

Safety and privacy in the app: the web server only serves files from `frontend/dist`; emergency words are answered
even after an opt-out; the doctor's Agree / Disagree changes what outreach does; the outreach queue and phone demo use
current consent, and restarting the phone demo keeps the consent history. Sign-in and roles (checked on the server, `api/auth.py`):
- Accounts: `python scripts/add_user.py NAME ROLE [--region R] [--person ID]` (roles: admin, doctor, outreach,
  manager, patient). Once `config/users.json` exists, every API call needs a session from the sign-in page, each role
  can only call the API behind its own pages, region and patient accounts only reach their own people, and decisions
  and consent changes record who made them.
- Quick demo: set `PP_PASSWORD` for one shared admin account instead. With neither, the app is open (own computer only).
- HTTPS: `python scripts/make_dev_cert.py`, then `python server.py --host 0.0.0.0 --certfile config/tls/cert.pem
  --keyfile config/tls/key.pem`. The server warns if it is reachable without sign-in or without HTTPS. A pilot would
  use the clinic's own sign-in and certificate.

Reproducible runs: every step treats `run_date` in `config/real_values.json` (design section) as today, so a rerun
gives the same data and results. `PP_RUN_DATE=YYYY-MM-DD` overrides it.

Frontend packages: React 18.3.1, React Router 7.18.5, Vite 6.4.6 (Rollup's WebAssembly build, so Windows Smart App
Control does not block the build). `npm audit`: 0 vulnerabilities.

The phone demo keeps every start and reply in `outputs/phone_log.csv` and replays it after a restart, so bookings and
STOP replies survive.

Family visits: when one person books, family members with an in-person check due are booked for the same visit.
Children's check-ups depend on a parent bringing them, and this raised children's completed care from about 30% to
about 50%. A normal blood pressure and blood sugar at a visit moves the risk part of that person's priority to the
bottom until the next check is due.

Known limits: the simulation creates no new lab values, so a normal result changes priority but the score is not
recomputed; the values marked "assumption" in the config still have no published source; the visit price is a
price-directory estimate (the official DoH tariff is a spreadsheet that was not checked).

See `README_DATA.md` for the data, and `tests/` for the checks.
