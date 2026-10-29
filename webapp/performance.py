"""Ad Performance tab: compares every ad that spent money, and breaks results
down by the targeting each ad used (pincodes, states, audience type)."""

FORM_OBJECTIVES = ("LEAD_GENERATION", "OUTCOME_LEADS")

# Minimum volume before an ad can win a category -- otherwise an ad with one
# lucky lead at ₹20 "beats" an ad with 60 leads at ₹30.
MIN_RESULTS_FOR_COST_AWARD = 10
MIN_QUALITY_FOR_BEST_OVERALL = 3
MIN_IMPRESSIONS_FOR_CTR_AWARD = 5000
MIN_LPV_FOR_LPV_AWARD = 100

# Indian PIN codes are allocated by postal region, so the first three digits
# identify the state closely enough to compare delivery with lead origin.
# A handful of border ranges are shared, so treat state-level lead counts as
# approximate.
_PIN_PREFIX_STATES = [
    (110, 110, "Delhi"), (121, 136, "Haryana"), (140, 159, "Punjab"), (160, 160, "Chandigarh"),
    (171, 177, "Himachal Pradesh"), (180, 194, "Jammu and Kashmir"),
    (246, 249, "Uttarakhand"), (262, 263, "Uttarakhand"), (201, 285, "Uttar Pradesh"),
    (301, 345, "Rajasthan"), (360, 396, "Gujarat"), (400, 445, "Maharashtra"),
    (450, 488, "Madhya Pradesh"), (490, 497, "Chhattisgarh"), (500, 509, "Telangana"),
    (510, 535, "Andhra Pradesh"), (560, 591, "Karnataka"), (600, 643, "Tamil Nadu"),
    (670, 695, "Kerala"), (700, 743, "West Bengal"), (750, 770, "Odisha"),
    (781, 788, "Assam"), (814, 835, "Jharkhand"), (800, 855, "Bihar"),
]


def state_for_pincode(pin):
    if not pin or len(pin) != 6 or not pin.isdigit():
        return None
    prefix = int(pin[:3])
    for lo, hi, state in _PIN_PREFIX_STATES:
        if lo <= prefix <= hi:
            return state
    return "Other"


def _meta_region_name(name):
    name = (name or "").strip()
    return name[: -len(" region")] if name.endswith(" region") else name


def _safe_div(a, b):
    return round(a / b, 2) if b else None


def audience_type(interests_behaviors):
    text = (interests_behaviors or "").strip()
    if not text or text.startswith(("Rate-limited", "Could not load", "Not available", "Not recorded")):
        return "Unknown"
    if text.startswith("Advantage+ Audience only"):
        return "Advantage+ (automatic)"
    return "Manual interests / job titles"


def audience_detail(interests_behaviors):
    text = (interests_behaviors or "").strip()
    if audience_type(text) != "Manual interests / job titles":
        return ""
    return text.replace(" (Advantage+ Audience also on -- Meta may deliver beyond this list)", " + Advantage+ on")


def geography_bucket(n_pins, locations):
    if n_pins:
        if n_pins <= 15:
            return "1-15 pincodes"
        if n_pins <= 30:
            return "16-30 pincodes"
        if n_pins <= 45:
            return "31-45 pincodes"
        return "46+ pincodes"
    joined = " ".join(locations or [])
    if "country-wide" in joined:
        return "Whole country"
    if "Not available" in joined or "Rate-limited" in joined or not joined:
        return "Unknown"
    return "Cities / radius"


_LEAD_ACTIONS = ("lead", "onsite_conversion.lead_grouped")
_GENDER_NAMES = {1: "Men only", 2: "Women only"}
_PLATFORM_NAMES = {"facebook": "Facebook", "instagram": "Instagram", "audience_network": "Audience Network",
                   "messenger": "Messenger", "threads": "Threads"}


def _action_value(actions, types):
    # Meta reports the same lead under several action types -- take the max,
    # never the sum, or one lead counts twice.
    best = 0
    for a in actions or []:
        if a.get("action_type") in types:
            best = max(best, int(float(a.get("value") or 0)))
    return best


