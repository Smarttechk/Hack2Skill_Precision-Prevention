// What each role is, shown on the landing page and the sign-up forms. Which pages and people a role may open is
// decided by the server (config/roles.json); this file only describes it.
export const ROLE_INFO = {
  patient: {
    title: "Patient or family", icon: "heart", color: "#3e8a9a",
    who: "Adults, and parents or guardians of children",
    does: "See what check-ups, vaccines and screenings are due for you and your children, your booked visits and the clinic's messages, and choose how we may contact you.",
    sees: "Only your own record and the children you are guardian of",
    approval: "The hospital checks your Emirates ID and date of birth, then links the account to your health record.",
  },
  doctor: {
    title: "Doctor", icon: "stethoscope", color: "#2f6a55",
    who: "Licensed physicians in primary care",
    does: "Review the people the system flags (Urgent, High, Routine) with their warning signs, risk range and reasons. Agree or put on hold, review abnormal results and emergency call-backs.",
    sees: "People in your region (or all regions)",
    approval: "The hospital admin checks your DoH licence number before activating the account.",
  },
  outreach: {
    title: "Outreach staff", icon: "megaphone", color: "#6b8fb8",
    who: "Nurses, call-centre and community health workers",
    does: "Work through today's contact list: who to call, in which language and channel, at the best time, and mark each contact done.",
    sees: "People in your region (or all regions)",
    approval: "The hospital admin checks your staff number before activating the account.",
  },
  manager: {
    title: "Programme manager", icon: "chart", color: "#c08a3e",
    who: "Public-health planners and programme leads",
    does: "Follow results against usual care, fairness by language, nationality and region, the map of care gaps and suggested community actions.",
    sees: "Totals only, never an individual patient's record",
    approval: "The hospital admin confirms your department before activating the account.",
  },
  admin: {
    title: "Hospital admin", icon: "building", color: "#1f3a44",
    who: "The person who runs the system for the hospital",
    does: "Approve sign-ups, create and remove accounts, reset passwords, unlock accounts and read the access log. Opens every page.",
    sees: "Everyone",
    approval: "The first hospital admin is active at once; later admins are approved by an existing admin.",
  },
};

export const ROLE_ORDER = ["patient", "doctor", "outreach", "manager", "admin"];

// The longer description on the "Who sees what" page (/roles): what a role can do and what it can never open.
// The list of pages itself is not written here: that page reads it from the server (/api/roles).
export const ROLE_DETAILS = {
  patient: {
    can: ["See which check-ups, vaccines and screenings are due for you and your children",
      "See your booked visits and the messages the clinic sent you",
      "Read why you were contacted and which data was used",
      "Choose your language and how we may contact you, or stop the messages"],
    cannot: ["Any other person's or family's record", "Programme totals, the map or the staff pages"],
  },
  doctor: {
    can: ["Review the people the system flags, with their warning signs, risk range and reasons",
      "Agree with a suggested step or put it on hold: every clinical decision is yours",
      "Review abnormal results and emergency call-backs",
      "Open a family's records together, and one person's journey step by step",
      "See your own patients, the ones you cover for a colleague, and people a family has shared with your hospital",
      "In an emergency, open one other patient for a few hours after giving a reason"],
    cannot: ["Patients on other doctors' lists, except an emergency opening that is logged and shown to the hospital admin",
      "Another hospital's patients, unless their family shared the record", "Accounts, sign-up requests and the access log"],
  },
  outreach: {
    can: ["Work through today's contact list: who to contact, in which language and by which channel",
      "See the best time to call and the care option to offer",
      "Mark each contact as done",
      "Open a family's open care gaps together, and one person's journey"],
    cannot: ["People outside your region, when your account is set to one region", "Clinical decisions: those stay with the doctor",
      "Accounts, sign-up requests and the access log"],
  },
  manager: {
    can: ["Follow results against usual care", "Check fairness by language, nationality and region",
      "See the map of care gaps", "Read the suggested action for each community"],
    cannot: ["Any individual patient's record", "The contact list and the doctor's review pages"],
  },
  admin: {
    can: ["Approve or refuse sign-up requests", "Create and remove accounts, reset passwords and unlock accounts",
      "Read the access log: who signed in and who opened which record", "Open every page",
      "Match patients to doctors by hospital, specialty and language, name a backup for leave, and see who opened a record outside their own patients"],
    cannot: ["A hospital admin cannot see another hospital's accounts, doctors or patients; the central admin (no hospital set) sees all",
      "Nothing else is hidden from this role, so it is given to very few people, and its own record openings are logged too"],
  },
};

// one line on what each page is for
export const PAGE_INFO = {
  home: "Totals for the whole programme at a glance",
  today: "Everything that needs a decision or a follow-up today, in one place",
  audit: "Who opened which record and why, break-glass openings, family sharing and a download of the log",
  accounts: "Sign-up requests, accounts and the access log",
  careteam: "Which doctor looks after which patients, who covers whom, and who opened a record outside their own patients",
  journey: "Every step taken for one person, day by day",
  phone: "A demo phone that shows the messages a family receives",
  doctor: "Flagged people with warning signs, risk range and reasons",
  queue: "Today's contact list, most urgent first",
  family: "A household's open care gaps together",
  why: "Plain reasons for a contact, the data used and contact choices",
  map: "Where the care gaps are",
  communities: "Suggested actions place by place",
  results: "Comparison with usual care and the fairness checks",
  mycare: "What is due, booked visits and the clinic's messages",
};

// how each sign-up field is asked for
export const FIELD_INFO = {
  full_name: { label: "Full name", icon: "user", placeholder: "As on your Emirates ID" },
  email: { label: "Work email", icon: "mail", type: "email", placeholder: "name@hospital.ae" },
  phone: { label: "Mobile number", icon: "phone", type: "tel", placeholder: "+971 50 123 4567" },
  hospital: { label: "Hospital or health network", icon: "building", placeholder: "e.g. Abu Dhabi Health Services" },
  job_title: { label: "Job title", icon: "id", placeholder: "e.g. Head of Digital Health" },
  license_number: { label: "DoH licence number", icon: "id", placeholder: "e.g. GD12345" },
  specialty: { label: "Specialty", icon: "stethoscope", options: ["Family medicine", "General practice", "Paediatrics", "Internal medicine", "Endocrinology", "Obstetrics and gynaecology", "Other"] },
  facility: { label: "Clinic or health centre", icon: "building", placeholder: "Where you work" },
  staff_id: { label: "Staff number", icon: "id", placeholder: "From your staff card" },
  languages: { label: "Languages you speak with patients", icon: "message", placeholder: "e.g. Arabic, English, Urdu" },
  department: { label: "Department", icon: "building", placeholder: "e.g. Public Health Programmes" },
  date_of_birth: { label: "Date of birth", icon: "calendar", type: "date" },
  emirates_id: { label: "Emirates ID number", icon: "id", placeholder: "784-1990-1234567-1", note: "We keep only the last 4 digits, to check against your card." },
  language: { label: "Preferred language", icon: "message", options: ["Arabic", "English", "Hindi", "Urdu", "Tagalog"] },
  region: { label: "Region you work in", icon: "map", regions: true },
  record_number: { label: "Health record number (if you know it)", icon: "id", placeholder: "Optional: leave empty if you are new" },
  home_region: { label: "Hospital region you use (if you are new)", icon: "map", regions: true },
  children: { label: "Children you look after (first names and ages)", icon: "users", placeholder: "Optional, e.g. Sara 4, Omar 9" },
};
