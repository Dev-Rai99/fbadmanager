import json

from .. import config

CSP_GEOCODED_PATH = config.DATA_DIR / "csp_geocoded.json"
PUNJAB_HARYANA_PATH = config.DATA_DIR / "punjab_haryana_tehsils_geocoded.json"

# Radius scales with how precise the geocode match was -- a village-level match
# can stay tight, a coarser tehsil/district-level anchor needs a wider radius
# to still likely cover the actual target village cluster around it.
RADIUS_BY_LEVEL_KM = {
    "village": 12,
    "tehsil": 18,
    "district": 25,
}

# Punjab/Haryana's own source data explicitly labels its catchment radius as
# "~20 KM Catchment" per tehsil -- use that instead of the generic scale above.
PUNJAB_HARYANA_RADIUS_KM = 20


def _load_csp_dataset():
    """Original 10 UP/Maharashtra/Rajasthan districts -- anchored on real
    existing-CSP points (village-level precision), from
    AO_District_Rural_Villages.xlsx 'CSP Village Flags' sheet."""
    if not CSP_GEOCODED_PATH.exists():
        return []
    with open(CSP_GEOCODED_PATH, encoding="utf-8") as f:
        rows = json.load(f)

    locations = []
    for row in rows:
        geo = row.get("geo")
        if not geo:
            continue
        locations.append({
            "district": row["district"],
            "tehsil": row["tehsil"],
            "anchor": row["village"],
            "lat": geo["lat"],
            "lon": geo["lon"],
            "radius_km": RADIUS_BY_LEVEL_KM.get(geo["matched_level"], 20),
            "match_precision": geo["matched_level"],
            "source": "existing_csp",
        })
    return locations


def _load_punjab_haryana_dataset():
    """Punjab/Haryana expansion -- anchored on tehsil centers (from
    AO_Dist_Campaign_Villages_Full.xlsx), each covering its stated
    ~20km village catchment, for districts flagged 'Need to Run Campaign: Yes'."""
    if not PUNJAB_HARYANA_PATH.exists():
        return []
    with open(PUNJAB_HARYANA_PATH, encoding="utf-8") as f:
        rows = json.load(f)

    locations = []
    for row in rows:
        geo = row.get("geo")
        if not geo:
            continue
        locations.append({
            "district": row["district"],
            "tehsil": row["tehsil"],
            "anchor": row["tehsil"],
            "lat": geo["lat"],
            "lon": geo["lon"],
            "radius_km": PUNJAB_HARYANA_RADIUS_KM,
            "match_precision": geo["matched_level"],
            "source": "punjab_haryana_expansion",
        })
    return locations


def load_all_locations():
    return _load_csp_dataset() + _load_punjab_haryana_dataset()


def districts_available():
    return sorted({loc["district"] for loc in load_all_locations()})


def locations_for_districts(district_names):
    """Filter the combined anchor list down to just the districts requested
    (case-insensitive), regardless of which source dataset they came from."""
    wanted = {d.upper() for d in district_names}
    return [loc for loc in load_all_locations() if loc["district"].upper() in wanted]
