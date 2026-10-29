"""Pulls real website signup submissions from the SBI Kiosk site's own admin
panel -- the ground truth for "did someone actually complete the signup
form," which Meta's URL-ad insights can't answer on their own (no pixel is
installed, so Meta only ever sees clicks, never completions; confirmed by
direct testing earlier -- every URL ad showed real reach and clicks but zero
leads according to Meta, despite the signup site itself recording real
completions in the same window).

The admin API returns each application's utm_source/utm_medium/utm_campaign
and fbclid -- Facebook appends fbclid automatically to any link someone
clicks from a Facebook/Instagram ad, even if the destination URL carries no
UTM parameters, so it's the most reliable signal that a signup came from a
Facebook ad. utm_campaign, when present, is literally a Meta campaign ID,
letting a signup be tied back to the exact ad that drove it.
"""
import requests
import urllib3

from .. import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _login():
    """The admin panel's own app-level login (separate from, and in addition
    to, any infrastructure Basic Auth in front of it) -- confirmed by direct
    testing that /api/applications rejects Basic Auth alone with "Admin login
    required," and that the site's own JS authenticates by POSTing here and
    then sending the returned token as `Authorization: Bearer <token>` on
    every subsequent call (never as a cookie, despite the page's fetch calls
    using credentials:"include" -- that turned out to be a red herring left
    over from an older version of the login flow)."""
    resp = requests.post(
        f"{config.EKO_ADMIN_BASE_URL}/api/admin/login",
        json={"username": config.EKO_ADMIN_USER, "password": config.EKO_ADMIN_PASS},
        verify=False,  # self-signed cert on this internal IP
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != 0:
        raise RuntimeError(data.get("message") or "Admin login failed")
    return data["data"]["token"]


def _get(path, params=None):
    token = _login()
    resp = requests.get(
        f"{config.EKO_ADMIN_BASE_URL}{path}",
        params=params or {},
        headers={"Authorization": f"Bearer {token}"},
        verify=False,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def _classify_attribution(app):
    """Three tiers, most to least certain:
    1. campaign_matched -- utm_campaign names a real Meta campaign ID
    2. fbclid_only -- came from a Facebook click, but the destination URL
       didn't carry UTM params (so we know "Facebook" but not which ad)
    3. none -- no Facebook signal at all (organic, direct, or another channel)
    """
    campaign = (app.get("utm_campaign") or "").strip()
    has_fbclid = bool(app.get("fbclid"))
    source = (app.get("utm_source") or "").strip().lower()

    if campaign and (has_fbclid or source == "fb"):
        return "campaign_matched", campaign
    if has_fbclid or source == "fb":
        return "fbclid_only", None
    return "none", None


def get_all_signups(known_campaign_names=None):
    """Returns {"error": None, "signups": [...]} or {"error": "...", "signups": []}.
    known_campaign_names optionally maps campaign_id -> human-readable name
    (from all_ads.get_all_real_ads()) so a campaign-matched signup can show
    which ad actually drove it, not just a raw Meta campaign ID."""
    if not config.EKO_ADMIN_CONNECTED:
        return {"error": "Admin panel credentials not configured (EKO_ADMIN_USER / EKO_ADMIN_PASS).", "signups": []}

    try:
        data = _get("/api/applications")
    except Exception as e:
        return {"error": f"Could not reach the signup admin panel: {e}", "signups": []}

    known_campaign_names = known_campaign_names or {}
    apps = data.get("data", [])
    signups = []
    for app in apps:
        tier, campaign_id = _classify_attribution(app)
        name = (f"{app.get('first_name', '')} {app.get('last_name', '')}").strip()
        signups.append({
            "id": app.get("id"),
            "mobile": app.get("mobile", ""),
            "name": name,
            "submitted_at": app.get("submitted_at") or app.get("created_at"),
            "qualification_verdict": app.get("qualification_verdict", ""),
            "shop_address": app.get("shop_address", ""),
            "attribution_tier": tier,
            "matched_campaign_id": campaign_id,
            "matched_campaign_name": known_campaign_names.get(campaign_id) if campaign_id else None,
            "utm_source": app.get("utm_source") or "",
            "utm_medium": app.get("utm_medium") or "",
            "utm_campaign": app.get("utm_campaign") or "",
        })
    signups.sort(key=lambda s: s.get("submitted_at") or "", reverse=True)
    return {"error": None, "signups": signups}
