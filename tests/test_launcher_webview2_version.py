"""A WebView2 runtime too old for the window says so, before the window opens.

A support email on 2.29.2 (Windows 10): "Full white screen on startup, never
changing and no menus or sidebars to click on ... I have tried reinstalling
as well." Nine minutes later: "my web2 runtime was out of date. Updated it
and now it opens correctly."

The launcher only asked whether a runtime was there. pywebview takes any
runtime from 86 up, and its WinForms control then asks the runtime for
ICoreWebView2Environment10 (pywebview always sets IsInPrivateModeEnabled),
which only 101.0.1210.39 and later have: on anything older the control fails
to start and the window stays white. The page's own JavaScript needs 93 on
top of that. These pin the check with a fake registry (what counts as
missing, old and new enough, which key counts), the message each reader
gets, and that an old runtime never gets the window.
"""

import sys
import types

import pytest

import desktop_launcher as dl

RUNTIME = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
BETA = "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}"
CANARY = "{65C35B14-6C1D-4122-AC46-7148CC9D6497}"


def machine(guid):
    """A per-machine install on 64-bit Windows."""
    return ("HKLM", rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}")


def machine32(guid):
    """A per-machine install on 32-bit Windows."""
    return ("HKLM", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}")


def user(guid):
    """A per-user install."""
    return ("HKCU", rf"Software\Microsoft\EdgeUpdate\Clients\{guid}")


class _FakeWinreg:
    """Just enough of winreg for the launcher: OpenKey + QueryValueEx."""

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


def _registry(monkeypatch, values):
    monkeypatch.setattr(dl.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg(values))


# ---------------------------------------------------------------------------
# What counts as missing, old and new enough
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values, expected",
    [
        ({}, ("missing", None)),
        # Microsoft's docs: an uninstalled runtime can leave pv = 0.0.0.0
        ({machine(RUNTIME): "0.0.0.0"}, ("missing", None)),
        ({machine(RUNTIME): ""}, ("missing", None)),
        # pywebview's own floor: it uses this runtime, and the window is white
        ({machine(RUNTIME): "86.0.622.38"}, ("old", "86.0.622.38")),
        # crypto.randomUUID is there, Object.hasOwn is not
        ({machine(RUNTIME): "92.0.902.84"}, ("old", "92.0.902.84")),
        # the page's JavaScript would run, but the window still does not open
        ({machine(RUNTIME): "93.0.0.0"}, ("old", "93.0.0.0")),
        ({machine(RUNTIME): "101.0.1210.38"}, ("old", "101.0.1210.38")),
        ({machine(RUNTIME): "101.0.1210.39"}, ("ok", "101.0.1210.39")),
        ({machine(RUNTIME): "130.0.2849.80"}, ("ok", "130.0.2849.80")),
        ({machine32(RUNTIME): "130.0.2849.80"}, ("ok", "130.0.2849.80")),
        ({user(RUNTIME): "130.0.2849.80"}, ("ok", "130.0.2849.80")),
    ],
)
def test_the_runtime_is_missing_old_or_new_enough(monkeypatch, values, expected):
    _registry(monkeypatch, values)
    assert dl._webview2_status() == expected


def test_versions_compare_as_numbers_not_text(monkeypatch):
    # "99" sorts after "130" as text, and 101.0.1210.100 after .39 only as a number
    _registry(monkeypatch, {machine(RUNTIME): "99.0.1150.55"})
    assert dl._webview2_status() == ("old", "99.0.1150.55")
    _registry(monkeypatch, {machine(RUNTIME): "101.0.1210.100"})
    assert dl._webview2_status() == ("ok", "101.0.1210.100")


def test_the_highest_version_among_the_runtime_keys_counts(monkeypatch):
    _registry(
        monkeypatch,
        {machine(RUNTIME): "92.0.902.84", user(RUNTIME): "130.0.2849.80"},
    )
    assert dl._webview2_status() == ("ok", "130.0.2849.80")
    _registry(monkeypatch, {machine(RUNTIME): "0.0.0.0", user(RUNTIME): "92.0.902.84"})
    assert dl._webview2_status() == ("old", "92.0.902.84")


def test_an_edge_preview_channel_counts_when_the_runtime_is_absent(monkeypatch):
    """pywebview reads Beta, Dev and Canary too, and so does the loader."""
    _registry(monkeypatch, {user(BETA): "131.0.2903.9"})
    assert dl._webview2_status() == ("ok", "131.0.2903.9")
    assert dl._webview2_installed() is True
    _registry(monkeypatch, {user(CANARY): "92.0.902.0"})
    assert dl._webview2_status() == ("old", "92.0.902.0")


def test_the_runtime_is_used_before_a_preview_channel(monkeypatch):
    """The WebView2 loader takes the most stable channel installed, so a new
    Canary does not rescue an old runtime: the window would run on the
    runtime."""
    _registry(
        monkeypatch,
        {machine(RUNTIME): "92.0.902.84", user(CANARY): "140.0.3400.0"},
    )
    assert dl._webview2_status() == ("old", "92.0.902.84")


def test_off_windows_there_is_nothing_to_check(monkeypatch):
    monkeypatch.setattr(dl.sys, "platform", "darwin")
    assert dl._webview2_status() == ("ok", None)
    assert dl._webview2_version() is None
    assert dl._webview2_installed() is True


def test_the_minimum_is_one_dotted_version():
    parts = dl._version_parts(dl.MIN_WEBVIEW2)
    assert parts and len(parts) == 4, dl.MIN_WEBVIEW2


