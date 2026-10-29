"""Experiments tab: every comparison the ads have already run, what it showed,
what to avoid repeating, and what to try next.

Nothing here is hand-typed results: each experiment is computed from the same
live ad data as the Performance tab, so the numbers and the lessons move with
the dashboard. The wording of the lessons is conditional on those numbers.
"""
import re

LEAD = "Lead Form"
WEB = "Website"


def m(v):
    if v is None:
        return "-"
    return f"₹{v:,.0f}" if v >= 100 else f"₹{v:,.2f}"


def n(v):
    return "-" if v is None else f"{v:,}"


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "-"


def _confidence(counts):
    counts = [c for c in counts if c is not None]
    if not counts:
        return "Low", "no results to compare yet"
    low = min(counts)
    if low >= 30:
        return "High", f"smallest group has {low} results"
    if low >= 10:
        return "Medium", f"smallest group has only {low} results"
    return "Low", f"smallest group has only {low} result{'s' if low != 1 else ''}"


def _group_rows(groups):
    out = []
    for g in groups:
        out.append([g["group"], n(g["ads"]), m(g["spend"]), n(g["results"]), m(g["cost_per_result"]),
                    n(g["quality"]), (f"{g['quality_rate']}%" if g.get("quality_rate") is not None else "-"),
                    m(g["cost_per_quality"])])
    return out


GROUP_HEADERS = ["Option", "Ads", "Spend", "Results", "Cost / result", "Quality results", "Quality %", "Cost / quality result"]


def _best(groups, min_results, min_quality=3):
    ok = [g for g in groups if g["results"] >= min_results and g["quality"] >= min_quality and g["cost_per_quality"]]
    return min(ok, key=lambda g: g["cost_per_quality"]) if ok else None


def _worst(groups, min_results, min_quality=3):
    ok = [g for g in groups if g["results"] >= min_results and g["quality"] >= min_quality and g["cost_per_quality"]]
    return max(ok, key=lambda g: g["cost_per_quality"]) if len(ok) > 1 else None


def _cheapest(groups, min_results):
    ok = [g for g in groups if g["results"] >= min_results and g["cost_per_result"]]
    return min(ok, key=lambda g: g["cost_per_result"]) if ok else None


def _exp(id_, title, question, did, headers, rows, learning, verdict, confidence, dont_repeat=None, best_row=None, note=None):
    conf, conf_why = confidence
    return {"id": id_, "title": title, "question": question, "did": did, "headers": headers, "rows": rows,
            "learning": learning, "verdict": verdict, "confidence": conf, "confidence_why": conf_why,
            "dont_repeat": dont_repeat, "best_row": best_row, "note": note}


VERDICTS = {
    "worked": "Clear result",
    "didnt": "Didn't work",
    "mixed": "Mixed result",
    "unclear": "Can't tell yet",
    "notyet": "Not tried yet",
    "problem": "Problem found",
}


def _budget_sentence(t_rows):
    def val(x):
        try:
            return float(x.replace("₹", "").replace(",", ""))
        except Exception:
            return None
    costs = [val(r[4]) for r in t_rows]
    if len(costs) >= 3 and None not in costs:
        if costs[0] > costs[1] > costs[2]:
            return "Cost per lead fell as the ad's total spend rose."
        if costs[0] < costs[1] < costs[2]:
            return "Cost per lead rose as the ad's total spend rose."
        return "There is no steady pattern: cost per lead didn't consistently rise or fall as the ad's total spend grew."
    return "Not enough spend tiers to see a pattern."



# ---------------------------------------------------------------------------
# Images and captions
# ---------------------------------------------------------------------------
_MONEY_RE = re.compile(r"₹|\brs\.?\s?\d|income|earn|salary|commission|कमाई|कमाएं|कमायें|आमदनी|कमीशन|ਕਮਾਈ|ਕਮਿਸ਼ਨ", re.I)
_INVEST_RE = re.compile(r"invest|निवेश|ਨਿਵੇਸ਼|5,00,000|5 lakh|५ लाख|5 लाख|ਲੱਖ", re.I)
_SBI_RE = re.compile(r"\bsbi\b|एसबीआई|ਐਸਬੀਆਈ", re.I)


