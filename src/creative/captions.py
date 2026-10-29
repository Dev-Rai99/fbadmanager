# Verbatim from Eko_SBI_Facebook_Instagram_Ads_Documentation.docx, section 9.
# These are FINAL approved copy, not a starting point for rewriting -- each is
# "hook | body | cta", with cta always one of the 4 approved phrases.
LOCKED_CAPTIONS = [
    "SBI Kiosk Operator बनने का अवसर | अपने इलाके में बैंकिंग सेवाओं से जुड़ें | आज ही आवेदन करें",
    "अपने इलाके में नई पहचान बनाएं | लोगों तक बैंकिंग सेवाएं पहुंचाएं | आज ही शुरुआत करें",
    "अपने इलाके के लोगों तक बैंकिंग सेवाएं पहुंचाएं | SBI Kiosk Operator बनने का अवसर जानें | अभी आवेदन करें",
    "क्या आप एक नया व्यवसायिक अवसर तलाश रहे हैं? | SBI Kiosk Operator बनने की प्रक्रिया जानें | आज ही आवेदन करें",
    "अपने इलाके में बैंकिंग सेवाओं का हिस्सा बनें | SBI Kiosk Operator बनने का अवसर जानें | अभी जानकारी प्राप्त करें",
]

# Not part of the locked doc -- a sensible default bullet set for the recruitment
# angle. Review/replace with anything the doc specifies before this ships for real.
DEFAULT_BULLETS = [
    "स्थिर मासिक आय का अवसर",
    "अपने क्षेत्र में सम्मानजनक कार्य",
    "SBI के साथ सीधा जुड़ाव",
    "प्रशिक्षण एवं सहयोग उपलब्ध",
]


def caption_to_ad_copy(index):
    """index: 1-5. Splits the locked 'hook | body | cta' string into the fields
    the compose template needs."""
    if not (1 <= index <= len(LOCKED_CAPTIONS)):
        raise ValueError(f"Locked caption index must be 1-{len(LOCKED_CAPTIONS)}")

    raw = LOCKED_CAPTIONS[index - 1]
    hook, body, cta_text = [p.strip() for p in raw.split("|")]

    from .copy_gen import CTA_TO_META_TYPE  # local import to avoid a cycle at module load

    return {
        "headline_line1": hook,
        "headline_line2": "",
        "subheading": body,
        "primary_text": f"{hook} {body}",
        "description": body,
        "bullets": DEFAULT_BULLETS,
        "cta_text": cta_text,
        "cta": CTA_TO_META_TYPE.get(cta_text, "LEARN_MORE"),
        "headline": hook,
        "source": "locked_caption",
        "caption_index": index,
    }
