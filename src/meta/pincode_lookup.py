import math
import time
import requests

REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
SEARCH_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "AdMarketer-EkoCSP/1.0 (contact: kiosk.eko.in)"}
RATE_LIMIT_SECONDS = 1.1


def _reverse(lat, lon, zoom, retries=3):
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                REVERSE_URL,
                params={"lat": lat, "lon": lon, "format": "json", "addressdetails": 1, "zoom": zoom},
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
    time.sleep(RATE_LIMIT_SECONDS)
    return resp.json().get("address", {}).get("postcode")


def _search_postcode(query, retries=3):
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                SEARCH_URL,
                # limit=1 originally took only Nominatim's top-ranked match --
                # for an ambiguous name like a district ("JALANDHAR, Punjab,
                # India"), that top match is often the district BOUNDARY
                # record itself (type "administrative"/"state_district"),
                # which structurally has no postcode field at all, even
                # though a lower-ranked match (the actual city) does.
                # Confirmed by direct testing: this silently turned a real,
                # resolvable place into a false "not found". Asking for more
                # candidates and taking the first one that actually carries a
                # postcode fixes it without changing behavior for the common
                # case where the top match already has one.
                params={"q": query, "format": "json", "addressdetails": 1, "countrycodes": "in", "limit": 5},
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
    for row in data:
        postcode = row.get("address", {}).get("postcode")
        if postcode:
            return postcode
    return None


def get_pincode(lat, lon, retries=3):
    """Reverse-geocodes a lat/lon to its Indian PIN code via OpenStreetMap Nominatim.
    Returns the postcode string, or None if not found. Single-point lookup only --
    use get_pincode_with_fallback for the broader village->tehsil->district chain."""
    return _reverse(lat, lon, zoom=16, retries=retries)


def get_pincode_coverage_km(pincode, retries=3):
    """Looks up the PIN code as a place in its own right and returns an
    approximate coverage radius in km, computed from OSM's real bounding box
    for that postcode area (not a population-based guess). Returns None if the
    postcode isn't found as its own place record."""
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                SEARCH_URL,
                params={"q": f"{pincode}, India", "format": "json", "limit": 1},
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
    if not data or "boundingbox" not in data[0]:
        return None

    south, north, west, east = (float(x) for x in data[0]["boundingbox"])
    center_lat = (south + north) / 2
    lat_span_km = (north - south) * 111.0
    lon_span_km = (east - west) * 111.0 * math.cos(math.radians(center_lat))
    return round((lat_span_km + lon_span_km) / 4, 2)  # avg of the two half-spans


def get_pincode_by_name_with_fallback(village, tehsil, district, state):
    """Looks up a village's real PIN code by name, no coordinates needed --
    progressively widens the search when the village itself isn't resolvable:
    village+tehsil -> village+district -> tehsil (nearest fallback) -> district.
    Returns (pincode, matched_level), matched_level one of "village", "tehsil",
    "district", or None if nothing at all was found."""
    if village and tehsil:
        pincode = _search_postcode(f"{village}, {tehsil}, {district}, {state}, India")
        if pincode:
            return pincode, "village"

    if village:
        pincode = _search_postcode(f"{village}, {district}, {state}, India")
        if pincode:
            return pincode, "village"

    if tehsil:
        pincode = _search_postcode(f"{tehsil}, {district}, {state}, India")
        if pincode:
            return pincode, "tehsil"

    pincode = _search_postcode(f"{district}, {state}, India")
    if pincode:
        return pincode, "district"

    return None, None


def get_pincode_with_fallback(lat, lon, tehsil=None, district=None, state=None):
    """If the exact point has no PIN in OSM's data (common for very small rural
    points), progressively widens the search: same point at a broader zoom level,
    then a direct name search for the tehsil, then the district. Returns
    (pincode, matched_level) -- matched_level is one of "point", "tehsil",
    "district", or None if nothing at all was found."""
    pincode = _reverse(lat, lon, zoom=16)
    if pincode:
        return pincode, "point"

    pincode = _reverse(lat, lon, zoom=12)
    if pincode:
        return pincode, "point"

    if tehsil and district and state:
        pincode = _search_postcode(f"{tehsil}, {district}, {state}, India")
        if pincode:
            return pincode, "tehsil"

    if district and state:
        pincode = _search_postcode(f"{district}, {state}, India")
        if pincode:
            return pincode, "district"

    return None, None
