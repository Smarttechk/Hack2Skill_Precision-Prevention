// Interface labels in English and Arabic. Arabic is a draft: a native speaker must check it.
// Data from the system (reasons, checks, messages) stays as the system writes it.
import { createContext, useContext } from "react";
import { AR } from "./ar.js";
import { AR_MORE, CHECK_AR, PATTERNS } from "./ar_more.js";

export const LABELS = {
  en: {
    appTitle: "Precision Prevention and Screening",
    signedInAs: "Signed in as",
    banner: "Priority list for outreach. Not a diagnosis. The doctor decides.",
    pages: {
      home: "Overview", doctor: "Doctor view", queue: "Outreach queue", family: "Family view", journey: "Patient journey",
      phone: "Patient phone", why: "Why was I contacted?", map: "Map", communities: "Communities", results: "Results",
      accounts: "Accounts", careteam: "Care teams", mycare: "My care", today: "Today", audit: "Audit",
    },
    previewStaff: "Staff member", anyStaff: "Everyone in the region (no named person)",
    previewDoctor: "Doctor", previewHospital: "Hospital", allPatients: "All patients (no care team)", centralAdmin: "Central admin (all hospitals)",
    changePassword: "Change password", currentPassword: "Current password", newPassword: "New password",
    roles: { admin: "Hospital admin", doctor: "Doctor", outreach: "Outreach staff", manager: "Programme manager", patient: "Patient or family" },
    signIn: "Sign in", signOut: "Sign out", user: "User name", password: "Password",
    signInNote: "Sign in with the account the hospital gave you. You will see only the pages and people your role needs.",
    previewRole: "Preview as (no accounts set up yet)",
    openMode: "No accounts are set up, so anyone on this computer can preview every role. Create accounts with: python scripts/add_user.py",
    onlyRegion: "Only people in", ownRecord: "Your own record and your children",
    loading: "Loading...", agree: "Agree", disagree: "Disagree", save: "Save", cancel: "Cancel", note: "Note (optional)",
    urgency: "Urgency", region: "Region", awaitingOnly: "Only cards waiting for a doctor", people: "people",
    demoLogin: "Demo sign-in: roles only, no passwords. A real deployment uses the hospital's login.",
    challenge: "Future Health Challenge, Abu Dhabi", pagesLabel: "Pages", about: "About this project",
    offline: "Cannot reach the server. Start it with: python server.py (keep that window open). This page reconnects by itself.",
  },
  ar: {
    challenge: "تحدي مستقبل الصحة، أبوظبي", pagesLabel: "الصفحات", about: "عن هذا المشروع",
    appTitle: "الوقاية والفحص الدقيق",
    signedInAs: "تسجيل الدخول بصفة",
    banner: "قائمة أولويات للتواصل. ليست تشخيصاً. الطبيب هو من يقرر.",
    pages: {
      home: "نظرة عامة", doctor: "عرض الطبيب",
      queue: "قائمة التواصل", family: "عرض الأسرة",
      journey: "رحلة المريض", phone: "هاتف المريض",
      why: "لماذا تم التواصل معي؟", map: "الخريطة",
      communities: "المجتمعات", results: "النتائج",
      accounts: "الحسابات", careteam: "فرق الرعاية", mycare: "رعايتي", today: "اليوم", audit: "التدقيق",
    },
    previewStaff: "الموظف", anyStaff: "الجميع في المنطقة (بلا شخص محدد)",
    previewDoctor: "الطبيب", previewHospital: "المستشفى", allPatients: "كل المرضى (بلا فريق رعاية)", centralAdmin: "المدير المركزي (كل المستشفيات)",
    changePassword: "تغيير كلمة المرور", currentPassword: "كلمة المرور الحالية", newPassword: "كلمة المرور الجديدة",
    roles: {
      admin: "مدير المستشفى", doctor: "طبيب",
      outreach: "فريق التواصل", manager: "مدير البرنامج",
      patient: "مريض أو أسرة",
    },
    signIn: "تسجيل الدخول", signOut: "تسجيل الخروج", user: "اسم المستخدم", password: "كلمة المرور",
    signInNote: "سجّل الدخول بالحساب الذي أعطاك إياه المستشفى. سترى فقط الصفحات والأشخاص الذين يحتاجهم دورك.",
    previewRole: "معاينة بصفة (لا توجد حسابات بعد)",
    openMode: "لا توجد حسابات، لذا يمكن لأي شخص على هذا الجهاز معاينة كل الأدوار. أنشئ الحسابات بالأمر: python scripts/add_user.py",
    onlyRegion: "فقط الأشخاص في", ownRecord: "سجلك وسجلات أطفالك فقط",
    loading: "جارٍ التحميل...", agree: "أوافق",
    disagree: "لا أوافق", save: "حفظ", cancel: "إلغاء", note: "ملاحظة (اختياري)",
    urgency: "الأولوية", region: "المنطقة",
    awaitingOnly: "فقط الحالات بانتظار الطبيب", people: "أشخاص",
    demoLogin: "تسجيل دخول تجريبي: أدوار فقط بدون كلمات مرور.",
    offline: "تعذّر الوصول إلى الخادم. شغّله بالأمر: python server.py (واترك النافذة مفتوحة). ستُعاد محاولة الاتصال تلقائياً.",
  },
};

export const LangContext = createContext({ lang: "en", t: LABELS.en, setLang: () => {} });
export const useT = () => useContext(LangContext);

// tr("Sign up as {role}", { role }) -> the Arabic text when Arabic is chosen (falls back to English), values filled in.
// Text from the server (reasons, channels, event details) goes through toArabic: exact entry, then a sentence pattern,
// then a comma or "|" list part by part, then a check id; anything unknown stays as it is.
export function useTr() {
  const { lang } = useT();
  return (s, vars) => {
    if (s === null || s === undefined) return s;
    let out = lang === "ar" ? toArabic(String(s)) : String(s);
    if (vars) for (const [k, v] of Object.entries(vars)) out = out.split(`{${k}}`).join(String(v));
    return out;
  };
}

const ALL = { ...AR, ...AR_MORE };
export function toArabic(s) {
  if (!s) return s;
  if (ALL[s] !== undefined) return ALL[s];
  const t = s.trim();
  if (t !== s) return s.replace(t, toArabic(t));
  if (ALL[t] !== undefined) return ALL[t];
  for (const [re, fn] of PATTERNS) {
    const m = t.match(re);
    if (m) return fn(m, toArabic);
  }
  if (t.includes(" | ")) return t.split(" | ").map(toArabic).join(" | ");
  if (t.includes(", ")) {
    const parts = t.split(", ");
    const out = parts.map(toArabic);
    if (out.some((x, i) => x !== parts[i])) return out.join("، ");
  }
  if (CHECK_AR[t]) return CHECK_AR[t];
  const low = t.charAt(0).toLowerCase() + t.slice(1);
  if (low !== t && ALL[low] !== undefined) return ALL[low];
  const up = t.charAt(0).toUpperCase() + t.slice(1);
  if (up !== t && ALL[up] !== undefined) return ALL[up];
  return s;
}
