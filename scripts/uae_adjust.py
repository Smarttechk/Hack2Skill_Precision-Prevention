"""
Make the synthetic Synthea patients fit the UAE. Used by get_all_data.py. The raw files in data/raw are never changed;
the adjusted copy is written to data/processed/uae_synthea/.

  places    neighbourhoods, towns and villages of Abu Dhabi emirate (OpenStreetMap)       -> data/real/osm/
  calendar  Ramadan, Eid and UAE public holidays 2025-2027 (Aladhan API, Umm al-Qura)     -> data/real/calendar/
  adjust    1. homes in Abu Dhabi + real distance to the nearest clinic
            2. people context: nationality, language, insurance, phone, work shift, households and guardians
            3. missed well-child visits and vaccines, more often for families facing barriers
            4. hidden (undiagnosed) diabetes and hypertension so totals match WHO UAE figures, and UAE smoking rates
            5. drop the few records Synthea dates in the future

Every number below marked ASSUMPTION is a simulation choice: list it in the technical document.
"""
import json, os, sys, urllib.parse, urllib.request
from datetime import date, datetime

import numpy as np

UA = {"User-Agent": "hack2skill-data-setup/1.0"}
# every assumption lives in config/synthetic_data.json with its status and source
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "synthetic_data.json"),
          encoding="utf-8") as _fh:
    S = {k: v["value"] for k, v in json.load(_fh)["values"].items()}
SEED = S["adjust_seed"]
REGION_SHARE = S["region_share"]
UNDIAGNOSED_SHARE = S["undiagnosed_share"]
SMOKING = S["smoking_rate"]
MCV2_TARGET = S["mcv2_target"]
NATIONALITY = {k: (list(v), list(v.values())) for k, v in S["nationality_mix"].items()}
LANGUAGE = {k: (list(v), list(v.values())) for k, v in S["language_by_nationality"].items()}


def _mix(d):
    return list(d), list(d.values())

def _get(url, data=None, timeout=240):
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- places
PLACE_QUERY = """[out:json][timeout:180];area["ISO3166-2"="AE-AZ"]->.ad;
nwr["place"~"^(city|town|suburb|quarter|neighbourhood|village|hamlet)$"](area.ad);out center tags;"""


def region_of(lat, lon):
    lat, lon = np.asarray(lat), np.asarray(lon)
    b = S["region_boundaries"]
    return np.where(lon < b["al_dhafra_west_of_lon"], "Al Dhafra",
                    np.where((lon > b["al_ain_east_of_lon"]) & (lat < b["al_ain_south_of_lat"]), "Al Ain", "Abu Dhabi"))


def step_places(ctx):
    import pandas as pd
    out = os.path.join(ctx["REAL"], "osm", "abu_dhabi_places.csv")
    if os.path.exists(out):
        ctx["log"]("places: already present, skipping")
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    el = json.loads(_get("https://overpass-api.de/api/interpreter",
                         urllib.parse.urlencode({"data": PLACE_QUERY}).encode()))["elements"]
    rows = []
    for e in el:
        t = e.get("tags", {})
        lat = e.get("lat", e.get("center", {}).get("lat"))
        lon = e.get("lon", e.get("center", {}).get("lon"))
        if lat is None or not t.get("name"):
            continue
        rows.append({"osm_id": e["id"], "name": t.get("name:en") or t["name"], "name_ar": t.get("name:ar", ""),
                     "place": t["place"], "population": t.get("population", ""), "lat": lat, "lon": lon})
    p = pd.DataFrame(rows).drop_duplicates(["name", "place"])
    p["region"] = region_of(p.lat, p.lon)
    p.to_csv(out, index=False, encoding="utf-8-sig")
    ctx["log"](f"places: {len(p)} named places ({', '.join(f'{k} {v}' for k, v in p.region.value_counts().items())}) "
               f"-> {os.path.relpath(out, ctx['ROOT'])} (c) OpenStreetMap contributors, ODbL")


# ---------------------------------------------------------------- calendar
def _md(d):
    return {tuple(int(x) for x in k.split("-")): v for k, v in d.items()}


FIXED_HOLIDAYS = _md(S["uae_fixed_holidays"])
HIJRI_HOLIDAYS = _md(S["uae_hijri_holidays"])


