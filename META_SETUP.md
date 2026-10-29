# Meta Setup — Complete Step-by-Step Guide (Account: "Bank Sakhi by Kiosk")

This is the full journey from zero to a working `.env` connection, explained at every
step. Follow in order — each phase depends on the one before it.

---

## Phase 1: Business Manager Account

**What it is / why it matters:** Business Manager is the umbrella account that holds
everything together — your Ad Accounts, Pages, the Developer App, and who has access to
what. Without it, Meta has no single place to link your Page, ad account, and app
together.

1. Go to **[business.facebook.com](https://business.facebook.com/)**
2. If you don't already have a Business Manager, click **Create Account**
3. Enter:
   - **Business name**: `Bank Sakhi by Kiosk`
   - Your name
   - Business email address
4. Click **Submit** → you'll get a confirmation email, verify it
5. You're now inside your Business Manager dashboard for "Bank Sakhi by Kiosk"

*(If you already have a Business Manager from before, just make sure you're inside the
right one — check the name in the top-left corner.)*

---

## Phase 2: Facebook Page

**What it is / why it matters:** Ads on Facebook/Instagram are always published *from* a
Page — it's the public identity behind the ad (the "who is running this ad" the viewer
sees). You need a Page named/branded for this before you can create ads.

1. Inside Business Manager, go to **Business Settings** (gear icon, or
   [business.facebook.com/settings](https://business.facebook.com/settings/))
2. Left sidebar → **Accounts → Pages**
3. Click **Add** → **Create a new Page**
4. Enter:
   - **Page name**: `Bank Sakhi by Kiosk`
   - **Category**: something like "Financial Service" or "Business Service"
   - A short description (e.g. "Recruiting SBI Kiosk Operators / CSP agents across India")
5. Click **Create Page**

*(If a Page with this name already exists and you're an admin on it, use **Add** →
**Add an existing Page** instead, and search for it.)*

---

## Phase 3: Ad Account

**What it is / why it matters:** This is the actual billing/account object that owns
campaigns, ad sets, and ads. This is the `act_<id>` we need for `.env`.

1. Business Settings → **Accounts → Ad Accounts**
2. Click **Add** → **Create a new ad account**
3. Enter:
   - **Ad account name**: `Bank Sakhi by Kiosk`
   - Time zone (use India Standard Time)
   - Currency: INR
4. Choose **My business** as who owns it (not "another business's account")
5. Click **Create**
6. You'll be asked to add a payment method at some point before spending real money —
   you can skip this for now since we're testing in dry-run / sandbox first, but you'll
   need it before any real ad actually goes live.

**To find the Ad Account ID afterward:** click into the ad account you just created —
the ID is shown near its name, a number like `1234567890`. We'll write this into `.env`
as `FB_AD_ACCOUNT_ID=act_1234567890` (note the `act_` prefix).

---

## Phase 4: Meta Developer App

**What it is / why it matters:** This is the "robot identity" that our code uses to talk
to Meta's API on your behalf. You already created one called `Kiosk_ad_maker` — this
phase just makes sure it's properly linked to the Business Manager and Page/Ad Account
from Phases 1-3.

1. Go to **[developers.facebook.com/apps](https://developers.facebook.com/apps/)**
2. Open your `Kiosk_ad_maker` app (or create a new one the same way if starting fresh —
   Business use case, select the same 3 use cases as before: **Create & manage ads**,
   **Measure ad performance**, **Capture & manage ad leads**)
3. In the app dashboard, go to **App settings → Basic**
4. Find **Business Account** / **Business Manager ID** field — link it to your
   "Bank Sakhi by Kiosk" Business Manager if it isn't already
5. Under each use case's **Tools** tab, when generating the token (Phase 5), Meta will
   only be able to grant access to Pages/Ad Accounts that are inside the same Business
   Manager — this is why Phases 1-3 come first.

---

## Phase 5: Access Token

**What it is / why it matters:** This is the actual credential our code uses. It proves
to Meta "this request is allowed to manage ads on behalf of this Page/Ad Account."

1. In the app dashboard, left sidebar → **Use cases** → click **Create & manage ads**
2. Click **Tools** in that use case's left sub-menu
3. Under **Get Access Token**, check all of:
   `ads_read`, `ads_management`, `business_management`, `pages_read_engagement`,
   `pages_show_list`, `pages_manage_ads`, `leads_retrieval`
4. Click **Get Token**
5. Copy the long token string that appears

*(You already did this — if the token is more than an hour or two old, generate a fresh
one before testing, since these are short-lived test tokens.)*

---

## Phase 6: Sandbox Ad Account (recommended before going live for real)

**What it is / why it matters:** A sandbox ad account behaves like a real one for API
testing (create campaigns, ad sets, ads) but **never actually spends real money or shows
real ads**. This lets us test our whole `campaign_builder.py` live code path safely
before pointing it at your real "Bank Sakhi by Kiosk" ad account.

1. Same **Tools** tab as Phase 5 → **Sandbox Ad Account Management**
2. Click **New Sandbox Ad Account**
3. Use the sandbox account's ID (also `act_<id>` format) as `FB_AD_ACCOUNT_ID` for a
   first test run
4. Once we confirm the full pipeline works against the sandbox with no errors, switch
   `FB_AD_ACCOUNT_ID` to your real "Bank Sakhi by Kiosk" ad account ID from Phase 3

---

## Phase 7: Page ID

1. Go to your "Bank Sakhi by Kiosk" Page
2. Click **About** (or Business Settings → Accounts → Pages → click the Page)
3. Scroll to find the **Page ID** — a long numeric string

---

## Phase 8: Put it all into `.env`

Open `C:\Users\devra\fbada\.env` and fill in:

```
FB_ACCESS_TOKEN=<token from Phase 5>
FB_AD_ACCOUNT_ID=act_<id from Phase 6 sandbox, or Phase 3 real account>
FB_PAGE_ID=<id from Phase 7>
```

Then verify:

```bash
python -c "from src.meta import auth; print(auth.connection_status())"
```

It should say **"Connected to ad account ... / page ..."** instead of "Not connected."

---

## What happens after this

Once connected, the Ad Studio web UI will automatically detect it (the status banner at
the top switches to green/connected) and building an ad will call the **real** Meta API
instead of dry-run — but it will still create everything **paused**, and will still ask
you to explicitly click **Activate Ad** before anything actually goes live and starts
spending.

Reminder: district-level targeting (Nashik, Dhule, etc.) still needs one more piece of
code — resolving district names to Meta's location IDs — before a live campaign will
successfully build. See `SYSTEM_STATUS.md` for the full pending-work list.
