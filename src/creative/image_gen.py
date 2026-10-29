import time
import urllib.parse
import requests

from .. import config

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}"


ANGLE_SCENES = {
    "general_opportunity": (
        "An Indian bank correspondent kiosk operator, a man around 40 years old with "
        "a mustache, plain collared shirt, sitting at a wooden table with a small "
        "biometric fingerprint POS device and receipt printer, helping an elderly "
        "Indian woman in a faded cotton saree with her head covered, and a "
        "middle-aged Indian man in a plain kurta."
    ),
    "women_first": (
        "A confident Indian woman kiosk operator in her late 20s-30s, wearing a "
        "simple cotton saree or salwar kameez with a small bindi, sitting at a "
        "wooden table with a small biometric fingerprint POS device, helping an "
        "elderly Indian woman in a saree and a village farmer. She looks capable "
        "and is treated with respect by her customers."
    ),
    "shop_owner": (
        "An Indian kirana/general store owner, a man around 40-50 with a mustache, "
        "standing behind his small shop counter stacked with everyday goods, who "
        "has added a small biometric fingerprint POS device and receipt printer on "
        "one side of the counter, serving a village customer alongside his regular "
        "shop business."
    ),
}


def _build_visual_prompt(brief):
    angle = brief.get("angle", "general_opportunity")
    scene = ANGLE_SCENES.get(angle, ANGLE_SCENES["general_opportunity"])
    return (
        f"Documentary photojournalism style photo, Canon 5D 85mm lens, shot in a "
        f"village in Uttar Pradesh India, for a recruitment advertisement about: "
        f"{brief['product']}. {scene} "
        "Setting: a small brick-walled banking kiosk with a tin roof, dusty village "
        "lane and thatched-roof huts visible in the background, warm golden hour "
        "sunlight, realistic South Asian faces and skin tones, National Geographic "
        "style editorial photography, ultra realistic -- this must read as "
        "genuinely Indian and rural, not a Western or generic stock-photo setting. "
        f"Additional style notes: {brief.get('visual_style', 'warm documentary photo-realistic, natural lighting')}. "
        "Compose the main subjects slightly right-of-center, leaving open space on the "
        "left third of the frame for a text overlay to be added later. "
        "IMPORTANT: no text, no letters, no logos, no watermark anywhere in the image — "
        "text will be added separately. High quality, square composition."
    )


def generate_images(brief, count=4, out_prefix="ad_image"):
    """Generates `count` image variants via Pollinations.ai (free, no key required).
    Returns a list of saved file paths."""
    prompt = _build_visual_prompt(brief)
    encoded = urllib.parse.quote(prompt)
    paths = []

    for i in range(count):
        seed = int(time.time()) + i  # vary seed so each call returns a different image
        url = POLLINATIONS_URL.format(prompt=encoded) + f"?width=1024&height=1024&nologo=true&seed={seed}"
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
        except Exception as e:
            print(f"[image_gen] variant {i+1} failed: {e}")
            continue

        out_path = config.OUTPUT_DIR / f"{out_prefix}_{i+1}.png"
        with open(out_path, "wb") as f:
            f.write(r.content)
        paths.append(str(out_path))
        print(f"[image_gen] saved {out_path}")

    return paths
