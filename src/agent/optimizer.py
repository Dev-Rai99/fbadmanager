def evaluate(insights, guardrails):
    """Rule-based, bounded decisions only -- never raises its own spend ceiling.
    guardrails: {"max_daily_spend": float, "target_cpa": float}
    Returns a list of (action, reason) tuples; the caller decides how to apply them."""
    actions = []

    if insights["spend"] > guardrails["max_daily_spend"]:
        actions.append(("pause", f"Spend {insights['spend']} exceeded max_daily_spend {guardrails['max_daily_spend']}"))

    cpa = insights.get("cpa")
    if cpa is not None and guardrails.get("target_cpa") and cpa > guardrails["target_cpa"] * 1.5:
        actions.append(("pause", f"CPA {cpa} is 1.5x above target_cpa {guardrails['target_cpa']}"))
    elif cpa is not None and guardrails.get("target_cpa") and cpa < guardrails["target_cpa"] * 0.7:
        actions.append(("increase_budget", f"CPA {cpa} well under target {guardrails['target_cpa']}, performing well"))

    if insights.get("frequency", 0) > 4:
        actions.append(("flag_fatigue", f"Frequency {insights['frequency']} is high, audience may be fatigued"))

    if not actions:
        actions.append(("hold", "Within guardrails, no action needed"))

    return actions


def summarize(insights, actions):
    lines = [
        f"Reach: {insights['reach']} | Impressions: {insights['impressions']} | "
        f"Clicks: {insights['clicks']} | CTR: {insights.get('ctr')}% | Spend: {insights['spend']}",
        f"Leads: {insights.get('leads')} | CPA: {insights.get('cpa')}",
        "Agent decisions:",
    ]
    for action, reason in actions:
        lines.append(f"  - {action}: {reason}")
    return "\n".join(lines)