def age_setting(setup):
    if not setup:
        return "Unknown"
    rng = setup.get("age_range")
    lo, hi = setup.get("age_min"), setup.get("age_max")
    if rng and len(rng) == 2:
        return f"{rng[0]}-{rng[1]}"
    if lo or hi:
        return f"{lo or 18}-{hi or 65}"
    return "Unknown"


def age_setting_note(setup):
    setup = setup or {}
    rng = setup.get("age_range")
    lo, hi = setup.get("age_min"), setup.get("age_max")
    if rng and len(rng) == 2 and [lo, hi] != list(rng) and setup.get("advantage_audience"):
        return f"Meta may widen to {lo}-{hi}"
    return ""


def gender_setting(setup):
    if not setup:
        return "Unknown"
    g = [x for x in (setup.get("genders") or []) if x in (1, 2)]
    if len(g) == 1:
        name = _GENDER_NAMES[g[0]]
        # Confirmed on the "women only" Bank Sakhi ad: with Advantage+
        # Audience on, 94% of its spend and 125 of 136 leads were men.
        return f"{name} (suggestion only, Advantage+ on)" if setup.get("advantage_audience") else name
    return "All genders"


def placement_setting(setup):
    if not setup:
        return "Unknown"
    plats = setup.get("publisher_platforms") or []
    if not plats:
        return "Automatic (Advantage+ placements)"
    return " + ".join(sorted(_PLATFORM_NAMES.get(p, p.title()) for p in plats))


def interest_items(setup):
    items = []
    for label, names in ((setup or {}).get("categories") or {}).items():
        for n in names:
            items.append(f"{label}: {n}")
    return items


def _top_age_note(age_spend):
    total = sum(age_spend.values())
    if total <= 0:
        return ""
    age, spend = max(age_spend.items(), key=lambda kv: kv[1])
    return f"{age} ({100 * spend / total:.0f}% of spend)"


def _delivered_gender_note(gender_spend):
    total = sum(gender_spend.values())
    if total <= 0:
        return ""
    men = 100 * gender_spend.get("male", 0) / total
    women = 100 * gender_spend.get("female", 0) / total
    return f"actually reached {men:.0f}% men, {women:.0f}% women"


def _position_name(platform, position):
    pos = (position or "unknown").replace("_", " ")
    for prefix in ("an ", "facebook ", "instagram "):
        if pos.startswith(prefix):
            pos = pos[len(prefix):]
    return f"{platform} {pos}"


def _pick_winner(groups, min_results, min_quality):
    """Best = lowest cost per quality result among groups with enough volume;
    falls back to lowest cost per result when nothing has enough quality
    results. Returns (winner, worst, basis) -- basis names the measure used."""
    eligible = [g for g in groups if g["results"] >= min_results]
    by_quality = [g for g in eligible if g["quality"] >= min_quality and g["cost_per_quality"] is not None]
    if by_quality:
        ranked = sorted(by_quality, key=lambda g: g["cost_per_quality"])
        return ranked[0], (ranked[-1] if len(ranked) > 1 else None), "cost per quality result"
    by_result = sorted([g for g in eligible if g["cost_per_result"] is not None], key=lambda g: g["cost_per_result"])
    if by_result:
        return by_result[0], (by_result[-1] if len(by_result) > 1 else None), "cost per result"
    return None, None, None


def _add_delivery(delivery, channel, dim, value, row):
    d = delivery.setdefault((channel, dim, value), {"channel": channel, "dim": dim, "group": value,
                                                    "spend": 0.0, "impressions": 0, "link_clicks": 0,
                                                    "landing_page_views": 0, "leads": 0})
    d["spend"] += float(row.get("spend") or 0)
    d["impressions"] += int(float(row.get("impressions") or 0))
    acts = row.get("actions")
    d["link_clicks"] += _action_value(acts, ("link_click",))
    d["landing_page_views"] += _action_value(acts, ("landing_page_view",))
    d["leads"] += _action_value(acts, _LEAD_ACTIONS)


