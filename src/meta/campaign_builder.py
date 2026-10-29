import json
import time
import requests

from .. import config
from . import geo_resolver
from . import village_targeting

GRAPH_URL = f"https://graph.facebook.com/{config.FB_API_VERSION}"


def _raise_with_meta_error(resp):
    """requests' default raise_for_status() swallows Meta's actual error body --
    this surfaces the real reason (e.g. 'Invalid parameter: ...') instead of just
    '400 Client Error'."""
    if resp.ok:
        return
    try:
        detail = resp.json().get("error", {})
        msg = detail.get("error_user_msg") or detail.get("message") or resp.text
    except Exception:
        msg = resp.text
    raise RuntimeError(f"Meta API error ({resp.status_code}) on {resp.url}: {msg}")


OBJECTIVE_MAP = {
    "leads": "OUTCOME_LEADS",
    "sales": "OUTCOME_SALES",
    "traffic": "OUTCOME_TRAFFIC",
    "awareness": "OUTCOME_AWARENESS",
    "engagement": "OUTCOME_ENGAGEMENT",
}


def _dry_run_payload(brief, ad_copy, image_paths, targeting, attachment):
    objective = OBJECTIVE_MAP.get(brief["objective"], "OUTCOME_TRAFFIC")
    ad_set_payload = {
        "name": f"{brief['product']} - AdSet 1",
        "daily_budget_minor_units": int(targeting["daily_budget"] * 100),
        "targeting": {
            # NOTE: Meta's real API needs resolved location "key" IDs from the
            # Targeting Search endpoint (GET /search?type=adgeolocation), not raw
            # district names. Districts are listed here for review; resolve them
            # to keys before this ever runs live.
            "geo_locations": {
                "countries": [targeting["country"]],
                "unresolved_districts": targeting.get("districts", []),
            },
            "age_min": targeting["age_min"],
            "age_max": targeting["age_max"],
            "genders": [] if targeting["gender"] == "all" else [targeting["gender"]],
        },
        "placements": targeting["placements"],
        "optimization_goal": "OFFSITE_CONVERSIONS" if objective == "OUTCOME_SALES" else "LINK_CLICKS",
        "status": "PAUSED",
        "duration_days": targeting["schedule_days"],
    }
    if objective == "OUTCOME_TRAFFIC" and config.FB_PIXEL_ID:
        # Website ("traffic") ads default to optimizing for Link Clicks, which
        # only teaches Meta to find clickers, not signups -- confirmed by real
        # account data where these ads got thousands of clicks and zero leads.
        # Once a pixel is configured, optimize for the actual signup event
        # instead so Meta can learn who converts, not just who clicks.
        ad_set_payload["optimization_goal"] = "OFFSITE_CONVERSIONS"
        ad_set_payload["promoted_object"] = {
            "pixel_id": config.FB_PIXEL_ID,
            # Confirmed directly against Meta Events Manager for this pixel
            # (4626002480962512): it fires "Lead" (10 times in the last 30
            # days), never "CompleteRegistration" -- that was the event this
            # was pointed at before, which the site simply never sends.
            "custom_event_type": "LEAD",
        }
    return {
        "campaign": {
            "name": f"{brief['product']} - {brief['objective']}",
            "objective": objective,
            "status": "PAUSED",
            "special_ad_categories": [],
            # Required by Meta as of 2026 when budget lives at the ad set level
            # (i.e. no Campaign Budget Optimization) rather than the campaign level.
            "is_adset_budget_sharing_enabled": False,
        },
        "ad_set": ad_set_payload,
        "ad_creative": {
            "headline": ad_copy["headline"],
            "primary_text": ad_copy["primary_text"],
            "description": ad_copy["description"],
            "cta": ad_copy["cta"],
            "image_paths": image_paths,
            "destination": attachment,
        },
        "ad": {
            "name": f"{brief['product']} - Ad 1",
            "status": "PAUSED",
            "url_tags": "utm_source=fb&utm_medium=paid&utm_campaign={{campaign.id}}&utm_content={{ad.id}}",
        },
    }


