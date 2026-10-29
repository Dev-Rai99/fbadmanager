# AdMarketer / Eko CSP Ad Studio — Full System Status

Last updated: reflects everything built in this project as of now.

## 1. What the system does, end to end

1. **Ad Brief intake** — product/opportunity, description, objective, tone, visual style, language, and one of 3 locked creative angles (general_opportunity / women_first / shop_owner).
2. **Ad copy** — either one of the 5 **locked, approved captions** (verbatim text from `Eko_SBI_Facebook_Instagram_Ads_Documentation.docx`, section 9) or LLM-generated draft copy (Gemini), which is still hard-constrained to the 4 approved CTA phrases and filtered against banned phrases ("eligibility check" wording).
3. **Image generation** — free AI background photos via Pollinations.ai (fully automated), or higher-quality images generated manually in ChatGPT and dropped into `output/` for the compositor to use.
4. **Compositing** — overlays the **real Eko logo** (downloaded from eko.in, used unmodified per the locked no-AI-recreation rule), the caption text, bullet points, and CTA boxes (website + call/WhatsApp shown together, matching the reference ad) onto the background photo. Done via a headless-browser (Playwright) render of an HTML/CSS template — chosen specifically because AI image models can't reliably render readable text/logos.
5. **Targeting** — district-level geo targeting (10 data-backed priority districts from `AO_District_Rural_Villages.xlsx`, with Kheri excluded as saturated), age range, gender, daily budget, placements, and run duration.
6. **Attachment/destination** — website link + call/WhatsApp number together, or a lead form (configurable fields).
7. **Ad build** — constructs the Campaign → Ad Set → Ad payload Meta's API requires. In **DRY_RUN** (no real Meta credentials), nothing is sent — the full payload is saved locally so it can be inspected. Once real credentials exist, it calls the actual Graph API to create these objects **paused**.
8. **Activation** — a separate, explicit confirmation step (with a "this will start spending money" warning) is required before an ad is ever switched from PAUSED to ACTIVE. Never happens automatically.
9. **Insights** — pulls performance data (see section 2). Returns realistic mock data until a real ad exists; pulls real numbers from Meta's Insights API once connected.
10. **Optimization agent** — evaluates the numbers against your guardrails and produces plain-language recommendations, not raw jargon.
11. **Web UI ("Ad Studio")** — one page: fill in the form (all fields pre-filled with sensible/locked defaults), click **Generate & Build Ad**, see the results and the "How Is Your Ad Doing?" insights section on the same page. Branded with Eko's real colors/logo, all icons are custom SVGs (no emoji).

## 2. What it tracks / measures

**Per-ad performance metrics** (mocked until Meta is connected, real thereafter):
- **Reach** — unique people who saw the ad
- **Impressions** — total times shown
- **Frequency** — impressions ÷ reach (fatigue signal — same people seeing it too often)
- **Clicks** / link clicks
- **CTR** — click-through rate
- **CPC / CPM** — cost per click / cost per 1,000 impressions
- **Spend** — running cost against budget
- **Leads** — form submissions or conversions
- **CPA** — cost per lead/acquisition

**Derived, plain-language guidance from the optimization agent:**
- 🛑 Pause warning if spend exceeds the daily budget cap
- ✅ "Increase budget" suggestion if CPA is well under target
- 😴 Fatigue warning if frequency gets too high
- 👍 "All fine" when within guardrails

**What's persisted locally** (not sent anywhere):
- The ad copy used per run (headline, subheading, bullets, CTA)
- Campaign/Ad Set/Ad IDs (placeholder IDs in dry-run, real Meta IDs once live)
- The full JSON payload that was or would be sent to Meta
- A snapshot of the last run (`data/last_run.json`) that powers the dashboard section

## 3. What's fully working right now (no Meta account needed)

- The entire creative pipeline: brief → copy → image → compositing → final ad image
- Targeting and attachment collection
- Dry-run "ad build" with full payload preview
- Mock insights + optimization agent recommendations
- The complete web UI, end to end, using dry-run data

## 4. What's pending on the Meta side (blocking a real, live ad)

1. **Access token** — not yet generated. You're currently stuck finding the "Generate Access Token" control in Graph API Explorer (Meta's UI has changed layout; last screenshot request is still pending from you).
2. **Ad Account ID** — not yet obtained.
3. **Page ID** — not yet obtained.
   → None of these three are in `.env` yet, so the system is still in DRY_RUN.
4. **District → Meta location ID resolution** — Meta's targeting API needs specific location "key" IDs from their Targeting Search endpoint (`GET /search?type=adgeolocation`), not raw district names like "Nashik". **This lookup has not been coded yet.** `campaign_builder.py` has a deliberate safeguard: it will **refuse to go live and throw an error** if it detects unresolved district names, rather than silently sending broken targeting to Meta. This needs to be built before any live campaign can actually launch, even once credentials exist.
5. **Long-lived / production token** — only short-lived (1-2 hour) test tokens have been discussed. A long-lived user token (~60 days) or a System User token (doesn't expire) hasn't been set up — needed for anything beyond one-off testing.
6. **App Review / Advanced Access** — Meta generally requires App Review for permissions like `ads_management` and `leads_retrieval` to work at scale beyond your own developer assets. Testing on your own ad account in Standard Access usually isn't blocked, but this hasn't been explicitly checked or requested yet.
7. **Live ad-creative code path is untested** — the code to upload images (`/adimages`), create ad creatives (`/adcreatives`), and create ads (`/ads`) via the real Graph API exists in `campaign_builder.py`, but has never actually run against Meta's live API since no token exists yet.
8. **Live Insights pulling is untested** — same situation in `insights.py`'s live branch.
9. **Lead form live integration is incomplete** — `attachments.py` supports configuring a lead form (fields to collect), but the actual Meta Lead Ads API call (creating a `leadgen_form` object and attaching it to the ad creative) has **not been implemented** in `campaign_builder.py` yet. Only the website + call/WhatsApp destination path is fully wired for going live.
10. **Business Portfolio / Business Manager linking** — the app-creation flow's "Business" tab (which likely asks you to connect a Business Portfolio, ad account, and Page directly) hasn't been completed yet.
11. **No webhook for real-time leads** — if/when the lead form path is built, leads would need to be pulled periodically via the Insights/Leads API rather than pushed instantly via webhook (webhooks not built).

## 5. Also not fully automated (non-Meta gaps)

- **Image generation isn't scripted end-to-end.** The high-quality images used so far came from you manually generating in ChatGPT's web UI and handing me the files — there's no automated API call for that step (would need OpenAI API billing, separate from your ChatGPT Plus subscription).
- **Google Imagen** was tested as an alternative and is blocked — quota is hard-set to 0 on the free API tier for image generation models, confirmed by direct testing. Would need billing enabled to use.

## 6. Bottom line

Everything creative and structural is done and working in dry-run. To get a **real, live ad running**, three things must happen in order:
1. Get the Meta access token + Ad Account ID + Page ID into `.env` (in progress — blocked on finding the token-generation UI)
2. Build the district-to-location-ID resolution step (not started)
3. Test the live campaign-build code path for the first time (currently only exercised by dry-run and can't be verified further until step 1 is done)
