"""Progress billing and retainage (#555): the owner pay application from the
schedule of values (previous / this period / stored / retainage, with a
release and a deposit), the invoice and balanced journal approve creates,
owner deposits, the sub pay-app schedule and retainage release, and the
seams it fills (build_sov, PUT /sov shrink, change-order void).

WIP / cost-to-complete / the change-order log / buyout report / job cost by
vendor are #556; this one leaves those figures reachable.
"""

from decimal import Decimal
from pathlib import Path

from app.models.bills import Bill
from app.models.invoices import Invoice
from app.models.pay_applications import PayApplication
from app.models.transactions import TransactionLine
from app.services.pay_applications import application_totals, line_computed, _Row

ROOT = Path(__file__).resolve().parents[1]


# ── Helpers ─────────────────────────────────────────────────────────────


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _contract(client, job_id, **terms):
    r = client.put(f"/api/jobs/{job_id}/contract", json=terms)
    assert r.status_code == 200, r.text
    return r.json()


def _sov(client, job_id, lines):
    r = client.put(f"/api/jobs/{job_id}/sov", json={"lines": lines})
    assert r.status_code == 200, r.text
    return client.get(f"/api/jobs/{job_id}/contract").json()["sov"]


def _two_line_job(client, customer_id, retainage_pct=10):
    job = _job(client, customer_id)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=100000,
        retainage_pct=retainage_pct,
    )
    sov = _sov(
        client,
        job["id"],
        [
            {"description": "Sitework", "kind": "work", "scheduled_value": 60000},
            {"description": "Structure", "kind": "work", "scheduled_value": 40000},
        ],
    )
    return job, sov


def _acct(db, number):
    from app.models.accounts import Account

    return db.query(Account).filter(Account.account_number == number).first()


def _gl(db, number):
    acct = _acct(db, number)
    if acct is None:
        return Decimal("0")
    rows = db.query(TransactionLine).filter_by(account_id=acct.id).all()
    return sum(
        (Decimal(str(r.debit)) - Decimal(str(r.credit)) for r in rows), Decimal("0")
    )


# ── Pure math over two applications ─────────────────────────────────────


def _rows(pairs):
    return [
        _Row(scheduled_value=s, previous=p, this_period=t, stored=st)
        for (s, p, t, st) in pairs
    ]


def test_application_math_two_periods_and_release():
    # app 1: 30,000 this period + 5,000 stored on line 1, 10% retainage
    rows = _rows([(50000, 0, 30000, 5000), (50000, 0, 0, 0)])
    t1 = application_totals(
        rows, retainage_pct=10, previous_retainage=0, previous_applications=0
    )
    assert t1["completed_to_date"] == Decimal("35000.00")
    assert t1["retainage_to_date"] == Decimal("3500.00")
    assert t1["retainage_this_period"] == Decimal("3500.00")
    assert t1["total_earned_less_retainage"] == Decimal("31500.00")
    assert t1["current_due"] == Decimal("31500.00")

    # app 2: 20,000 more this period on line 1; 5,000 still stored → 55,000
    rows = _rows([(50000, 30000, 20000, 5000), (50000, 0, 0, 0)])
    t2 = application_totals(
        rows,
        retainage_pct=10,
        previous_retainage=t1["retainage_to_date"],
        previous_applications=t1["current_due"],
    )
    assert t2["completed_to_date"] == Decimal("55000.00")
    assert t2["retainage_to_date"] == Decimal("5500.00")
    assert t2["retainage_this_period"] == Decimal("2000.00")
    assert t2["previous_applications"] == Decimal("31500.00")
    assert t2["current_due"] == Decimal("18000.00")

    # a final application that releases the 5,500 held (previous now 50,000)
    rows = _rows([(50000, 50000, 0, 5000), (50000, 0, 0, 0)])
    t3 = application_totals(
        rows,
        retainage_pct=10,
        previous_retainage=t2["retainage_to_date"],
        previous_applications=Decimal("49500.00"),
        release_retainage=5500,
    )
    assert t3["retainage_this_period"] == Decimal("0.00")
    assert t3["current_due"] == Decimal("5500.00")


