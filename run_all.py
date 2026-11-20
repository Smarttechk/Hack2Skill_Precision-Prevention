"""
Runs the whole project in order, from data to results.

    python run_all.py                  data (skips what exists) -> models -> 60-day simulation -> evaluation -> tests
    python run_all.py --sensitivity    also the worst-case analysis and the AI on/off comparison (over an hour extra)
    then: python server.py             opens the React app at http://127.0.0.1:8000
"""
import os
import shutil
import subprocess
import sys
import tempfile

STEPS = [
    ("Data and features", [sys.executable, "scripts/get_all_data.py"]),
    ("Real-data check of the published diabetes scores (NHANES)", [sys.executable, "-m", "models.validate_scores"]),
    ("Risk scores (published FINDRISC, scaled to the UAE level)", [sys.executable, "-m", "models.risk_scores"]),
    ("Trained second opinion: compared with the published score (NHANES)", [sys.executable, "-m", "models.ml_comparison"]),
    ("Trained second opinion: model scaled to the UAE level (information only)", [sys.executable, "-m", "models.ml_risk"]),
    ("Trained second opinion: shadow check on the simulated residents", [sys.executable, "-m", "models.ml_shadow"]),
    ("Calibrate to published UAE figures", [sys.executable, "scripts/calibrate.py"]),
    ("60-day simulation", [sys.executable, "orchestrator.py"]),
    ("Evaluation", [sys.executable, "evaluate.py"]),
    ("Tests", None),
    ("React app", "frontend"),
]
TESTS = ["tests/test_data.py", "tests/test_records.py", "tests/test_fhir_api.py", "tests/test_ai.py", "tests/test_ml_risk.py", "tests/test_assistant.py", "tests/test_features.py", "tests/test_system.py", "tests/test_dashboard.py",
         "tests/test_snips.py", "tests/test_snips2.py", "tests/test_snips3.py", "tests/test_api.py", "tests/test_careteam.py", "tests/test_outreach.py", "tests/test_family.py", "tests/test_demo_team.py", "tests/test_arabic_review.py", "tests/test_demo_data.py"]


def build_frontend():
    """Builds the React app once (frontend/dist). Needs Node.js; skipped with a note if npm is missing."""
    if os.path.exists("frontend/dist/index.html"):
        print("  React app already built (delete frontend/dist to rebuild).")
        return
    npm = shutil.which("npm")
    if not npm:
        print("  npm not found: install Node.js 18+ and run  cd frontend && npm install && npm run build")
        return
    for args in (["install"], ["run", "build"]):
        if subprocess.run([npm, *args], cwd="frontend").returncode:
            sys.exit("Step failed: React build")


def main():
    i = [name for name, _ in STEPS].index("Evaluation")
    if "--sensitivity" in sys.argv:
        STEPS[i] = ("Evaluation with sensitivity analysis and AI ablation", [sys.executable, "evaluate.py", "--sensitivity", "--seeds", "--ablation"])
    else:                                    # the headline averages over several seeds always follow the data
        STEPS[i] = ("Evaluation", [sys.executable, "evaluate.py", "--seeds"])
    for name, cmd in STEPS:
        print(f"\n===== {name} =====", flush=True)
        if cmd is None:
            failed = []
            scratch = tempfile.mkdtemp()           # the tests write their sign-ins and logs here, never into the real access log
            for t in TESTS:
                arg = ["tests"] if "snips" in t else []
                # read the output as UTF-8: some checks print Arabic, which the Windows default code page cannot decode
                r = subprocess.run([sys.executable, t, *arg], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env={**os.environ, "PYTHONIOENCODING": "utf-8", "PP_ACCESS_LOG": os.path.join(scratch, "access_log.csv"),
                                        "PP_SESSIONS_FILE": os.path.join(scratch, "sessions.json"), "PP_LIMITS_FILE": os.path.join(scratch, "limits.json"),
                                        "PP_OUTREACH_DIR": scratch, "PP_ANSWERS_FILE": os.path.join(scratch, "answers.json")})
                last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
                print(f"  {t}: {last}")
                if r.returncode:
                    failed.append(t)
            if failed:
                sys.exit(f"Tests failed: {failed}")
            continue
        if cmd == "frontend":
            build_frontend()
            continue
        r = subprocess.run(cmd)
        if r.returncode:
            sys.exit(f"Step failed: {name}")
    print("\nAll done. Start the app with: python server.py   then open http://127.0.0.1:8000")


if __name__ == "__main__":
    main()
