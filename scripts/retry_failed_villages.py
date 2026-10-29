"""One-off cleanup: retries every village in csp_villages_geocoded.json that
came back with no pincode (pincode is None), updating it in place. Meant for
a small transient-failure batch, not the main run -- see geocode_csp_villages.py
for the full job."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.meta.pincode_lookup import get_pincode_by_name_with_fallback

OUTPUT_PATH = ROOT / "data" / "csp_villages_geocoded.json"


def main():
    with open(OUTPUT_PATH, encoding="utf-8") as f:
        rows = json.load(f)

    failed_indices = [i for i, r in enumerate(rows) if not r.get("pincode")]
    print(f"Retrying {len(failed_indices)} failed villages...", flush=True)

    fixed = 0
    for n, i in enumerate(failed_indices, start=1):
        row = rows[i]
        try:
            pincode, level = get_pincode_by_name_with_fallback(
                row["village_clean"], row["tehsil"], row["district"], row["state"]
            )
        except Exception as e:
            pincode, level = None, f"error: {e}"
        row["pincode"] = pincode
        row["match_level"] = level
        if pincode:
            fixed += 1
        if n % 10 == 0 or n == len(failed_indices):
            with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=1)
            print(f"[{n}/{len(failed_indices)}] -- {fixed} fixed so far", flush=True)

    print(f"\nDONE. {fixed}/{len(failed_indices)} previously-failed villages now resolved.")
    still_failed = [r for r in rows if not r.get("pincode")]
    print(f"Still unresolved: {len(still_failed)}")


if __name__ == "__main__":
    main()
