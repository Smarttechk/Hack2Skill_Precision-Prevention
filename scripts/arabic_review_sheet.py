"""
Make a sheet a native Arabic speaker can review: every English text with its Arabic draft, newest screens first.

    python scripts/arabic_review_sheet.py       writes docs/arabic_review.csv (opens in Excel; UTF-8)

The Arabic in the app is a machine-assisted draft. A clinician who is a native speaker should check it before a pilot. The reviewer
fills "Correction" where the draft is wrong or unnatural and "Status" (OK / Changed / Unsure); the corrections are then pasted back into
frontend/src/ar_more.js (or ar.js) by the same English key.
"""
import csv
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [os.path.join(ROOT, "frontend", "src", "ar_more.js"), os.path.join(ROOT, "frontend", "src", "ar.js")]
PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"')
HAS_ARABIC = re.compile(r"[؀-ۿ]")


def js(s):
    return json.loads(f'"{s}"')                    # undo the JavaScript escapes (\n, \", \u...)


def read(path):
    rows, section = [], os.path.basename(path)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            t = line.strip()
            if t.startswith("//"):
                section = t.lstrip("/ ").strip() or section
                continue
            for k, v in PAIR.findall(line):
                try:
                    en, ar = js(k), js(v)
                except ValueError:
                    continue
                if HAS_ARABIC.search(ar) and re.search(r"[A-Za-z]", en):
                    rows.append((section, en, ar))
    return rows


def main():
    seen, rows = set(), []
    for path in FILES:
        if os.path.exists(path):
            for section, en, ar in read(path):
                if en not in seen:                  # a later duplicate key wins in the app; list each English text once
                    seen.add(en)
                    rows.append((section, en, ar))
    rows.sort(key=lambda r: 0 if "draft Arabic" in r[0] else 1)       # the newest screens (marked as drafts) first; the order inside is kept
    out = os.path.join(ROOT, "docs", "arabic_review.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["Screen or section", "English", "Arabic draft", "Correction", "Status (OK / Changed / Unsure)"])
        for section, en, ar in rows:
            w.writerow([section, en, ar, "", ""])
    sections = {}
    for s, _, _ in rows:
        sections[s] = sections.get(s, 0) + 1
    print(f"{len(rows)} texts written to {out}")
    for s, n in list(sections.items())[:12]:
        print(f"  {n:4d}  {s[:90]}")


if __name__ == "__main__":
    main()