def test_line_pct_and_balance():
    row = line_computed(_Row(50000, 10000, 15000, 0), 10)
    assert row["completed_to_date"] == Decimal("25000.00")
    assert row["balance_to_finish"] == Decimal("25000.00")
    assert row["retainage"] == Decimal("2500.00")
    assert row["pct_complete"] == 50.0


# ── Lifecycle ─────────────────────────────────────────────────────────────


def _new_app(client, job_id):
    r = client.post(
        "/api/pay-applications", json={"job_id": job_id, "date": "2026-06-01"}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _edit(client, app_id, **body):
    r = client.put(f"/api/pay-applications/{app_id}", json=body)
    return r


def _approve(client, app_id):
    r = client.post(f"/api/pay-applications/{app_id}/approve")
    return r


def test_one_open_application_per_job(client, seed_accounts, seed_customer):
    job, _sovl = _two_line_job(client, seed_customer.id)
    _new_app(client, job["id"])
    r = client.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-07-01"}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "application_open"


def test_line_over_scheduled_422(client, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 70000, "stored": 0}],
    )
    assert r.status_code == 422


def test_approve_creates_invoice_journal_and_retainage(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 5000},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["totals"]["current_due"] == 31500.0
    inv = db_session.query(Invoice).filter_by(id=body["invoice_id"]).first()
    assert inv.subtotal == Decimal("35000.00")
    assert inv.total == Decimal("35000.00")
    assert inv.retainage_held == Decimal("3500.00")
    assert inv.balance_due == Decimal("31500.00")
    # balanced journal: A/R 31,500 + 1150 3,500 debit; income 35,000 credit
    rows = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=inv.transaction_id)
        .all()
    )
    dr = sum((Decimal(str(r.debit)) for r in rows), Decimal("0"))
    cr = sum((Decimal(str(r.credit)) for r in rows), Decimal("0"))
    assert dr == cr == Decimal("35000.00")
    assert _gl(db_session, "1150") == Decimal("3500.00")  # retainage receivable
    assert _gl(db_session, "1100") == Decimal("31500.00")  # A/R


