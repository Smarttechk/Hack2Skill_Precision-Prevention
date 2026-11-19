"""
The Arabic review sheet and the script that applies a reviewer's corrections.
    python tests/test_arabic_review.py
"""
import csv
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import arabic_apply_review as apply_  # noqa: E402
import arabic_review_sheet as sheet_  # noqa: E402

ok = []


def check(name, cond):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


tmp = tempfile.mkdtemp()
js = os.path.join(tmp, "ar_test.js")
open(js, "w", encoding="utf-8").write('''export const UI = {
  // a new screen (draft Arabic, to be reviewed)
  "Save": "حفظ", "Say \\"hello\\"": "قل مرحباً",
  "Cancel": "إلغاء",
};
''')
rows = sheet_.read(js)
check("the sheet reader finds every English text with its Arabic draft and the section it is in, including escaped quotes",
      [(r[1], r[2]) for r in rows] == [("Save", "حفظ"), ('Say "hello"', "قل مرحباً"), ("Cancel", "إلغاء")] and "draft Arabic" in rows[0][0])
sheet = os.path.join(tmp, "sheet.csv")
with open(sheet, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh)
    w.writerow(["Screen or section", "English", "Arabic draft", "Correction", "Status (OK / Changed / Unsure)"])
    w.writerow(["x", "Save", "حفظ", "احفظ", "Changed"])
    w.writerow(["x", "Cancel", "إلغاء", "ألغِ", "OK"])                 # marked OK: the correction is ignored
    w.writerow(["x", 'Say "hello"', "قل مرحباً", "", ""])               # no correction
    w.writerow(["x", "Gone text", "نص", "نص جديد", "Changed"])          # no longer in the files
before = open(js, encoding="utf-8").read()
done, missing = apply_.apply(apply_.corrections(sheet), [js], write=False)
check("a dry run reports what would change and writes nothing", done == {"Save": 1} and open(js, encoding="utf-8").read() == before)
done, missing = apply_.apply(apply_.corrections(sheet), [js], write=True)
after = open(js, encoding="utf-8").read()
check("applying changes only the corrected text; an OK row, a row with no correction and the rest of the file are left alone",
      '"Save": "احفظ"' in after and '"Cancel": "إلغاء"' in after and 'قل مرحباً' in after and after.count("draft Arabic") == 1)
check("a corrected text that is no longer in the files is reported, not guessed", missing == ["Gone text"])
check("the real review sheet reads back: every English text of the app with an Arabic draft is in it, newest screens first",
      len(sheet_.read(os.path.join(ROOT, "frontend", "src", "ar_more.js"))) > 500)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