def build_campaign(brief, ad_copy, image_paths, targeting, attachment):
    """Returns (result_dict, mode) where mode is 'dry_run' or 'live'.
    In dry_run, nothing is sent to Meta -- the payload is saved locally so it can
    be inspected/replayed once real credentials are supplied."""
    payload = _dry_run_payload(brief, ad_copy, image_paths, targeting, attachment)

    if not config.META_CONNECTED:
        out_path = config.DATA_DIR / f"dry_run_campaign_{int(time.time())}.json"
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\n[campaign_builder] DRY RUN — no real Meta account connected.")
        print(f"[campaign_builder] Full payload saved to {out_path}")
        return {
            "mode": "dry_run",
            "campaign_id": "dry_run_campaign",
            "ad_set_id": "dry_run_ad_set",
            "ad_id": "dry_run_ad",
            "payload": payload,
        }, "dry_run"

    # --- Live path: only reached once FB_ACCESS_TOKEN / FB_AD_ACCOUNT_ID / FB_PAGE_ID are set ---
    token = config.FB_ACCESS_TOKEN
    account = config.FB_AD_ACCOUNT_ID

    campaign_resp = requests.post(
        f"{GRAPH_URL}/{account}/campaigns",
        params={"access_token": token},
        json=payload["campaign"],
        timeout=30,
    )
    _raise_with_meta_error(campaign_resp)
    campaign_id = campaign_resp.json()["id"]

    live_targeting = dict(payload["ad_set"]["targeting"])
    district_names = targeting.get("districts", [])
    if district_names:
        # Primary path: precise lat/lon radius targeting anchored on real existing-CSP
        # points (from AO_District_Rural_Villages.xlsx "CSP Village Flags" sheet), sized
        # to cover each CSP's mapped "nearest 10 villages" cluster. This is far more
        # accurate than city-name search, since most of those villages don't exist in
        # Meta's own location database at all.
        csp_anchors = village_targeting.locations_for_districts(district_names)
        covered_districts = {a["district"].upper() for a in csp_anchors}
        remaining = [d for d in district_names if d.upper() not in covered_districts]

        custom_locations = [
            {"latitude": a["lat"], "longitude": a["lon"], "radius": a["radius_km"], "distance_unit": "kilometer"}
            for a in csp_anchors
        ]

        # Fallback path: any requested district with no CSP data yet (not in the
        # spreadsheet) falls back to city-name search + radius, same as before.
        cities = []
        if remaining:
            resolved, unresolved = geo_resolver.resolve_districts(remaining, token)
            if unresolved:
                raise RuntimeError(
                    f"Could not resolve these districts to Meta locations: {', '.join(unresolved)}. "
                    "Check spelling, or add a state hint in geo_resolver.DISTRICT_STATE_HINTS "
                    "if the name is ambiguous across states."
                )
            cities = [
                {"key": r["key"], "radius": r["radius"], "distance_unit": r["distance_unit"]}
                for r in resolved
            ]

        if not custom_locations and not cities:
            raise RuntimeError(f"No targetable locations found for districts: {', '.join(district_names)}")

        geo_locations = {}
        if custom_locations:
            geo_locations["custom_locations"] = custom_locations
        if cities:
            geo_locations["cities"] = cities
        live_targeting["geo_locations"] = geo_locations
    else:
        live_targeting["geo_locations"] = {"countries": ["IN"]}

    # Disable Advantage Audience expansion -- the district-level targeting above
    # is deliberate (data-backed priority districts), so we don't want Meta
    # auto-broadening beyond it.
    live_targeting["targeting_automation"] = {"advantage_audience": 0}

    ad_set_body = {
        "name": payload["ad_set"]["name"],
        "campaign_id": campaign_id,
        "daily_budget": payload["ad_set"]["daily_budget_minor_units"],
        "billing_event": "IMPRESSIONS",
        "optimization_goal": payload["ad_set"]["optimization_goal"],
        # Automatic bidding -- Meta spends the full daily budget to get the most
        # results it can, no manual bid cap required.
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": live_targeting,
        "status": "PAUSED",
    }
    if "promoted_object" in payload["ad_set"]:
        ad_set_body["promoted_object"] = payload["ad_set"]["promoted_object"]
    ad_set_resp = requests.post(
        f"{GRAPH_URL}/{account}/adsets",
        params={"access_token": token},
        json=ad_set_body,
        timeout=30,
    )
    _raise_with_meta_error(ad_set_resp)
    ad_set_id = ad_set_resp.json()["id"]

    image_hashes = []
    for path in image_paths:
        with open(path, "rb") as f:
            img_resp = requests.post(
                f"{GRAPH_URL}/{account}/adimages",
                params={"access_token": token},
                files={"file": f},
                timeout=60,
            )
        _raise_with_meta_error(img_resp)
        images = img_resp.json()["images"]
        image_hashes.append(next(iter(images.values()))["hash"])

    creative_body = {
        "name": f"{brief['product']} creative",
        "object_story_spec": {
            "page_id": config.FB_PAGE_ID,
            "link_data": {
                "message": ad_copy["primary_text"],
                "link": attachment.get("url", "https://example.com"),
                "name": ad_copy["headline"],
                "description": ad_copy["description"],
                "image_hash": image_hashes[0] if image_hashes else None,
                "call_to_action": {"type": ad_copy["cta"]},
            },
        },
    }
    creative_resp = requests.post(
        f"{GRAPH_URL}/{account}/adcreatives",
        params={"access_token": token},
        json=creative_body,
        timeout=30,
    )
    _raise_with_meta_error(creative_resp)
    creative_id = creative_resp.json()["id"]

    ad_body = {
        "name": payload["ad"]["name"],
        "adset_id": ad_set_id,
        "creative": {"creative_id": creative_id},
        "status": "PAUSED",
        # Meta appends these as query params onto the creative's destination
        # link at click time -- {{campaign.id}}/{{adset.id}}/{{ad.id}} are its
        # own dynamic placeholders, filled in per-click, so every signup can be
        # traced back to the exact ad that drove it. Confirmed missing here was
        # the reason 141 of 146 real signups had no utm_campaign at all (the
        # link shipped with zero tracking params) -- fbclid alone only proves
        # "some Facebook ad," never which one.
        "url_tags": "utm_source=fb&utm_medium=paid&utm_campaign={{campaign.id}}&utm_content={{ad.id}}",
    }
    ad_resp = requests.post(
        f"{GRAPH_URL}/{account}/ads",
        params={"access_token": token},
        json=ad_body,
        timeout=30,
    )
    _raise_with_meta_error(ad_resp)
    ad_id = ad_resp.json()["id"]

    return {
        "mode": "live",
        "campaign_id": campaign_id,
        "ad_set_id": ad_set_id,
        "ad_id": ad_id,
        "payload": payload,
    }, "live"


def activate(ad_id_chain):
    """Flips campaign/ad set/ad from PAUSED to ACTIVE. Only called after the user
    explicitly confirms the spend amount -- never automatically."""
    if ad_id_chain["mode"] == "dry_run":
        print("[campaign_builder] DRY RUN — would activate:", ad_id_chain)
        return

    token = config.FB_ACCESS_TOKEN
    for node_id in (ad_id_chain["campaign_id"], ad_id_chain["ad_set_id"], ad_id_chain["ad_id"]):
        requests.post(
            f"{GRAPH_URL}/{node_id}",
            params={"access_token": token},
            json={"status": "ACTIVE"},
            timeout=30,
        ).raise_for_status()
    print("[campaign_builder] Campaign activated on Meta.")
