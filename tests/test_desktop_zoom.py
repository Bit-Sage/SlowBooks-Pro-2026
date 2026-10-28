"""Ctrl + / Ctrl - zoom the desktop window (#597).

pywebview's create_window defaults to zoomable=False, so every window the
launcher opens must ask for it.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "desktop_launcher.py"


def test_every_window_the_launcher_opens_is_zoomable():
    calls = [
        n
        for n in ast.walk(ast.parse(SRC.read_text(encoding="utf-8")))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "create_window"
    ]
    assert len(calls) >= 3  # main window and the two document windows
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert isinstance(kw.get("zoomable"), ast.Constant), call.lineno
        assert kw["zoomable"].value is True, call.lineno
