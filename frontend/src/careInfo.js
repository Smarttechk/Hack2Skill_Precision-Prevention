// What each check is and how to get ready, in the plain names a patient sees (config/language.json). Kept general on purpose:
// the clinic confirms anything specific (for example whether to avoid food before a blood test).
// "You", or the child's first name with the age, so two children of the same age can be told apart
export const nameOf = (p, tr) => (p.self ? tr("You") : p.first_name ? `${p.first_name} (${tr("{age} y", { age: p.age })})` : tr("Your child ({age} y)", { age: p.age }));

export const CARE_INFO = {
  "child health check": { what: "A routine check of your child's growth and development with a doctor or nurse.", prep: "Bring your child's health card and vaccination card." },
  "development check": { what: "A short check of how your child is learning, speaking and moving.", prep: "Bring your child's health card and tell the nurse about any worries." },
  "vaccination": { what: "A routine vaccine that protects against infections.", prep: "Bring the vaccination card, and tell the nurse if the person is unwell on the day." },
  "blood pressure check": { what: "A quick, painless measurement of your blood pressure.", prep: "Rest quietly for a few minutes before it is taken." },
  "blood sugar check": { what: "A simple test to see whether your blood sugar is raised, which can be an early sign of diabetes.", prep: "Your clinic will tell you if you need to avoid food beforehand." },
  "blood sugar test": { what: "A simple blood test that shows your average blood sugar.", prep: "Your clinic will tell you if you need to avoid food beforehand." },
  "blood test": { what: "A routine blood test, for example for cholesterol.", prep: "Your clinic will tell you if you need to avoid food beforehand." },
  "cervical screening": { what: "A short test that looks for early changes in the cervix.", prep: "Your clinic will explain how to get ready when you book." },
  "breast screening": { what: "A mammogram, an X-ray that looks for early changes in the breast.", prep: "Wear a two-piece outfit, and tell the staff if you are or may be pregnant." },
  "bowel screening": { what: "A simple test that looks for early signs of bowel problems.", prep: "Your clinic will give you the kit and instructions." },
  "wellbeing check": { what: "A few private questions about your mood and how you are coping.", prep: "Nothing to prepare." },
  "lung check": { what: "A test that looks for early lung problems in people who smoke or used to.", prep: "Your clinic will explain the test." },
  "health check": { what: "A general check-up with a doctor or nurse.", prep: "Bring your Emirates ID and a list of any medicines you take." },
  "premarital health check": { what: "A health check offered before marriage.", prep: "Bring your Emirates ID." },
  "appointment with our specialist team": { what: "A conversation with our specialist team about your family's health.", prep: "Nothing to prepare. You decide what to talk about." },
  "results appointment": { what: "A visit to go through your recent results with a doctor or nurse.", prep: "Write down your questions beforehand." },
  "healthy lifestyle programme": { what: "Support with food, activity and daily habits.", prep: "" },
  "healthy weight support": { what: "Support to reach and keep a healthy weight.", prep: "" },
  "stop-smoking support": { what: "Help and advice to stop smoking.", prep: "" },
  "family nutrition programme": { what: "Advice on healthy eating for the whole family.", prep: "" },
  "call from our nurse": { what: "A phone call from our nurse about your health.", prep: "Keep your phone nearby." },
  "call from our care team": { what: "A phone call from our care team about your health.", prep: "Keep your phone nearby." },
};
