"""The WebView2 minimum keeps up with the browser features the app uses.

MIN_WEBVIEW2 (desktop_launcher.py) is the oldest WebView2 runtime the desktop
window is opened on; below it the launcher says what to update, and the
installer updates it (tests/test_windows_packaging.py keeps the installer's
number the same). The page sets one floor under it: each feature below has
the Chromium version that first shipped it, the scan finds the ones the
app's own scripts and stylesheets use, and the minimum must be at least the
newest of them. So a :has() selector (Chromium 105) or Object.groupBy (117)
added later fails here until the minimum goes up with it.

pywebview sets the other floor, the window's own (see MIN_WEBVIEW2): the
last test pins the pywebview that number was measured on.
"""

import re
from pathlib import Path

import desktop_launcher as dl

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "app" / "static" / "js"
CSS_DIR = ROOT / "app" / "static" / "css"
# Shipped as published, with browser support of its own (Chart.js 4).
VENDORED = {"chart.umd.js"}

# (feature, pattern, the first Chromium that has it)
JS_FEATURES = [
    ("Object.hasOwn", r"\bObject\.hasOwn\s*\(", 93),
    ("crypto.randomUUID", r"\bcrypto\.randomUUID\b", 92),
    ("structuredClone", r"\bstructuredClone\s*\(", 98),
    (".at() on an array or string", r"\.at\(", 92),
    ("findLast / findLastIndex", r"\.findLast(?:Index)?\s*\(", 97),
    ("Object.groupBy / Map.groupBy", r"\b(?:Object|Map)\.groupBy\s*\(", 117),
    ("toSorted / toReversed / toSpliced", r"\.to(?:Sorted|Reversed|Spliced)\s*\(", 110),
    ("logical assignment (??= ||= &&=)", r"(?:\?\?|\|\||&&)=", 85),
    ("replaceAll", r"\.replaceAll\s*\(", 85),
    ("Promise.any", r"\bPromise\.any\s*\(", 85),
    ("AbortSignal.timeout", r"\bAbortSignal\.timeout\s*\(", 103),
    ("Array.fromAsync", r"\bArray\.fromAsync\s*\(", 121),
    ("Promise.withResolvers", r"\bPromise\.withResolvers\s*\(", 119),
]
# CSS, in the stylesheets and in the style strings scripts build
CSS_FEATURES = [
    (":has()", r":has\(", 105),
    ("@container", r"@container\b", 105),
    ("container-type", r"\bcontainer-type\s*:", 105),
    ("color-mix()", r"\bcolor-mix\(", 111),
    (":is() / :where()", r":(?:is|where)\(", 88),
    ("the inset property", r"(?<![\w-])inset\s*:", 87),
    ("aspect-ratio", r"\baspect-ratio\s*:", 88),
    ("@layer", r"@layer\b", 99),
    ("dynamic viewport units", r"\d(?:dvh|svh|lvh|dvw|svw|lvw)\b", 108),
    ("oklch() / oklab()", r"\bokl(?:ch|ab)\(", 111),
    ("light-dark()", r"\blight-dark\(", 123),
    ("@starting-style", r"@starting-style\b", 117),
]


def _js_lines(text):
    """The lines of a script that hold code. Comment lines (// and the lines
    of a /* */ block) are left out: a note about a feature is not a use of
    it. Not a tokenizer on purpose: stripping /* */ with a pattern would
    take accept="image/*" in a template string for a comment's start."""
    for n, line in enumerate(text.splitlines(), 1):
        if not line.lstrip().startswith(("//", "/*", "*")):
            yield n, line


def _css_lines(text):
    """The lines of a stylesheet with its comments blanked (line numbers kept)."""
    text = re.sub(
        r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S
    )
    return enumerate(text.splitlines(), 1)


def _scanned():
    scripts = sorted(p for p in JS_DIR.glob("*.js") if p.name not in VENDORED)
    sheets = sorted(CSS_DIR.glob("*.css"))
    return scripts, sheets


def _uses():
    """(file:line, feature, Chromium) for each use the scan finds."""
    scripts, sheets = _scanned()
    found = []
    for path in scripts:
        for n, line in _js_lines(path.read_text(encoding="utf-8")):
            for name, pattern, chromium in JS_FEATURES + CSS_FEATURES:
                if re.search(pattern, line):
                    found.append((f"{path.name}:{n}", name, chromium))
    for path in sheets:
        for n, line in _css_lines(path.read_text(encoding="utf-8")):
            for name, pattern, chromium in CSS_FEATURES:
                if re.search(pattern, line):
                    found.append((f"{path.name}:{n}", name, chromium))
    return found


