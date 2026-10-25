"""The read-only support diagnostics endpoint.

Covers the missing-log case (never a 500), redaction of the log tail against
the company's own names, and the no-telemetry promise: the endpoint makes no
outbound HTTP call."""

import app.routes.support as support_routes


def test_diagnostics_reports_environment(client):
    body = client.get("/api/support/diagnostics").json()
    assert body["platform"] in ("macOS", "Windows", "Linux")
    assert body["deployment"] in ("native", "Server Edition", "self-managed")
    assert "Version: v" in body["environment"]
    assert "Deployment:" in body["environment"]


def test_diagnostics_without_a_log_is_not_an_error(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))  # no launcher.log here
    r = client.get("/api/support/diagnostics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["log_available"] is False
    assert body["log_tail"] == ""
    assert body["log_note"]


def test_diagnostics_redacts_the_log_tail(client, tmp_path, monkeypatch):
    # A customer whose name must not leak, and a reply-to from the company file.
    client.post("/api/customers", json={"name": "Wayne Enterprises"})
    client.put("/api/settings", json={"company_email": "books@example.com"})

    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    (tmp_path / "launcher.log").write_text(
        "2026-10-25 09:15:01 INFO Wayne Enterprises paid $500.00\n"
        "contact jane@wayne.com or (305) 555-9999\n",
        encoding="utf-8",
    )

    body = client.get("/api/support/diagnostics").json()
    tail = body["log_tail"]
    assert body["log_available"] is True
    assert "2026-10-25 09:15:01 INFO" in tail  # timestamp/level kept
    assert "Wayne Enterprises" not in tail and "[name]" in tail
    assert "$500.00" not in tail and "[amount]" in tail
    assert "jane@wayne.com" not in tail and "[email]" in tail
    assert "(305) 555-9999" not in tail and "[phone]" in tail
    assert body["reply_to"] == "books@example.com"


def test_diagnostics_makes_no_outbound_call(client, monkeypatch):
    def _boom(*a, **k):  # any HTTP client construction is a telemetry leak
        raise AssertionError("support diagnostics must not open a network client")

    monkeypatch.setattr("httpx.AsyncClient", _boom)
    monkeypatch.setattr("httpx.Client", _boom)
    assert client.get("/api/support/diagnostics").status_code == 200


def test_reveal_log_is_404_when_not_native(client, monkeypatch):
    monkeypatch.delenv("SLOWBOOKS_DESKTOP", raising=False)
    assert client.post("/api/support/reveal-log").status_code == 404


def test_reveal_log_opens_the_folder_on_native(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DESKTOP", "1")
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE", raising=False)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    (tmp_path / "launcher.log").write_text("x\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        support_routes.subprocess, "run", lambda *a, **k: calls.append(a)
    )
    r = client.post("/api/support/reveal-log")
    assert r.status_code == 200 and r.json()["revealed"] is True
    assert calls  # a file manager was invoked


def test_reveal_log_reports_a_missing_file_on_native(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DESKTOP", "1")
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE", raising=False)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))  # no launcher.log
    calls = []
    monkeypatch.setattr(
        support_routes.subprocess, "run", lambda *a, **k: calls.append(a)
    )
    body = client.post("/api/support/reveal-log").json()
    assert body["revealed"] is False and body["reason"]
    assert not calls  # nothing to open, so no file manager launched


def test_diagnostics_note_points_to_the_console_on_a_server_deployment(
    client, tmp_path, monkeypatch
):
    # self-managed (Docker or a plain source run) / Server Edition: neither
    # desktop flag, no launcher.log file.
    monkeypatch.delenv("SLOWBOOKS_DESKTOP", raising=False)
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE", raising=False)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    body = client.get("/api/support/diagnostics").json()
    assert body["deployment"] == "self-managed"
    assert body["log_available"] is False
    assert "console" in body["log_note"].lower()


def test_both_endpoints_require_a_signed_in_session(unauthed_client):
    assert unauthed_client.get("/api/support/diagnostics").status_code == 401
    assert unauthed_client.post("/api/support/reveal-log").status_code == 401
