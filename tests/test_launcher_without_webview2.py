"""Issue #149: the portable Windows zip ships no WebView2 bootstrapper, so on
a machine without the runtime the launcher is the only thing standing between
the user and pywebview's silent fall-back to the IE control.

The check itself existed. What was wrong was what it SAID: the installed
build was told to run `python desktop_launcher.py --no-window`, an
instruction it cannot follow — no Python, no such file. Sixth appearance of
the class this month. These tests pin the detection with a fake registry,
pin that each message names only things its reader can do, and execute the
offered path (serve + browser + stop) rather than assert it exists.
"""

import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

import desktop_launcher as dl

GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


class _FakeWinreg:
    """Just enough of winreg for _webview2_installed: OpenKey + QueryValueEx."""

    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, values):
        self.values = values  # {(root, path): pv}

    def OpenKey(self, root, path):
        if (root, path) not in self.values:
            raise OSError(2, "not found")
        key = (root, path)

        class _Key:
            def __enter__(self_inner):
                return key

            def __exit__(self_inner, *a):
                return False

        return _Key()

    def QueryValueEx(self, key, name):
        assert name == "pv"
        return self.values[key], 1


def _with_registry(monkeypatch, values):
    monkeypatch.setattr(dl.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg(values))


def test_detects_the_evergreen_runtime_under_any_of_the_three_keys(monkeypatch):
    hklm64 = ("HKLM", rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{GUID}")
    hklm = ("HKLM", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{GUID}")
    hkcu = ("HKCU", rf"Software\Microsoft\EdgeUpdate\Clients\{GUID}")
    for key in (hklm64, hklm, hkcu):
        _with_registry(monkeypatch, {key: "120.0.2210.91"})
        assert dl._webview2_installed() is True, key


def test_absent_or_placeholder_version_means_not_installed(monkeypatch):
    _with_registry(monkeypatch, {})
    assert dl._webview2_installed() is False
    # Microsoft's own docs: an uninstalled runtime can leave pv = 0.0.0.0
    _with_registry(
        monkeypatch,
        {("HKLM", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{GUID}"): "0.0.0.0"},
    )
    assert dl._webview2_installed() is False


def test_not_windows_is_always_installed(monkeypatch):
    monkeypatch.setattr(dl.sys, "platform", "darwin")
    assert dl._webview2_installed() is True


# ---------------------------------------------------------------------------
# The message: each reader is told only what they can do.
# ---------------------------------------------------------------------------


def test_installed_build_is_not_told_to_run_python(monkeypatch):
    monkeypatch.setattr(dl, "FROZEN", True)
    msg = dl._no_webview2_message()
    assert "python" not in msg.lower()
    assert "desktop_launcher" not in msg
    assert dl.WEBVIEW2_URL in msg
    assert dl.WINDOWS_INSTALLER in msg


def test_the_installer_the_message_names_is_the_one_the_build_produces(monkeypatch):
    """Read the other half out of the packaging source, not from memory."""
    iss = (Path(dl.ROOT) / "packaging" / "windows" / "SlowBooksPro.iss").read_text(
        encoding="utf-8"
    )
    m = re.search(r"^OutputBaseFilename=(\S+)", iss, re.M)
    assert m, "OutputBaseFilename not found in the Inno Setup script"
    assert dl.WINDOWS_INSTALLER == m.group(1) + ".exe"


def test_checkout_message_names_a_flag_the_launcher_accepts(monkeypatch):
    """The checkout IS told the Python command — and the flag it names must
    be one --help lists, not one somebody remembered."""
    monkeypatch.setattr(dl, "FROZEN", False)
    msg = dl._no_webview2_message()
    m = re.search(r"python desktop_launcher\.py (--\S+)", msg)
    assert m, msg
    out = subprocess.run(
        [sys.executable, str(Path(dl.ROOT) / "desktop_launcher.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**__import__("os").environ, "SLOWBOOKS_DATA_DIR": str(Path(dl.ROOT))},
    )
    assert m.group(1) in out.stdout, out.stdout[-400:]


# ---------------------------------------------------------------------------
# The offered path is executed, not asserted to exist.
# ---------------------------------------------------------------------------


class _Proc:
    def __init__(self):
        self.stopped = False

    def poll(self):
        return None


@pytest.fixture
def browser_run(monkeypatch, tmp_path):
    """Stub the server start and the two native boxes; record what ran."""
    calls = {"launch": [], "opened": [], "held": [], "stopped": []}
    proc = _Proc()
    from app.services import company_service

    monkeypatch.setattr(company_service, "get_last_opened", lambda: "books.db")
    monkeypatch.setattr(
        dl,
        "launch_company",
        lambda filename, port, output=None, bind_host="127.0.0.1", persist=True: (
            calls["launch"].append((filename, port, bind_host, persist)),
            proc,
        )[1],
    )
    monkeypatch.setattr(dl, "stop_server", lambda p: calls["stopped"].append(p))
    monkeypatch.setattr(
        dl, "_hold_until_dismissed", lambda m, title="": calls["held"].append(m)
    )
    monkeypatch.setitem(
        sys.modules,
        "webbrowser",
        types.SimpleNamespace(open=lambda url: calls["opened"].append(url)),
    )
    return calls, proc


def test_yes_serves_on_loopback_opens_the_browser_and_stops_on_dismiss(
    monkeypatch, browser_run
):
    calls, proc = browser_run
    _with_registry(monkeypatch, {})  # no runtime
    monkeypatch.setattr(dl, "_ask_yes_no", lambda msg, title="": True)
    monkeypatch.setattr(
        dl, "_show_error_box", lambda m: pytest.fail("no error box on Yes")
    )
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace())

    assert dl.run_window(3001) == 0
    assert calls["launch"] == [("books.db", 3001, "127.0.0.1", False)]
    assert calls["opened"] == ["http://127.0.0.1:3001"]
    assert calls["held"] and "http://127.0.0.1:3001" in calls["held"][0]
    assert calls["stopped"] == [proc]  # the box closing stops the server


def test_no_shows_the_message_and_starts_nothing(monkeypatch, browser_run):
    calls, _ = browser_run
    _with_registry(monkeypatch, {})
    monkeypatch.setattr(dl, "_ask_yes_no", lambda msg, title="": False)
    shown = []
    monkeypatch.setattr(dl, "_show_error_box", lambda m: shown.append(m))
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace())

    assert dl.run_window(3001) == 1
    assert calls["launch"] == [] and calls["opened"] == []
    assert shown and dl.WEBVIEW2_URL in shown[0]


def test_the_question_only_gets_asked_on_windows(monkeypatch):
    monkeypatch.setattr(dl.sys, "platform", "linux")
    assert dl._ask_yes_no("anything") is False


# ---------------------------------------------------------------------------
# The fatal-error dialog's "Email support" mailto (contact-support feature).
# The launcher process never sets SLOWBOOKS_DESKTOP / SLOWBOOKS_SERVER_MODE
# (those go only into the server child's env), so the deployment must be
# passed in — otherwise a crashed native install would mail "Deployment:
# self-managed".
# ---------------------------------------------------------------------------


def test_support_mailto_labels_a_native_crash_native(monkeypatch):
    monkeypatch.delenv("SLOWBOOKS_DESKTOP", raising=False)
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE", raising=False)
    mailto = dl._support_mailto(None, "native")
    assert mailto and mailto.startswith("mailto:support@slowbookspro.com")
    assert "native" in mailto and "self-managed" not in mailto


def test_support_mailto_labels_a_serve_lan_crash_server_edition(monkeypatch):
    monkeypatch.delenv("SLOWBOOKS_DESKTOP", raising=False)
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE", raising=False)
    mailto = dl._support_mailto(None, "Server Edition")
    assert mailto and "Server%20Edition" in mailto and "self-managed" not in mailto