def test_the_minimum_runs_every_browser_feature_the_app_uses():
    uses = _uses()
    assert uses, "the scan found no feature at all: is it reading the right folders?"
    needed = max(chromium for _, _, chromium in uses)
    minimum = dl._version_parts(dl.MIN_WEBVIEW2)[0]
    newest = [
        f"{where} {name} (Chromium {c})" for where, name, c in uses if c == needed
    ]
    assert minimum >= needed, (
        f"MIN_WEBVIEW2 is {dl.MIN_WEBVIEW2}, but the app uses features from "
        f"Chromium {needed}: {', '.join(newest[:8])}. Raise MIN_WEBVIEW2 in "
        "desktop_launcher.py and MinWebView2 in packaging/windows/SlowBooksPro.iss "
        f"to at least {needed}.0.0.0, or do without the feature."
    )


def test_the_scan_reads_the_app_and_skips_only_what_is_vendored():
    scripts, sheets = _scanned()
    names = {p.name for p in scripts}
    assert {"app.js", "auth.js", "boot_check.js", "reports.js"} <= names
    assert len(scripts) > 50 and {p.name for p in sheets} >= {"style.css", "dark.css"}
    for name in VENDORED:
        assert (JS_DIR / name).is_file(), f"{name} is listed as vendored but is gone"
        assert name not in names


def test_each_pattern_finds_its_feature_and_not_its_neighbours():
    """A pattern that never matches would let any feature through."""
    hits = {
        "Object.hasOwn": (
            "Object.hasOwn(TABS, tab)",
            "Object.hasOwnProperty.call(o, k)",
        ),
        "crypto.randomUUID": ("crypto.randomUUID()", "crypto.getRandomValues(buf)"),
        "structuredClone": ("structuredClone(row)", "cloneRow(row)"),
        ".at() on an array or string": ("rows.at(-1)", "el.attachShadow({})"),
        "findLast / findLastIndex": ("rows.findLastIndex(f)", "rows.findIndex(f)"),
        "Object.groupBy / Map.groupBy": ("Object.groupBy(rows, f)", "groupBy(rows, f)"),
        "toSorted / toReversed / toSpliced": ("rows.toSorted(cmp)", "rows.sort(cmp)"),
        "logical assignment (??= ||= &&=)": (
            "(m[k] ||= []).push(r)",
            "a || b; a !== b",
        ),
        "replaceAll": ("s.replaceAll('a', 'b')", "s.replace(/a/g, 'b')"),
        "AbortSignal.timeout": ("AbortSignal.timeout(5000)", "new AbortController()"),
        ":has()": ("li:has(> a.active)", "li:hover > a"),
        "@container": ("@container card (min-width: 30em) {", ".container { }"),
        "color-mix()": ("color: color-mix(in srgb, red 40%, blue);", "color: mix;"),
        ":is() / :where()": (
            ":where(.btn, .tb-btn):focus-visible",
            ".btn:focus-visible",
        ),
        "the inset property": (
            "position:fixed;inset:0;",
            "box-shadow: inset 0 1px #000;",
        ),
        "dynamic viewport units": ("height: 100dvh;", "height: 100vh;"),
    }
    table = {name: pattern for name, pattern, _ in JS_FEATURES + CSS_FEATURES}
    for name, (use, near_miss) in hits.items():
        assert re.search(table[name], use), (name, use)
        assert not re.search(table[name], near_miss), (name, near_miss)


def test_the_window_floor_was_measured_on_the_pywebview_the_app_ships():
    """MIN_WEBVIEW2 is 101.0.1210.39 because pywebview 6.2.1's WinForms
    control asks every runtime for ICoreWebView2Environment10 (it always
    sets IsInPrivateModeEnabled). Another pywebview can ask for something
    newer, or nothing: read its webview/platforms/edgechromium.py and the
    WinForms control's CreationProperties use, then move MIN_WEBVIEW2 (and
    MinWebView2 in the installer) and the pin below together."""
    pins = re.findall(
        r"^pywebview==(\S+)", (ROOT / "requirements-desktop.txt").read_text(), re.M
    )
    assert pins == ["6.2.1"], pins
    assert dl._version_parts(dl.MIN_WEBVIEW2) >= dl._version_parts("101.0.1210.39")
