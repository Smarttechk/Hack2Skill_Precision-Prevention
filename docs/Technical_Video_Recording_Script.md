# Technical video: script for recording

Read the lines in order. Do **not** read the headings or slide numbers; they are only for you.

About 1206 words. At a calm 130 words a minute this takes about 9 minutes 16 seconds, plus the pauses.

## Before you record

- Use a quiet room, with the phone or microphone about a hand's width from your mouth, slightly to the side so breaths do not pop.
- Record in one go, in one file. Switch off fans, and put the phone on silent.
- **Pause for 2 full seconds of silence between slides.** Count "one, two" in your head. These pauses let me place every slide change exactly on your voice.
- If you make a mistake, stop, wait 2 seconds, and read the whole line again from its start. I will keep the last good take.
- Speak slowly and smile slightly; it makes the voice clearer. Slow down on numbers and on the words in the table below.

## How to say the hard words

| Written | Say it like |
|---|---|
| FHIR | "fire" |
| NHANES | "en-haynes" |
| FINDRISC | "find-risk" |
| ACC AHA | "A C C, A H A" (say each letter) |
| AUC | "A U C" (say each letter) |
| PBKDF2 | "P B K D F two" |
| HttpOnly | "H T T P only" |
| HTTPS | "H T T P S" |
| SQLite | "S Q L light" |
| FastAPI | "fast A P I" |
| PostgreSQL | "post-gres Q L" |
| Malaffi | "ma-LAF-fee" |
| SNOMED | "SNOW-med" |
| LOINC | "LOY-nk" |
| CVX | "C V X" |
| UAE | "U A E" |
| stdataai dot com | "S T data A I dot com" |

## Script

**Slide 1: Title**
Precision Prevention and Screening. The technical guide, by Smart Tech.

*(pause 2 seconds)*

**Slide 2: Architecture on one page**
This is the whole system on one page. Read it from top to bottom. First, the people who use it. Then the web app. Then the server. Then the decision logic, with its batch run. And last, the data. The dashed boxes are outside the system. Published scores set the priority, and a trained second opinion is information only.

*(pause 2 seconds)*

**Slide 3: The technology**
The front end is built with React and Vite. The back end is Python, using FastAPI. Everything runs on one port. Records are stored in a SQLite database, shaped like FHIR resources. For a pilot, one function changes, to point at the hospital's own PostgreSQL database. And by default nothing calls an outside AI service. One optional care assistant does, only when the hospital sets an API key; it reads one record, and it cannot send or change anything.

*(pause 2 seconds)*

**Slide 4: Full diagram, layers one to three**
Here is the full diagram, layers one to three. The people. The web app. And the server, with sign-in, roles and care teams, the audit log, the FHIR read API, and the safety checks.

*(pause 2 seconds)*

**Slide 5: Full diagram, layers four and five**
Layers four and five. The decision logic: published risk scores, care teams and consent, the seven agents, and the six-step batch run. Below it, the data. The contact channels, and the hospital or Malaffi, are outside the system. For now, they are simulated.

*(pause 2 seconds)*

**Slide 6: The batch run**
One command, run all, runs the whole batch. It builds five thousand simulated residents, adjusted to UAE figures. It checks the risk scores on real survey data. And it calibrates usual care to published figures. Then it compares both ways over sixty days, with the same staff capacity, and evaluates the results.

*(pause 2 seconds)*

**Slide 7: The seven agents**
Seven agents run in order, every day. Care gap. Risk. Priority. Outreach. Chat. Follow-up. And evaluation. Each one is explained, and can be checked. Urgent people always come first, and every clinical step stays with a doctor.

*(pause 2 seconds)*

**Slide 8: Where AI is used**
The score that sets the priority is not trained. Diabetes risk uses the published FINDRISC points, moved to UAE levels by one number. Heart risk uses the published ACC AHA equations, shown to doctors as information only. A second opinion on diabetes comes from a small logistic regression, trained on real NHANES adults. It is shown to doctors as information only, and never changes anyone's priority. We also tested a channel learner. It gave no gain, so it is switched off.

*(pause 2 seconds)*

**Slide 9: The trained second opinion, measured**
We compared the trained model with the published score on the same twelve thousand nine hundred and twenty-five real adults, scoring each model only on people it was not trained on. The model used in the app has an AUC of zero point seven nine five, against zero point seven four three for FINDRISC, and finds seventy-two percent of cases, against sixty-four. The gain comes from routine measurements, such as HDL, cholesterol and blood pressure, not from a more complex method. It is trained on US survey data, and has not been validated on UAE patients. On the app's own simulated residents it ranks worse than FINDRISC, with an AUC of zero point six three three against zero point six eight six, because the simulation builds its hidden cases from other inputs.

*(pause 2 seconds)*

