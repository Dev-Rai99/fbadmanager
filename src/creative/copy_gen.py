import json
import re
import requests

from .. import config

TEXT_MODEL = "gemini-3.6-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{TEXT_MODEL}:generateContent?key={{key}}"
)

# Locked decisions (Eko_SBI_Facebook_Instagram_Ads_Documentation.docx):
# only these 4 CTAs are approved, eligibility-check messaging is banned,
# and creative must be one of exactly 3 angles.
APPROVED_CTAS_HINDI = [
    "आज ही आवेदन करें",
    "अभी आवेदन करें",
    "आज ही शुरुआत करें",
    "अभी जानकारी प्राप्त करें",
]
CTA_TO_META_TYPE = {
    "आज ही आवेदन करें": "APPLY_NOW",
    "अभी आवेदन करें": "APPLY_NOW",
    "आज ही शुरुआत करें": "SIGN_UP",
    "अभी जानकारी प्राप्त करें": "LEARN_MORE",
}
APPROVED_ANGLES = ["general_opportunity", "women_first", "shop_owner"]
BANNED_PHRASES = ["Check Eligibility", "पात्रता जांचें", "पात्रता जांच"]


def _fallback_copy(brief):
    cta_text = APPROVED_CTAS_HINDI[0]
    return {
        "headline_line1": brief["product"],
        "headline_line2": "मौका है आपके पास",
        "subheading": brief["description"],
        "primary_text": brief["description"],
        "description": f"{brief['product']} के बारे में जानें।",
        "bullets": [brief["description"]],
        "cta_text": cta_text,
        "cta": CTA_TO_META_TYPE[cta_text],
        "headline": f"{brief['product']} मौका है आपके पास",
        "angle": "general_opportunity",
    }


def _validate_and_fix(data, brief):
    """Enforce the locked constraints regardless of what the model returned."""
    cta_text = data.get("cta_text", "")
    if cta_text not in APPROVED_CTAS_HINDI:
        cta_text = APPROVED_CTAS_HINDI[0]
    data["cta_text"] = cta_text
    data["cta"] = CTA_TO_META_TYPE[cta_text]

    for field in ("headline_line1", "headline_line2", "subheading", "primary_text"):
        text = data.get(field, "") or ""
        for banned in BANNED_PHRASES:
            if banned in text:
                print(f"[copy_gen] WARNING: banned phrase '{banned}' found in {field}, stripping.")
                text = text.replace(banned, "")
        data[field] = text.strip()

    data["bullets"] = [
        b for b in data.get("bullets", [])
        if not any(banned in b for banned in BANNED_PHRASES)
    ][:4] or [brief["description"]]

    return data


def generate_ad_copy(brief, angle="general_opportunity"):
    """brief: dict with product, description, objective, tone, visual_style, language.
    angle must be one of APPROVED_ANGLES -- this is a locked decision, not a free choice."""
    if angle not in APPROVED_ANGLES:
        print(f"[copy_gen] '{angle}' is not an approved creative angle, defaulting to general_opportunity.")
        angle = "general_opportunity"

    if not config.GOOGLE_API_KEY:
        return _fallback_copy(brief)

    language = brief.get("language", "hindi")
    angle_guidance = {
        "general_opportunity": "Broad appeal — anyone in the community who wants a stable local income opportunity.",
        "women_first": "Speak directly to women — running a CSP kiosk as a respected, safe, local income source for women.",
        "shop_owner": "Speak to existing shop/kirana store owners — add a CSP kiosk to their existing shop for extra income.",
    }[angle]

    prompt = f"""You are writing a Facebook/Instagram recruitment ad in {language} for SBI Kiosk Operators
(people who will run a CSP banking correspondent kiosk for Eko/SBI). This is a RECRUITMENT ad aimed at
people who might become kiosk operators -- it is NOT advertising banking services to bank customers.

Creative angle: {angle} — {angle_guidance}

HARD RULES (do not break these):
- Do NOT mention "eligibility", "check eligibility", "पात्रता जांचें" or anything implying the reader
  needs to pre-qualify before applying. This messaging is explicitly banned.
- The call-to-action MUST be exactly one of these four approved Hindi phrases, verbatim, nothing else:
  {json.dumps(APPROVED_CTAS_HINDI, ensure_ascii=False)}

Return ONLY valid JSON, no markdown fences, with keys:
- headline_line1 (max 20 characters, bold hook, first half of the headline)
- headline_line2 (max 20 characters, second half of the headline, different emphasis/color than line1)
- subheading (max 60 characters, one supporting line)
- primary_text (max 125 characters, for the ad body)
- description (max 30 characters, short link description)
- bullets (array of exactly 4 short benefit phrases, each under 30 characters)
- cta_text (must be exactly one of the four approved phrases above, verbatim)

If language is "hindi", write headline_line1, headline_line2, subheading, primary_text, and bullets
in Hindi using Devanagari script (this is for a rural/semi-urban Indian audience).

Product/service: {brief['product']}
Description: {brief['description']}
Tone: {brief.get('tone', 'friendly, trustworthy, aspirational')}
"""
    url = GEMINI_URL.format(key=config.GOOGLE_API_KEY)
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        r = requests.post(url, json=payload, timeout=90)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        data = json.loads(text)
        data = _validate_and_fix(data, brief)
        data.setdefault("headline", f"{data.get('headline_line1', '')} {data.get('headline_line2', '')}".strip())
        data["angle"] = angle
        return data
    except Exception as e:
        print(f"[copy_gen] LLM copy generation failed ({e}), using fallback template.")
        return _fallback_copy(brief)