def test_two_periods_then_release(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    # app 1
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 5000},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    assert _approve(client, a1["id"]).status_code == 200
    # app 2
    a2 = _new_app(client, job["id"])
    _edit(
        client,
        a2["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 20000, "stored": 5000},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    r2 = _approve(client, a2["id"])
    assert r2.status_code == 200, r2.text
    t2 = r2.json()["totals"]
    assert t2["completed_to_date"] == 55000.0
    assert t2["retainage_to_date"] == 5500.0
    assert t2["retainage_this_period"] == 2000.0
    # billed to date is the latest application's cumulative figure, not a
    # sum across applications (35,000 + 55,000 would count period 1 twice)
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["billed_to_date"] == 55000.0
    assert t2["current_due"] == 18000.0
    inv2 = db_session.query(Invoice).filter_by(id=r2.json()["invoice_id"]).first()
    assert inv2.subtotal == Decimal("20000.00")
    assert inv2.retainage_held == Decimal("2000.00")
    assert inv2.balance_due == Decimal("18000.00")
    # retainage receivable now holds 5,500
    assert _gl(db_session, "1150") == Decimal("5500.00")

    # app 3 releases the 5,500
    a3 = _new_app(client, job["id"])
    _edit(
        client,
        a3["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 0, "stored": 5000},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
        release_retainage=5500,
    )
    r3 = _approve(client, a3["id"])
    assert r3.status_code == 200, r3.text
    assert r3.json()["totals"]["current_due"] == 5500.0
    inv3 = db_session.query(Invoice).filter_by(id=r3.json()["invoice_id"]).first()
    assert inv3.retainage_released == Decimal("5500.00")
    assert inv3.balance_due == Decimal("5500.00")
    assert _gl(db_session, "1150") == Decimal("0.00")  # released back to zero


def test_release_over_held_422(client, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    assert _approve(client, a1["id"]).status_code == 200
    a2 = _new_app(client, job["id"])
    r = _edit(client, a2["id"], release_retainage=9999)
    assert r.status_code == 422


# ── Deposits ────────────────────────────────────────────────────────────


def _deposit(client, job_id, amount, bank_id, date="2026-05-01"):
    return client.post(
        f"/api/jobs/{job_id}/deposits",
        json={"date": date, "amount": amount, "deposit_to_account_id": bank_id},
    )


def test_deposit_received_and_applied(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    r = _deposit(client, job["id"], 10000, bank.id)
    assert r.status_code == 201, r.text
    # DR bank / CR Customer Deposits (2210)
    assert _gl(db_session, "2210") == Decimal("-10000.00")  # a liability credit
    listing = client.get(f"/api/jobs/{job['id']}/deposits").json()
    assert listing["received"] == 10000.0
    assert listing["unapplied"] == 10000.0

    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
        deposit_applied=10000,
    )
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    inv = db_session.query(Invoice).filter_by(id=r.json()["invoice_id"]).first()
    assert inv.amount_paid == Decimal("10000.00")
    # the deposit payment relieved A/R and drew down 2210
    assert _gl(db_session, "2210") == Decimal("0.00")
    listing = client.get(f"/api/jobs/{job['id']}/deposits").json()
    assert listing["applied"] == 10000.0
    assert listing["unapplied"] == 0.0


def test_deposit_over_unapplied_422(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    _deposit(client, job["id"], 5000, bank.id)
    app = _new_app(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
        deposit_applied=9000,
    )
    assert r.status_code == 422


# ── Void ──────────────────────────────────────────────────────────────────


def test_void_without_payment(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    inv_id = _approve(client, app["id"]).json()["invoice_id"]
    r = client.post(f"/api/pay-applications/{app['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    inv = db_session.query(Invoice).filter_by(id=inv_id).first()
    assert inv.status.value == "void"
    assert _gl(db_session, "1150") == Decimal("0.00")  # retainage reversed


def test_void_restores_deposit(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    _deposit(client, job["id"], 10000, bank.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
        deposit_applied=10000,
    )
    _approve(client, app["id"])
    r = client.post(f"/api/pay-applications/{app['id']}/void")
    assert r.status_code == 200, r.text
    listing = client.get(f"/api/jobs/{job['id']}/deposits").json()
    assert listing["unapplied"] == 10000.0  # deposit back to unapplied


def test_void_history_recomputed(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    _approve(client, a1["id"])
    a2 = _new_app(client, job["id"])
    _edit(
        client,
        a2["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0}],
    )
    _approve(client, a2["id"])
    # void app 2 then app 1 (latest first): a fresh app 2's `previous`
    # recomputes from approved ones only
    assert client.post(f"/api/pay-applications/{a2['id']}/void").status_code == 200
    assert client.post(f"/api/pay-applications/{a1['id']}/void").status_code == 200
    body = client.get(f"/api/pay-applications/{a2['id']}").json()
    line = next(l for l in body["lines"] if l["sov_line_id"] == sov[0]["id"])
    assert float(line["previous"]) == 0.0  # the only prior approved app is gone


# ── Contract summary figures ────────────────────────────────────────────


def test_contract_summary_billed_retainage_deposits(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    _deposit(client, job["id"], 4000, bank.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 5000}],
    )
    _approve(client, app["id"])
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["billed_to_date"] == 35000.0
    assert c["retainage"]["held_to_date"] == 3500.0
    assert c["retainage"]["outstanding"] == 3500.0
    assert c["deposits"]["received"] == 4000.0
    assert c["deposits"]["unapplied"] == 4000.0


# ── PDF ───────────────────────────────────────────────────────────────────


def test_pdf_renders_both_sheets(client, db_session, seed_accounts, seed_customer):
    from app.services.pdf_service import render_pay_application_html
    from app.services.settings_service import get_all_settings
    from app.routes.pay_applications import _out
    from app.models.jobs import Job

    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 5000}],
    )
    _approve(client, app["id"])
    pa = db_session.query(PayApplication).filter_by(id=app["id"]).first()
    out = _out(db_session, pa)
    html = render_pay_application_html(
        pa, out, db_session.query(Job).get(job["id"]), get_all_settings(db_session)
    )
    assert "APPLICATION FOR PAYMENT" in html
    assert "Line detail" in html
    assert "Current payment due" in html


# ── Seams ────────────────────────────────────────────────────────────────


def test_build_sov_blocked_after_application(
    client, db_session, seed_accounts, seed_customer
):
    from tests.test_contracts import _build_estimate_with_shape

    est, _pkg_c, _pkg_e = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id, name="FromEstimate")
    client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": job["id"]})
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 200, r.text
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
    )
    assert _approve(client, app["id"]).status_code == 200
    # rebuilding the schedule from the estimate is now refused
    again = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "sov_has_billings"


def test_put_sov_shrink_below_billed_422(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    _approve(client, app["id"])
    # shrinking line 1 below its billed 30,000 is refused
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {
                    "id": sov[0]["id"],
                    "description": "Sitework",
                    "kind": "work",
                    "scheduled_value": 10000,
                },
                {
                    "id": sov[1]["id"],
                    "description": "Structure",
                    "kind": "work",
                    "scheduled_value": 50000,
                },
            ]
        },
    )
    assert r.status_code == 422


# ── Sub pay-app schedule and retainage release ───────────────────────────


def _vendor(client, name="Ace Concrete", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _sent_po(client, vendor_id, lines, **extra):
    body = {"vendor_id": vendor_id, "date": "2026-05-01", "lines": lines}
    body.update(extra)
    po = client.post("/api/purchase-orders", json=body).json()
    up = client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    assert up.status_code == 200
    return up.json()


def test_sub_pay_app_schedule_and_release(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(
        client,
        vendor_type="sub",
        default_expense_account_id=_acct(db_session, "5100").id,
    )
    po = _sent_po(
        client,
        v["id"],
        [{"description": "Foundations", "quantity": 1, "rate": 10000}],
        retainage_pct=10,
    )
    # bill half the line, holding 10% retainage
    line_id = po["lines"][0]["id"]
    r = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={"lines": [{"line_id": line_id, "amount": 5000}]},
    )
    assert r.status_code == 200, r.text
    sched = client.get(f"/api/purchase-orders/{po['id']}/pay-app").json()
    assert sched["lines"][0]["previous_billed"] == 5000.0
    assert sched["lines"][0]["remaining"] == 5000.0
    assert sched["retainage_held_to_date"] == 500.0
    assert sched["retainage_outstanding"] == 500.0

    # release the retainage — a bill DR 2050 / CR A/P
    rel = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage", json={"amount": 500}
    )
    assert rel.status_code == 200, rel.text
    bill = db_session.query(Bill).filter_by(id=rel.json()["bill_id"]).first()
    assert bill.retainage_released == Decimal("500.00")
    assert bill.balance_due == Decimal("500.00")
    assert _gl(db_session, "2050") == Decimal("0.00")  # held 500, released 500

    # over the held amount is refused
    over = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage", json={"amount": 100}
    )
    assert over.status_code == 422

    # voiding the release restores the outstanding
    assert client.post(f"/api/bills/{bill.id}/void").status_code == 200
    sched = client.get(f"/api/purchase-orders/{po['id']}/pay-app").json()
    assert sched["retainage_released_to_date"] == 0.0
    assert sched["retainage_outstanding"] == 500.0


# ── Change-order seam ──────────────────────────────────────────────────────


def test_co_void_blocked_once_its_sov_line_is_billed(
    client, db_session, seed_accounts, seed_customer
):
    job, _sov = _two_line_job(client, seed_customer.id)
    co = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "funding": "contract",
            "lines": [
                {
                    "description": "Extra",
                    "cost_type": "material",
                    "quantity": 1,
                    "unit_cost": 5000,
                }
            ],
        },
    ).json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    assert client.post(f"/api/change-orders/{co['id']}/approve").status_code == 200
    # the approved CO appended a change_order SOV line — bill it
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    co_line = next(l for l in sov if l["kind"] == "change_order")
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": co_line["id"], "this_period": 5000, "stored": 0}],
    )
    assert _approve(client, app["id"]).status_code == 200
    r = client.post(f"/api/change-orders/{co['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "co_billed"


def test_sub_waivers_warning(client, db_session, seed_accounts, seed_customer):
    v = _vendor(
        client,
        vendor_type="sub",
        default_expense_account_id=_acct(db_session, "5100").id,
    )
    job, _sov = _two_line_job(client, seed_customer.id)
    # a bill on the job with no waiver, dated before the period
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-05-15",
            "job_id": job["id"],
            "lines": [
                {
                    "description": "Work",
                    "amount": 1000,
                    "account_id": _acct(db_session, "5100").id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    app = client.post(
        "/api/pay-applications",
        json={"job_id": job["id"], "date": "2026-06-01", "period_to": "2026-06-01"},
    ).json()
    body = client.get(f"/api/pay-applications/{app['id']}").json()
    assert body["sub_waivers"]["missing"] == 1
    assert body["sub_waivers"]["bills"][0]["vendor_name"] == "Ace Concrete"


# ── Review round: the fixes ─────────────────────────────────────────────


def test_number_keys_on_job_id_not_job_number(client, seed_accounts, seed_customer):
    # two jobs sharing a job_number get distinct application numbers
    j1 = _job(client, seed_customer.id, name="Tower A", job_number="100")
    _contract(client, j1["id"], contract_type="lump_sum", contract_amount=10000)
    _sov(
        client,
        j1["id"],
        [{"description": "x", "kind": "work", "scheduled_value": 10000}],
    )
    j2 = _job(client, seed_customer.id, name="Tower B", job_number="100")
    _contract(client, j2["id"], contract_type="lump_sum", contract_amount=10000)
    _sov(
        client,
        j2["id"],
        [{"description": "y", "kind": "work", "scheduled_value": 10000}],
    )
    a1 = _new_app(client, j1["id"])
    a2 = _new_app(client, j2["id"])
    assert a1["number"] == f"PA-{j1['id']}-01"
    assert a2["number"] == f"PA-{j2['id']}-01"
    assert a1["number"] != a2["number"]
    assert a1["job_number"] == "100"


def _balanced(db_session, txn_id):
    rows = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    dr = sum((Decimal(str(r.debit)) for r in rows), Decimal("0"))
    cr = sum((Decimal(str(r.credit)) for r in rows), Decimal("0"))
    return dr, cr


def test_negative_retainage_this_period_balances(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id, retainage_pct=10)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    assert _approve(client, a1["id"]).status_code == 200
    assert _gl(db_session, "1150") == Decimal("3000.00")
    # app 2 drops the percent to 5% and bills 10,000 more: retainage to date is
    # 40,000 × 5% = 2,000, below the 3,000 held, so this period is −1,000 and
    # the 1150 leg flips to a credit.
    a2 = _new_app(client, job["id"])
    _edit(
        client,
        a2["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0}],
        retainage_pct=5,
    )
    r = _approve(client, a2["id"])
    assert r.status_code == 200, r.text
    assert r.json()["totals"]["retainage_this_period"] == -1000.0
    inv = db_session.query(Invoice).filter_by(id=r.json()["invoice_id"]).first()
    dr, cr = _balanced(db_session, inv.transaction_id)
    assert dr == cr == Decimal("11000.00")  # A/R 11,000 = income 10,000 + 1150 1,000
    assert _gl(db_session, "1150") == Decimal("2000.00")


def test_many_applications_stay_linear(client, seed_accounts, seed_customer):
    import time

    job = _job(client, seed_customer.id)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=1000000,
        retainage_pct=10,
    )
    sov = _sov(
        client,
        job["id"],
        [{"description": "Work", "kind": "work", "scheduled_value": 1000000}],
    )
    for _ in range(15):
        a = _new_app(client, job["id"])
        _edit(
            client,
            a["id"],
            lines=[{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
        )
        assert _approve(client, a["id"]).status_code == 200
    t0 = time.perf_counter()
    assert client.get(f"/api/pay-applications?job_id={job['id']}").status_code == 200
    a = _new_app(client, job["id"])
    _edit(
        client,
        a["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
    )
    assert _approve(client, a["id"]).status_code == 200
    assert time.perf_counter() - t0 < 1.0


def _taxed_estimate_job(client, customer_id, rate="0.08"):
    """A job whose SOV came from an accepted estimate carrying a tax rate."""
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": "2026-02-01",
            "tax_rate": rate,
            "lines": [
                {"description": "Base", "quantity": 1, "rate": 10000, "unit_cost": 8000}
            ],
        },
    )
    assert r.status_code == 201, r.text
    est = r.json()
    job = _job(client, customer_id, name="Taxed")
    acc = client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": job["id"]})
    assert acc.status_code == 200, acc.text
    fe = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert fe.status_code == 200, fe.text
    client.put(f"/api/jobs/{job['id']}/contract", json={"retainage_pct": 10})
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    return job, sov


def test_tax_from_accepted_estimate(client, db_session, seed_accounts, seed_customer):
    job, sov = _taxed_estimate_job(client, seed_customer.id, rate="0.08")
    app = _new_app(client, job["id"])
    assert float(app["tax_rate"]) == 0.08  # defaulted from the estimate
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 5000, "stored": 0}],
    )
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    inv = db_session.query(Invoice).filter_by(id=r.json()["invoice_id"]).first()
    assert inv.tax_amount == Decimal("400.00")  # 5,000 × 8%
    assert inv.total == Decimal("5400.00")
    assert _gl(db_session, "2200") == Decimal("-400.00")  # sales tax payable
    dr, cr = _balanced(db_session, inv.transaction_id)
    assert dr == cr


def test_deduct_sov_line_bills_down_and_balances(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=9000,
        retainage_pct=0,
    )
    sov = _sov(
        client,
        job["id"],
        [
            {"description": "Work", "kind": "work", "scheduled_value": 10000},
            {"description": "Deduct alt", "kind": "work", "scheduled_value": -1000},
        ],
    )
    app = _new_app(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 10000, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": -1000, "stored": 0},
        ],
    )
    assert r.status_code == 200, r.text
    rr = _approve(client, app["id"])
    assert rr.status_code == 200, rr.text
    inv = db_session.query(Invoice).filter_by(id=rr.json()["invoice_id"]).first()
    assert inv.subtotal == Decimal("9000.00")
    # the deduct line debits income, so the journal's gross is 10,000 both
    # sides — balanced, with A/R taking the 9,000 net.
    dr, cr = _balanced(db_session, inv.transaction_id)
    assert dr == cr
    assert inv.balance_due == Decimal("9000.00")