def step_calendar(ctx):
    import pandas as pd
    out = os.path.join(ctx["REAL"], "calendar", "uae_calendar_2025_2027.csv")
    if os.path.exists(out):
        ctx["log"]("calendar: already present, skipping")
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    rows = []
    for year in S["calendar_years"]:
        for month in range(1, 13):
            data = json.loads(_get(f"https://api.aladhan.com/v1/gToHCalendar/{month}/{year}?calendarMethod=UAQ"))["data"]
            for d in data:
                g = datetime.strptime(d["gregorian"]["date"], "%d-%m-%Y").date()
                hm, hd = d["hijri"]["month"]["number"], int(d["hijri"]["day"])
                holiday = FIXED_HOLIDAYS.get((g.month, g.day)) or HIJRI_HOLIDAYS.get((hm, hd), "")
                rows.append({"date": g.isoformat(), "weekday": g.strftime("%A"), "hijri": f"{hd} {d['hijri']['month']['en']} {d['hijri']['year']}",
                             "is_ramadan": hm == S["ramadan_hijri_month"], "holiday": holiday,
                             "is_weekend": g.weekday() >= 5, "is_friday": g.weekday() == 4})
    c = pd.DataFrame(rows)
    c.to_csv(out, index=False, encoding="utf-8-sig")
    r = c[c.is_ramadan].groupby(c.date.str[:4]).date.agg(["min", "max"])
    ctx["log"](f"calendar: {len(c)} days; Ramadan " + "; ".join(f"{y}: {a} to {b}" for y, (a, b) in r.iterrows())
               + f" -> {os.path.relpath(out, ctx['ROOT'])} (Umm al-Qura; official dates can move one day with moon sighting)")


