"""The page never stays white: boot_check.js is loaded first, and the app
tells it each time a first screen is drawn.

A support email (2.29.2, Windows 10): "Full white screen on startup, never
changing and no menus or sidebars to click on." boot_check.js records an
error while the other scripts load and shows a plain message in place of the
page, and a watchdog shows it when no first screen is drawn in time
(tests/js/boot_check.test.cjs runs it). That only works if it runs before
every other script, on any engine, and if every first screen clears the
watchdog. These read the sources, so they need no browser.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
JS = ROOT / "app" / "static" / "js"
BOOT_CHECK = "/static/js/boot_check.js"


def _scripts(html):
    """Every <script ...> tag of the page, in order, with where it sits."""
    head_end = html.index("</head>")
    return [
        (m.start() < head_end, m.group(0))
        for m in re.finditer(r"<script\b[^>]*>", html, re.I)
    ]


def _src(tag):
    m = re.search(r'\bsrc="([^"]*)"', tag)
    return m.group(1) if m else None


def _strip_comments(js):
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(^|\s)//[^\n]*", r"\1", js)


def test_index_html_loads_boot_check_before_every_other_script():
    scripts = _scripts(INDEX.read_text(encoding="utf-8"))
    assert scripts, "no scripts in index.html"
    in_head, first = scripts[0]
    assert _src(first) == BOOT_CHECK, f"first script is {first}"
    assert in_head, "boot_check.js must load from <head>, ahead of the page body"
    # it has to run as it is read, not after the others
    assert not re.search(r"\b(async|defer|type=\"module\")", first), first
    srcs = [_src(tag) for _, tag in scripts]
    assert srcs.count(BOOT_CHECK) == 1, srcs
    # and no script before it, inline or not, could fail without it watching
    assert all(src for src in srcs), "an inline script in index.html"


def test_boot_check_is_a_same_origin_file_the_policy_allows():
    """An inline script would fall to a script-src without 'unsafe-inline'
    (the policy means to drop it); a file on this origin is 'self'."""
    assert (JS / "boot_check.js").is_file()
    from app.main import _build_csp

    for desktop in (False, True):
        script_src = re.search(r"script-src ([^;]*);", _build_csp(desktop)).group(1)
        assert "'self'" in script_src.split(), script_src


def test_boot_check_is_es5_so_any_engine_can_parse_it():
    """It has to run where the rest of the app cannot. No arrow functions,
    let/const, template strings, classes, async, spread, optional chaining or
    nullish coalescing (node --check accepts them all, so it is no test)."""
    code = _strip_comments((JS / "boot_check.js").read_text(encoding="utf-8"))
    for name, pattern in {
        "arrow function": r"=>",
        "let/const": r"\b(let|const)\s",
        "template string": r"`",
        "class": r"\bclass\s",
        "async/await": r"\b(async|await)\b",
        "spread or rest": r"\.\.\.",
        "optional chaining": r"\?\.",
        "nullish coalescing": r"\?\?",
        "for...of": r"\bfor\s*\([^)]*\bof\b",
    }.items():
        assert not re.search(pattern, code), f"boot_check.js uses {name}"


def test_the_sign_in_screen_clears_the_watchdog():
    """auth.js draws the sign-in (or first-run setup) screen, the company
    choice on it included; the moment it is on the page counts."""
    src = (JS / "auth.js").read_text(encoding="utf-8")
    body = re.search(
        r"function renderView\(mode, onSuccess\) \{(.*?)\n    \}", src, re.S
    )
    assert body, "renderView not found in auth.js"
    body = body.group(1)
    assert "document.body.appendChild(overlay)" in body
    drawn = body.find("window.SlowBooksBoot.drawn()")
    assert drawn > body.index("document.body.appendChild(overlay)"), body
    assert "if (window.SlowBooksBoot)" in body  # pages without boot_check.js


def test_the_first_page_clears_the_watchdog_as_it_starts():
    """App.init's first page: as soon as it has started, not when its data
    has come back, so a big company's slow first page never trips it."""
    src = (JS / "app.js").read_text(encoding="utf-8")
    init = src[src.index("    init() {") : src.index("    async initSystemInfo() {")]
    start = init.index("App.navigate(location.hash || '#/');")
    drawn = init.find("window.SlowBooksBoot.drawn()")
    assert drawn > start, "App.init does not tell boot_check.js the first page started"
    # in the same callback, right after the navigation starts (not awaited)
    between = init[start:drawn]
    assert "await" not in between and "});" not in between, between
