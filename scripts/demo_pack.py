"""
Demo pack: the files the app reads, in one small zip, so a downloaded copy runs in minutes instead of after the
multi-hour data build.

    python scripts/demo_pack.py make      write demo_pack/demo_pack.zip from a full build on this computer
    python scripts/demo_pack.py load      unpack it into this folder
    python scripts/demo_pack.py load --force   also replace files that are already there

`load` never overwrites a file you already have unless you pass --force, so it cannot damage a full build.
Not in the pack: the hospital records database (842 MB). Without it the app runs fully except the FHIR API, patient names,
past visits and the record download, which need `python scripts/get_all_data.py --only records` after the full data build.
Everything in the pack is simulated: no real person is in it.
"""
import glob
import json
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "demo_pack", "demo_pack.zip")
# what the server, the agents and the pages open (found by recording every file the app opens while every page runs)
PATTERNS = ["outputs/new/*", "outputs/old/*", "outputs/models/*", "outputs/results/*",
            "data/processed/features/patient_features.csv", "data/processed/uae_synthea/truth_hidden.csv",
            "data/reference/*.csv", "data/real/calendar/*.csv", "data/real/osm/*.csv", "frontend/dist/**/*"]
# state the app writes while it runs: never part of the pack
SKIP = {"outputs/auth_limits.json", "outputs/doctor_feedback.csv", "outputs/emergency_alerts.csv", "outputs/phone_log.csv",
        "outputs/consent_changes.csv", "outputs/outreach_log.csv", "outputs/sessions.json", "outputs/access_log.csv"}


def members():
    out = []
    for pattern in PATTERNS:
        for path in sorted(glob.glob(os.path.join(ROOT, pattern), recursive=True)):
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            if os.path.isfile(path) and rel not in SKIP:
                out.append(rel)
    return sorted(set(out))


def make():
    files = members()
    if not any(f.startswith("outputs/new/people_final") for f in files):
        sys.exit("No full build here: run python run_all.py first")
    os.makedirs(os.path.dirname(PACK), exist_ok=True)
    raw = 0
    with zipfile.ZipFile(PACK, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            z.write(os.path.join(ROOT, rel), rel)
            raw += os.path.getsize(os.path.join(ROOT, rel))
        z.writestr("demo_pack_manifest.json", json.dumps({
            "made": time.strftime("%Y-%m-%d %H:%M"), "files": len(files), "note": "Simulated people only. See scripts/demo_pack.py"}, indent=1))
    print(f"{len(files)} files, {raw / 1e6:.1f} MB packed into {os.path.getsize(PACK) / 1e6:.1f} MB: {os.path.relpath(PACK, ROOT)}")


def load(force=False):
    if not os.path.exists(PACK):
        sys.exit(f"{os.path.relpath(PACK, ROOT)} not found")
    put = kept = 0
    with zipfile.ZipFile(PACK) as z:
        for name in z.namelist():
            if name == "demo_pack_manifest.json" or name.startswith("/") or ".." in name.split("/"):
                continue
            target = os.path.join(ROOT, name)
            if os.path.exists(target) and not force:
                kept += 1
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(name) as src, open(target, "wb") as dst:
                dst.write(src.read())
            put += 1
    print(f"unpacked {put} files ({kept} already there and kept)")
    print("Next:  python -m pip install -r requirements.txt   then   python server.py   and open http://127.0.0.1:8000")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "make":
        make()
    elif cmd == "load":
        load(force="--force" in sys.argv)
    else:
        sys.exit(__doc__)