# ---------------------------------------------------------------- adjust
def _pick(rng, options, n):
    vals, p = options
    return rng.choice(vals, n, p=p)


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def access_barrier(df):
    """Same formula and weights as utils.access_barrier (Step 5.2 of the guide, 0 to 1), from config/real_values.json."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import utils
    return np.array([utils.access_barrier(r.language, r.insurance, r.work_shift, r.phone_type, r.travel_min)
                     for r in df.itertuples()])


def _filter_file(src, dst, drop_encounters, today, date_col, chunks=False):
    import pandas as pd
    first, kept, dropped = True, 0, 0
    reader = pd.read_csv(src, dtype=str, chunksize=1_000_000) if chunks else [pd.read_csv(src, dtype=str)]
    for ch in reader:
        d = pd.to_datetime(ch[date_col], utc=True, errors="coerce").dt.tz_localize(None)
        bad = (d > today)
        if "ENCOUNTER" in ch.columns:
            bad |= ch.ENCOUNTER.isin(drop_encounters)
        elif "Id" in ch.columns:                                        # encounters.csv itself
            bad |= ch.Id.isin(drop_encounters)
        ch[~bad].to_csv(dst, mode="w" if first else "a", header=first, index=False)
        first = False
        kept += int((~bad).sum()); dropped += int(bad.sum())
    return kept, dropped


def step_adjust(ctx):
    import pandas as pd
    RAW, REAL = ctx["RAW"], ctx["REAL"]
    OUT = os.path.join(ctx["DATA"], "processed", "uae_synthea")
    os.makedirs(OUT, exist_ok=True)
    rng = np.random.default_rng(SEED)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import utils
    today = utils.run_date()                  # fixed in config/real_values.json, so a rerun gives the same data
    log = ctx["log"]

    pts = pd.read_csv(os.path.join(RAW, "patients.csv"), dtype=str)
    pts["age"] = ((today - pd.to_datetime(pts.BIRTHDATE)).dt.days // 365.25).astype(int)
    alive = pts.DEATHDATE.isna()
    n = len(pts)

    # ---- 2. people context (done before homes so families share one home)
    adult_age = utils.config("clinical_rules")["adult_age"]
    working_men = (pts.GENDER == "M") & pts.age.between(*S["working_ages"])
    nat = np.empty(n, dtype=object)
    nat[working_men.values] = _pick(rng, NATIONALITY["working_men"], working_men.sum())
    nat[~working_men.values] = _pick(rng, NATIONALITY["others"], (~working_men).sum())
    pts["nationality"] = nat
    pts["language"] = [rng.choice(LANGUAGE[x][0], p=LANGUAGE[x][1]) for x in nat]
    # records imported from a hospital (scripts/fhir_import.py) may carry the real nationality group and language:
    # those replace the drawn ones (drawn first anyway, so a run without them gives exactly the same data)
    for src, col, known in (("NATIONALITY", "nationality", {g for v in S["nationality_mix"].values() for g in v}),
                            ("LANGUAGE", "language", {l for v in S["language_by_nationality"].values() for l in v})):
        if src in pts.columns:
            have = pts[src].isin(known)
            pts.loc[have, col] = pts.loc[have, src]
    adult = pts.age >= adult_age
    pts["work_shift"] = "none"
    wa = adult & pts.age.between(*S["working_ages"])
    south_asian_men = wa & (pts.GENDER == "M") & (pts.nationality == "South Asian")
    o, w_ = _mix(S["work_shift_mix"])
    pts.loc[wa, "work_shift"] = rng.choice(o, wa.sum(), p=w_)
    o, w_ = _mix(S["work_shift_mix_south_asian_men"])
    pts.loc[south_asian_men, "work_shift"] = rng.choice(o, south_asian_men.sum(), p=w_)
    (o1, w1), (o2, w2) = _mix(S["insurance_mix_day_shift"]), _mix(S["insurance_mix_others"])
    pts["insurance"] = np.where(pts.nationality == "Emirati", "Thiqa",
                                np.where(pts.work_shift == "day", rng.choice(o1, n, p=w1), rng.choice(o2, n, p=w2)))
    o, w_ = _mix(S["phone_mix"])
    pts["phone_type"] = rng.choice(o, n, p=w_)
    pts["can_read"] = rng.random(n) < S["can_read_share"]
    pts["smoker"] = (pts.age >= S["smoking_from_age"]) & (rng.random(n) < pts.GENDER.map(SMOKING).values)

    # households: each child gets a guardian (an adult aged 22-55 of the same nationality where possible)
    kids = pts.index[(pts.age < adult_age) & alive]
    cand = pts[alive & pts.age.between(*S["guardian_ages"])]
    pts["guardian_id"] = None
    by_nat = {k: g.Id.values for k, g in cand.groupby("nationality")}
    for i in kids:
        pool = by_nat.get(pts.at[i, "nationality"], cand.Id.values)
        pts.at[i, "guardian_id"] = rng.choice(pool)
    g = pts.set_index("Id")
    for col in ["nationality", "language", "insurance"]:              # a child takes these from the guardian
        pts.loc[kids, col] = pts.loc[kids, "guardian_id"].map(g[col]).values
    pts["household_id"] = np.where(pts.guardian_id.notna(), pts.guardian_id, pts.Id)

    # ---- 1. homes in Abu Dhabi and distance to the nearest clinic
    places = pd.read_csv(os.path.join(REAL, "osm", "abu_dhabi_places.csv"))
    fac = pd.read_csv(os.path.join(REAL, "osm", "abu_dhabi_health_facilities.csv"))
    w = places.place.map(S["place_weights"])
    w = w / w.groupby(places.region).transform("sum") * places.region.map(REGION_SHARE)   # each region gets its share
    adults_ids = pts.index[pts.guardian_id.isna()]
    home = rng.choice(places.index, len(adults_ids), p=(w / w.sum()).values)
    pts.loc[adults_ids, "home_place_idx"] = home
    pts.loc[kids, "home_place_idx"] = pts.loc[kids, "guardian_id"].map(pts.set_index("Id").home_place_idx).values
    pl = places.loc[pts.home_place_idx.astype(int)].reset_index(drop=True)
    pts["home_place"] = pl.name.values
    pts["region"] = pl.region.values
    jitter = rng.normal(0, S["home_jitter_degrees"], (n, 2))     # about 1 km, so neighbours are not stacked
    hh_jit = pd.DataFrame(jitter, index=pts.Id).loc[pts.household_id].values
    pts["home_lat"] = (pl.lat.values + hh_jit[:, 0]).round(5)
    pts["home_lon"] = (pl.lon.values + hh_jit[:, 1]).round(5)
    pts["area_type"] = np.where(pts.region == "Al Dhafra", "remote", np.where(pl.place.isin(["village", "hamlet"]).values, "rural", "city"))
    dist = _haversine_km(pts.home_lat.values[:, None], pts.home_lon.values[:, None], fac.lat.values[None, :], fac.lon.values[None, :])
    near = dist.argmin(axis=1)
    pts["nearest_facility"] = fac.name.fillna(fac.kind).values[near]
    pts["nearest_facility_km"] = dist.min(axis=1).round(1)
    pts["facilities_within_10km"] = (dist <= S["facilities_radius_km"]).sum(axis=1)
    speed = pts.area_type.map(S["road_speed_kmh"])
    pts["travel_min"] = (pts.nearest_facility_km * S["road_detour_factor"] / speed * 60 + S["travel_overhead_min"]).round(0)
    pts["access_barrier"] = access_barrier(pts)

    # ---- 3. missed well-child visits and vaccines
    enc = pd.read_csv(os.path.join(RAW, "encounters.csv"), dtype=str, usecols=["Id", "START", "PATIENT", "DESCRIPTION"])
    enc["START"] = pd.to_datetime(enc.START, utc=True).dt.tz_localize(None)
    wcv = enc[enc.DESCRIPTION.str.contains("Well child", na=False) & (enc.START <= today)].copy()
    imm = pd.read_csv(os.path.join(RAW, "immunizations.csv"), dtype=str, usecols=["PATIENT", "ENCOUNTER", "DESCRIPTION"])
    mmr = imm[imm.DESCRIPTION.str.contains("MMR", na=False)]
    MV = S["missed_visit_model"]
    fam_barrier = pts.set_index("Id").access_barrier
    wcv["barrier"] = wcv.PATIENT.map(pts.set_index("Id").guardian_id).map(fam_barrier).fillna(wcv.PATIENT.map(fam_barrier)).fillna(MV["default_barrier"]).values
    fam_effect = pd.Series(rng.normal(0, MV["family_sd"], n), index=pts.household_id.values)
    fam_effect = fam_effect[~fam_effect.index.duplicated()]
    wcv["family"] = wcv.PATIENT.map(pts.set_index("Id").household_id)
    u = rng.random(len(wcv))
    age_now = wcv.PATIENT.map(pts.set_index("Id").age)
    mmr_age = pts.set_index("Id").age

    def apply(k):
        p = np.clip(k * (MV["base"] + MV["barrier_slope"] * wcv.barrier.values) + wcv.family.map(fam_effect).fillna(0).values,
                    0, MV["max"])
        dropped = set(wcv.Id[u < p])
        left = mmr[~mmr.ENCOUNTER.isin(dropped)].groupby("PATIENT").size()
        # ages 5-9 only: Synthea exports about 10 years of history, so older children's doses are missing
        elig = mmr_age[mmr_age.between(*S["mmr_check_ages"]) & alive.values].index
        return dropped, float((left.reindex(elig).fillna(0) >= 2).mean())

    lo, hi = MV["search_range"]
    for _ in range(MV["search_steps"]):                            # find k so MMR second-dose coverage meets the target
        mid = (lo + hi) / 2
        _, cov = apply(mid)
        lo, hi = (mid, hi) if cov > MCV2_TARGET else (lo, mid)
    dropped, mcv2 = apply((lo + hi) / 2)

    # ---- 4. hidden (undiagnosed) diabetes and hypertension
    WHO = pd.read_csv(os.path.join(REAL, "who", "uae_who_indicators.csv"))
    WHO = WHO[(WHO.year <= today.year) & (WHO.sex.isna() | WHO.sex.astype(str).str.contains("BTSX"))]
    def who_val(code, age):
        x = WHO[(WHO.indicator == code) & (WHO.age_group.astype(str).str.contains(age) if age else True)].sort_values("year")
        return float(x.value.iloc[-1]) / 100
    pa = S["prevalence_ages"]
    target = {"diabetes": (who_val("NCD_DIABETES_PREVALENCE_AGESTD", "18-PLUS"), *pa["diabetes"]),
              "hypertension": (who_val("NCD_HYP_PREVALENCE_A", None), *pa["hypertension"])}
    cond = pd.read_csv(os.path.join(RAW, "conditions.csv"), dtype=str, usecols=["PATIENT", "STOP", "DESCRIPTION"])
    diagnosed = {"diabetes": set(cond[cond.DESCRIPTION.str.contains(r"^Diabetes mellitus type 2|^Diabetes \(", na=False) & cond.STOP.isna()].PATIENT),
                 "hypertension": set(cond[cond.DESCRIPTION.str.contains("[Hh]ypertension", na=False) & cond.STOP.isna()].PATIENT)}
    last_test = {}
    want = {"4548-4": "diabetes", "8480-6": "hypertension"}
    bmi_rows = []
    for ch in pd.read_csv(os.path.join(RAW, "observations.csv"), dtype=str, usecols=["DATE", "PATIENT", "CODE", "VALUE"], chunksize=1_000_000):
        bmi_rows.append(ch[ch.CODE == "39156-5"])
        ch = ch[ch.CODE.isin(want)]
        for code, gg in ch.groupby("CODE"):
            mx = gg.groupby("PATIENT").DATE.max()
            k = want[code]
            last_test[k] = mx if k not in last_test else pd.concat([last_test[k], mx]).groupby(level=0).max()
    # who becomes a hidden case is weighted by real risk factors: latest real BMI, prediabetes, age, smoking
    b = pd.concat(bmi_rows).sort_values("DATE").groupby("PATIENT").tail(1).set_index("PATIENT").VALUE
    HW = S["hidden_case_weights"]
    bmi = pd.to_numeric(b, errors="coerce").reindex(pts.Id).fillna(HW["default_bmi"])
    prediab = pts.Id.isin(cond[cond.DESCRIPTION.str.contains("Prediabetes", na=False) & cond.STOP.isna()].PATIENT)
    prediab = pd.Series(prediab.values, index=pts.Id)
    smoke = pd.Series(pts.smoker.values, index=pts.Id)
    truth = pd.DataFrame({"PATIENT": pts.Id})
    achieved = {}
    for disease, (tgt, a0, a1) in target.items():
        elig = pts[alive & pts.age.between(a0, a1)]
        diag = elig.Id.isin(diagnosed[disease])
        want_hidden = int(round(min(tgt - diag.mean(), UNDIAGNOSED_SHARE[disease] * tgt) * len(elig)))
        lt = pd.to_datetime(last_test[disease].reindex(elig.Id), utc=True).dt.tz_localize(None)
        not_recent = (lt.isna() | ((today - lt).dt.days > S["recent_normal_test_days"])).values   # a recent normal test rules it out
        pool = elig[~diag.values & not_recent]
        ids = pool.Id.values
        w = HW[disease]
        z = (w["age_per_year"] * (pool.age.values - HW["age_centre"]) + w["bmi_per_unit"] * (bmi[ids].values - HW["bmi_centre"])
             + w["male"] * (pool.GENDER.values == "M") + w["prediabetes"] * prediab[ids].values.astype(float)
             + w["smoking"] * smoke[ids].fillna(0).values.astype(float)
             + HW["ancestry"] * pool.nationality.isin(HW["ancestry_groups"]).values)
        # a case is chosen with odds that grow as exp(z), the way the log-odds from the real data are defined
        # (a sigmoid here would flatten every risk factor to about a third of its real effect)
        p = np.exp(z - (z.max() if len(z) else 0)); p = p / p.sum() if len(z) else p
        chosen = rng.choice(pool.Id.values, min(want_hidden, len(pool)), replace=False, p=p) if want_hidden > 0 else []
        truth[f"diagnosed_{disease}"] = truth.PATIENT.isin(diagnosed[disease])
        truth[f"hidden_{disease}"] = truth.PATIENT.isin(chosen)
        back = pd.Timedelta(days=S["hidden_onset_years"] * 365)
        onset_lo = lt.reindex(chosen).fillna(today - back).clip(lower=today - back)
        span = (today - onset_lo).dt.days.clip(lower=1).values
        onset = onset_lo + pd.to_timedelta((rng.random(len(chosen)) * span).astype(int), unit="D")
        truth[f"{disease}_onset"] = truth.PATIENT.map(pd.Series(onset.dt.date.astype(str).values, index=list(chosen)))
        achieved[disease] = (float(elig.Id.isin(diagnosed[disease]).mean()), float(elig.Id.isin(chosen).mean()), tgt, len(pool) < want_hidden)
    hd = truth.hidden_diabetes.values
    HV = S["hidden_values"]
    truth["hba1c_if_tested_now"] = np.where(hd, (HV["hba1c_from"] + rng.exponential(HV["hba1c_spread"], n)).round(1), np.nan)
    hh = truth.hidden_hypertension.values
    truth["systolic_if_measured_now"] = np.where(hh, (HV["systolic_from"] + rng.exponential(HV["systolic_spread"], n)).round(0), np.nan)

    # ---- write
    pts.drop(columns=["home_place_idx"]).to_csv(os.path.join(OUT, "patients.csv"), index=False)
    truth.to_csv(os.path.join(OUT, "truth_hidden.csv"), index=False)
    stats = {}
    for fname, col, big in [("encounters", "START", False), ("immunizations", "DATE", False), ("procedures", "START", False),
                            ("conditions", "START", False), ("observations", "DATE", True)]:
        stats[fname] = _filter_file(os.path.join(RAW, f"{fname}.csv"), os.path.join(OUT, f"{fname}.csv"), dropped, today, col, big)

    # ---- summary
    a = pts[alive]
    k6 = a[a.age < 6].Id
    enc_left = pd.read_csv(os.path.join(OUT, "encounters.csv"), dtype=str, usecols=["PATIENT", "START", "DESCRIPTION"])
    enc_left["START"] = pd.to_datetime(enc_left.START, utc=True).dt.tz_localize(None)
    recent_wcv = enc_left[enc_left.DESCRIPTION.str.contains("Well child", na=False) & (enc_left.START > today - pd.Timedelta(days=365))]
    summary = {
        "people_alive": int(alive.sum()),
        "region_share": a.region.value_counts(normalize=True).round(3).to_dict(),
        "area_type_share": a.area_type.value_counts(normalize=True).round(3).to_dict(),
        "median_km_to_clinic": {k: float(v) for k, v in a.groupby("region").nearest_facility_km.median().round(1).items()},
        "median_travel_min": {k: float(v) for k, v in a.groupby("region").travel_min.median().items()},
        "nationality_share": a.nationality.value_counts(normalize=True).round(3).to_dict(),
        "children_with_guardian": f"{a[a.age < 18].guardian_id.notna().mean():.0%}",
        "well_child_visits_removed": len(dropped),
        "under6_with_well_child_visit_last_12m": f"{k6.isin(recent_wcv.PATIENT).mean():.0%}",
        "mmr_2_doses_age_5_9": f"{mcv2:.1%} (target {MCV2_TARGET:.0%}; before adjustment {apply(0.0)[1]:.1%})",
        "well_child_visit_drop_multiplier": round((lo + hi) / 2, 3),
        "smokers_15plus": f"{a[a.age >= 15].smoker.mean():.1%} (WHO 8.8%)",
        "records_dropped_future_or_missed": {k: v[1] for k, v in stats.items()},
    }
    for d, (diag_rate, hid_rate, tgt, short) in achieved.items():
        summary[f"{d}_diagnosed_hidden_total_vs_WHO"] = (f"{diag_rate:.1%} + {hid_rate:.1%} hidden = {diag_rate + hid_rate:.1%} "
                                                       f"(WHO {tgt:.1%}; hidden capped at {UNDIAGNOSED_SHARE[d]:.0%} of WHO)"
                                                       + (" NOT ENOUGH ELIGIBLE PEOPLE" if short else ""))
    json.dump(summary, open(os.path.join(OUT, "adjust_summary.json"), "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    for k, v in summary.items():
        log(f"adjust: {k}: {v}")
    log(f"adjust: written to {os.path.relpath(OUT, ctx['ROOT'])} (truth_hidden.csv is for the simulation only: never a model feature)")
    # family history, genetic results and consent decisions: the import's own rows, or written for the test patients
    import clinical_records
    clinical_records.write(RAW, OUT, log=lambda m: log(f"adjust: {m}"))


ADJUST_STEPS = {"places": step_places, "calendar": step_calendar, "adjust": step_adjust}
