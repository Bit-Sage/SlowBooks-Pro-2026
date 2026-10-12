"""Charts: the figure on the bar, not only in the legend (issue #265).

The dashboard's and Analytics' charts had their text equivalents in the
legend and the table under them; someone who can't use the colour, or who
prints in grey, went from the bar to the legend and back. Every bar, slice
and point now carries its figure as text; what would land on another
label, or outside the chart, is left off and the table has it.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="no node")


def _probe(theme="light"):
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "chart_figures_probe.js"), theme],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_compact_money_is_the_size_a_label_can_hold():
    got = _probe()["compact"]
    assert got == ["$1.2k", "$12k", "$3.4M", "-$850", "$45.50", "$999", "$0", "$12M"]


@needs_node
def test_bars_carry_their_figure_past_the_end_or_inside_when_there_is_no_room():
    bars = _probe()["bars"]
    by = {b["text"]: b for b in bars}
    # 150: above the bar's top (y 120), in the theme's ink
    assert by["$150"]["y"] < 120 and by["$150"]["fill"] == "#1a1a28"
    # -40: below the bar's end (y 180)
    assert by["-$40"]["y"] > 180
    # 9999: the top is at y 3, no room above — inside the bar, in text that
    # reads on the bar's dark blue
    assert by["$10k"]["y"] > 3 and by["$10k"]["fill"] == "#ffffff"
    # a zero bar has nothing to say
    assert len(bars) == 3


@needs_node
def test_a_stacked_segment_is_labelled_only_when_it_can_hold_the_text():
    got = _probe()
    stacked = got["stacked"]
    assert [s["text"] for s in stacked] == ["$2.8k"]
    assert stacked[0]["x"] == 160 and stacked[0]["y"] == 50  # the segment's middle
    assert stacked[0]["fill"] == "#1a1a28"  # dark text on the pale segment
    [h] = got["hbar"]
    assert h["text"] == "$640" and h["x"] > 120 and h["y"] == 80  # past the end


@needs_node
def test_slices_carry_their_figure_except_one_too_thin_to_hold_it():
    slices = _probe()["slices"]
    assert [(s["text"], s["fill"]) for s in slices] == [
        ("$7k", "#ffffff"),  # white on the dark slice
        ("$2.7k", "#1a1a28"),  # dark on the pale one
    ]


@needs_node
def test_points_are_labelled_where_the_labels_do_not_collide():
    lines = _probe()["lines"]
    texts = [(p["text"], p["x"], p["y"]) for p in lines]
    # the first line's labels sit above, the second's below; at x 40 and 60
    # the two "$1k" labels would overlap (6px per character), so one is left off
    above = [t for t in texts if t[0] == "$1k"]
    below = [t for t in texts if t[0] == "$2k"]
    assert [t[1] for t in above] == [40, 200] and all(t[2] < 100 for t in above)
    assert [t[1] for t in below] == [40, 200] and all(t[2] > 104 for t in below)


@needs_node
def test_a_hidden_dataset_and_the_dark_theme():
    assert _probe()["hidden"] == []
    dark = _probe("dark")["bars"]
    assert {b["text"]: b["fill"] for b in dark}["$150"] == "#e6e6f0"


def test_every_chart_draws_its_figures():
    dash = (JS / "dashboard.js").read_text(encoding="utf-8")
    ana = (JS / "analytics.js").read_text(encoding="utf-8")
    for src, name in ((dash, "dashboard.js"), (ana, "analytics.js")):
        charts = len(re.findall(r"new Chart\(", src))
        assert charts == src.count("plugins: [chartFigures()]"), name
    assert dash.count("new Chart(") == 1 and ana.count("new Chart(") == 4
    # the CSS-drawn dashboard bars too: the aging segments and monthly revenue
    assert (
        "const figure = v / owed >= 0.14 && text.ratio >= 4.5 ? compactMoney(v) : '';"
        in dash
    )
    assert "figureTextOn(cssVar(`--aging-${key}`), dark)" in dash
    assert "${m.amount ? compactMoney(m.amount) : ''}" in dash
    # the trend's card grew to hold its labels
    assert 'height:200px"><canvas id="chart-bs-trend">' in dash


def test_the_open_item_is_closed_in_the_statement():
    docs = (ROOT / "docs/accessibility.md").read_text(encoding="utf-8")
    assert (
        "text equivalents in the legend"
        not in docs.split("## What we know is still open")[1]
    )
    assert "carries its figure" in docs
