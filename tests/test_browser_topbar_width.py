"""The top bar at 1280 wide, in a real browser: with Contact support beside
About, the company name was the one thing in the bar that could shrink, and
it shrank to an "N" (2.30.0 gate, macbase1). It keeps room for a word, the
search box gives next, and below 1366 the clock goes; nothing in the bar is
pushed past the window's edge."""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

MEASURE = """() => {
    const bar = document.getElementById('topbar');
    const company = document.getElementById('topbar-company');
    company.textContent = 'Riverbend Construction Services LLC';
    const right = document.querySelector('.topbar-right').getBoundingClientRect();
    const support = document.getElementById('support-btn').getBoundingClientRect();
    return {
        company: company.getBoundingClientRect().width,
        right: right.right,
        support: support.width,
        overflow: bar.scrollWidth - bar.clientWidth,
        clock: getComputedStyle(document.getElementById('topbar-clock')).display,
        window: window.innerWidth,
    };
}"""


@pytest.mark.parametrize("width", [1280, 1366, 1600])
def test_the_company_name_keeps_its_room_beside_contact_support(browser, width):
    page = _open(browser, width, 800, "#/")
    got = page.evaluate(MEASURE)
    page.close()
    assert got["window"] == width
    assert got["company"] >= 100, got
    assert got["support"] >= 80, got  # the button still says Contact support
    assert got["right"] <= width, got
    assert got["overflow"] <= 0, got
    assert got["clock"] == ("none" if width < 1366 else "block"), got
