"""One-off batch job: geocode every village in the CSP-flagged village list
(from AO_Dist_Campaign_CSP_Flagged.pdf) to its real PIN code, falling back to
tehsil/district when a specific village isn't resolvable in OpenStreetMap.

Long-running (Nominatim's usage policy caps requests at ~1/sec, and there are
~2,820 villages here) -- checkpoints progress to disk every 25 villages so it
can resume instead of restarting from zero if interrupted.

Usage: python scripts/geocode_csp_villages.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.meta.pincode_lookup import get_pincode_by_name_with_fallback

INPUT_PATH = ROOT / "data" / "csp_villages_parsed.json"
OUTPUT_PATH = ROOT / "data" / "csp_villages_geocoded.json"
CHECKPOINT_EVERY = 25

DISTRICT_STATE = {
    "GURDASPUR": "Punjab",
    "JALANDHAR": "Punjab",
    "KARNAL": "Haryana",
    "SIRSA": "Haryana",
}

VILLAGE_CODE_RE = None


def clean_village_name(raw):
    import re
    global VILLAGE_CODE_RE
    if VILLAGE_CODE_RE is None:
        VILLAGE_CODE_RE = re.compile(r"\s*\([^)]*\)\s*$")
    return VILLAGE_CODE_RE.sub("", raw).strip()


def main():
    with open(INPUT_PATH, encoding="utf-8") as f:
        rows = json.load(f)

    results = []
    done_keys = set()
    if OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            results = json.load(f)
        done_keys = {(r["village_raw"], r["tehsil"], r["district"]) for r in results}
        print(f"Resuming: {len(results)} villages already geocoded, skipping those.", flush=True)

    total = len(rows)
    for i, row in enumerate(rows, start=1):
        key = (row["village_raw"], row["tehsil"], row["district"])
        if key in done_keys:
            continue

        state = DISTRICT_STATE.get(row["district"], "")
        village_clean = clean_village_name(row["village_raw"])
        try:
            pincode, level = get_pincode_by_name_with_fallback(
                village_clean, row["tehsil"], row["district"], state
            )
        except Exception as e:
            pincode, level = None, f"error: {e}"

        results.append({
            **row,
            "village_clean": village_clean,
            "state": state,
            "pincode": pincode,
            "match_level": level,
        })

        if i % CHECKPOINT_EVERY == 0 or i == total:
            with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=1)
            found = sum(1 for r in results if r.get("pincode"))
            print(f"[{i}/{total}] checkpointed -- {found}/{len(results)} resolved so far", flush=True)

    found = sum(1 for r in results if r.get("pincode"))
    distinct_pins = {r["pincode"] for r in results if r.get("pincode")}
    print(f"\nDONE. {found}/{len(results)} villages resolved to a pincode.")
    print(f"Distinct pincodes found: {len(distinct_pins)}")


if __name__ == "__main__":
    main()