def _delivery_table(delivery, channel, dim, min_spend=1.0):
    out = []
    for (ch, dm, _), d in delivery.items():
        if ch != channel or dm != dim or d["spend"] < min_spend:
            continue
        result = d["leads"] if channel == "Lead Form" else d["landing_page_views"]
        out.append({
            "group": d["group"],
            "spend": round(d["spend"], 2),
            "impressions": d["impressions"],
            "ctr": round(100 * d["link_clicks"] / d["impressions"], 2) if d["impressions"] else None,
            "results": result,
            "cost_per_result": _safe_div(d["spend"], result),
        })
    total = sum(r["spend"] for r in out) or 1
    for r in out:
        r["share_of_spend"] = round(100 * r["spend"] / total, 1)
    if dim == "age":
        out.sort(key=lambda r: r["group"])
    else:
        out.sort(key=lambda r: -r["spend"])
    return out


def _summarize(group):
    spend = round(sum(r["spend"] for r in group), 2)
    impressions = sum(r["impressions"] for r in group)
    clicks = sum(r["link_clicks"] for r in group)
    results = sum(r["results"] for r in group)
    quality = sum(r["quality"] for r in group)
    return {
        "ads": len(group),
        "spend": spend,
        "impressions": impressions,
        "link_clicks": clicks,
        "ctr": round(100 * clicks / impressions, 2) if impressions else None,
        "results": results,
        "cost_per_result": _safe_div(spend, results),
        "quality": quality,
        "quality_rate": round(100 * quality / results, 1) if results else None,
        "cost_per_quality": _safe_div(spend, quality),
        "landing_page_views": sum(r["landing_page_views"] for r in group),
    }


def _grouped(rows, key, order=None):
    """Summaries per (channel, group) so Lead Form and Website results are
    never added together -- their results mean different things."""
    out = []
    for channel in ("Lead Form", "Website"):
        groups = {}
        for r in rows:
            if r["channel"] == channel:
                groups.setdefault(r[key], []).append(r)
        names = sorted(groups, key=lambda g: order.index(g) if order and g in order else len(order or []))
        for name in names:
            out.append({"channel": channel, "group": name, **_summarize(groups[name])})
    return out


