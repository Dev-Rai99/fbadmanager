from .. import config


def connection_status():
    if config.META_CONNECTED:
        return f"Connected to ad account {config.FB_AD_ACCOUNT_ID} / page {config.FB_PAGE_ID}."
    missing = [
        name
        for name, val in [
            ("FB_ACCESS_TOKEN", config.FB_ACCESS_TOKEN),
            ("FB_AD_ACCOUNT_ID", config.FB_AD_ACCOUNT_ID),
            ("FB_PAGE_ID", config.FB_PAGE_ID),
        ]
        if not val
    ]
    return (
        "Not connected to Meta. Missing in .env: " + ", ".join(missing) + "\n"
        "To connect for real:\n"
        "  1. Create a Meta app at developers.facebook.com and get it approved for "
        "the Marketing API (ads_management permission).\n"
        "  2. Run the Facebook Login for Business OAuth flow (or, for quick manual "
        "testing only, generate a short-lived token in Graph API Explorer with "
        "ads_management + pages_show_list scopes).\n"
        "  3. Put FB_ACCESS_TOKEN, FB_AD_ACCOUNT_ID (format act_<id>), and FB_PAGE_ID "
        "into .env.\n"
        "Until then, campaign building runs in DRY_RUN — payloads are printed and "
        "saved locally instead of being sent to Meta."
    )
