"""Unit tests for the support diagnostics service: the redactor (a pure
function) and the subject / body builders the in-app form and the desktop
launcher share."""

from urllib.parse import quote

from app.services import support_diagnostics as sd

NAMES = ["Acme Supply Co", "Jane Doe", "Bob", "My Company"]


def _encoded(body):
    return len(quote(body, safe=""))


# ---------------------------------------------------------------------------
# Redaction — the simple shapes
# ---------------------------------------------------------------------------


def test_emails_and_tax_ids_become_tokens():
    out = sd.redact("Paid jane@acme.com ein 12-3456789 ssn 078-05-1120", NAMES)
    assert "jane@acme.com" not in out and "[email]" in out
    assert "12-3456789" not in out and out.count("[tax-id]") == 2
    assert "078-05-1120" not in out


def test_street_addresses():
    out = sd.redact("rang from 123 Main Street about it", [])
    assert "123 Main Street" not in out and "[address]" in out


def test_timestamps_levels_and_stack_traces_stay_readable():
    log = (
        "2026-10-25 09:15:01 INFO uvicorn running\n"
        "Traceback (most recent call last):\n"
        '  File "/app/main.py", line 42, in handler\n'
        "ValueError: bad input"
    )
    out = sd.redact(log, NAMES)
    assert "2026-10-25 09:15:01 INFO uvicorn running" in out
    assert "Traceback (most recent call last):" in out
    assert "line 42, in handler" in out
    assert "ValueError: bad input" in out


# ---------------------------------------------------------------------------
# Redaction — names (item 1 and item 8)
# ---------------------------------------------------------------------------


def test_names_with_punctuation_and_odd_whitespace_are_caught():
    names = [
        "Acme Supply Co.",
        "Smith & Sons, Inc.",
        "#1 Fence Co",
        "Jane  Doe",  # doubled interior space in the DB value
        "My Company",
    ]
    text = (
        "Order for Acme Supply Co. from Smith & Sons, Inc.; #1 Fence Co called, "
        "and Jane Doe at My Company signed."
    )
    out = sd.redact(text, names)
    for leaked in [
        "Acme Supply Co",
        "Smith & Sons",
        "#1 Fence Co",
        "Jane Doe",
        "My Company",
    ]:
        assert leaked not in out, leaked
    assert out.count("[name]") == 5


def test_names_match_case_insensitively_when_multi_word():
    out = sd.redact("ACME SUPPLY CO shipped", ["Acme Supply Co"])
    assert "ACME SUPPLY CO" not in out and "[name]" in out


def test_short_common_single_word_names_do_not_blank_the_log():
    log = 'app.main started\n  File "/app/main.py", line 42'
    # "Log"/"App" are under four chars (skipped); "Main" is case-sensitive, so
    # lowercase "main" in the log is left alone.
    for name in (["Log"], ["App"], ["Main"]):
        assert sd.redact(log, name) == log, name


def test_single_word_names_are_case_sensitive():
    out = sd.redact("Summit paid; the summit was steep", ["Summit"])
    assert "[name] paid" in out
    assert "the summit was steep" in out  # the common word survives


# ---------------------------------------------------------------------------
# Redaction — amounts (item 6)
# ---------------------------------------------------------------------------


def test_amounts_dollar_bare_and_accounting_negative():
    out = sd.redact("fees $1,234.56 and 1234.56 and 1,234.56 and (1,234.56) today", [])
    assert "$1,234.56" not in out
    assert "1234.56" not in out
    assert "1,234.56" not in out
    assert "(1,234.56)" not in out
    assert out.count("[amount]") == 4


def test_version_strings_are_not_amounts():
    out = sd.redact("app 3.45.0 on sqlite 3.45.000000 ready", [])
    assert "3.45.0" in out and "3.45.000000" in out and "[amount]" not in out


# ---------------------------------------------------------------------------
# Redaction — account / routing numbers are contextual (item 7)
# ---------------------------------------------------------------------------


def test_bare_digit_runs_without_context_are_left_alone():
    line = "pid 123456 port 8000 took 1234 ms at 1761379200"
    assert sd.redact(line, []) == line


def test_account_and_routing_numbers_redacted_with_context():
    out = sd.redact("routing 021000021 acct 1234567890", [])
    assert "021000021" not in out and "[routing]" in out
    assert "1234567890" not in out and "[account]" in out


