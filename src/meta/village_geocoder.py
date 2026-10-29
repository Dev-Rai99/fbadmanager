import re
import time
import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "AdMarketer-EkoCSP/1.0 (contact: kiosk.eko.in)"}
RATE_LIMIT_SECONDS = 1.1  # Nominatim usage policy: max 1 request/second

# CSP village names in the source spreadsheet sometimes carry an annotation
# like "Buderna (HQ/Town - CSP location, not a distinct rural village)" --
# strip that before geocoding.
_ANNOTATION_RE = re.compile(r"\s*\([^)]*\)\s*")


def clean_village_name(raw_name):
    return _ANNOTATION_RE.sub("", raw_name).strip()


def _query(q, retries=3):
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={"q": q, "format": "json", "countrycodes": "in", "limit": 1},
                headers=HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            last_error = e
            time.sleep(2 * (attempt + 1))
    else:
        raise last_error
    data = resp.json()
    time.sleep(RATE_LIMIT_SECONDS)
    return data[0] if data else None


def geocode_csp_location(village, tehsil, district, state):
    """Tries progressively broader queries until something resolves. Returns
    (lat, lon, matched_level, matched_query) or None if nothing at all worked.

    Rural India has plenty of same-named villages in different districts (e.g.
    two "Babhulgaon"s, two "Navi Nagar"s). Once a query drops the district from
    its search string (the loose "village, state, India" attempt), the result
    could easily be a same-named village hundreds of km away in the wrong
    district -- so those loose matches are only accepted if the district name
    actually appears somewhere in the result's own address hierarchy."""
    village = clean_village_name(village)
    attempts = [
        (f"{village}, {tehsil}, {district}, {state}, India", "village", False),
        (f"{village}, {district}, {state}, India", "village", False),
        (f"{village}, {state}, India", "village", True),  # loose -- needs validation
        (f"{tehsil}, {district}, {state}, India", "tehsil", False),
        (f"{district}, {state}, India", "district", False),
    ]
    for query, level, needs_validation in attempts:
        result = _query(query)
        if not result:
            continue
        if needs_validation and district.lower() not in (result.get("display_name") or "").lower():
            continue  # likely a same-named place in a different district -- keep falling back
        return {
            "lat": float(result["lat"]),
            "lon": float(result["lon"]),
            "matched_level": level,
            "matched_query": query,
            "osm_display_name": result.get("display_name"),
        }
    return None
