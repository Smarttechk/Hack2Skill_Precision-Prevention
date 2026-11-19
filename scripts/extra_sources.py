"""
Extra public data sources, used by get_all_data.py. All free, no account needed.

  worldbank  UAE population by age and sex (World Bank API) -> real TARGET shares for Step 1.2d
  who        UAE health indicators (WHO Global Health Observatory API): diabetes, obesity, blood pressure,
             smoking, vaccination coverage -> "problem in numbers" and realism checks
  nhanes2    two more NHANES cycles (2015-2016 and 2021-2023) -> time-based test (Step 4.9) and a bigger sample
  growth     WHO child growth standards (BMI-for-age) and CDC BMI-for-age tables -> child growth percentiles
  geo        UAE boundary map files (geoBoundaries) -> map page of the dashboard
  clinics    hospitals, clinics and doctors in Abu Dhabi emirate (OpenStreetMap) -> real travel times, map
"""
import json, os, urllib.parse, urllib.request

UA = {"User-Agent": "hack2skill-data-setup/1.0"}


def _get(url, data=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_json(url, **kw):
    return json.loads(_get(url, **kw).decode("utf-8"))


# ------------------------------------------------------------------ World Bank: population by age and sex
WB_BANDS = ["0004", "0509", "1014", "1519", "2024", "2529", "3034", "3539", "4044",
            "4549", "5054", "5559", "6064", "6569", "7074", "7579", "80UP"]


def step_worldbank(ctx):
    import pandas as pd
    folder = os.path.join(ctx["REAL"], "worldbank")
    os.makedirs(folder, exist_ok=True)
    out_csv = os.path.join(folder, "uae_population_age_sex.csv")
    if os.path.exists(out_csv):
        ctx["log"]("worldbank: already present, skipping")
    else:
        rows = []
        codes = {"SP.POP.TOTL": ("total", "all", "people"), "SP.POP.TOTL.MA.ZS": ("male", "all", "pct_of_total")}
        for sex, s in (("male", "MA"), ("female", "FE")):
            for b in WB_BANDS:
                codes[f"SP.POP.{b}.{s}.5Y"] = (sex, b, "pct_of_sex")
        for code, (sex, band, unit) in codes.items():
            url = f"https://api.worldbank.org/v2/country/ARE/indicator/{code}?format=json&date=2015:2024&per_page=100"
            data = _get_json(url)
            for r in (data[1] if len(data) > 1 and data[1] else []):
                if r["value"] is not None:
                    rows.append({"indicator": code, "name": r["indicator"]["value"], "year": int(r["date"]),
                                 "sex": sex, "age_band": band, "unit": unit, "value": r["value"]})
        pd.DataFrame(rows).sort_values(["indicator", "year"]).to_csv(out_csv, index=False)
        ctx["log"](f"worldbank: {len(rows)} values -> {os.path.relpath(out_csv, ctx['ROOT'])}")

    # project bands (0-17, 18-44, 45-64, 65+) x sex, as shares of the whole population
    t = pd.read_csv(out_csv)
    year = int(t[t.unit == "pct_of_sex"].year.max())
    male_share = t[(t.indicator == "SP.POP.TOTL.MA.ZS") & (t.year == year)].value.iloc[0] / 100
    rows = []
    for sex, sx, share in (("male", "M", male_share), ("female", "F", 1 - male_share)):
        p = t[(t.year == year) & (t.sex == sex) & (t.unit == "pct_of_sex")].set_index("age_band").value / 100
        bands = {   # 15-19 is split 3/5 to children (15, 16, 17) and 2/5 to adults (18, 19)
            "0-17": p["0004"] + p["0509"] + p["1014"] + 0.6 * p["1519"],
            "18-44": 0.4 * p["1519"] + p["2024"] + p["2529"] + p["3034"] + p["3539"] + p["4044"],
            "45-64": p["4549"] + p["5054"] + p["5559"] + p["6064"],
            "65+": p["6569"] + p["7074"] + p["7579"] + p["80UP"],
        }
        for band, v in bands.items():
            rows.append({"year": year, "age_band": band, "sex": sx, "share_of_population": round(v * share, 4)})
    target = pd.DataFrame(rows)
    target["share_of_population"] = (target.share_of_population / target.share_of_population.sum()).round(4)
    target["synthea_people_per_5000"] = (target.share_of_population * 5000).round().astype(int)
    target.to_csv(os.path.join(folder, "uae_target_bands.csv"), index=False)
    ctx["log"](f"worldbank: real UAE shares for Step 1.2d ({year}) -> data/real/worldbank/uae_target_bands.csv")


# ------------------------------------------------------------------ WHO Global Health Observatory
WHO_INDICATORS = [   # code: plain name (codes that return no UAE data are skipped and reported)
    "NCD_DIABETES_PREVALENCE_AGESTD",   # diabetes, age-standardised
    "NCD_BMI_30A",                      # obesity in adults (BMI 30+)
    "NCD_BMI_PLUS2C",                   # obesity in children and adolescents 5-19
    "NCD_HYP_PREVALENCE_A",             # hypertension, age-standardised
    "M_Est_smk_curr_std",               # current tobacco smoking, age-standardised
    "NCD_PAA",                          # insufficient physical activity, adults
    "WHS8_110",                         # measles (MCV1) immunisation coverage, 1-year-olds
    "MCV2",                             # measles second dose coverage
    "WHS4_100",                         # DTP3 immunisation coverage
    "WHS4_117",                         # HepB3 immunisation coverage
    "NCDMORT3070",                      # probability of dying 30-70 from the 4 main NCDs
]


def step_who(ctx):
    import pandas as pd
    folder = os.path.join(ctx["REAL"], "who")
    os.makedirs(folder, exist_ok=True)
    out_csv = os.path.join(folder, "uae_who_indicators.csv")
    if os.path.exists(out_csv):
        ctx["log"]("who: already present, skipping")
        return
    rows, missing = [], []
    flt = urllib.parse.quote("SpatialDim eq 'ARE'")
    for code in WHO_INDICATORS:
        try:
            name = _get_json(f"https://ghoapi.azureedge.net/api/Indicator?$filter="
                             + urllib.parse.quote(f"IndicatorCode eq '{code}'"))["value"]
            name = name[0]["IndicatorName"] if name else code
            vals = _get_json(f"https://ghoapi.azureedge.net/api/{code}?$filter={flt}")["value"]
        except Exception as e:
            missing.append(f"{code} ({e})")
            continue
        if not vals:
            missing.append(code)
            continue
        for v in vals:
            rows.append({"indicator": code, "name": name, "year": v.get("TimeDim"),
                         "sex": v.get("Dim1"), "age_group": v.get("Dim2"), "value": v.get("NumericValue"),
                         "low": v.get("Low"), "high": v.get("High")})
    pd.DataFrame(rows).sort_values(["indicator", "year"]).to_csv(out_csv, index=False)
    ctx["log"](f"who: {len(rows)} values for {len(WHO_INDICATORS) - len(missing)} indicators -> "
               f"{os.path.relpath(out_csv, ctx['ROOT'])}" + (f"; no UAE data: {', '.join(missing)}" if missing else ""))


# ------------------------------------------------------------------ more NHANES cycles
NHANES_CYCLES = {   # folder, start year, file suffix, files (blood pressure changed method in 2021)
    "nhanes_2015": ("2015", "I", ["DEMO", "BMX", "BPX", "GHB", "DIQ", "MCQ", "SMQ", "PAQ", "TCHOL", "HDL", "BPQ", "HIQ"]),
    "nhanes_2021": ("2021", "L", ["DEMO", "BMX", "BPXO", "GHB", "DIQ", "MCQ", "SMQ", "PAQ", "TCHOL", "HDL", "BPQ", "HIQ"]),
}


def step_nhanes2(ctx):
    import pandas as pd
    for folder_name, (year, sfx, files) in NHANES_CYCLES.items():
        folder = os.path.join(ctx["REAL"], folder_name)
        got = []
        for f in files:
            name = f"{f}_{sfx}"
            path = os.path.join(folder, name + ".xpt")
            try:
                ctx["download"](f"https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/{year}/DataFiles/{name}.xpt",
                                path, f"{folder_name} {name}")
                got.append(name)
            except Exception as e:
                ctx["log"](f"{folder_name} {name}: not available ({e}); skipped")
        parts = []
        for name in got:
            d = pd.read_sas(os.path.join(folder, name + ".xpt"), format="xport")
            d.columns = d.columns.str.upper()
            d = d.drop_duplicates("SEQN").set_index("SEQN")
            seen = set().union(*[p.columns for p in parts]) if parts else set()
            parts.append(d.drop(columns=[c for c in d.columns if c in seen]))
        joined = parts[0].join(parts[1:], how="left").copy()
        if "BPXSY1" not in joined.columns and "BPXOSY1" in joined.columns:
            joined["BPXSY1"] = joined["BPXOSY1"]      # 2021+: machine reading used in place of the manual one
            joined["BP_METHOD"] = "oscillometric"
        joined["CYCLE"] = f"{year}-{int(year) + 1 if sfx != 'L' else 2023}"
        out = os.path.join(folder, f"{folder_name}_joined.csv")
        joined.to_csv(out)
        ctx["log"](f"{folder_name}: {joined.shape[0]} people x {joined.shape[1]} columns -> "
                   f"{os.path.relpath(out, ctx['ROOT'])}")


# ------------------------------------------------------------------ child growth references
GROWTH_FILES = {   # file name: url
    "who_bmi_for_age_boys_0_5_zscores.xlsx": "https://cdn.who.int/media/docs/default-source/child-growth/child-growth-standards/indicators/body-mass-index-for-age/expanded-tables/bfa-boys-zscore-expanded-tables.xlsx",
    "who_bmi_for_age_girls_0_5_zscores.xlsx": "https://cdn.who.int/media/docs/default-source/child-growth/child-growth-standards/indicators/body-mass-index-for-age/expanded-tables/bfa-girls-zscore-expanded-tables.xlsx",
    "who_bmi_for_age_boys_5_19_zscores.xlsx": "https://cdn.who.int/media/docs/default-source/child-growth/growth-reference-5-19-years/bmi-for-age-(5-19-years)/bmi-boys-z-who-2007-exp.xlsx",
    "who_bmi_for_age_girls_5_19_zscores.xlsx": "https://cdn.who.int/media/docs/default-source/child-growth/growth-reference-5-19-years/bmi-for-age-(5-19-years)/bmi-girls-z-who-2007-exp.xlsx",
}   # (CDC's US growth tables refuse automated downloads; WHO is the international reference anyway)


def step_growth(ctx):
    folder = os.path.join(ctx["REAL"], "growth")
    ok, failed = [], []
    for fname, url in GROWTH_FILES.items():
        try:
            ctx["download"](url, os.path.join(folder, fname), f"growth {fname}")
            ok.append(fname)
        except Exception as e:
            failed.append(f"{fname} ({e})")
    ctx["log"](f"growth: {len(ok)} of {len(GROWTH_FILES)} files" + (f"; failed: {'; '.join(failed)}" if failed else ""))


# ------------------------------------------------------------------ UAE boundaries
def step_geo(ctx):
    folder = os.path.join(ctx["REAL"], "geo")
    for level in ("ADM1",):          # the 7 emirates (geoBoundaries has no finer open level for the UAE)
        path = os.path.join(folder, f"uae_{level.lower()}.geojson")
        if os.path.exists(path):
            ctx["log"](f"geo {level}: already present, skipping")
            continue
        try:
            meta = _get_json(f"https://www.geoboundaries.org/api/current/gbOpen/ARE/{level}/")
            url = meta.get("simplifiedGeometryGeoJSON") or meta.get("gjDownloadURL")
            ctx["download"](url, path, f"geo {level} ({meta.get('boundaryType')}, source {meta.get('boundarySource')}, "
                                       f"licence {meta.get('boundaryLicense')})")
        except Exception as e:
            ctx["log"](f"geo {level}: not available ({e})")


# ------------------------------------------------------------------ OpenStreetMap health facilities
OVERPASS = "https://overpass-api.de/api/interpreter"
CLINIC_QUERY = """
[out:json][timeout:180];
area["ISO3166-2"="AE-AZ"]->.ad;
(
  nwr["amenity"~"^(hospital|clinic|doctors)$"](area.ad);
  nwr["healthcare"~"^(hospital|clinic|doctor|centre)$"](area.ad);
);
out center tags;
"""


def step_clinics(ctx):
    import pandas as pd
    folder = os.path.join(ctx["REAL"], "osm")
    os.makedirs(folder, exist_ok=True)
    out_csv = os.path.join(folder, "abu_dhabi_health_facilities.csv")
    if os.path.exists(out_csv):
        ctx["log"]("clinics: already present, skipping")
        return
    raw = _get(OVERPASS, data=urllib.parse.urlencode({"data": CLINIC_QUERY}).encode(), timeout=240)
    open(os.path.join(folder, "abu_dhabi_health_facilities_raw.json"), "wb").write(raw)
    rows = []
    for e in json.loads(raw)["elements"]:
        tags = e.get("tags", {})
        lat = e.get("lat", e.get("center", {}).get("lat"))
        lon = e.get("lon", e.get("center", {}).get("lon"))
        if lat is None:
            continue
        rows.append({"osm_type": e["type"], "osm_id": e["id"], "name": tags.get("name", ""),
                     "name_ar": tags.get("name:ar", ""), "name_en": tags.get("name:en", ""),
                     "amenity": tags.get("amenity", ""), "healthcare": tags.get("healthcare", ""),
                     "speciality": tags.get("healthcare:speciality", ""), "operator": tags.get("operator", ""),
                     "city": tags.get("addr:city", ""), "lat": lat, "lon": lon})
    t = pd.DataFrame(rows).drop_duplicates(["osm_type", "osm_id"])
    t["kind"] = t.amenity.where(t.amenity != "", t.healthcare)
    t.to_csv(out_csv, index=False, encoding="utf-8-sig")
    ctx["log"](f"clinics: {len(t)} health facilities in Abu Dhabi emirate "
               f"({', '.join(f'{k} {v}' for k, v in t.kind.value_counts().items())}) -> "
               f"{os.path.relpath(out_csv, ctx['ROOT'])} (data (c) OpenStreetMap contributors, ODbL)")


EXTRA_STEPS = {"worldbank": step_worldbank, "who": step_who, "nhanes2": step_nhanes2,
               "growth": step_growth, "geo": step_geo, "clinics": step_clinics}


def extra_report(ctx):
    """Lines for DATA_REPORT.md."""
    import pandas as pd
    R, out = ctx["REAL"], []
    p = os.path.join(R, "worldbank", "uae_target_bands.csv")
    if os.path.exists(p):
        t = pd.read_csv(p)
        out += ["", f"## UAE population by age and sex (World Bank, {t.year.iloc[0]}, real)",
                "| Band | Sex | Share | Synthea people per 5,000 |", "|---|---|---|---|"]
        out += [f"| {r.age_band} | {r.sex} | {r.share_of_population:.1%} | {r.synthea_people_per_5000} |" for r in t.itertuples()]
    p = os.path.join(R, "who", "uae_who_indicators.csv")
    if os.path.exists(p):
        from datetime import date
        t = pd.read_csv(p)
        t = t[t.year <= date.today().year]                      # WHO also publishes projections; leave them out
        t = t[t.sex.isna() | t.sex.astype(str).str.contains("BTSX")]
        latest = t.sort_values("year").groupby(["indicator", "age_group"], dropna=False).tail(1)
        out += ["", "## UAE health indicators (WHO, real; latest measured year, both sexes; projections left out)",
                "| Indicator | Age group | Year | Value | Range |", "|---|---|---|---|---|"]
        for r in latest.sort_values(["indicator", "age_group"]).itertuples():
            age = str(r.age_group).replace("AGEGROUP_", "").replace("YEARS", "").replace("-PLUS", "+") if pd.notna(r.age_group) else "-"
            rng = f"{r.low:.1f} to {r.high:.1f}" if pd.notna(r.low) and pd.notna(r.high) else ""
            out.append(f"| {r.name} | {age} | {int(r.year)} | {r.value:.1f} | {rng} |")
    for f in ("nhanes_2015", "nhanes_2021"):
        p = os.path.join(R, f, f"{f}_joined.csv")
        if os.path.exists(p):
            n = pd.read_csv(p, usecols=lambda c: c in {"SEQN", "RIDAGEYR", "LBXGH", "DIQ010"})
            a = n[(n.RIDAGEYR >= 20) & n.LBXGH.notna() & n.DIQ010.isin([2, 3])]
            out.append(f"- {f}: {len(n):,} people; {len(a):,} adults never told they have diabetes, "
                       f"{(a.LBXGH >= 6.5).sum()} with HbA1c 6.5% or more")
    p = os.path.join(R, "osm", "abu_dhabi_health_facilities.csv")
    if os.path.exists(p):
        t = pd.read_csv(p)
        out += ["", f"## Health facilities in Abu Dhabi emirate (OpenStreetMap, real): {len(t)}",
                f"- {', '.join(f'{k}: {v}' for k, v in t.kind.value_counts().items())}"]
    g = os.path.join(R, "growth")
    if os.path.exists(g):
        out += ["", f"## Child growth references: {len(os.listdir(g))} WHO BMI-for-age files (0-5 and 5-19 years)"]
    geo = os.path.join(R, "geo")
    if os.path.exists(geo):
        out += [f"## UAE boundary files: {', '.join(sorted(os.listdir(geo)))}"]
    return out
