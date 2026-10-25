# ============================================================================
# Support diagnostics — the environment block, the redacted log tail, and the
# subject/body an in-app "Contact support" email carries.
#
# Deliberately DEPENDENCY-FREE (stdlib only). The desktop launcher imports
# this module from its fatal-error dialog, where the app's third-party
# packages may not be importable, and the redaction has to be reviewable on
# its own. Nothing here touches the network — the no-telemetry promise holds;
# the browser opens the user's own mail client with the text we built.
# ============================================================================

import os
import platform
import re
import sys

SUPPORT_EMAIL = "support@slowbookspro.com"

# The mailto body is kept under this many characters: some mail clients
# silently truncate a long mailto body, so the log tail is trimmed to fit and
# the form tells the user to use "Copy diagnostics" for the whole block.
MAX_BODY_CHARS = 1800


# ---------------------------------------------------------------------------
# Redaction — a pure function. Emails, names (passed in from the DB), dollar
# amounts, account numbers, street addresses, phone numbers, tax IDs and bank
# routing numbers become bracketed tokens. Timestamps, log levels and stack
# traces stay readable: the patterns below only match the sensitive shapes.
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.IGNORECASE)
# Social Security number (ddd-dd-dddd) and EIN (dd-ddddddd): both are tax IDs.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
# North American phone: optional +1, area code (parens optional), separators
# required so a bare run of digits is left to the account/routing rules.
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s])?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
# Dollar amounts, with or without thousands separators and cents.
_AMOUNT_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
# Bank routing numbers are exactly nine digits; handled before the broader
# account-number run so a routing number reads as one.
_ROUTING_RE = re.compile(r"\b\d{9}\b")
# Account / card numbers: a run of 6-17 digits (nine-digit runs are already
# gone). Catches most bank and card numbers without eating ports (<=5 digits),
# line numbers or ISO timestamp fields (which carry separators).
_ACCOUNT_RE = re.compile(r"\b\d{6,17}\b")
# Street address: a number then a few words then a street-type word.
_STREET_WORDS = (
    r"Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|"
    r"Way|Circle|Cir|Place|Pl|Terrace|Ter|Highway|Hwy|Parkway|Pkwy|Square|Sq"
)
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9.]+\s){1,4}(?:" + _STREET_WORDS + r")\b\.?",
    re.IGNORECASE,
)


def redact(text, names=None):
    """Replace sensitive values in ``text`` with bracketed tokens.

    ``names`` is the list of personal and company names from the company file
    (customers, vendors, employees, the company itself); each is matched as a
    whole word, case-insensitively. The function is pure — it reads nothing
    and calls nothing external.
    """
    if not text:
        return text or ""
    out = text
    out = _EMAIL_RE.sub("[email]", out)
    out = _SSN_RE.sub("[tax-id]", out)
    out = _EIN_RE.sub("[tax-id]", out)
    out = _PHONE_RE.sub("[phone]", out)
    out = _ADDRESS_RE.sub("[address]", out)
    out = _AMOUNT_RE.sub("[amount]", out)
    out = _ROUTING_RE.sub("[routing]", out)
    out = _ACCOUNT_RE.sub("[account]", out)
    # Names last, so a name that looks like nothing else is still caught.
    # Longest first: "Acme Supply Co" before "Acme", so the whole name goes.
    for name in sorted(
        {n.strip() for n in (names or []) if n and len(n.strip()) >= 3},
        key=len,
        reverse=True,
    ):
        out = re.sub(
            r"\b" + re.escape(name) + r"\b", "[name]", out, flags=re.IGNORECASE
        )
    return out


# ---------------------------------------------------------------------------
# What the app is running. Reused from the same flags system.py reads, so the
# support block and the update check never disagree about the install kind.
# ---------------------------------------------------------------------------


def detect_platform():
    """macOS / Windows / Linux — the human name, for the subject line."""
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform == "win32":
        return "Windows"
    if sys.platform.startswith("linux"):
        return "Linux"
    return sys.platform or "unknown"