def test_long_digit_runs_are_redacted_anywhere():
    out = sd.redact("value 1234567890123456 end", [])  # 16 digits, no keyword
    assert "1234567890123456" not in out and "[account]" in out


# ---------------------------------------------------------------------------
# Redaction — phones (item 10)
# ---------------------------------------------------------------------------


def test_phone_formats_including_parens_and_plus_one():
    out = sd.redact("call (407) 555-0123 or +1 407 555 0123 now", [])
    assert "(407) 555-0123" not in out and "([phone]" not in out
    assert "+1 407 555 0123" not in out and "+[phone]" not in out
    assert out.count("[phone]") == 2


# ---------------------------------------------------------------------------
# Subject / body builders
# ---------------------------------------------------------------------------


def test_subject_shape_and_short_title():
    subj = sd.build_subject("2.29.1", "macOS", "native", "The invoice PDF is blank")
    assert subj == "[SBP] v2.29.1 · macOS native · The invoice PDF is blank"


def test_short_title_trims_to_sixty_chars_on_one_line():
    title = sd.short_title("line one\nline two " + "x" * 200)
    assert "\n" not in title and len(title) <= 60


def test_short_title_falls_back_when_empty():
    assert sd.short_title("   ") == "support request"


def test_body_includes_sections_and_stays_within_the_encoded_budget():
    env = "Version: v2.29.1\nPlatform: macOS"
    tail = "a line of log\n" * 4000  # ~40k, far over budget
    body = sd.build_body("it broke", "it to work", "me@co.com", env, tail)
    assert "What happened:\nit broke" in body
    assert "What you expected:\nit to work" in body
    assert "Reply to: me@co.com" in body
    assert "--- Environment ---" in body and env in body
    assert _encoded(body) <= sd.MAX_ENCODED_BODY
    assert "use Copy diagnostics" in body


def test_body_drops_the_tail_when_the_user_text_alone_fills_the_budget():
    # what_happened over the budget on its own: the user's text is kept whole,
    # the 40k log is dropped rather than shipped.
    long_text = "x" * 1650
    tail = "SECRETLOGLINE\n" * 4000
    body = sd.build_body(long_text, "y", "", "env", tail)
    assert long_text in body
    assert "SECRETLOGLINE" not in body
    assert len(body) < 3000


def test_body_keeps_a_short_log_whole():
    body = sd.build_body("x", "y", "", "env", "three lines\nof log\nhere")
    assert body.endswith("three lines\nof log\nhere")
    assert "use Copy diagnostics" not in body
    assert _encoded(body) <= sd.MAX_ENCODED_BODY


def test_body_says_so_when_the_log_is_missing():
    body = sd.build_body("x", "y", "", "env", "", log_available=False)
    assert body.endswith("(log file not found)")


def test_body_carries_a_custom_missing_log_note():
    body = sd.build_body(
        "x", "y", "", "env", "", log_available=False, log_note="console only"
    )
    assert body.endswith("console only")


def test_empty_tail_is_handled():
    body = sd.build_body("x", "y", "", "env", "")
    assert "(log is empty)" in body and _encoded(body) <= sd.MAX_ENCODED_BODY


# ---------------------------------------------------------------------------
# Environment / deployment detection
# ---------------------------------------------------------------------------


def test_deployment_detection_by_flags(monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DESKTOP", "1")
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE", raising=False)
    assert sd.detect_deployment() == "native" and sd.is_native()

    monkeypatch.setenv("SLOWBOOKS_SERVER_MODE", "1")
    assert sd.detect_deployment() == "Server Edition" and not sd.is_native()

    monkeypatch.delenv("SLOWBOOKS_DESKTOP", raising=False)
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE", raising=False)
    assert sd.detect_deployment() == "self-managed"


def test_environment_block_includes_browser_when_given():
    block = sd.environment_block("2.29.1", None, "macOS", "3.13.0", "native", "WK/1")
    assert "Browser: WK/1" in block


def test_read_log_tail_missing_file_is_not_an_error(tmp_path):
    text, available = sd.read_log_tail(tmp_path / "nope.log")
    assert text == "" and available is False


def test_read_log_tail_returns_last_lines(tmp_path):
    p = tmp_path / "launcher.log"
    p.write_text("".join(f"line {i}\n" for i in range(100)), encoding="utf-8")
    text, available = sd.read_log_tail(p, lines=5)
    assert available is True
    assert text.splitlines() == ["line 95", "line 96", "line 97", "line 98", "line 99"]