**Slide 10: Real-data check, on screen**
Here is the real-data check. We tested the diabetes score on twelve thousand nine hundred and twenty-five real NHANES adults. The score we use has an AUC of zero point seven four three. It finds sixty-four percent of cases, by inviting thirty percent of adults. The broad rule used today has to invite eighty-seven percent, to reach ninety-nine.

*(pause 2 seconds)*

**Slide 11: Where every number comes from**
Every number is kept in one file, with its source and a status: published, calibrated, design, or assumption. Calibrated values are tuned to match published UAE figures. Assumptions stay visible, and are tested in the worst-case analysis.

*(pause 2 seconds)*

**Slide 12: Hospital records and FHIR**
Patient records are kept in a hospital database, shaped like FHIR resources. It uses SNOMED, LOINC and CVX codes, and also holds family history, genetic results and consent. FHIR bundles can be imported. We exported three hundred people, imported them back, and got identical rows.

*(pause 2 seconds)*

**Slide 13: The FHIR read API**
The same records can be read through a FHIR R4 API. So a clinic system, or Malaffi, can ask for a patient in the format it already speaks. It follows the same access rules as the app, logs every record opened, and never serves genetic results.

*(pause 2 seconds)*

**Slide 14: Login and guest option**
For testers, the app opens in open mode when no accounts exist. A Preview As switch lets you try each role. Real accounts are optional, and every sign-up needs approval from the hospital admin.

*(pause 2 seconds)*

**Slide 15: Sign-in and access control**
Passwords are stored as salted PBKDF2 hashes. The session is an HttpOnly cookie, and only its hash is stored. Five wrong passwords lock the account for fifteen minutes. Roles are enforced on the server. A wrong page returns forbidden, and another family's record looks as if it does not exist.

*(pause 2 seconds)*

**Slide 16: Care teams, on screen**
Here is the care teams page. The system proposes which doctor looks after which patient, by specialty, language, and how full their list is. The hospital admin approves. Emergency access, called break glass, needs a reason, which is logged.

*(pause 2 seconds)*

**Slide 17: The audit log**
Every record opened goes into a chained log. Each line holds a keyed hash of the line before it. So if a line is edited, removed or inserted, every hash after it breaks, and the Audit page shows where. The log never holds passwords, notes or names.

*(pause 2 seconds)*

**Slide 18: Consent, chat and safety**
Consent decides who may be contacted at all. Genetic results are used only with consent. Emergency words, in five languages, bring up the nine nine eight message straight away, even after an opt-out. And replies use fixed, approved text.

*(pause 2 seconds)*

**Slide 19: Fairness and consent, on screen**
On the left, fairness by group, with ninety-five percent ranges. Groups under ten people are hidden. On the right, the page a family sees: why they were contacted, and their consent choices.

*(pause 2 seconds)*

**Slide 20: Fairness and evaluation**
The comparison uses the same people, the same checks, and the same daily capacity for both ways. The evaluation agent alerts the team when a group falls behind. And lagging groups go first within each urgency level.

*(pause 2 seconds)*

**Slide 21: Results and map, on screen**
Here are the results against usual care, on our dataset. And here is the map of where most people need care now. Small groups are hidden.

*(pause 2 seconds)*

**Slide 22: Results table**
Over sixty days, on simulated people, more children and adults complete their care. The system finds three hundred and nineteen hidden cases, against sixty. Remember, this is generated data, not a real trial. Even in the worst of sixty-four harder settings, it finds more cases than usual care.

*(pause 2 seconds)*

**Slide 23: Testing**
We run six hundred and thirty-four automated checks, in nineteen test files. They cover roles, sign-in limits, the audit chain, and the FHIR round trip. Every step uses one run date, so a rerun gives the same results. And the front end has no known vulnerabilities.

*(pause 2 seconds)*

**Slide 24: Risks and safeguards**
On risks and safeguards: privacy, security, bias, explainability, misuse, and oversight. Every flag shows why it was raised. Messages use fixed approved text, even when the optional assistant is on. And a doctor decides every clinical step.

*(pause 2 seconds)*

**Slide 25: Path to a pilot**
The path to a pilot has three stages. First, set up one hospital over FHIR, with HTTPS and accounts. Second, run the agents on live records, and recalibrate on local data. Third, scale to all regions, with a shared database, and Malaffi.

*(pause 2 seconds)*

**Slide 26: Known limits**
We state the limits openly. This is built on synthetic patients, and the results are simulated. The heart equations are not validated here, and neither is the trained second opinion on UAE patients. Some values are assumptions. And doctors decide every clinical step.

*(pause 2 seconds)*

**Closing card**
Smart Tech LLC. Learn more at stdataai dot com.

*(stay silent for 2 seconds, then stop the recording)*

## When you have recorded

Send me the file. I will find your pauses, place each slide change on them, clean up the sound (noise, volume, clarity), and rebuild the video.
