import sys
import json
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import io
import os

from flask import Flask, render_template, request, send_from_directory, redirect, url_for, send_file, abort, jsonify

from src import config
from src.creative import copy_gen, image_gen, compose, captions
from src.meta import auth, campaign_builder, insights, all_ads, signups as signups_module
from src.agent import optimizer
from src.targeting import PRIORITY_DISTRICTS, EXCLUDED_DISTRICTS
import performance
import experiments

app = Flask(__name__)
import dash_login
dash_login.init_auth(app)

DEFAULT_LOGO_PATH = str(config.ROOT / "assets" / "eko-logo.svg")
LAST_RUN_PATH = config.DATA_DIR / "last_run.json"

# Plain-language translations of the optimizer's action codes, for people who
# don't know what "CPA" or "frequency" mean. "icon" refers to a macro name in
# templates/_icons.html.
ACTION_EXPLANATIONS = {
    "pause": {"icon": "warning", "tone": "warn", "text": "You've spent more than your daily budget. Consider pausing this ad or raising the budget."},
    "increase_budget": {"icon": "check", "tone": "good", "text": "This ad is getting good results for a low cost. You could reach more people by increasing the budget."},
    "flag_fatigue": {"icon": "sleep", "tone": "warn", "text": "The same people are seeing this ad too many times. It may be time to refresh the picture or wording."},
    "hold": {"icon": "thumbsup", "tone": "good", "text": "Everything looks fine. No action needed right now."},
}


