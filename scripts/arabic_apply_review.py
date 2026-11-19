"""
Apply a native speaker's corrections from the review sheet back into the app.

    python scripts/arabic_apply_review.py SHEET.csv              show what would change (nothing is written)
    python scripts/arabic_apply_review.py SHEET.csv --apply      write the corrections into frontend/src/ar_more.js and ar.js

SHEET.csv is docs/arabic_review.csv (made by scripts/arabic_review_sheet.py) after a reviewer filled the "Correction" column. A row is
applied when its Correction is not empty and its Status is not "OK" (blank or "Changed" both count); the English text is the key, so
every place that text is used changes. Rows whose English text is no longer in the files are listed, not guessed. Rebuild the app after
(cd frontend && npm run build) and rebuild the demo pack.
"""
import argparse
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [os.path.join(ROOT, "frontend", "src", "ar_more.js"), os.path.join(ROOT, "frontend", "src", "ar.js")]
PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"(\s*:\s*)"((?:[^"\\]|\\.)*)"')


def js(s):
    return json.loads(f'"{s}"')


def corrections(sheet):
    out = {}
    with open(sheet, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            fix = (r.get("Correction") or "").strip()
            status = (r.get("Status (OK / Changed / Unsure)") or "").strip().lower()
            if fix and status != "ok":
                out[r["English"]] = fix
    return out


def apply(fixes, files, write):
    done, found = {}, set()
    for path in files:
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()

        def swap(m):
            try:
                en = js(m.group(1))
            except ValueError:
                return m.group(0)
            if en not in fixes:
                return m.group(0)
            found.add(en)
            if js(m.group(3)) == fixes[en]:
                return m.group(0)
            done[en] = done.get(en, 0) + 1
            return f'"{m.group(1)}"{m.group(2)}{json.dumps(fixes[en], ensure_ascii=False)}'

        new = PAIR.sub(swap, text)
        if write and new != text:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
    return done, [e for e in fixes if e not in found]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheet")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--files", nargs="*", help="the JavaScript files to change (default: the app's two Arabic files)")
    a = ap.parse_args(argv)
    fixes = corrections(a.sheet)
    done, missing = apply(fixes, a.files or FILES, a.apply)
    print(f"{len(fixes)} corrections in the sheet; {sum(done.values())} places {'changed' if a.apply else 'would change'} for {len(done)} texts.")
    for en in missing:
        print(f"  not found in the files (left alone): {en[:80]}")
    if not a.apply and done:
        print("Nothing was written. Run again with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