def test_deduct_line_cannot_bill_past_its_value(client, seed_accounts, seed_customer):
    job = _job(client, seed_customer.id)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=9000)
    sov = _sov(
        client,
        job["id"],
        [
            {"description": "Work", "kind": "work", "scheduled_value": 10000},
            {"description": "Deduct", "kind": "work", "scheduled_value": -1000},
        ],
    )
    app = _new_app(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[1]["id"], "this_period": -2000, "stored": 0}],
    )
    assert r.status_code == 422  # -2,000 is past the -1,000 deduct


def test_approve_with_nothing_to_bill_422(client, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])  # every line zero, no release, no deposit
    r = _approve(client, app["id"])
    assert r.status_code == 422


def test_deposit_over_current_due_422(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    bank = _acct(db_session, "1000")
    _deposit(client, job["id"], 50000, bank.id)
    app = _new_app(client, job["id"])
    # bill 1,000 at 10% → current due 900; a 5,000 deposit is more than that
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 1000, "stored": 0}],
        deposit_applied=5000,
    )
    assert r.status_code == 422


def test_void_with_a_real_payment_409(client, db_session, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    inv_id = _approve(client, app["id"]).json()["invoice_id"]
    # a real customer payment on the invoice
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-06-05",
            "amount": 1000,
            "method": "Check",
            "allocations": [{"invoice_id": inv_id, "amount": 1000}],
        },
    )
    assert pay.status_code == 201, pay.text
    r = client.post(f"/api/pay-applications/{app['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "invoice_paid"


def test_void_with_an_unrelated_deposit_method_payment_409(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])  # no deposit applied
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    inv_id = _approve(client, app["id"]).json()["invoice_id"]
    bank = _acct(db_session, "1000")
    # an unrelated payment flagged method="deposit" — not this app's own
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-06-05",
            "amount": 500,
            "method": "deposit",
            "deposit_to_account_id": bank.id,
            "allocations": [{"invoice_id": inv_id, "amount": 500}],
        },
    )
    assert pay.status_code == 201, pay.text
    r = client.post(f"/api/pay-applications/{app['id']}/void")
    assert r.status_code == 409