def build(ads, signups):
    """Returns everything the Performance tab needs.

    Each channel produces a different kind of result:
      - Lead Form ads: result = form lead; quality = lead whose pincode is in
        that ad's own targeted pincodes
      - Website ads:   result = website signup whose utm_campaign names this
        ad's campaign; quality = STRONG_QUALIFY signup
    Website results are a floor -- signups without a campaign ID can't be
    credited to any ad.
    """
    signups_by_campaign = {}
    for s in signups:
        cid = (s.get("utm_campaign") or "").strip()
        if cid:
            signups_by_campaign.setdefault(cid, []).append(s)

    rows = []
    delivery = {}
    pincode_stats = {}
    state_delivery = {}
    state_leads = {}

    for a in ads:
        m = a["metrics"]
        spend = m["spend"]
        if spend <= 0:
            continue
        ts = a.get("targeting_summary") or {}
        pins = sorted(ts.get("targeted_pincodes") or [])
        target_states = sorted({st for st in (state_for_pincode(p) for p in pins) if st})
        is_form = a["objective"] in FORM_OBJECTIVES
        channel = "Lead Form" if is_form else "Website"

        if is_form:
            results = m["leads"]
            matched = [l for l in a["leads_data"] if l.get("pincode_qualified")]
            quality = len(matched)
            fully_qualified = sum(1 for l in a["leads_data"] if l.get("qualified"))
            for lead in a["leads_data"]:
                st = state_for_pincode(lead.get("submitted_pincode")) or "No pincode given"
                d = state_leads.setdefault(st, {"leads": 0, "matched": 0, "qualified": 0})
                d["leads"] += 1
                d["matched"] += 1 if lead.get("pincode_qualified") else 0
                d["qualified"] += 1 if lead.get("qualified") else 0
            if pins:
                share = spend / len(pins)
                for p in pins:
                    ps = pincode_stats.setdefault(p, {"pincode": p, "state": state_for_pincode(p), "ads": 0,
                                                       "est_spend": 0.0, "matched": 0, "qualified": 0})
                    ps["ads"] += 1
                    ps["est_spend"] += share
                for lead in matched:
                    ps = pincode_stats.get(lead.get("submitted_pincode"))
                    if ps:
                        ps["matched"] += 1
                        ps["qualified"] += 1 if lead.get("qualified") else 0
        else:
            signed = signups_by_campaign.get(a["campaign_id"], [])
            results = len(signed)
            quality = sum(1 for s in signed if s.get("qualification_verdict") == "STRONG_QUALIFY")
            fully_qualified = quality

        bd = a.get("breakdowns") or {}
        gender_spend = {}
        age_spend = {}
        for row in bd.get("age_gender") or []:
            gender_spend[row.get("gender")] = gender_spend.get(row.get("gender"), 0.0) + float(row.get("spend") or 0)
            age_spend[row.get("age")] = age_spend.get(row.get("age"), 0.0) + float(row.get("spend") or 0)
            _add_delivery(delivery, channel, "age", row.get("age") or "Unknown", row)
            _add_delivery(delivery, channel, "gender", (row.get("gender") or "unknown").title(), row)
        for row in bd.get("platform") or []:
            plat = _PLATFORM_NAMES.get(row.get("publisher_platform"), (row.get("publisher_platform") or "Unknown").title())
            _add_delivery(delivery, channel, "platform", plat, row)
            _add_delivery(delivery, channel, "position", _position_name(plat, row.get("platform_position")), row)

        for reg in (a.get("breakdowns") or {}).get("region") or []:
            name = _meta_region_name(reg.get("region"))
            d = state_delivery.setdefault((channel, name), {"channel": channel, "state": name, "spend": 0.0,
                                                           "impressions": 0, "clicks": 0})
            d["spend"] += float(reg.get("spend") or 0)
            d["impressions"] += int(float(reg.get("impressions") or 0))
            d["clicks"] += int(float(reg.get("clicks") or 0))

        interests = ts.get("interests_behaviors") or ""
        rows.append({
            "ad_id": a["ad_id"],
            "campaign_id": a["campaign_id"],
            "ad_name": a["ad_name"] or a["campaign_name"],
            "account": a["account_name"],
            "status": a["status"],
            "built": (a.get("created_time") or "")[:10],
            "channel": channel,
            "spend": spend,
            "impressions": m["impressions"],
            "reach": m["reach"],
            "ctr": m["ctr"],
            "link_clicks": m["link_clicks"],
            "cpc": _safe_div(spend, m["link_clicks"]),
            "landing_page_views": m["landing_page_views"],
            "cost_per_lpv": _safe_div(spend, m["landing_page_views"]),
            "results": results,
            "cost_per_result": _safe_div(spend, results),
            "results_per_1k_reach": round(1000 * results / m["reach"], 2) if m["reach"] else None,
            "quality": quality,
            "quality_rate": round(100 * quality / results, 1) if results else None,
            "cost_per_quality": _safe_div(spend, quality),
            "fully_qualified": fully_qualified,
            "n_pincodes": len(pins),
            "target_states": target_states,
            "geo_bucket": geography_bucket(len(pins), ts.get("locations")),
            "audience_type": audience_type(interests),
            "audience_detail": audience_detail(interests),
            "age_setting": age_setting(ts.get("setup")),
            "age_note": age_setting_note(ts.get("setup")),
            "gender_setting": gender_setting(ts.get("setup")),
            "placement_setting": placement_setting(ts.get("setup")),
            "interest_items": interest_items(ts.get("setup")),
            "delivered_gender": _delivered_gender_note(gender_spend),
            "pincodes": pins,
            "categories": ((ts.get("setup") or {}).get("categories") or {}),
            "advantage_on": (ts.get("setup") or {}).get("advantage_audience"),
            "top_age": _top_age_note(age_spend),
        })

    def pick(candidates, key, reverse=False):
        candidates = [r for r in candidates if r[key] is not None]
        if not candidates:
            return None
        return sorted(candidates, key=lambda r: r[key], reverse=reverse)[0]

    awards = {
        "best_overall": pick([r for r in rows if r["quality"] >= MIN_QUALITY_FOR_BEST_OVERALL
                              and r["results"] >= MIN_RESULTS_FOR_COST_AWARD], "cost_per_quality"),
        "cheapest_result": pick([r for r in rows if r["results"] >= MIN_RESULTS_FOR_COST_AWARD], "cost_per_result"),
        "most_results": pick([r for r in rows if r["results"]], "results", reverse=True),
        "most_quality": pick([r for r in rows if r["quality"]], "quality", reverse=True),
        "best_ctr": pick([r for r in rows if r["impressions"] >= MIN_IMPRESSIONS_FOR_CTR_AWARD], "ctr", reverse=True),
        "cheapest_lpv": pick([r for r in rows if r["channel"] == "Website" and r["landing_page_views"] >= MIN_LPV_FOR_LPV_AWARD], "cost_per_lpv"),
    }

    channels = {name: _summarize([r for r in rows if r["channel"] == name]) for name in ("Lead Form", "Website")}

    by_age = _grouped(rows, "age_setting")
    by_gender = _grouped(rows, "gender_setting")
    by_placement = _grouped(rows, "placement_setting")

    by_interest = []
    for channel in ("Lead Form", "Website"):
        items = {}
        for r in rows:
            if r["channel"] != channel or r["audience_type"] == "Unknown":
                continue
            for it in r["interest_items"] or ["(no manual list -- Meta chose the audience)"]:
                items.setdefault(it, []).append(r)
        bundles = {}
        for it, group in items.items():
            bundles.setdefault(frozenset(x["ad_id"] for x in group), (group, []))[1].append(it)
        for group, names in bundles.values():
            label = names[0] if len(names) == 1 else f"{len(names)} used together: " + "; ".join(names)
            by_interest.append({"channel": channel, "group": label, "bundle_size": len(names), **_summarize(group)})
    by_interest.sort(key=lambda g: (g["channel"], g["cost_per_quality"] is None, g["cost_per_quality"] or 0,
                                    g["cost_per_result"] is None, g["cost_per_result"] or 0))

    delivery_splits = {
        f"{ch}|{dim}": _delivery_table(delivery, ch, dim)
        for ch in ("Lead Form", "Website") for dim in ("age", "gender", "platform", "position")
    }

    by_audience = _grouped(rows, "audience_type", ["Advantage+ (automatic)", "Manual interests / job titles", "Unknown"])
    by_geo = _grouped(rows, "geo_bucket", ["1-15 pincodes", "16-30 pincodes", "31-45 pincodes", "46+ pincodes",
                                           "Cities / radius", "Whole country", "Unknown"])

    # Lead Form cost per lead by state: Meta's state-level spend for Lead Form
    # ads, against leads whose own pincode falls in that state.
    states = []
    for (channel, name), d in state_delivery.items():
        if channel != "Lead Form":
            continue
        leads = state_leads.get(name, {"leads": 0, "matched": 0, "qualified": 0})
        states.append({
            "state": name,
            "spend": round(d["spend"], 2),
            "impressions": d["impressions"],
            "ctr": round(100 * d["clicks"] / d["impressions"], 2) if d["impressions"] else None,
            "leads": leads["leads"],
            "cost_per_lead": _safe_div(d["spend"], leads["leads"]),
            "matched": leads["matched"],
            "qualified": leads["qualified"],
        })
    delivered = {s["state"] for s in states}
    leads_outside_delivery = {st: v for st, v in state_leads.items() if st not in delivered and v["leads"]}
    states.sort(key=lambda s: -s["spend"])

    website_states = sorted(
        ({"state": d["state"], "spend": round(d["spend"], 2), "impressions": d["impressions"],
          "ctr": round(100 * d["clicks"] / d["impressions"], 2) if d["impressions"] else None}
         for (channel, _), d in state_delivery.items() if channel == "Website"),
        key=lambda s: -s["spend"])

    pincodes = []
    for ps in pincode_stats.values():
        ps["est_spend"] = round(ps["est_spend"], 2)
        ps["est_cost_per_match"] = _safe_div(ps["est_spend"], ps["matched"])
        pincodes.append(ps)
    top_pincodes = sorted([p for p in pincodes if p["matched"]], key=lambda p: (-p["matched"], p["est_cost_per_match"] or 0))
    dead_pincodes = sorted([p for p in pincodes if not p["matched"]], key=lambda p: -p["est_spend"])

    # What worked best, one line per setting and channel. Setup settings are
    # judged on cost per quality result (falling back to cost per result);
    # delivery splits (who actually saw the ad) on cost per lead for Lead Form
    # and cost per landing-page visit for Website, because Meta doesn't split
    # pincode quality or website signups by age, gender or placement.
    setup_dims = [
        ("Age range set on the ad", by_age), ("Gender set on the ad", by_gender),
        ("Placements set on the ad", by_placement), ("Audience type", by_audience),
        ("Number of pincodes", by_geo), ("Individual interest / job title", by_interest),
    ]
    verdicts = []
    for channel, min_results, min_quality in (("Lead Form", MIN_RESULTS_FOR_COST_AWARD, MIN_QUALITY_FOR_BEST_OVERALL),
                                              ("Website", 3, 2)):
        for label, groups in setup_dims:
            gs = [g for g in groups if g["channel"] == channel and g["group"] != "Unknown"]
            win, worst, basis = _pick_winner(gs, min_results, min_quality)
            if win:
                verdicts.append({"channel": channel, "dimension": label, "winner": win, "worst": worst,
                                 "basis": basis, "compared": len(gs), "delivery": False})
        min_delivery = 10 if channel == "Lead Form" else 20
        for label, dim in (("Age group that saw the ad", "age"), ("Gender that saw the ad", "gender"),
                           ("Platform", "platform"), ("Placement position", "position")):
            table = [r for r in delivery_splits[f"{channel}|{dim}"]
                     if r["results"] >= min_delivery and not r["group"].lower().startswith("unknown")]
            ranked = sorted([r for r in table if r["cost_per_result"] is not None], key=lambda r: r["cost_per_result"])
            if ranked:
                verdicts.append({"channel": channel, "dimension": label, "winner": ranked[0],
                                 "worst": ranked[-1] if len(ranked) > 1 else None,
                                 "basis": "cost per lead" if channel == "Lead Form" else "cost per landing-page visit",
                                 "compared": len(table), "delivery": True})

    unattributed_signups = sum(1 for s in signups if not (s.get("utm_campaign") or "").strip())
    unknown_targeting = sum(1 for r in rows if r["audience_type"] == "Unknown")
    rows.sort(key=lambda r: (r["cost_per_quality"] is None, r["cost_per_quality"] or 0, -r["results"]))

    return {
        "rows": rows,
        "by_age": by_age,
        "by_gender": by_gender,
        "by_placement": by_placement,
        "by_interest": by_interest,
        "delivered": delivery_splits,
        "verdicts": verdicts,
        "awards": awards,
        "channels": channels,
        "by_audience": by_audience,
        "by_geo": by_geo,
        "states": states,
        "leads_outside_delivery": leads_outside_delivery,
        "website_states": website_states,
        "top_pincodes": top_pincodes,
        "dead_pincodes": dead_pincodes,
        "pincodes_targeted": len(pincodes),
        "unattributed_signups": unattributed_signups,
        "total_signups": len(signups),
        "unknown_targeting": unknown_targeting,
        "min_quality": MIN_QUALITY_FOR_BEST_OVERALL,
        "min_results": MIN_RESULTS_FOR_COST_AWARD,
    }