def _save_last_run(result, targeting_data, guardrails, metrics, actions):
    LAST_RUN_PATH.write_text(json.dumps({
        "result": result,
        "targeting": targeting_data,
        "guardrails": guardrails,
        "metrics": metrics,
        "actions": actions,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_last_run():
    if not LAST_RUN_PATH.exists():
        return None
    try:
        return json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _insights_context():
    """Shared by the Ad Studio page and the refresh action -- keeps the 'how's
    my ad doing' section and its plain-language advice in one place."""
    last_run = _load_last_run()
    if not last_run:
        return {"has_insights": False}

    explained_actions = [
        {**ACTION_EXPLANATIONS.get(action, {"icon": "info", "tone": "good", "text": reason}), "reason": reason}
        for action, reason in last_run["actions"]
    ]
    return {
        "has_insights": True,
        "metrics": last_run["metrics"],
        "explained_actions": explained_actions,
        "guardrails": last_run["guardrails"],
    }


@app.route("/")
def index():
    return render_template(
        "index.html",
        locked_captions=list(enumerate(captions.LOCKED_CAPTIONS, start=1)),
        priority_districts=PRIORITY_DISTRICTS,
        excluded_districts=EXCLUDED_DISTRICTS,
        default_logo=DEFAULT_LOGO_PATH,
        connection_status=auth.connection_status(),
        **_insights_context(),
    )


@app.route("/run", methods=["POST"])
def run():
    f = request.form

    brief = {
        "product": f.get("product", "SBI Kiosk Operator / CSP Agent opportunity"),
        "description": f.get("description", ""),
        "objective": f.get("objective", "leads"),
        "tone": f.get("tone", "friendly, trustworthy, aspirational"),
        "visual_style": f.get("visual_style", "warm documentary photo-realistic"),
        "language": f.get("language", "hindi"),
        "angle": f.get("angle", "general_opportunity"),
    }

    brand = {
        "logo_path": f.get("logo_path") or None,
        "brand_name": f.get("brand_name", "eko"),
        "brand_subtitle": f.get("brand_subtitle", "KIOSK"),
        "primary_color": f.get("primary_color", "#1b4f9c"),
        "accent_color": f.get("accent_color", "#f5821f"),
    }

    caption_choice = f.get("caption_choice", "1")
    if caption_choice == "generate":
        ad_copy = copy_gen.generate_ad_copy(brief, angle=brief["angle"])
    else:
        ad_copy = captions.caption_to_ad_copy(int(caption_choice))

    districts_raw = f.get("districts", ",".join(PRIORITY_DISTRICTS))
    districts = [d.strip() for d in districts_raw.split(",") if d.strip() and d.strip() not in EXCLUDED_DISTRICTS]

    targeting_data = {
        "districts": districts,
        "country": "India",
        "age_min": int(f.get("age_min", 21)),
        "age_max": int(f.get("age_max", 45)),
        "gender": f.get("gender", "all"),
        "daily_budget": float(f.get("daily_budget", 500)),
        "placements": f.get("placements", "advantage"),
        "schedule_days": int(f.get("schedule_days", 7)),
    }

    attachment = {"type": "website"}
    if f.get("url"):
        attachment["url"] = f.get("url")
    if f.get("number"):
        attachment["number"] = f.get("number")
    if not attachment.get("url") and not attachment.get("number"):
        attachment["url"] = "kiosk.eko.in/signup"

    image_count = int(f.get("image_count", 4))
    bg_paths = image_gen.generate_images(brief, count=image_count, out_prefix="run_bg")

    if not bg_paths:
        return render_template(
            "results.html",
            brief=brief, ad_copy=ad_copy, final_images=[],
            connection_status=auth.connection_status(),
            mode="error",
            build_error="No background images could be generated (Pollinations.ai may be unreachable or rate-limited). Nothing was built -- try again in a moment.",
            result=None, payload_json=None, metrics=None, actions=None,
        )

    final_paths = []
    for i, bg_path in enumerate(bg_paths):
        out_path = config.OUTPUT_DIR / f"run_final_{i+1}.png"
        try:
            compose.compose_ad(bg_path, ad_copy, attachment, brand=brand, out_path=out_path)
            final_paths.append(out_path.name)
        except Exception as e:
            print(f"[webapp] compose failed for variant {i+1}: {e}")

    # The creative (images + copy) is already done at this point -- whatever
    # happens with the Meta API next must never make that work disappear from
    # the page. A failed live build (e.g. app not yet published on Meta's side)
    # shows an error banner but still shows the finished creative.
    try:
        result, mode = campaign_builder.build_campaign(brief, ad_copy, bg_paths, targeting_data, attachment)
        build_error = None
    except Exception as e:
        result, mode = None, "error"
        build_error = str(e)
        print(f"[webapp] campaign build failed: {build_error}")

    guardrails = {
        "max_daily_spend": targeting_data["daily_budget"],
        "target_cpa": float(f.get("target_cpa", 150)),
    }

    metrics = actions = payload_json = None
    if result is not None:
        metrics = insights.get_insights(result)
        actions = optimizer.evaluate(metrics, guardrails)
        payload_json = json.dumps(result.get("payload", {}), indent=2, ensure_ascii=False)
        _save_last_run(result, targeting_data, guardrails, metrics, actions)

    return render_template(
        "results.html",
        brief=brief,
        ad_copy=ad_copy,
        final_images=final_paths,
        connection_status=auth.connection_status(),
        mode=mode,
        build_error=build_error,
        result=result,
        payload_json=payload_json,
        metrics=metrics,
        actions=actions,
    )


@app.route("/activate", methods=["POST"])
def activate():
    """Only reached if the user explicitly clicks Activate on the results page --
    never fires automatically."""
    campaign_id = request.form.get("campaign_id")
    ad_set_id = request.form.get("ad_set_id")
    ad_id = request.form.get("ad_id")
    mode = request.form.get("mode")
    result = {"mode": mode, "campaign_id": campaign_id, "ad_set_id": ad_set_id, "ad_id": ad_id}
    campaign_builder.activate(result)
    return render_template("activated.html", result=result)


@app.route("/refresh_insights", methods=["POST"])
def refresh_insights():
    """Pulls a fresh insights snapshot for the last ad -- real data if connected
    to Meta, a new simulated snapshot otherwise -- then returns to the Ad Studio
    page where the insights section lives."""
    last_run = _load_last_run()
    if last_run:
        metrics = insights.get_insights(last_run["result"])
        actions = optimizer.evaluate(metrics, last_run["guardrails"])
        _save_last_run(last_run["result"], last_run["targeting"], last_run["guardrails"], metrics, actions)
    return redirect(url_for("index"))


# Populated by /dashboard, keyed by ad_id -- lets /dashboard/download/<ad_id>
# reuse the already-fetched leads instead of re-running the ~2min live Meta
# pull just to serve one file.
_DASHBOARD_ADS_BY_ID = {}
_DASHBOARD_TOTALS = {}
_DASHBOARD_ALL_LEADS = []  # every lead across every ad, tagged with its source ad -- for the combined view/download

# A live Meta pull across every ad takes 60-90+ seconds and gets slower as
# more ads get built -- doing that on every single /dashboard page view was
# confirmed to make page loads pile up: two overlapping loads fight over the
# same rate-limited Graph API slots (_RATE_LIMIT_SEM in all_ads.py) and both
# end up slower than either alone. A short TTL cache means most page views
# are instant, a fresh pull happens automatically once the data is more than
# a few minutes old, and the lock means concurrent requests share one pull
# instead of each kicking off their own.
_DASHBOARD_FETCH_LOCK = threading.Lock()
_DASHBOARD_LAST_FETCH = 0.0
_DASHBOARD_CACHE_TTL_SECONDS = 300


def _clean_error(msg):
    """Meta's request errors include the full URL, access token and all.
    Never let that reach a page, a log line or the status endpoint."""
    if not msg:
        return msg
    msg = str(msg)
    low = msg.lower()
    if "session has expired" in low or "error validating access token" in low or "code\": 190" in low or "access token" in low and "expired" in low:
        return "TOKEN_EXPIRED"
    return re.sub(r"access_token=[^&\s)'\"]+", "access_token=REDACTED", msg)


def _refresh_dashboard_cache():
    """Live-pulls every ad and repopulates the module-level dashboard caches
    (_DASHBOARD_ADS_BY_ID / _DASHBOARD_TOTALS / _DASHBOARD_ALL_LEADS) that
    /dashboard, /overview, /signups, and the /api/facebook-leads integration
    endpoint all read from -- one place to keep them in sync instead of each
    route recomputing totals its own way."""
    data = all_ads.get_all_real_ads()
    ads = data["ads"]
    # A failed or empty pull must never wipe data we already have.
    if data["error"] or not ads:
        return _clean_error(data["error"]) or "Meta returned no ads", None

    _apply_ads(ads)
    return None, ads


def _apply_ads(ads):
    _DASHBOARD_ADS_BY_ID.clear()
    _DASHBOARD_ADS_BY_ID.update({a["ad_id"]: a for a in ads})

    totals = {
        "impressions": sum(a["metrics"]["impressions"] for a in ads),
        "reach": sum(a["metrics"]["reach"] for a in ads),
        "clicks": sum(a["metrics"]["clicks"] for a in ads),
        "link_clicks": sum(a["metrics"]["link_clicks"] for a in ads),
        "landing_page_views": sum(a["metrics"]["landing_page_views"] for a in ads),
        "leads": sum(a["metrics"]["leads"] for a in ads),
        "pincode_qualified_leads": sum(
            1 for a in ads for lead in a["leads_data"] if lead.get("pincode_qualified")
        ),
        "qualified_leads": sum(
            1 for a in ads for lead in a["leads_data"] if lead.get("qualified")
        ),
        "spend": round(sum(a["metrics"]["spend"] for a in ads), 2),
        "active_count": sum(1 for a in ads if a["status"] == "ACTIVE"),
        "completed_count": sum(1 for a in ads if a["status"] == "COMPLETED"),
        "other_count": sum(1 for a in ads if a["status"] not in ("ACTIVE", "COMPLETED")),
    }

    _DASHBOARD_TOTALS.clear()
    _DASHBOARD_TOTALS.update(totals)

    _DASHBOARD_ALL_LEADS.clear()
    for a in ads:
        for lead in a["leads_data"]:
            _DASHBOARD_ALL_LEADS.append({
                **lead,
                "_ad_name": a["ad_name"] or a["campaign_name"],
                "_account_name": a["account_name"],
                "_ad_built_date": a.get("created_time"),
            })
    _DASHBOARD_ALL_LEADS.sort(key=lambda l: l.get("created_time") or "", reverse=True)


SNAPSHOT_PATH = config.DATA_DIR / "dashboard_snapshot.json"


def _save_snapshot(ads, fetched_at):
    """Keeps the last good pull on disk so a restart (or a slow Meta) never
    leaves the dashboard blank -- it opens instantly on the saved data and
    refreshes in the background."""
    try:
        tmp = SNAPSHOT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": fetched_at, "ads": ads}, default=lambda o: sorted(o) if isinstance(o, set) else str(o),
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(SNAPSHOT_PATH)
    except Exception as e:
        print(f"[dashboard] could not save snapshot: {e}")


def _load_snapshot():
    try:
        d = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None, 0.0
    ads = d.get("ads") or []
    for a in ads:
        ts = a.get("targeting_summary") or {}
        ts["targeted_pincodes"] = set(ts.get("targeted_pincodes") or [])
    return ads, float(d.get("fetched_at") or 0)


_DASHBOARD_REFRESHING = False
_DASHBOARD_LAST_ERROR = None
_DASHBOARD_LAST_ATTEMPT = 0.0


def _background_refresh():
    global _DASHBOARD_LAST_FETCH, _DASHBOARD_REFRESHING, _DASHBOARD_LAST_ERROR, _DASHBOARD_LAST_ATTEMPT
    try:
        _DASHBOARD_LAST_ATTEMPT = time.time()
        with _DASHBOARD_FETCH_LOCK:
            error, ads = _refresh_dashboard_cache()
            _DASHBOARD_LAST_ERROR = error
            if ads is not None:
                _DASHBOARD_LAST_FETCH = time.time()
                _save_snapshot(ads, _DASHBOARD_LAST_FETCH)
    except Exception as e:
        _DASHBOARD_LAST_ERROR = _clean_error(e)
        print(f"[dashboard] background refresh failed: {_DASHBOARD_LAST_ERROR}")
    finally:
        _DASHBOARD_REFRESHING = False


def _start_background_refresh():
    global _DASHBOARD_REFRESHING
    if _DASHBOARD_REFRESHING:
        return
    _DASHBOARD_REFRESHING = True
    threading.Thread(target=_background_refresh, daemon=True).start()


@app.context_processor
def _inject_refresh_state():
    return {"refreshing": _DASHBOARD_REFRESHING, "dashboard_loaded": bool(_DASHBOARD_ADS_BY_ID),
            "refresh_error": _DASHBOARD_LAST_ERROR, "token_expired": _DASHBOARD_LAST_ERROR == "TOKEN_EXPIRED"}


@app.route("/dashboard/status")
def dashboard_status():
    return jsonify({"refreshing": _DASHBOARD_REFRESHING, "loaded": bool(_DASHBOARD_ADS_BY_ID),
                    "error": _DASHBOARD_LAST_ERROR})


def _get_dashboard_data(force=False, wait_if_empty=False):
    """Never makes a page wait on Meta when there is anything to show.

    - Data in memory: returned instantly; if it is older than the TTL (or
      force=True) a refresh runs in the background.
    - Nothing in memory but a saved snapshot on disk: loaded instantly
      (a restart no longer means a blank page), refresh runs in the
      background.
    - Nothing at all (first ever run): starts the pull in the background and
      returns empty so the page can say "loading" -- unless wait_if_empty
      (the JSON API), which waits for the pull.
    """
    global _DASHBOARD_LAST_FETCH
    if not _DASHBOARD_ADS_BY_ID:
        ads, fetched_at = _load_snapshot()
        if ads:
            _apply_ads(ads)
            _DASHBOARD_LAST_FETCH = fetched_at

    if not _DASHBOARD_ADS_BY_ID:
        if wait_if_empty:
            with _DASHBOARD_FETCH_LOCK:
                if not _DASHBOARD_ADS_BY_ID:
                    error, ads = _refresh_dashboard_cache()
                    if ads is not None:
                        _DASHBOARD_LAST_FETCH = time.time()
                        _save_snapshot(ads, _DASHBOARD_LAST_FETCH)
                    return error, list(_DASHBOARD_ADS_BY_ID.values()), _DASHBOARD_LAST_FETCH
        _start_background_refresh()
        return _DASHBOARD_LAST_ERROR, [], _DASHBOARD_LAST_FETCH

    # After a failed refresh, wait a full TTL before trying again; otherwise
    # every page view would kick off another failing call (and the auto-reload
    # would loop). A manual "Refresh now" (force) still tries immediately.
    since = time.time() - (_DASHBOARD_LAST_ATTEMPT if _DASHBOARD_LAST_ERROR else _DASHBOARD_LAST_FETCH)
    stale = force or since > _DASHBOARD_CACHE_TTL_SECONDS
    if stale:
        _start_background_refresh()
    return None, list(_DASHBOARD_ADS_BY_ID.values()), _DASHBOARD_LAST_FETCH


@app.route("/dashboard")
def dashboard():
    force = request.args.get("refresh") == "1"
    error, ads, last_fetch = _get_dashboard_data(force=force)
    return render_template(
        "dashboard.html",
        connection_status=auth.connection_status(),
        error=error,
        ads=ads,
        totals=_DASHBOARD_TOTALS,
        all_leads=_DASHBOARD_ALL_LEADS,
        last_fetch=datetime.fromtimestamp(last_fetch, tz=timezone.utc).isoformat() if last_fetch else None,
    )


@app.route("/api/facebook-leads")
def api_facebook_leads():
    """Read-only JSON feed of this dashboard's Facebook Lead Form data, meant
    to be pulled into an external "main" dashboard rather than embedding this
    whole page. Pass ?refresh=1 to force a fresh live pull from Meta (slow --
    tens of seconds); without it, serves the warm in-memory cache (shared
    with /dashboard, refreshed automatically once it's more than 5 minutes
    old)."""
    error, _, last_fetch = _get_dashboard_data(force=bool(request.args.get("refresh")), wait_if_empty=True)

    ads_out = []
    for a in _DASHBOARD_ADS_BY_ID.values():
        ads_out.append({
            "ad_id": a["ad_id"],
            "ad_name": a["ad_name"] or a["campaign_name"],
            "campaign_id": a["campaign_id"],
            "account_name": a["account_name"],
            "status": a["status"],
            "objective": a["objective"],
            "created_time": a.get("created_time"),
            "metrics": a["metrics"],
            "targeted_pincodes": sorted(a["targeting_summary"].get("targeted_pincodes") or []),
            "leads_count": len(a["leads_data"]),
            "qualified_count": sum(1 for l in a["leads_data"] if l.get("qualified")),
            "pincode_qualified_count": sum(1 for l in a["leads_data"] if l.get("pincode_qualified")),
        })

    leads_out = [
        {
            "lead_id": lead.get("id"),
            "ad_id": next((a["ad_id"] for a in _DASHBOARD_ADS_BY_ID.values()
                           if (a["ad_name"] or a["campaign_name"]) == lead["_ad_name"]), None),
            "ad_name": lead["_ad_name"],
            "account_name": lead["_account_name"],
            "created_time": lead.get("created_time"),
            "full_name": lead.get("answers", {}).get("Full name", ""),
            "phone_number": lead.get("answers", {}).get("Phone number", ""),
            "submitted_pincode": lead.get("submitted_pincode"),
            "pincode_qualified": lead.get("pincode_qualified"),
            "passes_screening_questions": lead.get("passes_screening_questions"),
            "qualified": lead.get("qualified"),
            "answers": lead.get("answers", {}),
        }
        for lead in _DASHBOARD_ALL_LEADS
    ]

    resp = jsonify({
        "error": error,
        "generated_at": datetime.fromtimestamp(last_fetch, tz=timezone.utc).isoformat() if last_fetch else None,
        "totals": dict(_DASHBOARD_TOTALS),
        "ads": ads_out,
        "leads": leads_out,
    })
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/performance")
def performance_page():
    error, ads, last_fetch = _get_dashboard_data()
    known_campaign_names = {a["campaign_id"]: a["campaign_name"] for a in ads}
    signup_data = signups_module.get_all_signups(known_campaign_names=known_campaign_names)
    report = performance.build(ads, signup_data["signups"])
    return render_template(
        "performance.html",
        connection_status=auth.connection_status(),
        error=error,
        signups_error=signup_data["error"],
        last_fetch=datetime.fromtimestamp(last_fetch, tz=timezone.utc).isoformat() if last_fetch else None,
        **report,
    )


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@app.route("/creative-image/<ad_id>")
def creative_image(ad_id):
    """Serves the picture saved for an ad. The id must be all digits, and the
    file name comes from our own record, never from the request."""
    if not ad_id.isdigit():
        abort(404)
    entry = _read_json(config.DATA_DIR / "ad_creatives.json").get(ad_id) or {}
    name = entry.get("image_file")
    if not name:
        abort(404)
    return send_from_directory(config.DATA_DIR / "creatives", name)


@app.route("/experiments")
def experiments_page():
    error, ads, last_fetch = _get_dashboard_data()
    known_campaign_names = {a["campaign_id"]: a["campaign_name"] for a in ads}
    signup_data = signups_module.get_all_signups(known_campaign_names=known_campaign_names)
    report = performance.build(ads, signup_data["signups"])
    try:
        opt = json.loads((config.DATA_DIR / "ad_optimization.json").read_text(encoding="utf-8"))
    except Exception:
        opt = {}
    creatives = _read_json(config.DATA_DIR / "ad_creatives.json")
    labels = _read_json(config.DATA_DIR / "creative_labels.json")
    data = experiments.build(report, dict(_DASHBOARD_TOTALS), opt, creatives, labels) if ads else {
        "experiments": [], "mistakes": [], "idea_groups": [], "n_ideas": 0, "verdict_counts": {}, "verdict_names": experiments.VERDICTS,
        "creative": {"pending": True, "groups": [], "trait_tables": [], "learning": [], "verdict": "unclear", "n_ads": 0, "n_covered": 0, "confidence": ("Low", "no data")},
        "competitors": experiments.COMPETITORS, "competitor_caveats": experiments.COMPETITOR_CAVEATS}
    return render_template(
        "experiments.html",
        error=error,
        signups_error=signup_data["error"],
        last_fetch=datetime.fromtimestamp(last_fetch, tz=timezone.utc).isoformat() if last_fetch else None,
        **data,
    )


@app.route("/overview")
def overview_page():
    # Same reuse-the-warm-cache approach as /signups -- combining two data
    # sources shouldn't cost two live Meta pulls when one already ran.
    known_campaign_names = {a["campaign_id"]: a["campaign_name"] for a in _DASHBOARD_ADS_BY_ID.values()}
    form_leads = sum(a["metrics"]["leads"] for a in _DASHBOARD_ADS_BY_ID.values() if a["objective"] == "LEAD_GENERATION")
    form_pincode_qualified = sum(
        1 for a in _DASHBOARD_ADS_BY_ID.values() if a["objective"] == "LEAD_GENERATION"
        for lead in a["leads_data"] if lead.get("pincode_qualified")
    )
    form_pincode_checkable = sum(
        len(a["leads_data"]) for a in _DASHBOARD_ADS_BY_ID.values()
        if a["objective"] == "LEAD_GENERATION" and a["targeting_summary"].get("pincode_check_ok")
    )
    form_qualified = sum(
        1 for a in _DASHBOARD_ADS_BY_ID.values() if a["objective"] == "LEAD_GENERATION"
        for lead in a["leads_data"] if lead.get("qualified")
    )

    data = signups_module.get_all_signups(known_campaign_names=known_campaign_names)
    signups = data["signups"]
    url_signups_total = len(signups)
    url_signups_facebook = sum(1 for s in signups if s["attribution_tier"] != "none")
    url_strong_qualify = sum(1 for s in signups if s["qualification_verdict"] == "STRONG_QUALIFY")
    url_not_qualify = sum(1 for s in signups if s["qualification_verdict"] and s["qualification_verdict"] != "STRONG_QUALIFY")

    totals = {
        "dashboard_cache_warm": bool(_DASHBOARD_ADS_BY_ID),
        "form_leads": form_leads,
        "form_qualified": form_qualified,
        "form_pincode_qualified": form_pincode_qualified,
        "form_pincode_checkable": form_pincode_checkable,
        "url_signups_total": url_signups_total,
        "url_signups_facebook": url_signups_facebook,
        "url_strong_qualify": url_strong_qualify,
        "url_not_qualify": url_not_qualify,
        "overall_total": form_leads + url_signups_total,
    }

    return render_template(
        "overview.html",
        connection_status=auth.connection_status(),
        dashboard_error=None if _DASHBOARD_ADS_BY_ID else "Visit the Dashboard tab first to load Facebook ad data.",
        signups_error=data["error"],
        totals=totals,
    )


@app.route("/signups")
def signups_page():
    # Reuse whatever campaign names /dashboard already fetched rather than
    # forcing another full live Meta pull just to label a campaign ID --
    # Meta's per-account rate limit is real and easy to trip (confirmed
    # repeatedly on this account), so only pay that cost if the data isn't
    # already sitting in memory from a recent /dashboard visit.
    known_campaign_names = {a["campaign_id"]: a["campaign_name"] for a in _DASHBOARD_ADS_BY_ID.values()}
    facebook_lead_form_leads = sum(
        a["metrics"]["leads"] for a in _DASHBOARD_ADS_BY_ID.values() if a["objective"] == "LEAD_GENERATION"
    )

    data = signups_module.get_all_signups(known_campaign_names=known_campaign_names)
    signups = data["signups"]

    totals = {
        "total_signups": len(signups),
        "campaign_matched": sum(1 for s in signups if s["attribution_tier"] == "campaign_matched"),
        "fbclid_only": sum(1 for s in signups if s["attribution_tier"] == "fbclid_only"),
        "not_facebook": sum(1 for s in signups if s["attribution_tier"] == "none"),
        "strong_qualify": sum(1 for s in signups if s["qualification_verdict"] == "STRONG_QUALIFY"),
        "facebook_lead_form_leads": facebook_lead_form_leads,
        "dashboard_cache_warm": bool(_DASHBOARD_ADS_BY_ID),
    }
    totals["facebook_attributed"] = totals["campaign_matched"] + totals["fbclid_only"]

    return render_template(
        "signups.html",
        connection_status=auth.connection_status(),
        error=data["error"],
        signups=signups,
        totals=totals,
    )


@app.route("/dashboard/download-all")
def dashboard_download_all():
    if not _DASHBOARD_ALL_LEADS:
        abort(404, "No cached leads yet -- refresh the dashboard and try again.")

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    # Optional ?from=YYYY-MM-DD&to=YYYY-MM-DD to download just a date window
    # (e.g. "since I last downloaded") instead of every lead ever pulled.
    # Both are inclusive; either can be given alone. Compared against the
    # date portion of created_time, which is UTC (same as Meta reports it).
    from_date = request.args.get("from", "").strip()
    to_date = request.args.get("to", "").strip()
    # Optional ?filter=qualified|pincode for a one-click export matching the
    # same three counts shown on the Totals card, as a separate download per
    # metric rather than one export with everything mixed together.
    lead_filter = request.args.get("filter", "").strip()

    leads_to_export = _DASHBOARD_ALL_LEADS
    if lead_filter == "qualified":
        leads_to_export = [l for l in leads_to_export if l.get("qualified")]
    elif lead_filter == "pincode":
        leads_to_export = [l for l in leads_to_export if l.get("pincode_qualified")]

    if from_date or to_date:
        def _in_range(lead):
            day = (lead.get("created_time") or "")[:10]
            if not day:
                return False
            if from_date and day < from_date:
                return False
            if to_date and day > to_date:
                return False
            return True
        leads_to_export = [l for l in leads_to_export if _in_range(l)]

    if not leads_to_export:
        if lead_filter:
            abort(404, f"No leads match the '{lead_filter}' filter for this date range.")
        abort(404, f"No leads found between {from_date or 'the start'} and {to_date or 'now'}.")

    other_labels = []
    for lead in leads_to_export:
        for label in lead.get("answers", {}):
            if label not in ("Full name", "Phone number") and label not in other_labels:
                other_labels.append(label)

    wb = Workbook()
    ws = wb.active
    ws.title = "All Leads"

    headers = ["Ad", "Account", "Submitted", "Lead ID", "Qualified", "Pincode qualified"] + other_labels + ["Phone number", "Full name"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1B4F9C")
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"

    for lead in leads_to_export:
        answers = lead.get("answers", {})
        if lead.get("submitted_pincode") is None:
            qualified_text = "No pincode found"
        elif lead.get("pincode_qualified") is None:
            qualified_text = f"Could not check ({lead['submitted_pincode']})"
        elif lead.get("pincode_qualified"):
            qualified_text = f"Qualified ({lead['submitted_pincode']})"
        else:
            qualified_text = f"Not qualified ({lead['submitted_pincode']})"
        overall_text = "Qualified" if lead.get("qualified") else "Not qualified"
        ws.append([
            lead.get("_ad_name", ""),
            lead.get("_account_name", ""),
            (lead.get("created_time") or "")[:16].replace("T", " "),
            lead.get("id", ""),
            overall_text,
            qualified_text,
            *[answers.get(label, "") for label in other_labels],
            answers.get("Phone number", ""),
            answers.get("Full name", ""),
        ])
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Arial")
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    lead_id_col = 4
    phone_col = len(headers) - 1
    for col in (lead_id_col, phone_col):
        for cell in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            cell[0].number_format = "@"

    ws.auto_filter.ref = ws.dimensions

    widths = [26, 16, 16, 18, 14, 20] + [28] * len(other_labels) + [16, 20]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    name_parts = []
    if lead_filter == "qualified":
        name_parts.append("Qualified")
    elif lead_filter == "pincode":
        name_parts.append("Pincode_Matched")
    else:
        name_parts.append("All")
    name_parts.append("Facebook_Form_Leads")
    if from_date or to_date:
        name_parts.append(f"{from_date or 'start'}_to_{to_date or 'now'}")
    download_name = "_".join(name_parts) + ".xlsx"

    return send_file(
        buf,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/dashboard/download/<ad_id>")
def dashboard_download(ad_id):
    ad = _DASHBOARD_ADS_BY_ID.get(ad_id)
    if ad is None or not ad.get("leads_data"):
        abort(404, "No cached leads for this ad -- refresh the dashboard and try again.")

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    # Form-question columns get their own column, in the order the form
    # collects them, with Phone/Full name pinned last -- matches the layout
    # the user asked for (Submitted, Lead ID, Pin code/other questions..., Phone, Full name).
    other_labels = []
    for lead in ad["leads_data"]:
        for label in lead.get("answers", {}):
            if label not in ("Full name", "Phone number") and label not in other_labels:
                other_labels.append(label)

    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"

    headers = ["Submitted", "Lead ID", "Qualified", "Pincode qualified"] + other_labels + ["Phone number", "Full name"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1B4F9C")
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"

    for lead in ad["leads_data"]:
        answers = lead.get("answers", {})
        if lead.get("submitted_pincode") is None:
            qualified_text = "No pincode found"
        elif lead.get("pincode_qualified") is None:
            qualified_text = f"Could not check ({lead['submitted_pincode']})"
        elif lead.get("pincode_qualified"):
            qualified_text = f"Qualified ({lead['submitted_pincode']})"
        else:
            qualified_text = f"Not qualified ({lead['submitted_pincode']})"
        overall_text = "Qualified" if lead.get("qualified") else "Not qualified"
        ws.append([
            (lead.get("created_time") or "")[:16].replace("T", " "),
            lead.get("id", ""),
            overall_text,
            qualified_text,
            *[answers.get(label, "") for label in other_labels],
            answers.get("Phone number", ""),
            answers.get("Full name", ""),
        ])
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Arial")
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    # Force text format on Lead ID / Phone number -- long digit strings Excel
    # would otherwise auto-detect as numbers and mangle into scientific
    # notation (e.g. "9.19815E+11" instead of the actual phone number).
    lead_id_col = 2
    phone_col = len(headers) - 1
    for col in (lead_id_col, phone_col):
        for cell in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            cell[0].number_format = "@"

    ws.auto_filter.ref = ws.dimensions

    widths = [16, 18, 14, 20] + [28] * len(other_labels) + [16, 20]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in (ad.get("ad_name") or ad["campaign_id"]))[:60]
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"{safe_name}_leads.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/output/<path:filename>")
def output_file(filename):
    return send_from_directory(config.OUTPUT_DIR, filename)


def _find_free_port(preferred=5000, tries=20):
    import socket
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range {preferred}-{preferred + tries - 1}")


if __name__ == "__main__":
    # debug=True's auto-reloader runs the app in a child process; if that
    # child dies unexpectedly it can leave the port stuck in a state Windows
    # won't let a new process rebind to. Reloader off + auto port fallback
    # means a stuck port from a previous run can never fully block startup.
    # threaded=True keeps the slow /dashboard route (many live Meta API
    # calls) from blocking every other request on the server.
    # In a container set APP_HOST=0.0.0.0 (the Dockerfile uses gunicorn instead,
    # which reads PORT itself). Defaults keep the local behaviour unchanged.
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ["PORT"]) if os.environ.get("PORT") else _find_free_port(5000)
    print(f"\n  AdMarketer running at http://{host}:{port}\n")
    app.run(debug=False, use_reloader=False, threaded=True, host=host, port=port)
