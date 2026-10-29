from .meta import village_targeting

# Static fallback list, used only if data/csp_geocoded.json hasn't been generated yet
# (e.g. a fresh checkout). Once it exists, the real district list below is used instead.
_STATIC_FALLBACK_DISTRICTS = [
    "Dhule", "Nashik", "Thane", "Gorakhpur", "Deoria",
    "Lucknow", "Sitapur", "Moradabad", "Amroha", "Jaipur",
]
EXCLUDED_DISTRICTS = ["Kheri"]  # 7 CSPs already there, flagged saturated

PRIORITY_DISTRICTS = village_targeting.districts_available() or _STATIC_FALLBACK_DISTRICTS


def _ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else default


def collect_targeting():
    """Mirrors what Meta's own ad flow asks: location, age, gender, budget, schedule, placements.
    Detailed interest-based targeting is intentionally omitted -- Meta deprecated it Jan 2026.
    Location targeting is village-precise: each district here maps to real existing-CSP
    points (from AO_District_Rural_Villages.xlsx "CSP Village Flags" sheet) geocoded to
    lat/lon, with a radius sized to cover that CSP's mapped nearest-10-villages cluster --
    see src/meta/village_targeting.py."""
    print("\n--- Targeting ---")
    print(f"Priority districts (data-backed, village-level CSP targeting): {', '.join(PRIORITY_DISTRICTS)}")
    print(f"Excluded (saturated): {', '.join(EXCLUDED_DISTRICTS)}")
    districts_raw = _ask("Districts to target this run, comma separated", ",".join(PRIORITY_DISTRICTS))
    districts = [d.strip() for d in districts_raw.split(",") if d.strip() and d.strip() not in EXCLUDED_DISTRICTS]

    age_min = _ask("Minimum age", "21")
    age_max = _ask("Maximum age", "45")
    gender = _ask("Gender (all/male/female)", "all")
    daily_budget = _ask("Daily budget (INR)", "500")
    placements = _ask("Placements (advantage/manual)", "advantage")
    schedule_days = _ask("Run for how many days", "7")

    return {
        "districts": districts,
        "country": "India",
        "age_min": int(age_min),
        "age_max": int(age_max),
        "gender": gender,
        "daily_budget": float(daily_budget),
        "placements": placements,
        "schedule_days": int(schedule_days),
    }
