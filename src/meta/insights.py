import random
import requests

from .. import config

GRAPH_URL = f"https://graph.facebook.com/{config.FB_API_VERSION}"

INSIGHT_FIELDS = "reach,impressions,frequency,clicks,ctr,cpc,cpm,spend,actions"


def _mock_insights():
    """Used while there's no live campaign to poll -- lets the reporting/agent
    pipeline be exercised end-to-end before real Meta credentials exist."""
    impressions = random.randint(2000, 8000)
    reach = int(impressions / random.uniform(1.1, 1.8))
    clicks = int(impressions * random.uniform(0.01, 0.04))
    spend = round(clicks * random.uniform(3, 9), 2)
    leads = int(clicks * random.uniform(0.05, 0.15))
    return {
        "reach": reach,
        "impressions": impressions,
        "frequency": round(impressions / reach, 2),
        "clicks": clicks,
        "ctr": round(clicks / impressions * 100, 2),
        "cpc": round(spend / clicks, 2) if clicks else 0,
        "cpm": round(spend / impressions * 1000, 2),
        "spend": spend,
        "leads": leads,
        "cpa": round(spend / leads, 2) if leads else None,
        "source": "mock",
    }


def get_insights(ad_id_chain):
    if ad_id_chain["mode"] == "dry_run":
        return _mock_insights()

    token = config.FB_ACCESS_TOKEN
    resp = requests.get(
        f"{GRAPH_URL}/{ad_id_chain['campaign_id']}/insights",
        params={"access_token": token, "fields": INSIGHT_FIELDS},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    if not data:
        return {"reach": 0, "impressions": 0, "clicks": 0, "spend": 0, "leads": 0, "source": "live"}

    row = data[0]
    leads = 0
    for action in row.get("actions", []):
        if action.get("action_type") in ("lead", "onsite_conversion.lead_grouped"):
            leads += int(action.get("value", 0))

    spend = float(row.get("spend", 0))
    return {
        "reach": int(row.get("reach", 0)),
        "impressions": int(row.get("impressions", 0)),
        "frequency": float(row.get("frequency", 0)),
        "clicks": int(row.get("clicks", 0)),
        "ctr": float(row.get("ctr", 0)),
        "cpc": float(row.get("cpc", 0)),
        "cpm": float(row.get("cpm", 0)),
        "spend": spend,
        "leads": leads,
        "cpa": round(spend / leads, 2) if leads else None,
        "source": "live",
    }
