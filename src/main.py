import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from . import config
from .creative import copy_gen, image_gen, compose, captions
from . import targeting, attachments
from .meta import auth, campaign_builder, insights
from .agent import optimizer

DEFAULT_LOGO_PATH = str(config.ROOT / "assets" / "eko-logo.svg")


def _ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else default


def collect_brief():
    print("--- Ad Brief ---")
    print("(Locked scope: this recruits SBI Kiosk Operators / CSP agents -- it is NOT a")
    print(" customer-facing banking-services ad. See ARCHITECTURE.md for the locked decisions.)")
    product = _ask("What are you advertising?", "SBI Kiosk Operator / CSP Agent opportunity")
    description = _ask(
        "Describe it in a sentence or two",
        "Run your own SBI Customer Service Point (CSP) kiosk and earn a stable local income.",
    )
    objective = _ask("Objective (leads/sales/traffic/awareness/engagement)", "leads")
    tone = _ask("Tone (e.g. friendly, professional, energetic)", "friendly, trustworthy, aspirational")
    visual_style = _ask("Visual style (e.g. photo-realistic, flat illustration)", "warm documentary photo-realistic")
    language = _ask("Ad copy language (english/hindi)", "hindi")

    print("\nApproved creative angles: 1) general_opportunity  2) women_first  3) shop_owner")
    angle_choice = _ask("Choose angle (1-3)", "1")
    angle = {"1": "general_opportunity", "2": "women_first", "3": "shop_owner"}.get(angle_choice, "general_opportunity")

    return {
        "product": product,
        "description": description,
        "objective": objective,
        "tone": tone,
        "visual_style": visual_style,
        "language": language,
        "angle": angle,
    }


def collect_brand():
    print("\n--- Brand ---")
    logo_default = DEFAULT_LOGO_PATH if os.path.exists(DEFAULT_LOGO_PATH) else ""
    logo_path = _ask(
        "Path to the official Eko logo file (locked decision bans AI-recreated logos)",
        logo_default,
    )
    brand_name = _ask("Brand name (used only if no logo file given)", "eko")
    brand_subtitle = _ask("Sub-brand / tagline (used only if no logo file given)", "KIOSK")
    primary_color = _ask("Primary brand color (hex)", "#1b4f9c")
    accent_color = _ask("Accent brand color (hex)", "#f5821f")
    return {
        "logo_path": logo_path or None,
        "brand_name": brand_name,
        "brand_subtitle": brand_subtitle,
        "primary_color": primary_color,
        "accent_color": accent_color,
    }


def main():
    brief = collect_brief()
    brand = collect_brand()

    print("\n--- Ad copy ---")
    print("Approved locked captions (section 9 of the docs) -- final text, not a starting point:")
    for i, raw in enumerate(captions.LOCKED_CAPTIONS, start=1):
        print(f"  {i}) {raw}")
    choice = _ask("Use locked caption # (1-5), or 'g' to generate new copy for review", "1")

    if choice.lower() == "g":
        ad_copy = copy_gen.generate_ad_copy(brief, angle=brief["angle"])
    else:
        ad_copy = captions.caption_to_ad_copy(int(choice))

    print(f"Headline: {ad_copy['headline']}")
    print(f"Subheading: {ad_copy.get('subheading', '')}")
    print(f"Bullets: {ad_copy.get('bullets', [])}")
    print(f"CTA: {ad_copy.get('cta_text', ad_copy['cta'])} ({ad_copy['cta']})")

    print("\n--- Generating background photos (this may take a moment) ---")
    bg_paths = image_gen.generate_images(brief, count=4)
    if not bg_paths:
        print("No images were generated -- check network access and try again.")
        return

    targeting_data = targeting.collect_targeting()
    attachment_data = attachments.collect_attachment()

    print("\n--- Compositing final ads (logo, headline, bullets, CTA) ---")
    image_paths = []
    for i, bg_path in enumerate(bg_paths):
        out_path = config.OUTPUT_DIR / f"final_ad_{i+1}.png"
        try:
            final_path = compose.compose_ad(bg_path, ad_copy, attachment_data, brand=brand, out_path=out_path)
            image_paths.append(final_path)
            print(f"[compose] saved {final_path}")
        except Exception as e:
            print(f"[compose] variant {i+1} failed: {e}")

    if not image_paths:
        print("Compositing failed for all variants -- falling back to raw background photos.")
        image_paths = bg_paths

    print(f"\n{len(image_paths)} final ad image(s) saved to the output/ folder. Review them, then continue.")
    proceed = _ask("Continue with these? (y/n)", "y")
    if proceed.lower() != "y":
        print("Stopped after creative review. Re-run to regenerate.")
        return

    print("\n--- Meta connection status ---")
    print(auth.connection_status())

    result, mode = campaign_builder.build_campaign(
        brief, ad_copy, image_paths, targeting_data, attachment_data
    )

    print(f"\n--- Campaign built ({mode}) ---")
    print(f"Campaign ID: {result['campaign_id']}")
    print(f"Ad Set ID: {result['ad_set_id']}")
    print(f"Ad ID: {result['ad_id']}")

    if mode == "live":
        confirm = _ask(
            f"About to spend up to {targeting_data['daily_budget']}/day for "
            f"{targeting_data['schedule_days']} day(s). Activate now? (y/n)",
            "n",
        )
        if confirm.lower() == "y":
            campaign_builder.activate(result)
        else:
            print("Left paused. You can review and launch it manually in Ads Manager.")
    else:
        print(
            "\nThis was a DRY RUN (no Meta credentials configured yet) -- nothing was "
            "sent to Facebook. The full payload it would have sent is saved under data/."
        )

    print("\n--- Sample performance pull (mock data until Meta is connected) ---")
    guardrails = {
        "max_daily_spend": targeting_data["daily_budget"],
        "target_cpa": float(_ask("Target CPA for the optimization agent to watch", "150")),
    }
    metrics = insights.get_insights(result)
    actions = optimizer.evaluate(metrics, guardrails)
    print(optimizer.summarize(metrics, actions))


if __name__ == "__main__":
    main()
