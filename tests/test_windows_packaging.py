"""The Windows installer replaces the program file on a same-version
reinstall (issue #643).

Every build of one version is stamped x.y.z.0 (packaging/windows/
version_info.py pads the version to four numbers), so Inno Setup's version
comparison took the SlowBooksPro.exe already installed for the one it was
shipping and skipped it ("Same version. Skipping.") while replacing every
file around it. The ignoreversion flag on the bundle entry turns that
comparison off. These read the Inno Setup script as text, so they run on
every platform; no Windows is needed to notice the flag is gone.

The same goes for the WebView2 bootstrapper's entry (a support email on
2.29.2): it runs when the runtime is missing or older than the window needs,
and that minimum is the launcher's own.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "packaging" / "windows" / "SlowBooksPro.iss"

# The PyInstaller output: SlowBooksPro.exe beside its _internal\ tree.
BUNDLE_SOURCE = r"dist\SlowBooksPro\*"


def _section(text, name):
    """The entries of one [Section] of an Inno Setup script, comments and
    blank lines dropped."""
    m = re.search(
        rf"^\[{re.escape(name)}\][ \t]*$(.*?)(?=^\[\w+\][ \t]*$|\Z)",
        text,
        re.M | re.S,
    )
    assert m, f"no [{name}] section in {ISS.name}"
    return [
        line.strip()
        for line in m.group(1).splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ]


def _params(entry):
    """'Source: "a"; DestDir: "{app}"; Flags: x y' -> {"Source": "a", ...}."""
    return {
        key: value.strip().strip('"')
        for key, value in re.findall(r'(\w+):\s*("[^"]*"|[^;]*)', entry)
    }


def _bundle_entry():
    entries = [
        _params(line) for line in _section(ISS.read_text(encoding="utf-8"), "Files")
    ]
    found = [e for e in entries if e.get("Source") == BUNDLE_SOURCE]
    assert len(found) == 1, f"[Files] entries for {BUNDLE_SOURCE}: {found}"
    return found[0]


def test_the_bundle_entry_replaces_files_whatever_their_version():
    flags = _bundle_entry().get("Flags", "").split()
    assert "ignoreversion" in flags, (
        f"{BUNDLE_SOURCE} has no ignoreversion flag (got {flags}): a reinstall "
        "of the same version would skip SlowBooksPro.exe (#643)"
    )


def test_the_bundle_entry_still_lands_the_whole_tree_in_the_app_folder():
    entry = _bundle_entry()
    flags = entry.get("Flags", "").split()
    assert "recursesubdirs" in flags and "createallsubdirs" in flags, flags
    assert entry.get("DestDir") == "{app}", entry


# ---------------------------------------------------------------------------
# An old WebView2 runtime is updated, not only a missing one installed.
#
# A support email on 2.29.2: a white window, and "I have tried reinstalling
# as well". The bootstrapper's [Run] entry checked only that a runtime was
# there, so a reinstall over an old one left it old. The launcher stops at
# start below MIN_WEBVIEW2; the installer runs the bootstrapper below the
# same number, compared as numbers.
# ---------------------------------------------------------------------------

BOOTSTRAPPER = r"{tmp}\MicrosoftEdgeWebView2Setup.exe"


def _entries(text, name):
    """The entries of a section, each continued line (ending in a backslash,
    Inno Setup's line spanning) joined to the next."""
    joined, entries = "", []
    for line in _section(text, name):
        if line.endswith("\\"):
            joined += line[:-1] + " "
            continue
        entries.append(joined + line)
        joined = ""
    return entries


def _bootstrapper_entry():
    entries = [_params(e) for e in _entries(ISS.read_text(encoding="utf-8"), "Run")]
    found = [e for e in entries if e.get("Filename") == BOOTSTRAPPER]
    assert len(found) == 1, f"[Run] entries for {BOOTSTRAPPER}: {entries}"
    return found[0]


def _code():
    return "\n".join(_section(ISS.read_text(encoding="utf-8"), "Code"))


def _pascal_function(name):
    """The body of one [Code] function, from its header to its final end;."""
    m = re.search(rf"^function {name}\(.*?^end;", _code(), re.M | re.S | re.IGNORECASE)
    assert m, f"no function {name} in [Code]"
    return m.group(0)


def test_the_bootstrapper_runs_when_the_runtime_is_missing_or_old():
    entry = _bootstrapper_entry()
    assert entry.get("Check") == "not IsWebView2Current", entry
    assert entry.get("Parameters") == "/silent /install", entry
    assert entry.get("StatusMsg", "").startswith("Installing or updating "), entry


def test_current_means_installed_and_at_least_the_minimum():
    body = _pascal_function("IsWebView2Current")
    assert "WebView2Version()" in body, body
    assert re.search(
        r"CompareDottedVersions\(\s*Version\s*,\s*MinWebView2\s*\)\s*>=\s*0", body
    ), body
    # a missing runtime is not current, whatever the comparison says
    assert re.search(r"Version\s*<>\s*''", body), body


def test_versions_are_compared_as_numbers_part_by_part():
    """'99.0' against '101.0' as text says 99 is the newer; the comparison
    takes each dotted part as a number."""
    compare = _pascal_function("CompareDottedVersions")
    assert "NextVersionPart(" in compare, compare
    part = _pascal_function("NextVersionPart")
    assert "StrToIntDef(" in part and "Pos('.'" in part, part
    # and nothing compares the version text with the minimum directly
    assert not re.search(r"Version\s*(?:>=|<=|<|>)\s*MinWebView2", _code())


def test_the_runtime_version_is_read_from_the_keys_the_launcher_reads():
    import desktop_launcher as dl

    client = re.search(r"WebView2Client\s*=\s*'([^']+)'", _code())
    assert client, "WebView2Client not declared in [Code]"
    assert client.group(1) == rf"Microsoft\EdgeUpdate\Clients\{dl.WEBVIEW2_CLIENTS[0]}"
    body = _pascal_function("WebView2Version")
    for key in (
        r"HKLM, 'SOFTWARE\WOW6432Node\' + WebView2Client",  # per machine, 64-bit
        r"HKLM, 'SOFTWARE\' + WebView2Client",  # per machine, 32-bit Windows
        r"HKCU, 'Software\' + WebView2Client",  # per user
    ):
        assert key in body, (key, body)


def test_the_installer_and_the_launcher_need_the_same_runtime():
    m = re.search(r"MinWebView2\s*=\s*'([^']+)'\s*;", _code())
    assert m, "MinWebView2 not declared in [Code]"
    import desktop_launcher as dl

    assert m.group(1) == dl.MIN_WEBVIEW2, (m.group(1), dl.MIN_WEBVIEW2)
