"""A recurring template that has made invoices cannot be deleted.

Deleting one answered 200 and left its invoices pointing at a template that
no longer existed (found in PNWImport's fork, SlowBooks-Pro-Testing#101).
Nothing ends on the delete: it is a 409 and the schedule keeps billing until
the user sets an End Date (stop) or clears Active (pause).
"""

from app.models.invoices import Invoice
from app.models.recurring import RecurringInvoice

LINE = {"description": "Monthly service", "quantity": 1, "rate": 100}


def _schedule(client, customer_id):
    r = client.post(
        "/api/recurring",
        json={
            "customer_id": customer_id,
            "frequency": "monthly",
            "start_date": "2026-09-01",
            "lines": [LINE],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_template_that_made_an_invoice_is_not_deleted(
    client, db_session, seed_accounts, seed_customer
):
    rec_id = _schedule(client, seed_customer.id)
    made = client.post("/api/recurring/generate?as_of=2026-09-15").json()
    assert made["invoices_created"] == 1
    inv_id = made["invoice_ids"][0]

    r = client.delete(f"/api/recurring/{rec_id}")

    assert r.status_code == 409
    assert r.json()["detail"] == (
        "This recurring invoice has made 1 invoice, so it cannot be deleted, "
        "and it keeps billing until you act. Set an End Date to stop it for "
        "good, or clear Active to pause it."
    )
    db_session.expire_all()
    assert db_session.get(RecurringInvoice, rec_id) is not None
    assert db_session.get(Invoice, inv_id).recurring_invoice_id == rec_id


def test_the_sentence_counts_every_invoice_it_made(
    client, db_session, seed_accounts, seed_customer
):
    rec_id = _schedule(client, seed_customer.id)
    client.post("/api/recurring/generate?as_of=2026-09-15")
    client.post("/api/recurring/generate?as_of=2026-10-15")

    r = client.delete(f"/api/recurring/{rec_id}")

    assert r.status_code == 409
    assert "has made 2 invoices" in r.json()["detail"]


def test_a_template_that_made_nothing_still_deletes(
    client, db_session, seed_accounts, seed_customer
):
    rec_id = _schedule(client, seed_customer.id)

    r = client.delete(f"/api/recurring/{rec_id}")

    assert r.status_code == 200
    db_session.expire_all()
    assert db_session.get(RecurringInvoice, rec_id) is None


def test_ending_it_stops_it_and_keeps_the_invoices(
    client, db_session, seed_accounts, seed_customer
):
    rec_id = _schedule(client, seed_customer.id)
    inv_id = client.post("/api/recurring/generate?as_of=2026-09-15").json()[
        "invoice_ids"
    ][0]

    r = client.put(f"/api/recurring/{rec_id}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    later = client.post("/api/recurring/generate?as_of=2026-10-15").json()
    assert later["invoices_created"] == 0
    db_session.expire_all()
    assert db_session.get(Invoice, inv_id).recurring_invoice_id == rec_id


def test_the_edit_form_has_an_active_box():
    from pathlib import Path

    js = (Path(__file__).parents[1] / "app/static/js/recurring.js").read_text()
    assert 'name="is_active"' in js
    assert "data.is_active = form.is_active.checked" in js
