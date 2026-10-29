"""Pulls every real ad the connected Meta user has run, across every ad
account the access token can see -- not just the one wired into .env.

The .env-configured account (FB_AD_ACCOUNT_ID) is only ever used to *build*
new campaigns. Ads actually run day-to-day often live in other ad accounts
under the same Business Manager (e.g. a personal ad account or a client
account), so the dashboard needs to look at all of them and keep only the
campaigns that actually delivered -- campaign/ad-set shells with no ad
underneath (failed or unfinished builds) are skipped rather than shown as
zeroes.
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .. import config

MAX_WORKERS = 10

VILLAGE_PINCODES_PATH = config.DATA_DIR / "csp_villages_geocoded.json"


def _load_village_pincodes():
    """Kept for reference/export use only -- no longer used to qualify leads.
    See AD_PINCODE_DB_PATH below for the current qualification reference,
    per the user's later explicit instruction to verify only against each
    ad's own real Meta targeting, persisted across restarts."""
    try:
        with open(VILLAGE_PINCODES_PATH, encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        return set()
    return {r["pincode"] for r in rows if r.get("pincode")}


# Persistent, cross-restart record of what pincodes each campaign was ever
# actually targeted at -- per explicit instruction: "collect all the ads
# location targeting data, make a database for these... verify the pincode
# of the leads that have came from there only." Unlike _TARGETING_CACHE
# (in-memory, wiped every server restart), this survives restarts and keeps
# growing as older or newer campaigns get pulled, so a lead's pincode can
# always be checked against its own ad's real targeting even if that ad's
# targeting fetch is rate-limited on a later refresh.
AD_PINCODE_DB_PATH = config.DATA_DIR / "ad_targeted_pincodes.json"
_AD_PINCODE_DB_LOCK = threading.Lock()


def _load_ad_pincode_db():
    try:
        with open(AD_PINCODE_DB_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_ad_pincode_db(db):
    AD_PINCODE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AD_PINCODE_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def _update_ad_pincode_db(results):
    """Upserts every campaign's full targeting (pincodes, locations,
    interests/behaviors) into the persistent database -- only when the fetch
    actually succeeded, so a rate-limited fetch never overwrites good data
    collected on an earlier run. Storing the whole summary, not just pincodes,
    lets an ad's interests/behaviors still be shown and compared on a refresh
    where Meta rate-limits the targeting call."""
    with _AD_PINCODE_DB_LOCK:
        db = _load_ad_pincode_db()
        changed = False
        for r in results:
            ts = r.get("targeting_summary", {})
            if not ts.get("pincode_check_ok") or ts.get("restored_from_db"):
                continue
            entry = {
                "campaign_name": r.get("campaign_name"),
                "ad_name": r.get("ad_name"),
                "account_name": r.get("account_name"),
                "pincodes": sorted(ts.get("targeted_pincodes") or []),
                "locations": ts.get("locations") or [],
                "interests_behaviors": ts.get("interests_behaviors") or "",
                "setup": ts.get("setup") or {},
            }
            if db.get(r["campaign_id"]) != entry:
                db[r["campaign_id"]] = entry
                changed = True
        if changed:
            _save_ad_pincode_db(db)
        return db


def _restore_targeting_from_db(results):
    """Fills in targeting for any ad whose live fetch failed this run (Meta
    rate limit), from the last successful fetch saved in the database.
    Targeting doesn't change once an ad is built, so the saved copy is as
    good as a fresh one."""
    db = _load_ad_pincode_db()
    for r in results:
        ts = r.get("targeting_summary", {})
        if ts.get("pincode_check_ok"):
            continue
        entry = db.get(r["campaign_id"])
        if not entry:
            continue
        pins = entry.get("pincodes") or []
        locations = entry.get("locations") or ([f"{len(pins)} pincode(s): {', '.join(pins)}"] if pins else ["Not available"])
        r["targeting_summary"] = {
            "locations": locations,
            "interests_behaviors": entry.get("interests_behaviors") or "Not recorded",
            "setup": entry.get("setup") or {},
            "targeted_pincodes": set(pins),
            "pincode_check_ok": True,
            "restored_from_db": True,
        }


def _effective_targeted_pincodes(result, db):
    """The pincode set to qualify this campaign's leads against: whatever was
    just freshly fetched if that succeeded, otherwise whatever this same
    campaign had persisted from an earlier successful run."""
    ts = result.get("targeting_summary", {})
    if ts.get("pincode_check_ok") and ts.get("targeted_pincodes"):
        return ts["targeted_pincodes"]
    entry = db.get(result["campaign_id"])
    return set(entry["pincodes"]) if entry else set()

GRAPH_URL = f"https://graph.facebook.com/{config.FB_API_VERSION}"

BASE_FIELDS = "impressions,reach,clicks,ctr,cpc,cpm,spend,inline_link_clicks,actions"

# Each campaign spawns its own 5-worker pool for insights/breakdowns/leads/
# targeting, and many campaigns from the SAME ad account can be in flight at
# once under the outer campaign-level pool -- confirmed by direct testing that
# this genuinely saturates a single ad account's Graph API rate limit (code
# 17, "User request limit reached") for the whole run, not just briefly, so a
# short per-call retry never finds a clear window. Capping total concurrent
# Graph API calls globally keeps the account under its ceiling.
_RATE_LIMIT_SEM = threading.Semaphore(6)

# A campaign's targeting configuration essentially never changes once an ad
# is built -- re-fetching it on every single dashboard refresh (for every ad,
# every time) is what saturates this account's rate limit in the first place,
# and it's pure waste since the answer rarely differs from last time.
# Caching it means a transient rate limit on one refresh no longer wipes out
# data we already successfully fetched -- confirmed by direct testing that
# this was showing "Rate-limited by Meta" for ads whose targeting had already
# loaded correctly minutes earlier. Process-lifetime cache, no TTL: targeting
# only changes when a new campaign is built, which happens through this same
# process and can just be handled by restarting the server.
_TARGETING_CACHE = {}
_TARGETING_CACHE_LOCK = threading.Lock()


def _get(path, params):
    """Retries on Meta's per-account rate limit (code 17 / subcode 2446079)
    in addition to the global concurrency cap above -- without both, a
    rate-limited call silently fell back to 'Not available' even though the
    real targeting data existed."""
    params = {**params, "access_token": config.FB_ACCESS_TOKEN}
    last_exc = None
    for attempt, delay in enumerate((0, 4, 8)):
        if delay:
            time.sleep(delay)
        with _RATE_LIMIT_SEM:
            resp = requests.get(f"{GRAPH_URL}/{path}", params=params, timeout=30)
        if resp.status_code < 400:
            return resp.json()
        try:
            err = resp.json().get("error", {})
        except Exception:
            err = {}
        if err.get("code") == 17 and attempt < 2:
            last_exc = requests.exceptions.HTTPError(resp.text, response=resp)
            continue
        resp.raise_for_status()
    raise last_exc


def _list_ad_accounts():
    data = _get("me/adaccounts", {"fields": "id,name,account_status", "limit": 200})
    return data.get("data", [])


def _list_campaigns(account_id):
    data = _get(f"{account_id}/campaigns", {
        "fields": "id,name,status,effective_status,created_time,objective,stop_time",
        "limit": 200,
    })
    return data.get("data", [])


def _list_ads_for_campaign(campaign_id):
    data = _get(f"{campaign_id}/ads", {
        "fields": "id,name,status,effective_status,creative{thumbnail_url,image_url,object_story_spec}",
        "limit": 50,
    })
    return data.get("data", [])


CREATIVES_PATH = config.DATA_DIR / "ad_creatives.json"
CREATIVE_IMAGES_DIR = config.DATA_DIR / "creatives"
_CREATIVES_LOCK = threading.Lock()


def _extract_creative(creative):
    """The image and caption text each ad actually ran with. Meta already
    returns this inside object_story_spec on the ad request we make anyway --
    it was simply being thrown away, so no ad could be compared on creative."""
    creative = creative or {}
    spec = creative.get("object_story_spec") or {}
    out = {"type": "unknown", "primary_text": "", "headline": "", "description": "", "cta": "",
           "image_hash": "", "image_url": creative.get("image_url") or "", "thumbnail_url": creative.get("thumbnail_url") or ""}
    ld = spec.get("link_data")
    vd = spec.get("video_data")
    pd = spec.get("photo_data")
    if ld:
        kids = ld.get("child_attachments") or []
        out["type"] = "carousel" if kids else "image"
        out["primary_text"] = ld.get("message") or ""
        out["headline"] = ld.get("name") or (kids[0].get("name") if kids else "") or ""
        out["description"] = ld.get("description") or ld.get("caption") or ""
        out["cta"] = (ld.get("call_to_action") or {}).get("type") or ""
        out["image_hash"] = ld.get("image_hash") or (kids[0].get("image_hash") if kids else "") or ""
        out["image_url"] = out["image_url"] or ld.get("picture") or ""
    elif vd:
        out["type"] = "video"
        out["primary_text"] = vd.get("message") or ""
        out["headline"] = vd.get("title") or ""
        out["description"] = vd.get("link_description") or ""
        out["cta"] = (vd.get("call_to_action") or {}).get("type") or ""
        out["image_url"] = out["image_url"] or vd.get("image_url") or ""
    elif pd:
        out["type"] = "image"
        out["primary_text"] = pd.get("caption") or ""
        out["image_hash"] = pd.get("image_hash") or ""
        out["image_url"] = out["image_url"] or pd.get("url") or ""
    return out


def _resolve_full_image_urls(todo):
    """Full-size picture links, looked up by image hash on each ad's account.
    The ad request only returns a 64px thumbnail for most ads, which is too
    small to judge a creative by. Best effort: any failure just leaves that ad
    on its thumbnail, and the next refresh tries again."""
    by_account = {}
    for r in todo:
        h = (r.get("creative") or {}).get("image_hash")
        if h and r.get("account_id"):
            by_account.setdefault(r["account_id"], set()).add(h)
    urls = {}
    for account_id, hashes in by_account.items():
        hashes = sorted(hashes)
        for i in range(0, len(hashes), 20):
            try:
                data = _get(f"{account_id}/adimages", {"hashes": json.dumps(hashes[i:i + 20]), "fields": "hash,url", "limit": 20})
            except Exception:
                continue
            for img in data.get("data", []):
                if img.get("hash") and img.get("url"):
                    urls[img["hash"]] = img["url"]
    return urls


def _download_creative_image(ad_id, creative, full_url="", replace=False):
    """Meta's image links stop working within days, so each ad's picture is
    saved to disk the moment it is first seen. Returns (file name, is_full_size);
    replace=True overwrites a previously saved thumbnail."""
    if not replace:
        for existing in CREATIVE_IMAGES_DIR.glob(f"{ad_id}.*"):
            return existing.name, False
    for url, is_full in ((full_url, True), (creative.get("image_url"), True), (creative.get("thumbnail_url"), False)):
        if not url:
            continue
        try:
            r = requests.get(url, timeout=15)
            ctype = (r.headers.get("content-type") or "").lower()
            if r.status_code != 200 or not ctype.startswith("image/") or len(r.content) > 5_000_000:
                continue
            ext = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(ctype.split(";")[0], "jpg")
            CREATIVE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            for stale in CREATIVE_IMAGES_DIR.glob(f"{ad_id}.*"):
                stale.unlink()
            name = f"{ad_id}.{ext}"
            (CREATIVE_IMAGES_DIR / name).write_bytes(r.content)
            return name, is_full
        except Exception:
            continue
    return "", False


def _persist_creatives(results):
    """Keeps every ad's caption text and picture permanently (data/ad_creatives.json
    plus data/creatives/). Existing text is never blanked by a thinner later read.
    Pictures saved earlier as thumbnails are upgraded to full size when Meta
    will give one."""
    with _CREATIVES_LOCK:
        try:
            with open(CREATIVES_PATH, encoding="utf-8") as f:
                db = json.load(f)
        except Exception:
            db = {}
        todo = [r for r in results if r.get("creative")]
        # Only ads with no full-size picture saved yet need a lookup.
        need_full = [r for r in todo if not db.get(r["ad_id"], {}).get("image_full")]
        full_urls = _resolve_full_image_urls(need_full) if need_full else {}
        need_ids = {r["ad_id"] for r in need_full}

        def fetch(r):
            full_url = full_urls.get((r["creative"] or {}).get("image_hash"), "")
            return _download_creative_image(r["ad_id"], r["creative"], full_url, replace=bool(full_url) and r["ad_id"] in need_ids)

        with ThreadPoolExecutor(max_workers=4) as pool:
            fetched = list(pool.map(fetch, todo))
        changed = False
        for r, (image_file, is_full) in zip(todo, fetched):
            c = r["creative"]
            old = db.get(r["ad_id"], {})
            entry = {
                "campaign_id": r["campaign_id"], "account_name": r.get("account_name"), "built": (r.get("created_time") or "")[:10],
                "type": c["type"] if c["type"] != "unknown" else old.get("type", "unknown"),
                "primary_text": c["primary_text"] or old.get("primary_text", ""),
                "headline": c["headline"] or old.get("headline", ""),
                "description": c["description"] or old.get("description", ""),
                "cta": c["cta"] or old.get("cta", ""),
                "image_hash": c["image_hash"] or old.get("image_hash", ""),
                "image_file": image_file or old.get("image_file", ""),
                "image_full": bool(is_full or old.get("image_full")),
            }
            if entry != old:
                db[r["ad_id"]] = entry
                changed = True
        if changed:
            CREATIVES_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CREATIVES_PATH, "w", encoding="utf-8") as f:
                json.dump(db, f, indent=1, ensure_ascii=False)


def _get_targeting(campaign_id):
    data = _get(f"{campaign_id}/adsets", {"fields": "targeting", "limit": 1})
    adsets = data.get("data", [])
    return adsets[0].get("targeting", {}) if adsets else {}


def _summarize_targeting(targeting):
    """Meta's Insights API only breaks delivery down to state-level 'region'
    -- it has no pincode/district breakdown, so that can't be shown as actual
    delivery data. What CAN be shown truthfully is what was configured: the
    real geo targeting (pincodes/cities/regions/radius points) and whether
    manual interest/behavior targeting was used at all."""
    geo = targeting.get("geo_locations", {}) or {}

    locations = []
    targeted_pincodes = set()
    if geo.get("zips"):
        pins = [z.get("name") or z.get("key", "").split(":")[-1] for z in geo["zips"]]
        targeted_pincodes = set(pins)
        locations.append(f"{len(pins)} pincode(s): {', '.join(pins)}")
    if geo.get("custom_locations"):
        locations.append(f"{len(geo['custom_locations'])} custom radius point(s)")
    if geo.get("cities"):
        locations.append(", ".join(c.get("name", "") for c in geo["cities"]) + " (city-level)")
    if geo.get("regions"):
        locations.append(", ".join(r.get("name", "") for r in geo["regions"]) + " (state-level)")
    if geo.get("countries"):
        locations.append(", ".join(geo["countries"]) + " (country-wide)")
    if not locations:
        locations.append("Not available")

    # Meta's "detailed targeting" spans many categories beyond interests/
    # behaviors -- job title (work_positions), employer, industry, education,
    # life events, etc. -- all delivered as flexible_spec entries keyed by
    # category. Missing any of these silently under-reports what's actually
    # configured (confirmed against Ads Manager's own "Audience details" panel,
    # which showed "Job title: Chartered accountant, Bank teller, ..." for an
    # ad set our earlier interests/behaviors-only extraction reported as
    # "no manual list was set").
    CATEGORY_LABELS = {
        "interests": "Interests",
        "behaviors": "Behaviors",
        "work_positions": "Job title",
        "work_employers": "Employer",
        "industries": "Industry",
        "education_statuses": "Education status",
        "education_schools": "School",
        "education_majors": "Field of study",
        "relationship_statuses": "Relationship status",
        "family_statuses": "Family status",
        "life_events": "Life event",
        "income": "Income",
        "home_ownership": "Home ownership",
        "home_type": "Home type",
        "net_worth": "Net worth",
        "generation": "Generation",
        "parents": "Parents",
    }

    def _category_names(container):
        found = {}
        for key, items in (container or {}).items():
            if not (isinstance(items, list) and items and isinstance(items[0], dict) and "name" in items[0]):
                continue
            label = CATEGORY_LABELS.get(key, key.replace("_", " ").title())
            found.setdefault(label, [])
            found[label] += [i["name"] for i in items if i.get("name")]
        return found

    categories = _category_names(targeting)
    for spec in targeting.get("flexible_spec", []) or []:
        for label, names in _category_names(spec).items():
            categories.setdefault(label, [])
            categories[label] += names

    advantage_on = (targeting.get("targeting_automation") or {}).get("advantage_audience") == 1

    if categories:
        parts = [f"{label}: {', '.join(dict.fromkeys(names))}" for label, names in categories.items()]
        interests_behaviors = "; ".join(parts)
        if advantage_on:
            interests_behaviors += " (Advantage+ Audience also on -- Meta may deliver beyond this list)"
    elif advantage_on:
        interests_behaviors = "Advantage+ Audience only (Meta auto-targets -- no manual interest/behavior/job-title list was set)"
    else:
        interests_behaviors = "Not available"

    setup = {
        "age_min": targeting.get("age_min"),
        "age_max": targeting.get("age_max"),
        # With Advantage+ Audience on, age_range is the advertiser's own
        # suggestion and age_min/age_max is the wider range Meta may use.
        "age_range": targeting.get("age_range"),
        "genders": targeting.get("genders") or [],
        "publisher_platforms": targeting.get("publisher_platforms") or [],
        "advantage_audience": advantage_on,
        "categories": {label: list(dict.fromkeys(names)) for label, names in categories.items()},
    }
    return {"locations": locations, "interests_behaviors": interests_behaviors, "targeted_pincodes": targeted_pincodes,
            "pincode_check_ok": True, "setup": setup}


def _display_status(campaign):
    """Meta's effective_status can stay ACTIVE past the campaign's own
    scheduled stop_time -- confirmed by direct testing: several campaigns here
    have a stop_time 1-2 days in the past while effective_status still reads
    ACTIVE. Surface that as COMPLETED instead of the raw (stale) ACTIVE label."""
    raw = campaign.get("effective_status", campaign.get("status"))
    stop_time = campaign.get("stop_time")
    if raw == "ACTIVE" and stop_time:
        from datetime import datetime, timezone
        if datetime.fromisoformat(stop_time) < datetime.now(timezone.utc):
            return "COMPLETED"
    return raw


def _first_impression_hour(campaign_id, date):
    """The daily insights row only says which *day* an ad first delivered.
    To get the actual time of day, re-query that single day with an hourly
    breakdown and take the earliest hour that has any impressions."""
    import json as _json
    data = _get(f"{campaign_id}/insights", {
        "fields": "impressions",
        "breakdowns": "hourly_stats_aggregated_by_advertiser_time_zone",
        "time_range": _json.dumps({"since": date, "until": date}),
    })
    rows = [r for r in data.get("data", []) if int(float(r.get("impressions", 0))) > 0]
    if not rows:
        return None
    rows.sort(key=lambda r: r["hourly_stats_aggregated_by_advertiser_time_zone"])
    return rows[0]["hourly_stats_aggregated_by_advertiser_time_zone"].split(" - ")[0]


def _extract_lead_form_id(ad):
    """The lead form isn't a normal field -- it's buried in the creative's
    object_story_spec, under whichever content type (link/video) the ad
    actually uses."""
    spec = ((ad.get("creative") or {}).get("object_story_spec")) or {}
    for block in ("link_data", "video_data"):
        cta = (spec.get(block) or {}).get("call_to_action") or {}
        form_id = (cta.get("value") or {}).get("lead_gen_form_id")
        if form_id:
            return form_id
    return None


def _get_lead_form_questions(form_id):
    data = _get(form_id, {"fields": "questions"})
    return {q["key"]: q["label"] for q in data.get("questions", [])}


def _get_leads(form_id):
    """Fetches every lead ever submitted to this form, not just the most
    recent 100 -- a single unpaginated fetch silently dropped older leads
    once a shared form's total submissions passed one page, which
    undercounted attributable leads even though Meta's own per-campaign
    insights count (what the dashboard totals are built from) stayed
    correct the whole time. Confirmed by direct testing: the gap between
    the dashboard's "146 leads" total and the "112" actually shown in the
    leads table matched this exact page-size cutoff."""
    leads = []
    params = {"fields": "id,created_time,field_data", "limit": 100}
    while True:
        data = _get(f"{form_id}/leads", params)
        leads.extend(data.get("data", []))
        paging = data.get("paging", {})
        after = paging.get("cursors", {}).get("after")
        if not paging.get("next") or not after:
            break
        params = {"fields": "id,created_time,field_data", "limit": 100, "after": after}
    return leads


def _fetch_readable_leads(form_id):
    """Real names/phone numbers/answers people submitted -- personal data,
    only ever shown in this local dashboard, never sent anywhere else."""
    try:
        questions = _get_lead_form_questions(form_id)
    except Exception:
        questions = {}
    try:
        raw_leads = _get_leads(form_id)
    except Exception:
        return []

    leads = []
    for lead in raw_leads:
        answers = {}
        for field in lead.get("field_data", []):
            label = questions.get(field["name"], field["name"])
            answers[label] = ", ".join(field.get("values", []))
        leads.append({"id": lead["id"], "created_time": lead.get("created_time"), "answers": answers})
    leads.sort(key=lambda l: l.get("created_time") or "", reverse=True)
    return leads


# The two screening questions on the locked lead form -- a lead only counts
# as genuinely qualified if the pincode matches AND both of these are "yes",
# per explicit instruction: pincode alone isn't enough, since someone in the
# right area who can't actually invest or has no franchise background isn't
# a real prospect. Matched by a distinctive English substring rather than the
# full bilingual label text -- robust to any whitespace/encoding differences
# in how the label got typed here vs. how Meta returns it.
INVESTMENT_QUESTION_MARKER = "invest minimum"
FRANCHISE_QUESTION_MARKER = "franchise background"


def _find_answer(answers, marker):
    for label, value in answers.items():
        if marker in label.lower():
            return value
    return None


_YES_VALUES = ("yes", "y", "हाँ", "हां", "ha")


def _answered_yes(answers, marker):
    val = (_find_answer(answers, marker) or "").strip().lower()
    return val in _YES_VALUES


def _passes_screening_questions(answers):
    return _answered_yes(answers, INVESTMENT_QUESTION_MARKER) and _answered_yes(answers, FRANCHISE_QUESTION_MARKER)


_PINCODE_RE = re.compile(r"\b(\d{6})\b")


def _extract_pincode(answers):
    """Finds the pincode the lead actually typed in, whatever the form
    labelled that question -- the field label varies by form (e.g. "Pin
    code/पिन कोड") but the answer itself is always a 6-digit Indian PIN."""
    for label, value in answers.items():
        if "pin" in label.lower() or "पिन" in label:
            m = _PINCODE_RE.search(value or "")
            if m:
                return m.group(1)
    for value in answers.values():
        m = _PINCODE_RE.search(value or "")
        if m:
            return m.group(1)
    return None


def _mark_pincode_qualified(leads_data, targeted_pincodes, pincode_check_ok=True):
    """Cross-checks each lead's self-reported pincode against the ad's actual
    targeted pincode list. A mismatch (e.g. someone outside the targeted area
    still submitting the form) is a real lead-quality signal worth surfacing,
    not just raw lead count.

    When the targeting fetch itself failed (Meta rate limit, etc.), there's no
    real pincode list to check against -- marking every lead "not qualified"
    in that case would silently misreport a data gap as a real finding
    (confirmed by direct testing: a transient per-ad rate limit turned a
    genuine "6 qualified" into a misleading "0 qualified" on one refresh).
    pincode_qualified is left as None so the UI can say "couldn't check"
    instead of "checked, didn't match"."""
    for lead in leads_data:
        pin = _extract_pincode(lead.get("answers", {}))
        lead["submitted_pincode"] = pin
        if not pincode_check_ok:
            lead["pincode_qualified"] = None
        else:
            lead["pincode_qualified"] = bool(pin and targeted_pincodes and pin in targeted_pincodes)


def _assign_leads_across_shared_forms(results):
    """Meta ties leads to the lead-generation FORM, not to whichever specific
    ad is being viewed -- when several ads share one form (as they do here,
    across states with completely different targeting), /form/leads returns
    every submission ever made to it, with no per-ad attribution at all.

    Each campaign's *count* of leads is still exact (from its own insights),
    but *which* of the form's leads those are has to be reconstructed. Since
    every campaign here has its own distinct targeted-pincode list, a lead's
    self-reported pincode is a real signal for whose lead it actually is --
    far better than guessing "most recent N," which broke down badly once
    ads targeting entirely different states shared one form (confirmed by
    direct testing: a Punjab-only ad's "most recent 36" ended up 100%
    non-Punjab, because dozens of newer UP/Delhi/Maharashtra leads from OTHER
    ads sharing the form crowded out every real Punjab lead).

    Assignment runs per form, across every campaign sharing it, so the same
    lead is never handed to two different ads at once:
      1. Fetch that form's full lead list once (not once per campaign).
      2. Campaigns with a narrower (more specific) targeted-pincode list get
         first pick of their real matches, so a broad/untargeted campaign
         can't grab a lead a narrower campaign would have matched exactly.
      3. Whatever's left after every campaign's real matches are claimed
         fills any remaining slots by recency, same fallback as before.
    """
    by_form = {}
    for r in results:
        form_id = r.get("_lead_form_id")
        if form_id and r.get("_campaign_lead_count", 0) > 0:
            by_form.setdefault(form_id, []).append(r)

    for form_id, campaigns in by_form.items():
        try:
            raw_leads = _fetch_readable_leads(form_id)
        except Exception:
            continue
        for lead in raw_leads:
            lead["submitted_pincode"] = _extract_pincode(lead.get("answers", {}))

        unassigned = list(raw_leads)  # newest-first, same order _fetch_readable_leads returns
        # Narrowest (most specific) targeting claims its real matches first --
        # campaigns with no usable pincode list (empty or rate-limited) go
        # last and only ever get recency leftovers.
        campaigns_by_specificity = sorted(
            campaigns,
            key=lambda r: len(r["targeting_summary"]["targeted_pincodes"]) or float("inf"),
        )

        for r in campaigns_by_specificity:
            need = r["_campaign_lead_count"]
            targeted = r["targeting_summary"]["targeted_pincodes"]
            pincode_check_ok = r["targeting_summary"]["pincode_check_ok"]
            assigned = []

            if pincode_check_ok and targeted:
                matches = [l for l in unassigned if l.get("submitted_pincode") in targeted]
                assigned.extend(matches[:need])
            for l in assigned:
                unassigned.remove(l)

            if len(assigned) < need:
                fill = unassigned[: need - len(assigned)]
                assigned.extend(fill)
                for l in fill:
                    unassigned.remove(l)

            _mark_pincode_qualified(assigned, targeted, pincode_check_ok)
            r["leads_data"] = assigned

    for r in results:
        r.pop("_lead_form_id", None)
        r.pop("_campaign_lead_count", None)


def _base_insights(campaign_id):
    data = _get(f"{campaign_id}/insights", {"fields": BASE_FIELDS, "date_preset": "maximum"})
    rows = data.get("data", [])
    return rows[0] if rows else None


def _breakdown_insights(campaign_id, breakdowns):
    data = _get(f"{campaign_id}/insights", {
        "fields": "impressions,reach,clicks,spend,actions",
        "breakdowns": ",".join(breakdowns),
        "date_preset": "maximum",
        "limit": 200,
    })
    return data.get("data", [])


def _extract_actions(actions):
    # "lead" and "onsite_conversion.lead_grouped" both represent the SAME
    # underlying conversions (Meta reports the same events under multiple
    # action_type labels for different attribution models) -- they must be
    # deduplicated by taking the max across the group, never summed. Confirmed
    # by direct testing: a campaign with 3 real leads reported both types at
    # value=3, which summing turned into a false "6".
    dedup_groups = {"leads": {"lead", "onsite_conversion.lead_grouped"}}
    single_keys = {
        "link_click": "link_clicks",
        "landing_page_view": "landing_page_views",
        "post_engagement": "post_engagement",
        "video_view": "video_views",
    }

    out = {}
    for a in actions or []:
        action_type = a.get("action_type")
        value = int(float(a.get("value", 0)))
        if action_type in single_keys:
            key = single_keys[action_type]
            out[key] = out.get(key, 0) + value
        else:
            for key, members in dedup_groups.items():
                if action_type in members:
                    out[key] = max(out.get(key, 0), value)
    return out


def get_all_real_ads():
    """Returns a list of dicts, one per campaign that actually delivered
    (has at least one ad object), sorted newest-first by created_time.
    Campaigns with no underlying ad (never fully built / failed mid-build)
    are silently skipped -- they have nothing real to show. Old, long-dead
    zero-delivery shells (e.g. 2021 test campaigns) are skipped too; a
    *recent* ad with zero impressions (just launched, still pending review)
    is kept.
    """
    try:
        accounts = _list_ad_accounts()
    except Exception as e:
        # Meta error 190 = the access token expired or was revoked. Report it
        # as such: the raw text carries neither the reason nor anything safe
        # to show (it includes the full request URL with the token).
        try:
            if e.response.json().get("error", {}).get("code") == 190:
                return {"error": "TOKEN_EXPIRED", "ads": []}
        except Exception:
            pass
        return {"error": str(e), "ads": []}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        campaigns_by_account = {}
        futures = {pool.submit(_list_campaigns, a["id"]): a for a in accounts}
        for future in as_completed(futures):
            account = futures[future]
            try:
                campaigns_by_account[account["id"]] = (account, future.result())
            except Exception:
                continue

        campaign_jobs = [
            (account, campaign)
            for account, campaigns in campaigns_by_account.values()
            for campaign in campaigns
        ]

        results = []
        futures = {pool.submit(_process_campaign, account, campaign): campaign for account, campaign in campaign_jobs}
        for future in as_completed(futures):
            try:
                row = future.result()
            except Exception:
                row = None
            if row is not None:
                results.append(row)

    try:
        _persist_creatives(results)
    except Exception as e:  # saving creatives must never break the main pull
        print(f"[all_ads] could not save creatives: {e}")
    _restore_targeting_from_db(results)
    _assign_leads_across_shared_forms(results)
    db = _update_ad_pincode_db(results)
    _remark_qualified_with_ad_pincode_db(results, db)

    results.sort(key=lambda r: r.get("created_time") or "", reverse=True)
    return {"error": None, "ads": results}


def _remark_qualified_with_ad_pincode_db(results, db):
    """Final pincode_qualified/qualified pass, per explicit instruction: check
    each lead's self-reported pincode against its OWN ad's real Meta
    targeting -- pulled from the persistent AD_PINCODE_DB_PATH database (built
    up across every campaign this account has ever run, old or new) rather
    than a village-name-derived list or a cache that resets on restart.

    Final "qualified" is two-stage: pincode match is necessary but not
    sufficient -- a lead in the right area who can't actually invest
    ₹5,00,000 or has no franchise background isn't a real prospect, so both
    screening-question answers must also be "yes." pincode_qualified is kept
    as its own field (stage 1 only) so the dashboard can still show *why* a
    lead didn't qualify, not just that it didn't."""
    for r in results:
        targeted = _effective_targeted_pincodes(r, db)
        for lead in r.get("leads_data", []):
            pin = lead.get("submitted_pincode")
            lead["pincode_qualified"] = bool(pin and targeted and pin in targeted)
            lead["passes_screening_questions"] = _passes_screening_questions(lead.get("answers", {}))
            lead["qualified"] = bool(lead["pincode_qualified"]) and lead["passes_screening_questions"]


def _process_campaign(account, campaign):
    """Fetches ads + insights (base + all breakdowns) for one campaign,
    running those calls concurrently. Returns None for shell campaigns
    (no ad ever built) or old dead zero-delivery test campaigns.
    """
    try:
        ads = _list_ads_for_campaign(campaign["id"])
    except Exception:
        ads = []
    if not ads:
        return None  # shell campaign, never actually ran

    ad = ads[0]
    lead_form_id = _extract_lead_form_id(ad)

    with ThreadPoolExecutor(max_workers=5) as pool:
        base_f = pool.submit(_base_insights, campaign["id"])
        age_gender_f = pool.submit(_breakdown_insights, campaign["id"], ["age", "gender"])
        platform_f = pool.submit(_breakdown_insights, campaign["id"], ["publisher_platform", "platform_position"])
        region_f = pool.submit(_breakdown_insights, campaign["id"], ["region"])
        with _TARGETING_CACHE_LOCK:
            cached_targeting_summary = _TARGETING_CACHE.get(campaign["id"])
        targeting_f = None if cached_targeting_summary else pool.submit(_get_targeting, campaign["id"])

        try:
            base = base_f.result() or {}
        except Exception:
            base = {}
        try:
            age_gender = age_gender_f.result()
        except Exception:
            age_gender = []
        try:
            platform = platform_f.result()
        except Exception:
            platform = []
        try:
            region = region_f.result()
        except Exception:
            region = []
        if cached_targeting_summary is not None:
            targeting_summary = cached_targeting_summary
        else:
            try:
                targeting_summary = _summarize_targeting(targeting_f.result())
                with _TARGETING_CACHE_LOCK:
                    _TARGETING_CACHE[campaign["id"]] = targeting_summary
            except requests.exceptions.HTTPError as e:
                # Distinguish "Meta is temporarily rate-limiting this account" from
                # "no targeting data exists" -- the former looks identical to a
                # settled/permanent state to a viewer if both just say "Not
                # available", but it clears on its own within a few minutes.
                # Confirmed by direct testing: string-matching the error text is
                # fragile (Meta's JSON has "code":17 with no space, not "code": 17)
                # -- parse the response body instead of guessing its formatting.
                # Deliberately NOT cached -- a failed fetch should retry fresh
                # on the next load, not freeze a fallback message in place.
                try:
                    is_rate_limited = e.response.json().get("error", {}).get("code") == 17
                except Exception:
                    is_rate_limited = False
                msg = "Rate-limited by Meta -- refresh the dashboard in a few minutes" if is_rate_limited else "Could not load"
                targeting_summary = {"locations": [msg], "interests_behaviors": msg, "targeted_pincodes": set(), "pincode_check_ok": False}
            except Exception:
                targeting_summary = {"locations": ["Not available"], "interests_behaviors": "Not available", "targeted_pincodes": set(), "pincode_check_ok": False}

    actions = _extract_actions(base.get("actions"))
    # Meta ties leads to the FORM, not the specific ad -- the same form can be
    # (and here, is) reused across multiple ads/ad accounts, so /form/leads
    # returns every lead ever submitted to it regardless of which ad is being
    # looked at. The properly per-campaign-scoped *count* comes from this
    # campaign's own insights actions (onsite_conversion.lead_grouped); which
    # *specific* leads those are gets resolved afterward, once every campaign
    # sharing this form is known -- see _assign_leads_across_shared_forms.
    campaign_lead_count = actions.get("leads", 0)
    impressions = int(float(base.get("impressions", 0)))
    created_time = campaign.get("created_time") or ""
    # date_start on the insights row is the earliest day Meta has delivery
    # data for -- i.e. the day the ad actually first got an impression, which
    # is often later than created_time (campaigns sit in review before they
    # start showing).
    first_impression_date = base.get("date_start") if impressions > 0 else None
    first_impression_hour = None
    if first_impression_date:
        try:
            first_impression_hour = _first_impression_hour(campaign["id"], first_impression_date)
        except Exception:
            first_impression_hour = None

    if impressions == 0 and created_time[:4] < "2025":
        return None

    return {
        "account_name": account["name"],
        "account_id": account["id"],
        "campaign_id": campaign["id"],
        "campaign_name": campaign["name"],
        "status": _display_status(campaign),
        "objective": campaign.get("objective"),
        "created_time": created_time,
        "first_impression_date": first_impression_date,
        "first_impression_hour": first_impression_hour,
        "ad_id": ad["id"],
        "ad_name": ad.get("name"),
        "thumbnail_url": (ad.get("creative") or {}).get("thumbnail_url"),
        "creative": _extract_creative(ad.get("creative")),
        "metrics": {
            "impressions": impressions,
            "reach": int(float(base.get("reach", 0))),
            "clicks": int(float(base.get("clicks", 0))),
            "ctr": round(float(base.get("ctr", 0)), 2),
            "cpc": round(float(base.get("cpc", 0)), 2),
            "cpm": round(float(base.get("cpm", 0)), 2),
            "spend": round(float(base.get("spend", 0)), 2),
            "link_clicks": actions.get("link_clicks", int(float(base.get("inline_link_clicks", 0) or 0))),
            "landing_page_views": actions.get("landing_page_views", 0),
            "leads": campaign_lead_count if lead_form_id else actions.get("leads", 0),
        },
        "breakdowns": {
            "age_gender": age_gender,
            "platform": platform,
            "region": region,
        },
        "targeting_summary": targeting_summary,
        "leads_data": [],  # filled in afterward by _assign_leads_across_shared_forms
        "_lead_form_id": lead_form_id,
        "_campaign_lead_count": campaign_lead_count,
    }