def test_put_sov_dropping_a_billed_line_422(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    _approve(client, app["id"])
    # resend only line 2 — dropping the billed line 1 is refused
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {
                    "id": sov[1]["id"],
                    "description": "Structure",
                    "kind": "work",
                    "scheduled_value": 40000,
                }
            ]
        },
    )
    assert r.status_code == 422


def test_second_application_after_a_void(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    _approve(client, a1["id"])
    assert client.post(f"/api/pay-applications/{a1['id']}/void").status_code == 200
    # a fresh application opens (the open-application guard let go on void)
    a2 = _new_app(client, job["id"])
    body = client.get(f"/api/pay-applications/{a2['id']}").json()
    line = next(line for line in body["lines"] if line["sov_line_id"] == sov[0]["id"])
    assert float(line["previous"]) == 0.0  # the only prior app is void


def test_business_invoice_journal_unchanged_with_zero_retainage(
    client, db_session, seed_accounts, seed_customer
):
    # a plain invoice (no retainage) posts the same two-account A/R + income set
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "tax_rate": "0",
            "lines": [{"description": "Work", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    inv = db_session.query(Invoice).filter_by(id=r.json()["id"]).first()
    assert inv.retainage_held == Decimal("0")
    assert inv.retainage_released == Decimal("0")
    rows = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=inv.transaction_id)
        .all()
    )
    # a plain invoice posts only A/R (1100) and income (4000) — no construction
    # retainage accounts are even created on a business books.
    assert _acct(db_session, "1150") is None
    assert len(rows) == 2
    assert {_acct(db_session, "1100").id, _acct(db_session, "4000").id} == {
        row.account_id for row in rows
    }


# ── Migration ───────────────────────────────────────────────────────────


def test_migration_up_down_reverses(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "pa.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    command.upgrade(cfg, "head")
    eng = create_engine("sqlite:///" + db.as_posix())
    insp = inspect(eng)
    assert "pay_applications" in insp.get_table_names()
    assert "job_deposits" in insp.get_table_names()
    pa_cols = {c["name"] for c in insp.get_columns("pay_applications")}
    assert {"tax_rate", "deposit_payment_id"} <= pa_cols
    assert "retainage_held" in {c["name"] for c in insp.get_columns("invoices")}
    eng.dispose()
    command.downgrade(cfg, "-1")
    eng = create_engine("sqlite:///" + db.as_posix())
    insp = inspect(eng)
    assert "pay_applications" not in insp.get_table_names()
    assert "retainage_held" not in {c["name"] for c in insp.get_columns("invoices")}
    eng.dispose()


# ── Review fixes (#576) ──────────────────────────────────────────────────


def _bill(client, app_id, lines, **extra):
    """Edit then approve an application with `lines` = [(sov_idx, this, stored)]."""
    _edit(client, app_id, lines=lines, **extra)
    return _approve(client, app_id)


def test_retainage_to_date_is_net_of_releases(
    client, db_session, seed_accounts, seed_customer
):
    # Trent's repro: bill 30k, release 1,500, bill 10k. The application, the
    # invoice and account 1150 all agree.
    job, sov = _two_line_job(client, seed_customer.id)
    rows = lambda tp: [
        {"sov_line_id": sov[0]["id"], "this_period": tp, "stored": 0},
        {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
    ]
    a1 = _new_app(client, job["id"])
    assert _bill(client, a1["id"], rows(30000)).status_code == 200
    assert _gl(db_session, "1150") == Decimal("3000.00")

    a2 = _new_app(client, job["id"])
    r2 = _bill(client, a2["id"], rows(0), release_retainage=1500)
    assert r2.status_code == 200, r2.text
    t2 = r2.json()["totals"]
    assert t2["retainage_to_date"] == 1500.0
    assert t2["current_due"] == 1500.0
    assert _gl(db_session, "1150") == Decimal("1500.00")

    a3 = _new_app(client, job["id"])
    r3 = _bill(client, a3["id"], rows(10000))
    assert r3.status_code == 200, r3.text
    body = r3.json()
    t3 = body["totals"]
    assert t3["retainage_to_date"] == 2500.0
    assert t3["current_due"] == 9000.0
    inv = db_session.query(Invoice).filter_by(id=body["invoice_id"]).first()
    assert inv.balance_due == Decimal("9000.00")
    assert _gl(db_session, "1150") == Decimal("2500.00")
    assert body["retainage_outstanding"] == 2500.0


def _approved_30k(client, customer_id):
    job, sov = _two_line_job(client, customer_id)
    a1 = _new_app(client, job["id"])
    r = _bill(
        client,
        a1["id"],
        [
            {"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    assert r.status_code == 200, r.text
    return job, sov, r.json()


def test_pay_app_invoice_refuses_amount_edits_allows_notes(
    client, db_session, seed_accounts, seed_customer
):
    _job_, _sov_, app = _approved_30k(client, seed_customer.id)
    inv_id = app["invoice_id"]
    before = _gl(db_session, "1150")

    r = client.put(f"/api/invoices/{inv_id}", json={"tax_rate": 0.07})
    assert r.status_code == 409, r.text
    r = client.put(
        f"/api/invoices/{inv_id}",
        json={
            "lines": [
                {"description": "x", "quantity": 1, "rate": 5, "is_taxable": False}
            ]
        },
    )
    assert r.status_code == 409, r.text

    r = client.put(f"/api/invoices/{inv_id}", json={"notes": "Sent by email"})
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "Sent by email"
    inv = db_session.query(Invoice).filter_by(id=inv_id).first()
    db_session.refresh(inv)
    assert inv.total == Decimal("30000.00")
    assert inv.retainage_held == Decimal("3000.00")
    assert _gl(db_session, "1150") == before == Decimal("3000.00")


def test_only_the_latest_approved_application_can_be_voided(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    rows = lambda tp: [
        {"sov_line_id": sov[0]["id"], "this_period": tp, "stored": 0},
        {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
    ]
    a1 = _new_app(client, job["id"])
    assert _bill(client, a1["id"], rows(30000)).status_code == 200
    a2 = _new_app(client, job["id"])
    assert _bill(client, a2["id"], rows(10000)).status_code == 200

    r = client.post(f"/api/pay-applications/{a1['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "later_application"
    assert _gl(db_session, "1150") == Decimal("4000.00")  # nothing reversed

    assert client.post(f"/api/pay-applications/{a2['id']}/void").status_code == 200
    assert client.post(f"/api/pay-applications/{a1['id']}/void").status_code == 200
    assert _gl(db_session, "1150") == Decimal("0.00")
