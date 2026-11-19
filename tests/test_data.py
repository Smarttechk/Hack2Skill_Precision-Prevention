"""
Checks on the downloaded and adjusted data. Run after scripts/get_all_data.py:
    python tests/test_data.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "data")
P = os.path.join(D, "processed", "uae_synthea")
ok = []


def check(name, cond):
    ok.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)


# ---- real data
for f, n in [("nhanes/nhanes_2017_2018_joined.csv", 9254), ("nhanes_2015/nhanes_2015_joined.csv", 9971),
             ("nhanes_2021/nhanes_2021_joined.csv", 11933)]:
    t = pd.read_csv(os.path.join(D, "real", f), usecols=["SEQN"])
    check(f"NHANES {f.split('/')[0]}: {len(t)} people equals the official CDC count {n}", len(t) == n and t.SEQN.is_unique)
tb = pd.read_csv(os.path.join(D, "real", "worldbank", "uae_target_bands.csv"))
check("World Bank: project bands add up to 100%", abs(tb.share_of_population.sum() - 1) < 1e-3)
who = pd.read_csv(os.path.join(D, "real", "who", "uae_who_indicators.csv"))
check("WHO: 11 UAE indicators present", who.indicator.nunique() == 11)
fac = pd.read_csv(os.path.join(D, "real", "osm", "abu_dhabi_health_facilities.csv"))
check(f"OSM: {len(fac)} facilities, all inside Abu Dhabi emirate", len(fac) > 300 and (fac.lat.between(22.6, 24.9) & fac.lon.between(51.5, 56.1)).all())
cal = pd.read_csv(os.path.join(D, "real", "calendar", "uae_calendar_2025_2027.csv"))
check("Calendar: 3 full years, about 30 Ramadan days a year", len(cal) == 1095 and cal[cal.is_ramadan].groupby(cal.date.str[:4]).size().between(29, 30).all())
check("Calendar: Eid al-Fitr 2026 on 20 March", (cal[(cal.holiday == "Eid al-Fitr")].date == "2026-03-20").any())

# ---- adjusted synthetic data
pts = pd.read_csv(os.path.join(P, "patients.csv"), dtype={"Id": str})
alive = pts[pts.DEATHDATE.isna()]
kids = alive[alive.age < 18]
g = pts.set_index("Id")
check(f"Adjusted: {len(alive)} living people", len(alive) == 5000)
check("Adjusted: every child has an adult guardian aged 22-55", kids.guardian_id.notna().all() and g.loc[kids.guardian_id].age.between(22, 55).all())
check("Adjusted: a child shares the guardian's home, language and insurance",
      (kids.home_place.values == g.loc[kids.guardian_id].home_place.values).all()
      and (kids.language.values == g.loc[kids.guardian_id].language.values).all())
check("Adjusted: every Emirati has Thiqa", (pts[pts.nationality == "Emirati"].insurance == "Thiqa").all())
check("Adjusted: no smokers under 15, no workers under 18", not (pts.smoker & (pts.age < 15)).any() and (pts[pts.age < 18].work_shift == "none").all())
check("Adjusted: homes inside Abu Dhabi emirate", (pts.home_lat.between(22.5, 25.0) & pts.home_lon.between(51.4, 56.2)).all())
med = alive.groupby("region").travel_min.median()
check(f"Adjusted: remote Al Dhafra has the longest travel ({med.to_dict()})", med.idxmax() == "Al Dhafra")
check("Adjusted: access barrier between 0 and 1", pts.access_barrier.between(0, 1).all())
today = pd.Timestamp.today()
for f, c in [("encounters", "START"), ("immunizations", "DATE"), ("procedures", "START")]:
    d = pd.to_datetime(pd.read_csv(os.path.join(P, f + ".csv"), usecols=[c])[c], utc=True).dt.tz_localize(None)
    raw_n = sum(1 for _ in open(os.path.join(D, "raw", f + ".csv"), encoding="utf-8")) - 1
    check(f"Adjusted {f}: no future dates, fewer rows than raw ({len(d)} < {raw_n})", (d <= today + pd.Timedelta(days=1)).all() and len(d) < raw_n)
s = json.load(open(os.path.join(P, "adjust_summary.json"), encoding="utf-8"))
mcv2 = float(s["mmr_2_doses_age_5_9"].split("%")[0])
check(f"Adjusted: second MMR dose {mcv2}% is close to the WHO 92%", 89 <= mcv2 <= 95)
t = pd.read_csv(os.path.join(P, "truth_hidden.csv"))
check("Truth: hidden cases are never already diagnosed",
      not (t.hidden_diabetes & t.diagnosed_diabetes).any() and not (t.hidden_hypertension & t.diagnosed_hypertension).any())
check("Truth: hidden diabetes has HbA1c 6.5 or more; hidden hypertension has systolic 140 or more",
      (t.loc[t.hidden_diabetes, "hba1c_if_tested_now"] >= 6.5).all() and (t.loc[t.hidden_hypertension, "systolic_if_measured_now"] >= 140).all())
check("Truth: onset dates are in the past", (pd.to_datetime(t.diabetes_onset.dropna()) <= today).all())
ad = alive[alive.age.between(30, 79)]
total = t.set_index("PATIENT").loc[ad.Id, ["diagnosed_hypertension", "hidden_hypertension"]].any(axis=1).mean()
check(f"Truth: hypertension 30-79 total {total:.1%} matches WHO 41.4%", abs(total - 0.414) < 0.02)
check("Raw files untouched: data/raw/patients.csv has no added columns",
      "nationality" not in pd.read_csv(os.path.join(D, "raw", "patients.csv"), nrows=0).columns)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
