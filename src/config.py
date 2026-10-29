import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

FB_ACCESS_TOKEN = os.environ.get("FB_ACCESS_TOKEN", "")
FB_AD_ACCOUNT_ID = os.environ.get("FB_AD_ACCOUNT_ID", "")  # format: act_<id>
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "")
FB_API_VERSION = os.environ.get("FB_API_VERSION", "v26.0")
# Once a Meta Pixel is installed on the signup site and firing a conversion
# event (e.g. CompleteRegistration) on successful signup, set this so website
# ("traffic") ads can optimize for that conversion instead of Link Clicks --
# see campaign_builder.py. Empty/unset means no pixel yet, so those ads keep
# using Link Clicks as they always have.
FB_PIXEL_ID = os.environ.get("FB_PIXEL_ID", "")

# Real website signup data, straight from the SBI Kiosk signup site's own
# admin panel -- this is the ground truth for "did someone actually complete
# the signup form," which Meta's own URL-ad tracking can't answer without a
# pixel. Protected by HTTP Basic Auth at the reverse proxy.
EKO_ADMIN_BASE_URL = os.environ.get("EKO_ADMIN_BASE_URL", "https://122.176.147.78:8443/eko-kiosk")
EKO_ADMIN_USER = os.environ.get("EKO_ADMIN_USER", "")
EKO_ADMIN_PASS = os.environ.get("EKO_ADMIN_PASS", "")
EKO_ADMIN_CONNECTED = bool(EKO_ADMIN_USER and EKO_ADMIN_PASS)

OUTPUT_DIR = ROOT / "output"
DATA_DIR = ROOT / "data"
OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# The system only talks to the real Meta Graph API once all three of these
# are present. Otherwise every Meta-facing step runs in DRY_RUN and only
# prints/saves the payload it *would* send.
META_CONNECTED = bool(FB_ACCESS_TOKEN and FB_AD_ACCOUNT_ID and FB_PAGE_ID)
