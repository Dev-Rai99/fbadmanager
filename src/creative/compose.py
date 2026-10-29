from pathlib import Path
from playwright.sync_api import sync_playwright

from .. import config

TEMPLATE_PATH = Path(__file__).parent / "templates" / "ad_template.html"

DEFAULT_BRAND = {
    "primary_color": "#1b4f9c",
    "accent_color": "#f5821f",
    "brand_name": "eko",
    "brand_subtitle": "KIOSK",
}


def _bullet_html(text):
    return f'<div class="bullet"><span class="dot">&#10003;</span><span>{text}</span></div>'


def _cta_box_html(kind, icon, label, value):
    css_class = "primary" if kind == "primary" else "accent"
    return (
        f'<div class="cta-box {css_class}">'
        f'<div class="cta-icon">{icon}</div>'
        f'<div class="cta-text"><div class="label">{label}</div>'
        f'<div class="value">{value}</div></div></div>'
    )


def _bottom_badge_html(brand):
    return (
        '<div class="badge-bottom">'
        f'<div class="brand">{brand["brand_name"]}<span class="accent"> {brand["brand_subtitle"]}</span></div>'
        "</div>"
    )


def _logo_html(brand):
    logo_path = brand.get("logo_path")
    if logo_path and Path(logo_path).exists():
        uri = Path(logo_path).resolve().as_uri()
        return f'<img src="{uri}" class="logo-image" alt="logo">'
    # No approved logo file supplied -- placeholder only, not for real use.
    return f'<div class="logo">{brand["brand_name"]}</div><div class="logo-sub">{brand["brand_subtitle"]}</div>'


def compose_ad(background_image_path, ad_copy, attachment, brand=None, out_path=None):
    """Overlays branded, legible text (logo, headline, bullets, CTAs) onto an
    AI-generated background photo using a headless-browser render, since
    diffusion image models can't reliably render text (especially Devanagari)."""
    brand = {**DEFAULT_BRAND, **(brand or {})}

    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    bullets = ad_copy.get("bullets") or [ad_copy.get("description", "")]
    bullets_html = "\n".join(_bullet_html(b) for b in bullets[:4])

    cta_text = ad_copy.get("cta_text", "अभी आवेदन करें")
    cta_boxes = []
    # Both a call/WhatsApp box and a website box can appear together, matching the
    # reference layout -- these are independent, not mutually exclusive by type.
    if attachment.get("number"):
        cta_boxes.append(_cta_box_html("primary", "&#128222;", "कॉल / व्हाट्सऐप करें", attachment["number"]))
    if attachment.get("url"):
        cta_boxes.append(_cta_box_html("accent", "&#127760;", cta_text, attachment["url"]))
    if not cta_boxes:
        cta_boxes.append(_cta_box_html("primary", "&#128222;", cta_text, "N/A"))
    cta_html = "\n".join(cta_boxes)

    bg_uri = Path(background_image_path).resolve().as_uri()

    html = (
        template
        .replace("__BACKGROUND_IMAGE__", bg_uri)
        .replace("__PRIMARY_COLOR__", brand["primary_color"])
        .replace("__ACCENT_COLOR__", brand["accent_color"])
        .replace("__LOGO_HTML__", _logo_html(brand))
        .replace("__HEADLINE_LINE1__", ad_copy.get("headline_line1", ad_copy.get("headline", "")))
        .replace("__HEADLINE_LINE2__", ad_copy.get("headline_line2", ""))
        .replace("__SUBHEADING__", ad_copy.get("subheading", ad_copy.get("primary_text", "")))
        .replace("__BULLETS_HTML__", bullets_html)
        .replace("__CTA_BOXES_HTML__", cta_html)
        .replace("__BOTTOM_BADGE_HTML__", _bottom_badge_html(brand))
    )

    tmp_html = config.OUTPUT_DIR / "_tmp_ad_render.html"
    tmp_html.write_text(html, encoding="utf-8")

    out_path = out_path or (config.OUTPUT_DIR / "final_ad.png")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1536, "height": 1024})
        page.goto(tmp_html.resolve().as_uri())
        page.wait_for_timeout(300)  # let web fonts finish loading
        page.screenshot(path=str(out_path))
        browser.close()

    tmp_html.unlink(missing_ok=True)
    return str(out_path)
