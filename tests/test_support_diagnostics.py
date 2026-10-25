"""Unit tests for the support diagnostics service: the redactor (a pure
function) and the subject / body builders the in-app form and the desktop
launcher share."""

from app.services import support_diagnostics as sd

NAMES = ["Acme Supply Co", "Jane Doe", "Bob", "My Company"]


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_emails_amounts_and_tax_ids_become_tokens():
    out = sd.redact(
        "Paid jane@acme.com $1,234.56 ein 12-3456789 ssn 078-05-1120", NAMES
    )
    assert "jane@acme.com" not in out and "[email]" in out
    assert "$1,234.56" not in out and "[amount]" in out
    assert "12-3456789" not in out and out.count("[tax-id]") == 2
    assert "078-05-1120" not in out


def test_phones_routing_and_account_numbers():
    out = sd.redact("call (305) 555-1234, routing 021000021, acct 1234567890123", [])
    assert "(305) 555-1234" not in out and "[phone]" in out
    assert "021000021" not in out and "[routing]" in out
    assert "1234567890123" not in out and "[account]" in out


def test_street_addresses_and_names():
    out = sd.redact("Jane Doe at 123 Main Street rang about Acme Supply Co", NAMES)
    assert "123 Main Street" not in out and "[address]" in out
    assert "Jane Doe" not in out and "Acme Supply Co" not in out
    assert out.count("[name]") == 2


def test_names_match_case_insensitively_as_whole_words():
    out = sd.redact("ACME SUPPLY CO shipped; bobby did not", NAMES)
    assert "ACME SUPPLY CO" not in out
    # "Bob" is a whole word, so "bobby" is left alone.
    assert "bobby" in out


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


def test_redact_is_safe_on_empty_and_short_names():
    assert sd.redact("", NAMES) == ""
    # a one- or two-character name is skipped, so it can't blank the log out
    assert sd.redact("a or an elephant", ["a", "an"]) == "a or an elephant"


# ---------------------------------------------------------------------------
# Subject / body builders
# ---------------------------------------------------------------------------


def test_subject_shape_and_short_title():
    subj = sd.build_subject("2.29.1", "macOS", "native", "The invoice PDF is blank")
    assert subj == "[SBP] v2.29.1 · macOS native · The invoice PDF is blank"


def test_short_title_trims_to_sixty_chars_on_one_line():
    long = "x" * 200
    title = sd.short_title("line one\nline two " + long)
    assert "\n" not in title and len(title) <= 60


def test_short_title_falls_back_when_empty():
    assert sd.short_title("   ") == "support request"


def test_body_includes_sections_and_trims_a_long_log():
    env = "Version: v2.29.1\nPlatform: macOS"
    tail = "L\n" * 2000  # far over the mailto budget
    body = sd.build_body("it broke", "it to work", "me@co.com", env, tail)
    assert "What happened:\nit broke" in body
    assert "What you expected:\nit to work" in body
    assert "Reply to: me@co.com" in body
    assert "--- Environment ---" in body and env in body
    assert "--- Recent log ---" in body
    assert len(body) <= sd.MAX_BODY_CHARS
    assert "use Copy diagnostics" in body


def test_body_says_so_when_the_log_is_missing():
    body = sd.build_body("x", "y", "", "env", "", log_available=False)
    assert body.endswith("(log file not found)")


def test_body_keeps_a_short_log_whole():
    body = sd.build_body("x", "y", "", "env", "three lines\nof log\nhere")
    assert body.endswith("three lines\nof log\nhere")
    assert "use Copy diagnostics" not in body


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
    assert sd.detect_deployment() == "Docker"


def test_read_log_tail_missing_file_is_not_an_error(tmp_path):
    text, available = sd.read_log_tail(tmp_path / "nope.log")
    assert text == "" and available is False


def test_read_log_tail_returns_last_lines(tmp_path):
    p = tmp_path / "launcher.log"
    p.write_text("".join(f"line {i}\n" for i in range(100)), encoding="utf-8")
    text, available = sd.read_log_tail(p, lines=5)
    assert available is True
    assert text.splitlines() == ["line 95", "line 96", "line 97", "line 98", "line 99"]
