// Readable names for checks and pathways, for staff pages (patients see plainer words from the server).
export const CHECK_LABEL = {
  well_child_visit: "Well-Child visit", developmental_screen: "Development screen", autism_screen: "Autism screen",
  mmr_vaccine: "MMR vaccine", vaccine_catch_up: "Vaccine catch-up", bp_check: "Blood pressure check",
  diabetes_screen: "Diabetes screen", hba1c_test: "HbA1c test", lipid_test: "Cholesterol test", cholesterol_test: "Cholesterol test",
  cervical_screening: "Cervical screening", breast_screening: "Breast screening", bowel_screening: "Bowel screening",
  depression_screen: "Wellbeing (depression) screen", lung_screening: "Lung screening", basic_health_check: "Basic health check",
  blood_sugar_test: "Blood sugar test", genetic_counselling: "Genetic counselling", carrier_counselling: "Carrier counselling",
  premarital_screening: "Premarital screening", results_review: "Results review", prediabetes_programme: "Prediabetes programme",
  weight_support: "Weight support", stop_smoking_service: "Stop-smoking service", family_nutrition_programme: "Family nutrition programme",
  emergency_callback: "Emergency call-back", reach_without_messages: "Reach without messages",
};

export function checkLabel(id) {
  if (!id) return "";
  return CHECK_LABEL[id] || id.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
