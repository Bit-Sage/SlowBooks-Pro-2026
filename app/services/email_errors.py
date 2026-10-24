# ============================================================================
# Email send failures, in words the operator can act on (#447).
#
# send_email() stores the raw SMTP error on the EmailLog row. That text is
# accurate but unfriendly — a Gmail rejection reads
# "(535, b'5.7.8 Username and Password not accepted. For more information...')".
# One function turns a stored error into a plain reason and, for the handful
# of mistakes new users actually hit, a short hint. Kept small on purpose:
# three cases, matched on the error text (and the host, so Gmail is named).
# ============================================================================


def _clean_reason(error_text: str) -> str:
    """The SMTP error with Python's tuple/bytes wrapping stripped off.

    `str(SMTPAuthenticationError)` is "(535, b'5.7.8 ...')"; the bit a person
    reads is "535 5.7.8 ...". Leave anything we don't recognise untouched."""
    text = (error_text or "").strip()
    if not text:
        return ""
    if text.startswith("(") and text.endswith(")") and "," in text:
        code, _, rest = text[1:-1].partition(",")
        rest = rest.strip()
        # b'...' or b"..." -> the inner text
        if (rest.startswith("b'") and rest.endswith("'")) or (
            rest.startswith('b"') and rest.endswith('"')
        ):
            rest = rest[2:-1]
        rest = rest.replace("\\n", " ").replace("\\r", " ").strip()
        code = code.strip()
        if code.isdigit() and rest:
            return f"{code} {rest}"
    return text


def explain_send_failure(error_text: str, host: str = "") -> tuple[str, str]:
    """(reason, hint) for a failed send.

    `reason` is the SMTP error cleaned up for reading; `hint` is one sentence
    of advice for a common case, or "" when we have nothing useful to add.
    The mapping is deliberately tiny: username/password (535/534), a server
    we couldn't reach (refused/timed out/unknown host), and Gmail or Google
    Workspace, which reject an ordinary password and need an app password."""
    reason = _clean_reason(error_text)
    text = (error_text or "").lower()
    host = (host or "").lower()
    is_google = any(s in host for s in ("gmail", "google")) or any(
        s in text for s in ("gmail", "google")
    )

    hint = ""
    if "535" in text or "534" in text:
        if is_google:
            hint = (
                "Google will not accept a normal password here. Turn on "
                "2-Step Verification on the account and use a 16-character "
                "app password as the SMTP password."
            )
        else:
            hint = "The server rejected the username or password — check both."
    elif any(
        s in text
        for s in (
            "refused",
            "timed out",
            "timeout",
            "getaddrinfo",
            "name or service not known",
            "no route to host",
            "network is unreachable",
        )
    ):
        hint = (
            "The server could not be reached — check the host, port, and the "
            "Use TLS setting."
        )

    return reason, hint


# The sentence a failed send is shown with, wherever it is shown. The Recent
# sends list reads it from the API (GET /api/email-log's `failure`) rather than
# keeping a second copy of the mapping in settings.js, so the list and the
# test-email failure can never word the same error two ways.
_FALLBACK_SENTENCE = "The email could not be sent."
_NOT_CONFIGURED_SENTENCE = (
    "SMTP is not configured — fill in the fields under Settings → Email (SMTP)."
)


def failure_sentence(error_text: str, host: str = "") -> str:
    """One sentence a person can act on for a stored send error: the hint
    for a common mistake, else "not configured", else a plain fallback."""
    _, hint = explain_send_failure(error_text, host)
    if hint:
        return hint
    if "not configured" in (error_text or "").lower():
        return _NOT_CONFIGURED_SENTENCE
    return _FALLBACK_SENTENCE


def where_to_see_why(admin: bool) -> str:
    """Where a failed send's reason can be read. Recent sends is an
    administrator's section (recipients and raw SMTP errors), so anyone else
    is told who can read it rather than sent to a page they cannot open."""
    if admin:
        return "The reason is under Settings → Email (SMTP) → Recent sends."
    return "An administrator can see why under Settings → Recent sends."
