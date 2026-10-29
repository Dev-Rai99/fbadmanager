def _ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else default


LEAD_FORM_FIELD_OPTIONS = ["full_name", "email", "phone_number", "city", "custom_question"]


def collect_attachment():
    """Asks what the ad should attach/link to, same options Meta's ad flow offers at the ad level.
    Website and call/WhatsApp can both be set -- the compositor renders both boxes together,
    matching the eko Kiosk reference layout."""
    print("\n--- Attachment / Destination ---")
    print("1) Website link")
    print("2) Lead form (collect info inside Facebook)")
    print("3) Website + call/WhatsApp (both boxes, matches the reference ad layout)")
    choice = _ask("Choose destination type (1-3)", "3")

    if choice == "2":
        print(f"Available lead form fields: {', '.join(LEAD_FORM_FIELD_OPTIONS)}")
        fields_raw = _ask("Fields to collect, comma separated", "full_name,phone_number")
        fields = [f.strip() for f in fields_raw.split(",") if f.strip()]
        return {"type": "lead_form", "fields": fields}

    if choice == "1":
        url = _ask("Destination URL", "kiosk.eko.in/signup")
        return {"type": "website", "url": url}

    url = _ask("Website URL", "kiosk.eko.in/signup")
    number = _ask("Call/WhatsApp number (with country code)", "+91 7291988625")
    return {"type": "website", "url": url, "number": number}