def _language(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "No text"
    total = len(letters)
    gur = sum("\u0a00" <= c <= "\u0a7f" for c in letters)
    dev = sum("\u0900" <= c <= "\u097f" for c in letters)
    lat = sum(c.isascii() for c in letters)
    parts = []
    if gur / total >= 0.2:
        parts.append("Punjabi")
    if dev / total >= 0.2:
        parts.append("Hindi")
    if lat / total >= 0.2:
        parts.append("English")
    return " + ".join(parts) or "Other"


def _length_bucket(text):
    k = len(text.strip())
    if k == 0:
        return "No caption"
    return "Short (under 120 characters)" if k < 120 else ("Medium (120-300)" if k <= 300 else "Long (over 300)")


def _agg(label, rs):
    spend = round(sum(r["spend"] for r in rs), 2)
    res = sum(r["results"] for r in rs)
    q = sum(r["quality"] for r in rs)
    return {"group": label, "ads": len(rs), "spend": spend, "results": res,
            "cost_per_result": round(spend / res, 2) if res else None,
            "quality": q, "quality_rate": round(100 * q / res, 1) if res else None,
            "cost_per_quality": round(spend / q, 2) if q else None}


def _creative_analysis(rows, creatives, labels):
    spent = [r for r in rows if r["spend"] > 0]
    covered = [r for r in spent if r["ad_id"] in creatives and (creatives[r["ad_id"]].get("primary_text") or creatives[r["ad_id"]].get("headline")
                                                              or creatives[r["ad_id"]].get("image_file"))]
    out = {"pending": not covered, "n_ads": len(spent), "n_covered": len(covered), "groups": [], "trait_tables": [],
           "verdict": "unclear", "learning": [], "confidence": ("Low", "no creative data yet")}
    if not covered:
        return out

    def label_for(c):
        return (labels.get(c.get("image_hash") or "") or labels.get(c.get("_ad_id") or "") or {}).get("image_type") or ""

    enriched = []
    for r in covered:
        c = dict(creatives[r["ad_id"]])
        c["_ad_id"] = r["ad_id"]
        text = (c.get("primary_text") or "") + " " + (c.get("headline") or "")
        enriched.append((r, c, {
            "Language": _language((c.get("primary_text") or "").strip() or (c.get("headline") or "")),
            "Caption length": _length_bucket(c.get("primary_text") or ""),
            "Mentions money or earnings": "Yes" if _MONEY_RE.search(text) else "No",
            "Mentions the investment": "Yes" if _INVEST_RE.search(text) else "No",
            "Mentions SBI": "Yes" if _SBI_RE.search(text) else "No",
            "Button": (c.get("cta") or "none").replace("_", " ").title(),
            "Format": (c.get("type") or "unknown").title(),
            "Image type (labelled)": label_for(c) or "Not labelled yet",
        }))

    # distinct creatives = same channel + picture + caption + headline
    groups = {}
    for r, c, _ in enriched:
        key = (r["channel"], c.get("image_hash") or c.get("image_file") or r["ad_id"], (c.get("primary_text") or "").strip(), (c.get("headline") or "").strip())
        g = groups.setdefault(key, {"rows": [], "c": c, "channel": r["channel"]})
        g["rows"].append(r)
    glist = []
    for g in groups.values():
        a = _agg("", g["rows"])
        c = g["c"]
        glist.append({**a, "channel": g["channel"], "ad_id": c["_ad_id"], "has_image": bool(c.get("image_file")),
                      "primary_text": c.get("primary_text") or "", "headline": c.get("headline") or "", "cta": (c.get("cta") or "").replace("_", " ").title(),
                      "type": (c.get("type") or "unknown").title(), "image_type": label_for(c) or "",
                      "built": f"{min(x['built'] for x in g['rows'])}" + (f" to {max(x['built'] for x in g['rows'])}" if len(g["rows"]) > 1 else "")})
    glist.sort(key=lambda g: (g["channel"] != LEAD, g["cost_per_quality"] is None, g["cost_per_quality"] or 0, -g["results"]))
    out["groups"] = glist

    # one table per trait, channels kept apart
    for trait in ("Language", "Caption length", "Mentions money or earnings", "Mentions the investment", "Mentions SBI", "Button", "Format", "Image type (labelled)"):
        trows = []
        for channel in (LEAD, WEB):
            by = {}
            for r, c, t in enriched:
                if r["channel"] == channel:
                    by.setdefault(t[trait], []).append(r)
            if len(by) == 1 and trait != "Image type (labelled)":
                continue
            for k, rs in sorted(by.items()):
                a = _agg(k, rs)
                trows.append([channel, a["group"], n(a["ads"]), m(a["spend"]), n(a["results"]), m(a["cost_per_result"]), n(a["quality"]),
                              (f"{a['quality_rate']}%" if a["quality_rate"] is not None else "-"), m(a["cost_per_quality"])])
        if trows:
            out["trait_tables"].append({"title": trait, "headers": ["Channel"] + GROUP_HEADERS, "rows": trows})

    lf = [g for g in glist if g["channel"] == LEAD and g["results"] >= 10 and g["quality"] >= 3 and g["cost_per_quality"]]
    n_distinct = len(glist)
    out["learning"].append(f"{len(covered)} ads used {n_distinct} distinct picture-and-caption combinations"
                           + (". Creative has barely been varied, so almost none of the results can be credited to the picture or the words." if n_distinct <= max(3, len(covered) // 4) else "."))
    if len(lf) >= 2:
        lf.sort(key=lambda g: g["cost_per_quality"])
        b, nxt = lf[0], lf[1]
        if b["cost_per_quality"] <= 0.7 * nxt["cost_per_quality"]:
            out["verdict"] = "worked"
            out["learning"].append(f"One creative stands out: {m(b['cost_per_quality'])} per lead from a targeted pincode, against {m(nxt['cost_per_quality'])} for the next best.")
        else:
            out["verdict"] = "unclear"
            out["learning"].append(f"No creative clearly beats the others: the best costs {m(b['cost_per_quality'])} per lead from a targeted pincode and the next {m(nxt['cost_per_quality'])}. That gap is within what the ad's targeting could explain.")
        out["confidence"] = _confidence([g["results"] for g in lf])
    else:
        out["learning"].append("Fewer than two creatives have enough Lead Form results (10 leads and 3 from a targeted pincode) to compare, so no creative can be called best yet.")
        out["confidence"] = ("Low", "fewer than two creatives with enough results")
    out["learning"].append("Each ad also had its own targeting, budget and dates, so a creative's result is mixed in with those. Treat this as a first look; a real answer needs two ads that differ only in the picture or only in the caption.")
    if len(covered) < len(spent):
        out["learning"].append(f"Creative details are saved for {len(covered)} of {len(spent)} ads; the rest will fill in as ads are refreshed.")
    return out


COMPETITORS = [
    {"who": "PayNearby", "does": "Leads with earnings: its recruitment pages say five to seven retailers in your network can add ₹40,000 to ₹60,000 a month, pay 'best in industry' commission, and start free on some products with packages from ₹1,000. It also runs training for retailers.",
     "means": "The promise is easy income with a low start. Your offer needs ₹5,00,000, so earnings-led ads may attract people who expect little or no investment.",
     "url": "https://paynearby.in/faqs/", "label": "PayNearby FAQ"},
    {"who": "Spice Money (Adhikari)", "does": "Recruitment page promises 'best in industry commission' and 'start your own business with zero investment'. Its FAQ is more careful: no upfront fee, but service charges may apply. Sign-up asks for Aadhaar, PAN and bank details.",
     "means": "'Zero investment' is the claim you are competing against. Saying the investment up front, and why it buys an owned kiosk, is the opposite positioning.",
     "url": "https://spicemoney.com/join-as-adhikari", "label": "Spice Money recruitment page"},
    {"who": "Fino Payments Bank", "does": "Presents its shops as banking points that work like a small branch, an add-on to the shopkeeper's main business, with training and support. Its form says a representative will get in touch.",
     "means": "'Add a banking counter to the shop you already run' suits shop owners, your best-fit group, better than 'start a new business'.",
     "url": "https://content.fino.bank.in/en/node/191", "label": "Fino merchant page"},
    {"who": "Regional language (market case studies)", "does": "A real-estate campaign ran the same ads in Tamil and also put the lead form in the regional language; cost per lead fell. A mutual-fund campaign found regional-language copy beat English on engagement.",
     "means": "Two single case studies, not proof. They back testing Punjabi and Hindi copy and a local-language form. Your question labels are already Hindi and English.",
     "url": "https://www.socialbeat.in/blog/how-multilingual-ads-helped-generate-high-quality-leads-with-lower-cpl-for-real-estate/", "label": "Social Beat case study"},
    {"who": "Click-to-WhatsApp (market case study)", "does": "Meta's own case study of an education company says click-to-WhatsApp ads brought 60% of its business, with 80% of customers from remote parts of Rajasthan. Meta also says click-to-message ad revenue in India doubled in a year.",
     "means": "Strong signal that this format works for rural India. One benchmark note warns that cost per chat can rise while cost per qualified lead falls, so judge it on qualified leads, not chats.",
     "url": "https://whatsappbusiness.com/?p=5213", "label": "WhatsApp Business case study"},
]
COMPETITOR_CAVEATS = [
    "These come from the companies' own websites and published case studies. I could not open Meta's Ad Library from here, so I have not seen their actual Facebook ads.",
    "You can: open facebook.com/ads/library, choose India, and search PayNearby, Spice Money and Fino Payments Bank. Look at which pictures and first lines they repeat, since ads that keep running are usually working.",
    "I found no independent evidence on video versus image for rural India, so that is a test to run yourself.",
    "Earnings claims in ads are regulated in India (ASCI guidelines). Only show an income figure you can back up in writing, and check it with your compliance team.",
]

def build(report, totals, opt, creatives=None, labels=None):
    rows = report["rows"]
    form_rows = [r for r in rows if r["channel"] == LEAD]
    web_rows = [r for r in rows if r["channel"] == WEB]
    exps = []

    # 1. Narrow vs broad pincode lists ------------------------------------------------
    geo = [g for g in report["by_geo"] if g["channel"] == LEAD and g["results"] > 0 and g["group"] != "Unknown"]
    best, worst, cheap = _best(geo, 10), _worst(geo, 10), _cheapest(geo, 10)
    learning = []
    if best and cheap:
        learning.append(f"Cheapest leads came from {cheap['group']} ({m(cheap['cost_per_result'])} each). "
                        f"Best leads in the target area came from {best['group']} ({m(best['cost_per_quality'])} per lead from a targeted pincode).")
    if best and worst and best["group"] != worst["group"]:
        learning.append(f"{worst['group']} cost {worst['cost_per_quality'] / best['cost_per_quality']:.1f}x as much per quality lead as {best['group']}.")
    mixed = bool(best and cheap and best["group"] != cheap["group"])
    exps.append(_exp(
        "geo", "Narrow vs. broad pincode lists",
        "Does targeting fewer pincodes bring better leads than targeting many?",
        "Lead Form ads were run with pincode lists from a handful of pincodes up to 55+, built from your CSP village list.",
        GROUP_HEADERS, _group_rows(geo), learning or ["Not enough leads in each group to compare yet."],
        "mixed" if mixed else ("worked" if best else "unclear"),
        _confidence([g["results"] for g in geo if g["results"]]),
        dont_repeat="Judging a pincode list by cost per lead alone. Broad lists always look cheapest but bring the most out-of-area leads."
        if mixed else None,
        best_row=next((i for i, g in enumerate(geo) if best and g["group"] == best["group"]), None)))

    # 2. Meta-chosen vs manual audience ---------------------------------------------------
    aud = [g for g in report["by_audience"] if g["group"] != "Unknown" and g["results"] > 0]
    learning, parts = [], []
    for ch, floor in ((LEAD, 10), (WEB, 3)):
        g = [x for x in aud if x["channel"] == ch]
        auto = next((x for x in g if x["group"].startswith("Advantage+")), None)
        man = next((x for x in g if x["group"].startswith("Manual")), None)
        if auto and man and auto["results"] >= floor and man["results"] >= floor:
            w, l = (man, auto) if man["cost_per_result"] < auto["cost_per_result"] else (auto, man)
            learning.append(f"{ch} ads: {w['group'].lower()} was cheaper per result ({m(w['cost_per_result'])} vs {m(l['cost_per_result'])}).")
            parts.append((ch, w["group"]))
        elif auto and man:
            learning.append(f"{ch} ads: not enough results in one of the two groups to compare.")
    differs = len({p[1][:3] for p in parts}) > 1
    exps.append(_exp(
        "audience", "Let Meta choose the audience vs. a manual list of interests and job titles",
        "Do interests, behaviours and job titles (broker, insurance agent, bank officer...) beat leaving the audience to Meta?",
        "Some ads used Advantage+ (Meta finds the audience). Others were given interests such as Personal finance, behaviours such as Small business owners, and finance job titles.",
        ["Channel"] + GROUP_HEADERS, [[g["channel"]] + r for g, r in zip(aud, _group_rows(aud))],
        learning + (["The winner is different for each channel, so one audience rule won't fit both."] if differs else []) or ["Not enough data."],
        "mixed" if differs else ("worked" if parts else "unclear"),
        _confidence([g["results"] for g in aud]),
        dont_repeat="Using the same audience setting for Lead Form and Website ads. What works for one hasn't worked for the other." if differs else None))

    # 3. Lead Form vs Website --------------------------------------------------------------
    ch = report["channels"]
    lf, wb = ch[LEAD], ch[WEB]
    ratio = (wb["cost_per_result"] / lf["cost_per_result"]) if lf["cost_per_result"] and wb["cost_per_result"] else None
    exps.append(_exp(
        "channel", "Lead Form ads vs. Website signup ads",
        "Which kind of ad turns spend into leads more cheaply?",
        "Two kinds of ad ran side by side: Facebook's built-in Lead Form, and ads that send people to the kiosk.eko.in signup page.",
        ["Channel", "Ads", "Spend", "Link CTR", "Results", "Cost / result", "Quality results", "Cost / quality result"],
        [[c, n(v["ads"]), m(v["spend"]), (f"{v['ctr']}%" if v["ctr"] is not None else "-"), n(v["results"]), m(v["cost_per_result"]),
          n(v["quality"]), m(v["cost_per_quality"])] for c, v in ((LEAD, lf), (WEB, wb))],
        ([f"A website signup that carried an ad's campaign ID cost {ratio:.0f}x as much as a Lead Form lead, even though website ads got more clicks ({(wb['ctr'] or 0)}% vs {(lf['ctr'] or 0)}% click-through)."] if ratio else [])
        + [f"Website spend was {pct(wb['spend'], lf['spend'] + wb['spend'])} of the total. The true gap is smaller than {ratio:.0f}x" + ", because most signups carry no campaign ID and can't be credited to any ad." if ratio else "Website results are a minimum."],
        "worked" if ratio and ratio > 2 else "unclear",
        _confidence([lf["results"], wb["results"]]),
        dont_repeat="Putting most of the budget behind website ads because they get more clicks. Clicks weren't turning into signups." if ratio and ratio > 2 and wb["spend"] > lf["spend"] else None,
        best_row=0 if ratio and ratio > 1 else 1,
        note="Lead Form result = a lead. Website result = a signup that carried that ad's campaign ID."))

    # 4. Women-only targeting ----------------------------------------------------------------
    wo = [r for r in rows if r["gender_setting"].startswith("Women only")]
    pairs = []
    for r in wo:
        mm = re.search(r"(\d+)% men", r["delivered_gender"] or "")
        if mm:
            pairs.append((r, int(mm.group(1))))
    spend_w = sum(r["spend"] for r, _ in pairs)
    men_share = round(sum(r["spend"] * p for r, p in pairs) / spend_w) if spend_w else None
    exps.append(_exp(
        "women", "Targeting women only (Bank Sakhi)",
        "Can we reach women by setting the ad to 'women only'?",
        "Ads were set to women only, but with Advantage+ Audience switched on, which treats gender as a suggestion.",
        ["Ad built", "Channel", "Spend", "Results", "Set to", "Actually reached"],
        [[r["built"], r["channel"], m(r["spend"]), n(r["results"]), "Women only", r["delivered_gender"] or "-"] for r, _ in pairs],
        ([f"About {men_share}% of the money still reached men. The setting was not respected."] if men_share is not None else ["No data yet."])
        + ["Advantage+ lets Meta go beyond your age and gender settings. To really reach women, switch Advantage+ Audience off for that ad."],
        "didnt" if men_share is not None and men_share >= 50 else "unclear",
        (("High" if spend_w >= 1000 else "Medium"), f"based on {m(round(spend_w))} of spend, measured by Meta's own reach split"),
        dont_repeat="Choosing 'women only' with Advantage+ left on and assuming it worked. Always check who the ad actually reached."
        if men_share is not None and men_share >= 50 else None))

    # 5. Age range ------------------------------------------------------------------------------
    age = [g for g in report["by_age"] if g["channel"] == LEAD and g["results"] > 0 and g["group"] != "Unknown"]
    best, worst = _best(age, 10), _worst(age, 10)
    dl = [r for r in report["delivered"].get("Lead Form|age", []) if r["results"] >= 10]
    cheap_age = min(dl, key=lambda r: r["cost_per_result"]) if dl else None
    learning = []
    if best:
        learning.append(f"Best setting for quality leads: age {best['group']} ({m(best['cost_per_quality'])} per quality lead)"
                        + (f", against {m(worst['cost_per_quality'])} for {worst['group']}." if worst else "."))
    if cheap_age:
        learning.append(f"Of the people who actually saw the ads, age {cheap_age['group']} gave the cheapest leads ({m(cheap_age['cost_per_result'])}).")
    exps.append(_exp(
        "age", "Age range", "Does a narrower age range (21-55) beat the widest (18-65)?",
        "Lead Form ads were set to different age ranges. With Advantage+ on, Meta may show the ad slightly outside the range you set.",
        GROUP_HEADERS, _group_rows(age), learning or ["Not enough data."],
        "worked" if best and worst else "unclear", _confidence([g["results"] for g in age]),
        best_row=next((i for i, g in enumerate(age) if best and g["group"] == best["group"]), None),
        note="This isn't a clean test: the ad with the 21-55 setting also differs in other ways. It shows what the whole setup did."))

    # 6. Placements ---------------------------------------------------------------------------------
    pl = [g for g in report["by_placement"] if g["channel"] == LEAD and g["results"] > 0 and g["group"] != "Unknown"]
    best = _best(pl, 10)
    wp = {r["group"]: r for r in report["delivered"].get("Website|platform", [])}
    an = wp.get("Audience Network")
    others = [r for g, r in wp.items() if g in ("Facebook", "Instagram")]
    learning = []
    if best:
        learning.append(f"Lead Form: best setting was {best['group']} ({m(best['cost_per_quality'])} per quality lead).")
    if an and others:
        learning.append(f"Website ads: Audience Network took {an['share_of_spend']}% of spend, at {m(an['cost_per_result'])} per landing visit "
                        f"vs {m(min(o['cost_per_result'] for o in others if o['cost_per_result']))} on the next cheapest. "
                        "Meta can't show signups per placement, so whether those cheap visits became signups is unknown. Taps inside other companies' apps and games are often accidental.")
    exps.append(_exp(
        "placements", "Where the ads were shown (placements)",
        "Do Facebook-only ads beat showing everywhere, including Audience Network (other companies' apps)?",
        "Some ads ran on Facebook only. Most ran on Facebook, Instagram, Messenger and Audience Network together.",
        GROUP_HEADERS, _group_rows(pl), learning or ["Not enough data."],
        "mixed" if best and an else ("worked" if best else "unclear"), _confidence([g["results"] for g in pl]),
        dont_repeat=(f"Leaving Audience Network switched on for website ads without a way to check what it produces. {an['share_of_spend']}% of the spend went there, and its signups can't be measured.")
        if an and an["share_of_spend"] >= 25 else None,
        best_row=next((i for i, g in enumerate(pl) if best and g["group"] == best["group"]), None)))

    # 7. Two ad accounts ------------------------------------------------------------------------------
    accts = {}
    for r in form_rows:
        a = accts.setdefault(r["account"], {"ads": 0, "spend": 0.0, "results": 0, "quality": 0, "from": r["built"], "to": r["built"]})
        a["ads"] += 1
        a["spend"] += r["spend"]
        a["results"] += r["results"]
        a["quality"] += r["quality"]
        a["from"], a["to"] = min(a["from"], r["built"]), max(a["to"], r["built"])
    acc_rows = [[k, n(v["ads"]), f"{v['from']} to {v['to']}", m(round(v["spend"], 2)), n(v["results"]),
                 m(round(v["spend"] / v["results"], 2) if v["results"] else None), n(v["quality"]),
                 m(round(v["spend"] / v["quality"], 2) if v["quality"] else None)] for k, v in accts.items()]
    names = list(accts)
    overlap = None
    if len(names) == 2:
        a, b = accts[names[0]], accts[names[1]]
        overlap = max(a["from"], b["from"]) <= min(a["to"], b["to"])
    cpr = {k: (v["spend"] / v["results"] if v["results"] else None) for k, v in accts.items()}
    cheaper = min((k for k in cpr if cpr[k]), key=lambda k: cpr[k], default=None)
    exps.append(_exp(
        "accounts", "Two ad accounts",
        "Does the same kind of ad do better from one ad account than the other?",
        "Lead Form ads were run from both the Cashere and Pooja Goswami ad accounts.",
        ["Account", "Ads", "Built between", "Spend", "Leads", "Cost / lead", "Leads in targeted pincode", "Cost / quality lead"], acc_rows,
        ([f"{cheaper} had the lower cost per lead ({m(round(cpr[cheaper], 2))})."] if cheaper else [])
        + ["The two accounts ran different ads with different targeting, budgets and dates, so this does not prove one account is better."],
        "unclear", _confidence([v["results"] for v in accts.values()]),
        dont_repeat="Comparing accounts without running the same ad in both at the same time.",
        note="To test it properly, run an identical ad from both accounts in the same week."))

    # 8. States ---------------------------------------------------------------------------------------------
    st = [s for s in report["states"] if s["spend"] >= 100][:7]
    st_rows = [[s["state"], m(s["spend"]), n(s["leads"]), m(s["cost_per_lead"]), n(s["matched"]), pct(s["matched"], s["leads"]),
                n(s["qualified"])] for s in st]
    cheap = min((s for s in st if s["leads"] >= 10 and s["cost_per_lead"]), key=lambda s: s["cost_per_lead"], default=None)
    inpin = max((s for s in st if s["leads"] >= 10), key=lambda s: s["matched"] / s["leads"], default=None)
    learning = []
    if cheap:
        learning.append(f"{cheap['state']} gave the cheapest leads ({m(cheap['cost_per_lead'])}), but only {pct(cheap['matched'], cheap['leads'])} of them were in a targeted pincode.")
    if inpin and (not cheap or inpin["state"] != cheap["state"]):
        learning.append(f"{inpin['state']} had the highest share of leads in a targeted pincode ({pct(inpin['matched'], inpin['leads'])}) at {m(inpin['cost_per_lead'])} per lead.")
    exps.append(_exp(
        "states", "Which states the money went to", "Which states produce leads that are actually in our target villages?",
        "Meta's own state-level spend for Lead Form ads, against the state of the pincode each lead typed in.",
        ["State", "Spend", "Leads", "Cost / lead", "In targeted pincode", "% in targeted pincode", "Fully qualified"], st_rows,
        learning or ["Not enough data."], "mixed" if cheap and inpin and cheap["state"] != inpin["state"] else "unclear",
        _confidence([s["leads"] for s in st if s["leads"]]),
        dont_repeat="Chasing the state with the cheapest leads. If most of its leads are outside your target pincodes, the low cost doesn't help."
        if cheap and inpin and cheap["state"] != inpin["state"] else None))

    # 9. Budget size ------------------------------------------------------------------------------------------
    tiers = [("Under ₹500", 0, 500), ("₹500 - ₹1,500", 500, 1500), ("Over ₹1,500", 1500, 10 ** 9)]
    t_rows, t_res = [], []
    for label, lo, hi in tiers:
        g = [r for r in form_rows if lo <= r["spend"] < hi]
        if not g:
            continue
        sp, rs, ql = sum(r["spend"] for r in g), sum(r["results"] for r in g), sum(r["quality"] for r in g)
        t_res.append(rs)
        t_rows.append([label, n(len(g)), m(round(sp, 2)), n(rs), m(round(sp / rs, 2) if rs else None), n(ql),
                       m(round(sp / ql, 2) if ql else None)])
    exps.append(_exp(
        "budget", "Budget size", "Do ads that spend more get leads more cheaply, or less?",
        "Lead Form ads spent anywhere from under ₹100 to over ₹3,000 in total.",
        ["Total spend of the ad", "Ads", "Spend", "Leads", "Cost / lead", "Leads in targeted pincode", "Cost / quality lead"], t_rows,
        [_budget_sentence(t_rows)],
        "unclear", _confidence(t_res),
        note="Small ads (under ₹500) have few leads, so their cost per lead swings a lot."))

    # 10. Screening questions -------------------------------------------------------------------------------------
    leads, matched, qual = totals.get("leads", 0), totals.get("pincode_qualified_leads", 0), totals.get("qualified_leads", 0)
    exps.append(_exp(
        "screening", "The two screening questions as free text",
        "Do the two questions (can you invest ₹5,00,000? franchise background?) sort real prospects from the rest?",
        "Both questions are open text boxes on the Lead Form. A lead is qualified only if the pincode matches AND both answers are yes.",
        ["Stage", "Leads", "% of all leads"],
        [["All leads", n(leads), "100%"], ["In a targeted pincode", n(matched), pct(matched, leads)], ["Pincode AND both questions yes", n(qual), pct(qual, leads)]],
        [f"Of {n(matched)} leads in a targeted pincode, only {n(qual)} also answered yes to both questions ({pct(qual, matched)}).",
         "Many answers are typed as amounts ('50000', '50k') or free text instead of yes/no, so the strict yes check can't read them. Some of these people clearly can't invest ₹5,00,000; others may be genuine prospects."],
        "problem", ("High", f"{n(leads)} leads"),
        dont_repeat="Free-text answers to a yes/no question. The qualified count can't be trusted, so ads can't be judged fairly on it.",
        note="The fix (turn both into Yes/No multiple choice in Ads Manager) hasn't been tried yet - it's listed under What to try next."))

    # 11. Campaign tracking ----------------------------------------------------------------------------------------------
    tot, un = report["total_signups"], report["unattributed_signups"]
    exps.append(_exp(
        "tracking", "Tracking which ad a website signup came from",
        "Can we tell which ad produced each website signup?",
        "Early website ads sent people to a plain link with no tracking tags. Ads built later add the campaign ID automatically.",
        ["Signups", "Count", "Share"], [["With a campaign ID (traceable to an ad)", n(tot - un), pct(tot - un, tot)], ["No campaign ID", n(un), pct(un, tot)]],
        [f"{pct(tot - un, tot)} of {n(tot)} signups can be tied to an ad. The rest came from ads without tags, or from outside Facebook.",
         "Ads built after the fix are traceable; older ones can't be fixed after launch."],
        "worked" if tot and (tot - un) / tot >= 0.5 else "mixed", _confidence([tot]),
        dont_repeat="Launching a website ad without the campaign tag. Those signups can never be credited to the ad."))

    # 12. Optimisation goal (pixel) ----------------------------------------------------------------------------------------
    by_goal = {}
    for r in rows:
        o = opt.get(r["campaign_id"])
        if not o:
            continue
        goal = o.get("optimization_goal") or "Unknown"
        if o.get("custom_event_type"):
            goal += f" ({o['custom_event_type']})"
        g = by_goal.setdefault((r["channel"], goal), {"ads": 0, "spend": 0.0, "results": 0, "quality": 0})
        g["ads"] += 1
        g["spend"] += r["spend"]
        g["results"] += r["results"]
        g["quality"] += r["quality"]
    goal_rows = [[c, goal, n(v["ads"]), m(round(v["spend"], 2)), n(v["results"]), m(round(v["spend"] / v["results"], 2) if v["results"] else None),
                  n(v["quality"])] for (c, goal), v in sorted(by_goal.items())]
    web_goals = {k[1] for k in by_goal if k[0] == WEB}
    pixel = any("OFFSITE_CONVERSIONS" in g or "LEAD" in g.split("(")[-1] for g in web_goals)
    if not by_goal:
        verdict, learning = "unclear", ["Not checked yet: the optimisation goal of the newer ads hasn't been recorded."]
    elif pixel:
        verdict, learning = "unclear", ["At least one website ad was set to optimise for the pixel's Lead event. Compare its cost per signup to the click-optimised ads above."]
    else:
        verdict, learning = "notyet", ["Every website ad checked optimised for clicks or landing-page visits, never for finished signups. Meta was told to find people who click, not people who sign up."]
    exps.append(_exp(
        "optimisation", "What Meta was told to optimise for",
        "Did any website ad ask Meta to find people who finish signing up (the pixel's Lead event) rather than just click?",
        "In early September the Ad Studio tool was changed to optimise for the pixel's Lead event, but that only applies to ads Ad Studio builds. The website ads checked here use the older Link Clicks objective, so they were set up in Ads Manager and never got the change.",
        ["Channel", "Optimised for", "Ads", "Spend", "Results", "Cost / result", "Quality results"], goal_rows or [["-", "not recorded", "-", "-", "-", "-", "-"]],
        learning, verdict, _confidence([v["results"] for v in by_goal.values() if v["results"]]) if by_goal else ("Low", "no data"),
        dont_repeat="Optimising website ads for clicks. It produces cheap visits and very few signups." if verdict == "notyet" else None,
        note="Only ads built since Sep 1 were checked."))

    # ---- mistakes to avoid
    mistakes = [{"from": e["title"], "text": e["dont_repeat"]} for e in exps if e["dont_repeat"]]

    # ---- what to try next
    dead = report.get("dead_pincodes") or []
    dead_spend = sum(p["est_spend"] for p in dead)
    top = report.get("top_pincodes") or []
    n_targeted = report.get("pincodes_targeted") or 0
    web_an = f"{an['share_of_spend']}% of website spend went to Audience Network" if an else "Website ads ran on Audience Network"
    best_ad = report["awards"].get("best_overall")
    out_of_area = pct(leads - matched, leads)
    up = next((x for x in report["states"] if x["state"] == "Uttar Pradesh"), None)
    hr = next((x for x in report["states"] if x["state"] == "Haryana"), None)
    top_names = ", ".join(p["pincode"] for p in top[:5])

    DATA, DATAH, HUNCH, PRACTICE = "Your data", "Your data + a hunch", "Untested hunch", "Good practice"

    def idea(priority, title, basis, why, how, measure, effort):
        return {"priority": priority, "title": title, "basis": basis, "why": why, "how": how, "measure": measure, "effort": effort}

    groups = [
        ("Fix the leaks first", "Your ads already bring many leads. Most are lost afterwards, so these usually pay back fastest.", [
            idea("High", "Make both screening questions Yes/No multiple choice", DATA,
                 f"Only {n(qual)} of {n(matched)} leads in a targeted pincode passed, and many answers are typed as amounts or text instead of yes/no.",
                 "Edit the Lead Form in Ads Manager: replace the two open questions with Yes / No choices (keep the Hindi text).",
                 "Share of pincode-matched leads that pass both questions.", "20 minutes"),
            idea("High", "Phone the in-target leads the same day", HUNCH,
                 f"{n(matched)} of {n(leads)} leads ({pct(matched, leads)}) are in a targeted pincode. Calling them is the quickest way to find out which are real, and leads usually go cold within hours.",
                 "Download the pincode-matched Excel from the Dashboard (tile download button) and call each within the hour. Note who can invest ₹5,00,000.",
                 "How many of those calls become a signup or a visit to the kiosk.", "1 hour a day"),
            idea("High", "Say the requirement in the ad: ₹5,00,000 and the area", DATAH,
                 f"{out_of_area} of leads aren't in a targeted pincode, and many answers show people can't invest ₹5,00,000. Saying it up front lets them screen themselves out.",
                 "Put one line in the first sentence of the ad and the form intro: 'Needs ₹5,00,000 investment. Punjab and Haryana villages only.'",
                 "Share of leads that are in a targeted pincode and pass both questions. Expect fewer leads, better ones.", "15 minutes"),
            idea("Medium", "Save a partial lead as soon as the phone is verified on the signup page", DATA,
                 "In the Meta pixel check on Sep 9, 1,228 people viewed the signup page but only 10 finished it. People who verify their phone and stop leave no record at all.",
                 "Ask the site's developer to save the verified mobile number at step 1, before the rest of the form.",
                 "Number of verified numbers you can follow up with.", "Developer task"),
        ]),
        ("Aim the money better", "Put the budget where your own numbers say it works.", [
            idea("High", "Run a 'winners only' ad", DATA,
                 (f"Only {len(top)} of {n_targeted} targeted pincodes have ever produced a lead. The most productive: {top_names}." if top else "Only a small share of targeted pincodes has ever produced a lead.")
                 + " The best result so far came from a narrow, Facebook-only list.",
                 "Build one narrow ad (15 pincodes or fewer) from the pincodes on the Performance tab under 'Pincodes producing leads'. Facebook only, Advantage+ audience.",
                 "Cost per lead from a targeted pincode, against the 1-15 pincode benchmark above.", "1 hour"),
            idea("High", "Stop sending cold traffic to the website. Capture on the Lead Form, then invite on WhatsApp", DATA,
                 (f"A website signup that carried an ad's campaign ID cost about {ratio:.0f}x a Lead Form lead (the true gap is smaller, since most signups have no campaign ID). " if ratio else "")
                 + "Website ads get the clicks but lose almost everyone in the 4-step form.",
                 "Move most website budget to Lead Form ads. Send in-target leads the kiosk.eko.in signup link on WhatsApp after you've spoken to them.",
                 "Signups per rupee spent, compared with the website ads.", "Half a day"),
            idea("High", "Switch off Audience Network on website ads", DATA,
                 f"{web_an}, the cheapest traffic, and Meta can't show signups per placement.",
                 "Ads Manager > Ad set > Placements > Manual placements: untick Audience Network. Keep everything else the same.",
                 "Website signups per rupee against the previous website ad.", "10 minutes"),
            idea("High", "Run a website ad that optimises for the pixel's Lead event", DATA,
                 "No website ad has asked Meta to find people who finish signing up." if verdict == "notyet" else "Compare the pixel-optimised ad against the click-optimised ones.",
                 "In Ads Manager, create a website ad with the Leads or Sales objective, choose your pixel, and set 'Lead' as the conversion event. Let it collect about 50 events before judging it. (Ad Studio's version of this hasn't been tested yet.)",
                 "Cost per signup against the click-optimised ads.", "1 hour"),
            idea("High", "Repeat the best setup, then change one thing", DATA,
                 (f"The best ad so far was {best_ad['account']}, built {best_ad['built']}: {best_ad['n_pincodes']} pincodes, ages {best_ad['age_setting']}, {best_ad['placement_setting']}." if best_ad else "Find the best ad on the Performance tab.")
                 + " It ran once, in one week.",
                 "Rebuild the same ad in the same account, then in the other account in the same week. After that change only one setting at a time.",
                 "Does the cost per quality lead hold? Which single change moves it?", "1 hour"),
            idea("Medium", "Fix or re-aim the Uttar Pradesh pincodes", DATA,
                 (f"Uttar Pradesh gives the cheapest leads ({m(up['cost_per_lead'])}) but only {pct(up['matched'], up['leads'])} are in a targeted pincode"
                  + (f", against {pct(hr['matched'], hr['leads'])} in Haryana." if hr else ".")) if up and up["leads"] else "Check how many Uttar Pradesh leads are in a targeted pincode.",
                 "Keep only the UP pincodes that have produced leads (for example 244901). Move the rest of the UP budget to Haryana and Punjab.",
                 "Share of UP leads that are in a targeted pincode.", "30 minutes"),
            idea("Medium", "Drop pincodes that have produced nothing", DATA,
                 f"{len(dead)} targeted pincodes have produced no leads, with an estimated {m(round(dead_spend))} spent on them." if dead else "No dead pincodes found.",
                 "Take the 'Targeted, but no leads yet' list on the Performance tab and remove the costliest from the next ad.",
                 "Cost per lead from a targeted pincode before and after.", "30 minutes"),
            idea("Medium", "Make a real women-only Bank Sakhi ad: Advantage+ Audience off", DATA,
                 (f"About {men_share}% of the 'women only' spend reached men." if men_share is not None else "Women-only settings weren't respected."),
                 "Ad set > Audience: use Original audience (not Advantage+), gender = Women, age 25-50, Facebook + Instagram.",
                 "Share of spend that reaches women, and cost per lead.", "30 minutes"),
        ]),
        ("Creative: ideas from competitors", "From the competitor pages and case studies in the section above. Competitors sell easy income and a low start; your offer is an owned kiosk that needs ₹5,00,000. These test whether being open about that works better.", [
            idea("High", "Test an honest 'what it takes' caption against an earnings-led caption", "Competitor research",
                 "PayNearby and Spice Money lead with income and low or zero investment. Your screening shows many leads can't invest ₹5,00,000, so an earnings-led message may be pulling in the wrong people.",
                 "Caption A: the ₹5,00,000 investment, the SBI kiosk you own, and the area. Caption B: the earnings-led version. Same picture, same pincodes, same budget, run the same week.",
                 "Cost per lead from a targeted pincode that also passes both questions. Expect A to bring fewer leads and better ones.", "1 hour"),
            idea("Medium", "Frame it as an add-on to the shop you already run", "Competitor research",
                 "Fino presents its banking point as an add-on to the shopkeeper's main business. Shop owners are your best-fit group.",
                 "Caption: 'Add an SBI banking counter to your shop.' Picture: a counter inside an ordinary village shop. Compare with 'start your own business'.",
                 "Share of leads that say they have a franchise or shop background.", "1 hour"),
            idea("Medium", "Add a training and support line", "Competitor research",
                 "Fino and PayNearby both stress training and support. A cash-handling business is a bigger leap for someone with no banking background.",
                 "One line in the caption and on the form: what training is given and who supports the operator after the kiosk opens.",
                 "Click-through rate and cost per lead.", "30 minutes"),
            idea("Medium", "Show a typical monthly commission range, only if you can back it up", "Competitor research",
                 "PayNearby's pages lead with a monthly income figure. Operators will ask the same question before spending ₹5,00,000.",
                 "Only use a range Eko can prove from real operators, and show what it depends on. Check it with compliance first (earnings claims are regulated).",
                 "Cost per lead from a targeted pincode against an ad with no figure.", "1 hour plus approval"),
            idea("Medium", "Tell people what happens next on the form's thank-you screen", "Competitor research",
                 "Fino's form tells applicants a representative will get in touch. People who know a call is coming are more likely to pick up.",
                 "Add 'We will call you within 24 hours from [number]. Please keep ₹5,00,000 ready to discuss.' to the Lead Form's completion screen.",
                 "Share of leads who answer the first call.", "15 minutes"),
        ]),
        ("Creative: from your own pictures and captions", "Drawn from the saved pictures and captions of 44 ads (Oct 19 figures). Only 13 of 866 leads fully qualified, so each of these is a direction to test, not a proven result.", [
            idea("High", "Take 'low investment' wording off every picture and caption", DATAH,
                 "Several pictures say 'कम निवेश' or 'छोटा निवेश' (low or small investment), but the form needs ₹5,00,000 and only 13 of 109 pincode-matched leads passed the two questions. The wording may be attracting people who can't invest.",
                 "Remake the infographic without those words and put the real figure in instead: 'निवेश: ₹5,00,000'. Keep everything else identical so the effect is clear.",
                 "Share of pincode-matched leads that pass both questions.", "1 hour"),
            idea("High", "Drop the 'every profession can apply' strip", HUNCH,
                 "The strip (teacher, farmer, student, youth, retired) invites people who may not have a shop or ₹5,00,000. Ads using it brought many leads but few qualified.",
                 "Replace it with one line for the real target: 'दुकान या जगह है? ₹5 लाख लगा सकते हैं?' Run it against the old picture.",
                 "Qualified leads per ₹1,000 spent.", "1 hour"),
            idea("High", "Use real photos of real shops, and stop AI-generated pictures", DATA,
                 "Every AI-generated creative produced 0 fully qualified leads, and the AI-picture website ads got 1 result from about ₹3,000. All four ads with qualified leads used real photos.",
                 "Pause the AI-picture ads. Take new photos at existing Eko CSPs: the counter, the owner, a customer. Get the owner's written consent.",
                 "Cost per qualified lead against the real-photo ads.", "Half a day"),
            idea("High", "Make more single-image lead-form ads, not only carousels", DATA,
                 "On lead forms, single images had a 35% quality rate against 12% for carousels. The 'Kiosk Operator बनें' image ad cost about ₹600 per qualified lead against roughly ₹2,000-2,500 for the two big carousels. It had only 36 leads, so repeat it before trusting it.",
                 "Duplicate the single-image ad with a new budget in the same pincodes, then try two or three new pictures.",
                 "Cost per qualified lead against the carousels.", "1 hour"),
            idea("Medium", "Try 'Kiosk Operator बनें' as the headline on other pictures", DATA,
                 "That headline was on the best-efficiency ad. Most other ads use the headline 'Bank Sakhi by Eko Bharat Ventures', which doesn't say what the person would do.",
                 "Keep picture and caption fixed and change only the headline: 'Kiosk Operator बनें' against 'Bank Sakhi by Eko Bharat Ventures'.",
                 "Cost per lead from a targeted pincode, and the quality rate.", "30 minutes"),
            idea("Medium", "One idea and less text per picture", DATAH,
                 "The dense infographic brought cheap leads (about ₹20 each) but only 5.7% quality. Its long list of benefits may make the offer look easy.",
                 "Test one picture with a single short line and the ₹5,00,000 figure against the dense infographic.",
                 "Quality rate, not cost per lead.", "1 hour"),
            idea("Medium", "Show what you need to start, in the picture", HUNCH,
                 "People can't judge ₹5,00,000 without seeing what it buys: a counter, a laptop, a fingerprint device, shop space.",
                 "A real photo of a complete setup with three short labels (space, device, the ₹ amount).",
                 "Share of leads that pass the investment question.", "Half a day"),
            idea("Medium", "Check whether the SBI sign reads as a job offer", HUNCH,
                 "Most pictures show a large SBI sign. Some people may read it as a bank job, not a business they fund and run.",
                 "Test one picture with the line 'अपना बिज़नेस, SBI का CSP' against the current picture, same caption and pincodes.",
                 "Share of leads that pass the franchise-background question.", "1 hour"),
            idea("Medium", "Video: a real operator, with subtitles", HUNCH,
                 "No video has been tried. People decide on this business by trust, and a face and a voice carry more of it than a poster.",
                 "20-30 seconds on a phone: the operator says what the work is, what they put in, and what they earn only if Eko can prove it. Add Hindi subtitles. Use real people, not AI video.",
                 "Cost per lead from a targeted pincode, against the best image ad.", "Half a day"),
            idea("Medium", "Video: a 15-second 'what you need' clip", HUNCH,
                 "A short checklist video can screen people before they reach the form.",
                 "Three cards on a real-photo background: 'दुकान या जगह', '₹5,00,000', 'लैपटॉप'. End on 'आवेदन करें'.",
                 "Quality rate against the single-image ads.", "2 hours"),
            idea("Medium", "Match the picture to the state", HUNCH,
                 "The ads use one set of pictures across Punjab, Haryana and Uttar Pradesh, but clothes, faces and shops differ by region.",
                 "Make Punjab, Haryana and UP versions with local dress and shops, and show each only to its own pincodes.",
                 "Cost per lead and quality rate by state.", "1 day"),
            idea("Medium", "Add a third form question: do you own or rent a shop or space?", DATAH,
                 "Only the investment and franchise-background questions screen today, but a kiosk needs a place to run from.",
                 "Add a Yes/No question: 'क्या आपके पास अपनी दुकान या जगह है?'. Don't make it a qualification rule until you've looked at the answers.",
                 "Share of qualified leads that have a shop or space.", "15 minutes"),
            idea("Medium", "Judge every creative on cost per qualified lead", DATA,
                 "The cheapest-lead ad (about ₹20) is also one of the weakest on quality (5.7%). Cost per lead alone would pick the wrong winner.",
                 "Use the cost per quality result column in the Images and captions table, and don't switch an ad off on cost per lead alone.",
                 "Cost per qualified lead.", "Ongoing"),
            idea("Low", "Pause ads that have had a fair chance and produced nothing", DATA,
                 "25 of the 44 ads with spend have no leads. Some have spent only a few hundred rupees, which is too little to judge.",
                 "Pause ads with more than ₹1,000 spent and no qualified leads. Leave newer ones until they reach the same spend.",
                 "Total spend on ads with no qualified lead.", "30 minutes"),
        ]),
        ("Creative and format", "Image and caption details are saved from the next refresh, so these can soon be checked against real results. Until then they are hunches to test.", [
            idea("Medium", "Click-to-WhatsApp ads, judged on qualified leads", "Market research",
                 "Meta's case study of a rural education company credits click-to-WhatsApp with 60% of its business, 80% of customers from remote Rajasthan. You can ask the screening questions in the chat. Judge it on qualified leads, since cost per chat can mislead.",
                 "Build an ad with the Messages objective that opens a WhatsApp chat with a short greeting and the two questions.",
                 "Cost per chat and share of chats that qualify, against Lead Form ads.", "2 hours"),
            idea("Medium", "Test local language and local photos, one change at a time", "Market research",
                 "Two published case studies found regional-language copy (and a regional-language form) did better than English, but they are single campaigns, not proof. No ad of yours has been compared on language or image.",
                 "Run two identical ads that differ only in language (or only in the image). Write down which is which, so it can be compared later.",
                 "Click-through rate and cost per lead from a targeted pincode.", "1 hour"),
            idea("Medium", "A short video from a real CSP operator", HUNCH,
                 "This business handles other people's cash, so trust may matter more than the headline. I found no independent evidence on video versus image for rural India, so test it yourself.",
                 "A 20-30 second phone video of an existing operator saying what the work is and the investment. Run it as a Reel on Facebook and Instagram.",
                 "Cost per lead from a targeted pincode, against the image ads.", "Half a day"),
            idea("Medium", "Retarget people who viewed the signup page but didn't finish", HUNCH,
                 "The pixel has seen thousands of people view the page and only a handful finish. They already know the offer.",
                 "Ads Manager > Audiences: Custom Audience from your pixel, 'ViewContent' in the last 30 days, excluding 'Lead'. Show them a reminder with the WhatsApp or Lead Form option.",
                 "Cost per lead from this audience against a cold audience.", "1 hour"),
            idea("Medium", "Build a lookalike audience from your qualified leads", DATAH,
                 f"You have {n(qual)} fully qualified and {n(matched)} pincode-matched leads, plus signups that passed qualification. Meta can find people who resemble them.",
                 "Ads Manager > Audiences: create a Custom Audience from the qualified leads' phone numbers, then a 1% Lookalike in India.",
                 "Share of leads in a targeted pincode, against a normal ad.", "1 hour"),
        ]),
        ("Process and tracking", "Not ads themselves, but they decide whether you can trust the results.", [
            idea("Medium", "Change one thing at a time, and wait for enough results", PRACTICE,
                 "Most past comparisons changed several things at once, so the cause can't be known. Several results rest on fewer than 30 leads.",
                 "Give each test ad about ₹500 a day for 5-7 days, with only one difference between the two ads. Judge only after about 50 results each.",
                 "Check the Confidence rating on this page after each test.", "Ongoing"),
            idea("Medium", "Use a Meta System User token that doesn't expire", DATA,
                 "The access token expired on Oct 18 and the dashboard couldn't refresh until it was replaced. Tokens from the Graph API Explorer last only a few days.",
                 "In Business Settings > System users, create a system user, give it your ad accounts and page, and generate a token with ads_read.",
                 "The dashboard keeps refreshing without anyone updating .env.", "30 minutes"),
            idea("Low", "Send leads to Meta from the server too (Conversions API)", PRACTICE,
                 "The browser pixel misses people with ad blockers and strict privacy settings, which weakens Meta's optimisation.",
                 "Developer task: send the same Lead event from the server with a shared event ID so it isn't counted twice.",
                 "Event Match Quality and Lead events in Events Manager.", "Developer task"),
            idea("Low", "Find the best days and hours", PRACTICE,
                 "Delivery by day of week and hour hasn't been looked at yet.",
                 "Ads Manager > Breakdown > By time. Compare cost per lead by hour and weekday on the biggest ads.",
                 "Cost per lead by time slot.", "30 minutes"),
        ]),
    ]
    idea_groups = [{"name": name, "intro": intro, "items": items} for name, intro, items in groups]
    n_ideas = sum(len(g["items"]) for g in idea_groups)
    counts = {}
    for e in exps:
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    creative = _creative_analysis(rows, creatives or {}, labels or {})
    return {"experiments": exps, "mistakes": mistakes, "idea_groups": idea_groups, "n_ideas": n_ideas,
            "verdict_counts": counts, "verdict_names": VERDICTS, "creative": creative,
            "competitors": COMPETITORS, "competitor_caveats": COMPETITOR_CAVEATS}
