"""Reports redraw after an edit, and a Refresh control (#482): the sources.

- every report dialog (ReportsPage.openPeriodModal) draws a Refresh button
  that runs the open report again in place (render({ again: true }));
- the API client tells the page (and other windows of the same books)
  after a successful write, preferences and sign-in left out;
- "Refresh reports automatically" is the per-user preference reports, on
  unless auto_refresh is false; any signed-in role, read-only included,
  sets its own, and an API token none.

What it does in a browser is tests/test_browser_report_refresh.py.
"""

from pathlib import Path

from app.models.users import User
from app.services import auth as auth_service

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def test_every_report_dialog_has_refresh():
    src = _js("reports.js")
    shell = src[src.index("    async openPeriodModal(") :]
    shell = shell[: shell.index("\n    },")]
    assert 'id="report-refresh-btn" onclick="ReportsPage.refresh()"' in shell
    assert 'id="report-refreshed" role="status" aria-live="polite"' in shell
    assert "return ReportsPage._live.render({ again: true });" in src


def test_the_api_client_says_a_write_went_through():
    api = _js("api.js")
    assert "if (method !== 'GET') API.changed(path);" in api
    assert "/^\\/(preferences|auth)(\\/|$)/" in api
    assert "new BroadcastChannel('slowbooks-books')" in api
    reports = _js("reports.js")
    assert "window.addEventListener('slowbooks:books-changed'" in reports
    assert (
        "if (!ReportsPage.autoRefresh || !ReportsPage._liveShown()) return;" in reports
    )


def test_on_by_default_and_read_once_at_start():
    reports = _js("reports.js")
    assert "autoRefresh: true," in reports
    assert "!(value && value.auto_refresh === false)" in reports
    assert "fetch('/api/preferences/reports'" in _js("app.js")


def test_a_read_only_sign_in_sets_its_own(client, db_session):
    db_session.add(
        User(
            username="viewer",
            display_name="Viewer",
            password_hash=auth_service.hash_password("long-enough-pw-1"),
            role="readonly",
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": "viewer", "password": "long-enough-pw-1"}
    )
    assert r.status_code == 200, r.text
    r = client.put("/api/preferences/reports", json={"value": {"auto_refresh": False}})
    assert r.status_code == 200, r.text
    assert client.get("/api/preferences/reports").json()["value"] == {
        "auto_refresh": False
    }
    assert client.post("/api/customers", json={"name": "Nope"}).status_code == 403
