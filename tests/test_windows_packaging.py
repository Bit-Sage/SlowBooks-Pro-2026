"""The Windows installer replaces the program file on a same-version
reinstall (issue #643).

Every build of one version is stamped x.y.z.0 (packaging/windows/
version_info.py pads the version to four numbers), so Inno Setup's version
comparison took the SlowBooksPro.exe already installed for the one it was
shipping and skipped it ("Same version. Skipping.") while replacing every
file around it. The ignoreversion flag on the bundle entry turns that
comparison off. These read the Inno Setup script as text, so they run on
every platform; no Windows is needed to notice the flag is gone.
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
