"""CC and BCC on outgoing email (#611, from Discussion #607).

Cc is a header the recipient reads; Bcc must never appear in the message,
only in the SMTP envelope. Both are recorded on the EmailLog row. Settings
smtp_always_cc / smtp_always_bcc are added to every send so batch sends
(statements, acknowledgments) carry them with no dialog.
"""

import smtplib
from email import message_from_string

import pytest

from app.models.email_log import EmailLog
from app.services.email_service import clean_addresses, send_email
from app.services.settings_service import set_setting


@pytest.fixture
def smtp(monkeypatch, db_session):
    """SMTP configured and faked: returns the list of (envelope, message)."""
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, _from, to, message):
            sent.append((list(to), message_from_string(message)))

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    set_setting(db_session, "smtp_host", "mail.example.com")
    set_setting(db_session, "smtp_from_email", "books@example.com")
    db_session.commit()
    return sent


def _set(db, key, value):
    set_setting(db, key, value)
    db.commit()


def _send(db, **kw):
    return send_email(
        db=db,
        to_email="customer@example.com",
        subject="Invoice 1",
        html_body="<p>hi</p>",
        entity_type="invoice",
        entity_id=1,
        **kw,
    )


# ---------------------------------------------------------------------------
# address parsing
# ---------------------------------------------------------------------------


def test_addresses_split_on_commas_and_semicolons_and_drop_blanks():
    assert clean_addresses("a@x.com, b@x.com;; c@x.com ,") == [
        "a@x.com",
        "b@x.com",
        "c@x.com",
    ]
    assert clean_addresses(["a@x.com", "", None, "A@X.com"]) == ["a@x.com"]
    assert clean_addresses(None) == [] and clean_addresses("") == []


def test_header_injection_characters_are_stripped():
    assert clean_addresses("a@x.com\r\nBcc: evil@x.com") == ["a@x.comBcc: evil@x.com"]


# ---------------------------------------------------------------------------
# the message and the envelope
# ---------------------------------------------------------------------------


def test_cc_is_a_header_and_bcc_is_envelope_only(db_session, smtp):
    assert _send(db_session, cc="office@example.com", bcc="hidden@example.com")
    envelope, msg = smtp[0]
    assert envelope == [
        "customer@example.com",
        "office@example.com",
        "hidden@example.com",
    ]
    assert msg["To"] == "customer@example.com"
    assert msg["Cc"] == "office@example.com"
    assert msg["Bcc"] is None
    assert "hidden@example.com" not in msg.as_string()


def test_no_copies_means_no_cc_header(db_session, smtp):
    assert _send(db_session)
    envelope, msg = smtp[0]
    assert envelope == ["customer@example.com"]
    assert msg["Cc"] is None


def test_the_log_records_the_copies(db_session, smtp):
    _send(db_session, cc="a@x.com, b@x.com", bcc="c@x.com")
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert row.status == "sent"
    assert row.recipient == "customer@example.com"
    assert row.cc == "a@x.com, b@x.com"
    assert row.bcc == "c@x.com"


def test_the_log_records_copies_on_a_failed_send(db_session, monkeypatch):
    # SMTP unconfigured: send_email logs "failed" and the copies are still there
    assert not _send(db_session, bcc="c@x.com")
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert row.status == "failed" and row.bcc == "c@x.com"


# ---------------------------------------------------------------------------
# always-copy settings
# ---------------------------------------------------------------------------


def test_always_cc_and_bcc_settings_apply_to_every_send(db_session, smtp):
    _set(db_session, "smtp_always_cc", "office@example.com")
    _set(db_session, "smtp_always_bcc", "books@example.com, audit@example.com")
    assert _send(db_session)
    envelope, msg = smtp[0]
    assert msg["Cc"] == "office@example.com"
    assert envelope == [
        "customer@example.com",
        "office@example.com",
        "books@example.com",
        "audit@example.com",
    ]
    assert "audit@example.com" not in msg.as_string()


def test_per_send_copies_come_before_the_always_copies(db_session, smtp):
    _set(db_session, "smtp_always_bcc", "books@example.com")
    assert _send(db_session, bcc="partner@example.com")
    envelope, _ = smtp[0]
    assert envelope == [
        "customer@example.com",
        "partner@example.com",
        "books@example.com",
    ]


def test_an_address_already_on_the_mail_is_not_copied_again(db_session, smtp):
    _set(db_session, "smtp_always_bcc", "Customer@example.com")
    assert _send(
        db_session,
        cc="customer@example.com, office@example.com",
        bcc="office@example.com",
    )
    envelope, msg = smtp[0]
    assert envelope == ["customer@example.com", "office@example.com"]
    assert msg["Cc"] == "office@example.com"


# ---------------------------------------------------------------------------
# the invoice route and the settings API
# ---------------------------------------------------------------------------


def test_the_invoice_email_route_passes_cc_and_bcc_through(
    client, seed_accounts, monkeypatch
):
    captured = {}

    def fake_send(**kw):
        captured.update(kw)
        return True

    # The route imports send_email inside the handler, so patch the service.
    monkeypatch.setattr("app.services.email_service.send_email", fake_send)
    cust = client.post(
        "/api/customers", json={"name": "Probe Co", "email": "a@b.com"}
    ).json()
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "due_date": "2026-06-30",
            "lines": [
                {"description": "work", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    ).json()
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "a@b.com", "cc": "x@b.com", "bcc": "y@b.com"},
    )
    assert r.status_code == 200, r.text
    assert captured["to_email"] == "a@b.com"
    assert captured["cc"] == "x@b.com" and captured["bcc"] == "y@b.com"


def test_always_copy_settings_round_trip_and_log_rows_carry_copies(client, db_session):
    r = client.put(
        "/api/settings",
        json={
            "smtp_always_cc": "office@example.com",
            "smtp_always_bcc": "books@example.com",
        },
    )
    assert r.status_code == 200, r.text
    s = client.get("/api/settings").json()
    assert s["smtp_always_cc"] == "office@example.com"
    assert s["smtp_always_bcc"] == "books@example.com"

    db_session.add(
        EmailLog(
            entity_type="invoice",
            entity_id=0,
            recipient="a@x.com",
            cc="b@x.com",
            bcc="c@x.com",
            subject="S",
            status="sent",
        )
    )
    db_session.commit()
    row = client.get("/api/email-log").json()["rows"][0]
    assert row["cc"] == "b@x.com" and row["bcc"] == "c@x.com"