def detect_deployment():
    """native / Server Edition / Docker.

    The launcher sets SLOWBOOKS_DESKTOP=1 for every server it starts and
    SLOWBOOKS_SERVER_MODE=1 only when it serves beyond loopback (--serve-lan).
    A browser install with neither flag is a Docker / self-managed server.
    """
    desktop = os.environ.get("SLOWBOOKS_DESKTOP") == "1"
    server_mode = os.environ.get("SLOWBOOKS_SERVER_MODE") == "1"
    if desktop and server_mode:
        return "Server Edition"
    if desktop:
        return "native"
    return "Docker"


def is_native():
    """The native desktop window — the only place Reveal log file makes sense
    (a local folder to open; a browser/Server/Docker client has none)."""
    return detect_deployment() == "native"


def app_commit():
    """Best-effort source commit, or None. Baked builds can set
    SLOWBOOKS_COMMIT; a source checkout has none unless the env says so. Never
    shells out — a frozen app has no git, and this must not fail."""
    commit = os.environ.get("SLOWBOOKS_COMMIT", "").strip()
    return commit or None


def python_version():
    return platform.python_version()


def environment_block(version, commit, platform_name, python, deployment, browser=None):
    """The fixed facts a support email needs, one per line. Carries no PII, so
    it is placed as-is; the log tail below it is what gets redacted."""
    lines = [
        f"Version: v{version}" + (f" ({commit})" if commit else ""),
        f"Platform: {platform_name}",
        f"Python: {python}",
        f"Deployment: {deployment}",
    ]
    if browser:
        lines.append(f"Browser: {browser}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The log tail. Never raises if the file is missing or unreadable — a support
# email is still useful without it, and the form says the log was not found.
# ---------------------------------------------------------------------------


def read_log_tail(path, lines=50):
    """Return ``(text, available)`` for the last ``lines`` of ``path``.

    ``available`` is False (and ``text`` empty) when the file is missing or
    cannot be read. The caller redacts ``text`` before placing it.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-lines:]
        return "".join(tail).rstrip("\n"), True
    except (OSError, ValueError):
        return "", False


# ---------------------------------------------------------------------------
# The email. The JS form builds its own mailto (it has the user's typed text
# live), but the subject/body shape lives here so the desktop launcher's
# error dialog sends exactly the same thing.
# ---------------------------------------------------------------------------


def short_title(what_happened, limit=60):
    """The first ~``limit`` characters of what happened, one line, for the
    subject's tail. Empty text gives a neutral placeholder."""
    text = " ".join((what_happened or "").split())
    if not text:
        return "support request"
    return text[:limit].rstrip()


def build_subject(version, platform_name, deployment, what_happened):
    """[SBP] v<version> · <platform> <deployment> · <short title>"""
    return (
        f"[SBP] v{version} · {platform_name} {deployment} "
        f"· {short_title(what_happened)}"
    )


def build_body(
    what_happened,
    what_expected,
    reply_to,
    env_block,
    log_tail,
    log_available=True,
    max_chars=MAX_BODY_CHARS,
):
    """Assemble the email body, trimming the log tail so the whole body stays
    under ``max_chars`` (long mailto bodies get truncated by some clients)."""
    header_parts = [
        "What happened:",
        (what_happened or "").strip() or "(not described)",
        "",
        "What you expected:",
        (what_expected or "").strip() or "(not described)",
        "",
    ]
    if reply_to:
        header_parts += [f"Reply to: {reply_to}", ""]
    header_parts += [
        "--- Environment ---",
        env_block,
        "",
        "--- Recent log ---",
    ]
    header = "\n".join(header_parts) + "\n"
    if not log_available:
        return header + "(log file not found)"
    footer = "\n(log trimmed — use Copy diagnostics for the full block)"
    room = max_chars - len(header)
    tail = log_tail or "(log is empty)"
    if room <= 0:
        return header.rstrip()
    if len(tail) <= room:
        return header + tail
    # Keep the END of the log (the newest lines, where the error is).
    return header + footer + "\n" + tail[-(room - len(footer) - 1) :]
