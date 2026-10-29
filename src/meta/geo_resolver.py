import requests

from .. import config

GRAPH_URL = f"https://graph.facebook.com/{config.FB_API_VERSION}"

# Disambiguates districts whose name also matches a same-named city in another
# Indian state (e.g. "Gorakhpur" exists in UP, Haryana, and MP; "Jaipur" exists
# in Rajasthan, Maharashtra, and Assam). Add an entry here for any new district
# that turns out to be ambiguous.
DISTRICT_STATE_HINTS = {
    "Dhule": "Maharashtra",
    "Nashik": "Maharashtra",
    "Thane": "Maharashtra",
    "Gorakhpur": "Uttar Pradesh",
    "Deoria": "Uttar Pradesh",
    "Lucknow": "Uttar Pradesh",
    "Sitapur": "Uttar Pradesh",
    "Moradabad": "Uttar Pradesh",
    "Amroha": "Uttar Pradesh",
    "Jaipur": "Rajasthan",
}

DEFAULT_RADIUS_KM = 50  # covers the district town + surrounding rural villages


def _search(name, access_token):
    resp = requests.get(
        f"{GRAPH_URL}/search",
        params={
            "type": "adgeolocation",
            "q": name,
            "location_types": '["city","region"]',
            "limit": 5,
            "access_token": access_token,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return [row for row in resp.json().get("data", []) if row.get("country_code") == "IN"]


def resolve_district(name, access_token):
    """Returns a Meta geo-location match dict for one district name, or None if
    nothing usable was found. Disambiguates same-named cities across states
    using DISTRICT_STATE_HINTS."""
    candidates = _search(name, access_token)
    if not candidates:
        return None

    hint = DISTRICT_STATE_HINTS.get(name)
    if hint:
        for c in candidates:
            if c.get("region") == hint:
                return c

    # No hint, or hint didn't match anything -- fall back to the top result.
    return candidates[0]


def resolve_districts(district_names, access_token):
    """Returns (resolved, unresolved) -- resolved is a list of Meta 'key' dicts
    ready to drop into geo_locations['cities'], unresolved is a list of the
    district names that couldn't be matched to anything."""
    resolved = []
    unresolved = []
    for name in district_names:
        match = resolve_district(name, access_token)
        if match:
            resolved.append({
                "key": match["key"],
                "name": match["name"],
                "region": match.get("region"),
                "radius": DEFAULT_RADIUS_KM,
                "distance_unit": "kilometer",
            })
        else:
            unresolved.append(name)
    return resolved, unresolved
