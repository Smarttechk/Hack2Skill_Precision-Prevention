"""
Calibrates the two simulation numbers that have a published target but no direct value:

* base_attendance_logit: tuned until usual care misses 20.8% of booked visits
  (Emirates Health Services primary care, 2022).
* stop_share: the share of replies that are STOP, tuned until 2.5% of contacted people opt out
  (Kaiser Permanente, JAMA Network Open 2021).

Both are tuned on the usual-care run, because the published figures describe usual care.
Writes config/calibrated.json, which simulation.py reads.

    python scripts/calibrate.py
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import orchestrator   # noqa: E402
import simulation     # noqa: E402

OUT = os.path.join(os.path.dirname(simulation.__file__), "config", "calibrated.json")
B = simulation.B


def logit(p):
    return math.log(p / (1 - p))


CAL, SIM = simulation.DESIGN["calibration"], simulation.DESIGN["simulation"]


def measure(params, seeds=tuple(CAL["seeds"])):
    att, booked, opted, contacted = 0, 0, 0, 0
    for s in seeds:
        ctx = orchestrator.run("old", s, SIM["days"], params=params)
        att += sum(i["attended"] for i in ctx.invites)
        booked += len(ctx.invites)
        opted += sum(p.state == "Opted out" for p in ctx.people.values())
        contacted += sum(p.first_invite_day is not None for p in ctx.people.values())
    return att / max(booked, 1), opted / max(contacted, 1), booked


def main():
    target_show = 1 - B["no_show_rate"]
    target_opt = B["text_opt_out_rate"]
    params = dict(simulation.DEFAULT_PARAMS)
    show, opt, n = measure(params)
    for step in range(CAL["max_rounds"]):
        print(f"  round {step + 1}: logit {params['base_attendance_logit']:.3f}, STOP share {params['stop_share']:.4f} -> "
              f"attended {show:.3f} (target {target_show:.3f}), opted out {opt:.4f} (target {target_opt:.4f}), {n} visits")
        if abs(show - target_show) < CAL["attendance_tolerance"] and abs(opt - target_opt) < CAL["opt_out_tolerance"]:
            break
        damp = 1.0 if step == 0 else CAL["damping_after_first"]   # smaller steps after the first, so noise does not make it swing
        params["base_attendance_logit"] += damp * (logit(target_show) - logit(min(max(show, 0.01), 0.99)))
        params["stop_share"] = min(CAL["max_stop_share"], params["stop_share"] * (target_opt / max(opt, 1e-4)) ** damp)
        show, opt, n = measure(params)
    result = {
        "base_attendance_logit": {"value": round(params["base_attendance_logit"], 3), "status": "calibrated",
                                  "target": f"usual care attends {target_show:.1%} of booked visits", "achieved": round(show, 3),
                                  "source": REAL_SRC("no_show_rate")},
        "stop_share": {"value": round(params["stop_share"], 4), "status": "calibrated",
                       "target": f"{target_opt:.1%} of contacted people opt out", "achieved": round(opt, 4),
                       "source": REAL_SRC("text_opt_out_rate")},
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Saved {OUT}: attendance {show:.3f}, opt-out {opt:.4f}")


def REAL_SRC(key):
    v = simulation.REAL["behaviour"][key]
    return {"text": v["source"], "url": v.get("url")}


if __name__ == "__main__":
    main()