# ---------------------------------------------------------------------------
# The message: the version found, the version needed, and what to do
# ---------------------------------------------------------------------------

FIRST_SENTENCE = (
    "SlowBooks Pro needs a newer Microsoft Edge WebView2 runtime to show its "
    "window. This computer has version 92.0.902.84; SlowBooks needs {} or later."
)


def test_the_installed_build_is_told_to_update_or_rerun_the_installer(monkeypatch):
    monkeypatch.setattr(dl, "FROZEN", True)
    msg = dl._old_webview2_message("92.0.902.84")
    assert msg.startswith(FIRST_SENTENCE.format(dl.MIN_WEBVIEW2)), msg
    assert f"Update it from Microsoft: {dl.WEBVIEW2_URL}" in msg
    assert dl.WINDOWS_INSTALLER in msg and "again, which updates it" in msg
    # it has no Python and no such file (#149)
    assert "python" not in msg.lower() and "desktop_launcher" not in msg


def test_a_checkout_is_offered_the_same_no_window_line_as_a_missing_runtime(
    monkeypatch,
):
    monkeypatch.setattr(dl, "FROZEN", False)
    msg = dl._old_webview2_message("92.0.902.84")
    assert msg.startswith(FIRST_SENTENCE.format(dl.MIN_WEBVIEW2)), msg
    assert f"Update it from Microsoft: {dl.WEBVIEW2_URL}" in msg
    # the flag test_launcher_without_webview2 checks against --help
    line = "    python desktop_launcher.py --no-window"
    assert line in dl._no_webview2_message() and line in msg
    assert dl.WINDOWS_INSTALLER not in msg


# ---------------------------------------------------------------------------
# An old runtime never gets the window; a new enough one does
# ---------------------------------------------------------------------------


class _Proc:
    def poll(self):
        return None


class _FakeWebview:
    """pywebview, recording whether a window was made and started."""

    def __init__(self):
        self.settings = {}
        self.windows = []
        self.started = []

    def create_window(self, *args, **kwargs):
        self.windows.append((args, kwargs))
        return types.SimpleNamespace(load_html=lambda html: None)

    def start(self, *args, **kwargs):
        self.started.append(kwargs)


@pytest.fixture
def launch(monkeypatch, tmp_path):
    """Stub the server start, the native boxes and pywebview; record them."""
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    calls = {"launch": [], "opened": [], "held": [], "asked": [], "shown": []}
    proc = _Proc()
    from app.services import company_service

    monkeypatch.setattr(company_service, "get_last_opened", lambda: "books.db")
    monkeypatch.setattr(
        dl,
        "launch_company",
        lambda filename, port, output=None, bind_host="127.0.0.1", persist=True: (
            calls["launch"].append((filename, port, bind_host)),
            proc,
        )[1],
    )
    monkeypatch.setattr(dl, "stop_server", lambda p: None)
    monkeypatch.setattr(
        dl, "_hold_until_dismissed", lambda m, title="": calls["held"].append(m)
    )
    monkeypatch.setattr(dl, "_show_error_box", lambda m: calls["shown"].append(m))
    monkeypatch.setitem(
        sys.modules,
        "webbrowser",
        types.SimpleNamespace(open=lambda url: calls["opened"].append(url)),
    )
    fake = _FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    return calls, fake


def _answer(monkeypatch, calls, yes):
    def ask(message, title=""):
        calls["asked"].append(message)
        return yes

    monkeypatch.setattr(dl, "_ask_yes_no", ask)


def test_an_old_runtime_is_told_why_and_the_window_never_opens(
    monkeypatch, launch, capsys
):
    calls, webview = launch
    _registry(monkeypatch, {machine(RUNTIME): "92.0.902.84"})
    _answer(monkeypatch, calls, yes=False)

    assert dl.run_window(3001) == 1
    assert webview.windows == [] and webview.started == []
    assert calls["launch"] == [] and calls["opened"] == []
    # the missing runtime's dialog, with the old runtime's message in it
    (asked,) = calls["asked"]
    assert "92.0.902.84" in asked and dl.MIN_WEBVIEW2 in asked
    assert asked.endswith("Open SlowBooks Pro in your web browser instead?")
    (shown,) = calls["shown"]
    assert shown == dl._old_webview2_message("92.0.902.84")
    assert dl.WEBVIEW2_URL in shown
    # and launcher.log says what was found
    out = capsys.readouterr().out
    assert f"WebView2 runtime: 92.0.902.84 (needs {dl.MIN_WEBVIEW2} or later)" in out


def test_an_old_runtime_can_still_open_the_app_in_the_browser(monkeypatch, launch):
    calls, webview = launch
    _registry(monkeypatch, {machine(RUNTIME): "86.0.622.38"})
    _answer(monkeypatch, calls, yes=True)

    assert dl.run_window(3001) == 0
    assert webview.windows == []
    assert calls["launch"] == [("books.db", 3001, "127.0.0.1")]
    assert calls["opened"] == ["http://127.0.0.1:3001"]
    assert calls["shown"] == []


def test_a_new_enough_runtime_gets_the_window(monkeypatch, launch):
    calls, webview = launch
    _registry(monkeypatch, {machine(RUNTIME): "130.0.2849.80"})
    _answer(monkeypatch, calls, yes=False)

    assert dl.run_window(3001) == 0
    assert len(webview.windows) == 1 and len(webview.started) == 1
    assert calls["asked"] == [] and calls["shown"] == []
