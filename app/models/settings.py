# ============================================================================
# Settings — one key-value table, merged over DEFAULT_SETTINGS on read.
# ============================================================================

from sqlalchemy import Column, Integer, String, Text, DateTime, func

from app.database import Base


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Default settings keys
DEFAULT_SETTINGS = {
    # Receipt-scanning engine preference: "auto" = platform-native engine
    # first (Windows OCR / Apple Vision), tesseract fallback; "tesseract"
    # = prefer tesseract when installed (sharper region reads). The
    # SLOWBOOKS_OCR_ENGINE env var (support tool) outranks this.
    "ocr_engine": "auto",
    # Company type drives the vocabulary (Customer/Donor, Invoice/Pledge,
    # Class/Fund ...), the nonprofit nav items and reports, the default
    # dashboard layout and the on-demand net-asset accounts. Data never
    # changes name; only what is rendered. business | nonprofit | construction
    "company_type": "business",
    "company_name": "My Company",
    # First month of the fiscal year, "1"-"12" (#444). "1" = the calendar
    # year (the default) and changes nothing. Moves reports' fiscal-year
    # presets and default periods, the dashboard's profit card and nonprofit
    # FY-to-date; payroll forms (W-2, 941) stay on the calendar year by law.
    "fiscal_year_start_month": "1",
    # Legal entity type (#488) — separate from company_type, and placed away
    # from it so a sibling PR editing that line merges cleanly. Drives which
    # federal return the company files (Schedule C only for a sole proprietor
    # or a single-member LLC taxed as one). "" = unset, so existing companies
    # see no change until they choose. llc_tax_election is only meaningful
    # when entity_type == "llc". Books never change; this is tax form only.
    # entity_type: sole_proprietor | general_partnership | limited_partnership
    #              | llp | llc | c_corp | s_corp
    # llc_tax_election: disregarded | partnership | s_corp | c_corp
    "entity_type": "",
    "llc_tax_election": "",
    "company_address1": "",
    "company_address2": "",
    "company_city": "",
    "company_state": "",
    "company_zip": "",
    "company_phone": "",
    "company_email": "",
    "company_website": "",
    "company_tax_id": "",
    "operator_name": "",
    "operator_email": "",
    # Desktop: "true" signs the company out whenever the app is quit, so the
    # next start asks for the password (services/auth.py). Off by default.
    "ask_password_on_start": "false",
    "default_terms": "Net 30",
    "default_tax_rate": "0.0",
    # "false" starts every new sales line (invoice, estimate, sales receipt,
    # credit memo, recurring, Quick Entry) and every new item untaxed (#486):
    # a service company that charges no sales tax stops unticking the Tax box
    # line by line. "true" (the default) is how it always was. A line or an
    # item that says is_taxable keeps what it says; a tax-exempt customer
    # still makes every line untaxed.
    "new_lines_taxable": "true",
    "invoice_prefix": "",
    "invoice_next_number": "1001",
    "estimate_prefix": "E-",
    "estimate_next_number": "1001",
    "invoice_notes": "Thank you for your business.",
    "invoice_footer": "",
    # Giving statements (#415, nonprofit mode): the ministry's own footer on
    # the year-end statement (its address, website, EIN, a verse) replaces the
    # old product footer. Plain text with line breaks, escaped on the page;
    # empty prints what printed before, minus the product name. The opening
    # message is a saved giving-statement message (invoice_messages table), not
    # a settings key. The IRS sentence is always printed and is not editable.
    "giving_statement_footer": "",
    # Feature 10: Closing Date Enforcement
    "closing_date": "",
    "closing_date_password": "",
    # Feature 8: SMTP Email
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from_email": "",
    "smtp_from_name": "",
    "smtp_use_tls": "true",
    # Feature 15: Company Logo
    "company_logo_path": "",
    "invoice_show_logo": "true",
    # Report PDF paper size: letter | a4
    "pdf_paper_size": "letter",
    # Pre-printed check stock (#440, from discussion #354). "plain" prints
    # the full voucher — labels, lines, borders and the two stubs — as it
    # always has. "preprinted" prints only the values (payee, amount, amount
    # in words, date, memo) onto stock that already carries "PAY TO THE ORDER
    # OF", the MEMO and signature lines and the borders; the stubs are
    # unchanged. The nudge shifts the check portion only (not the stubs) to
    # line the values up with the pre-printed boxes, in inches from -0.5 to
    # +0.5. Stored as a decimal string.
    "check_stock": "plain",
    "check_nudge_x_in": "0",
    "check_nudge_y_in": "0",
    # Opening-balance wizard readiness metadata
    "chart_setup_source": "",
    "chart_setup_ready_at": "",
    # Multi-currency: ISO code the general ledger is kept in
    "home_currency": "USD",
    # Stripe Online Payments
    "stripe_enabled": "false",
    "stripe_publishable_key": "",
    "stripe_secret_key": "",
    "stripe_webhook_secret": "",
    # PayPal Online Payments
    "paypal_enabled": "false",
    "paypal_environment": "sandbox",  # sandbox | live
    "paypal_client_id": "",
    "paypal_client_secret": "",
    "paypal_webhook_id": "",
    # Square Online Payments
    "square_enabled": "false",
    "square_environment": "sandbox",  # sandbox | production
    "square_access_token": "",
    "square_location_id": "",
    "square_webhook_signature_key": "",
    # Square signs webhooks over the EXACT notification URL registered in
    # its dashboard; set this to that URL (required for webhook verification)
    "square_notification_url": "",
    # SimpleFIN bank feeds (user-held bridge.simplefin.org credential).
    # access_url embeds basic-auth creds — listed in SECRET_KEYS.
    "simplefin_access_url": "",
    "simplefin_account_map": "{}",  # JSON {simplefin id: bank_account_id}
    # JSON [simplefin id, ...]: feeds whose bank connection reports amounts
    # with the opposite sign (deposits negative); imported flipped
    "simplefin_reversed": "[]",
    "simplefin_accounts_cache": "[]",  # last-seen bridge accounts, for the UI
    "simplefin_last_sync": "",
    # Mercury read-only feed; mapping and sync state are managed by its routes.
    "mercury_api_token": "",
    "mercury_webhook_secret": "",
    # Class tracking (#243): "true" starts every entry form with no class
    # chosen and asks before a transaction is saved without one (it would
    # be reported under Uncategorized); "false" starts the picker on
    # Uncategorized and asks nothing.
    "class_warn_blank": "false",
    # QuickBooks Desktop's warnings (#481), off until a company turns them
    # on under Settings → Transaction Warnings: a transaction dated more
    # than N days before today or after it asks before it saves (the closing date keeps its own lock); a check,
    # register payment or bill payment whose number the bank account
    # already has is a 409 the person answers (keep the number or edit it);
    # saving changes to a transaction already saved asks first.
    "warn_date_past": "false",
    "warn_date_past_days": "90",
    "warn_date_future": "false",
    "warn_date_future_days": "30",
    "warn_duplicate_check_number": "false",
    "warn_edit_transaction": "false",
    # Sidebar items a company doesn't use, hidden from the menu (#334): a
    # JSON list of nav-link pages ("payroll", "hr-onboarding", …) and nav
    # sections ("section:Payroll & HR"). The pages stay reachable by
    # address. Empty = everything shows.
    "hidden_menu": "[]",
    # Direct deposit (discussion #211): what the bank needs at the top of
    # an ACH file, saved once so a processed pay run downloads its file
    # from the page. The bank's routing number is the file's destination
    # and the originating DFI; the two 10-character identifiers are the
    # bank's to assign (usually "1" + EIN) and fall back to the EIN; the
    # company account funds the credits and is encrypted at rest.
    "ach_bank_routing": "",
    "ach_bank_name": "",
    "ach_immediate_origin": "",
    "ach_company_id": "",
    "ach_company_account": "",
    # Phase 10: Late Fee Automation
    "late_fee_enabled": "false",
    "late_fee_rate": "1.5",
    "late_fee_grace_days": "15",
}
