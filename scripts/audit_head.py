"""
Check the access log's chain, and write its head down somewhere else so the removal of the newest lines can be seen.

    python scripts/audit_head.py                         check the chain now and print the head
    python scripts/audit_head.py --record FILE           also add one line (time, number of lines, head) to FILE
    python scripts/audit_head.py --check FILE            compare the log with the last head recorded in FILE

The chain itself shows an edited, removed or inserted line, but not the removal of the *newest* lines: nothing comes after them to break.
A head written down earlier, in a place the log's own computer cannot change (a USB stick, another machine, a ticket, a printout), does:
the log must still be at least that long, and its line at that number must carry the recorded value. Run --record from time to time and
--check before an audit. Needs the same key as the server (PP_AUDIT_KEY, or the key file next to the log).
"""
import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from api import auth  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record", metavar="FILE")
    ap.add_argument("--check", metavar="FILE")
    a = ap.parse_args(argv)
    v = auth.verify_log()
    where = "an environment variable" if v["key"] == "environment" else "a file next to the log (set PP_AUDIT_KEY to move it off this computer)"
    print(f"Log {auth.ACCESS_LOG}\n  {v['lines']} lines, {v['chained']} chained, {v['before_chain']} from before the chain; key in {where}")
    if not v["ok"]:
        print(f"  CHAIN BROKEN from line {v['first_bad_line']}: the log was changed outside the app.")
        return 1
    print(f"  chain intact. Head {v['head']}")
    if a.record:
        full = auth.chain_at(v["lines"])
        if not full:
            print("  nothing to record: the log has no chained line yet.")
            return 1
        with open(a.record, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "lines": v["lines"], "chain": full}) + "\n")
        print(f"  recorded in {a.record}: keep that file somewhere this computer cannot change.")
    if a.check:
        if not os.path.exists(a.check):
            print(f"  {a.check} does not exist: nothing recorded to compare with.")
            return 1
        last = [json.loads(x) for x in open(a.check, encoding="utf-8") if x.strip()][-1]
        now = auth.chain_at(last["lines"])
        if v["lines"] < last["lines"]:
            print(f"  LOG SHORTER than recorded: {last['lines']} lines were recorded on {last['time']}, there are {v['lines']} now. Lines were removed.")
            return 1
        if now != last["chain"]:
            print(f"  LINE {last['lines']} DOES NOT MATCH the head recorded on {last['time']}: the log was rebuilt or replaced.")
            return 1
        print(f"  matches the head recorded on {last['time']} (line {last['lines']}); {v['lines'] - last['lines']} lines written since.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
