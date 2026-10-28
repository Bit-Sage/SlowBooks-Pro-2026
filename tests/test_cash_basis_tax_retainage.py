"""Reports on a cash basis, step 4 (#483, docs/design/cash-basis.md): the
Sales Tax Liability report on a basis, retainage held on a bill, and payroll
staying on both bases."""

from datetime import date
from decimal import Decimal

from app.models.bills import Bill
from app.services import cash_basis
from app.services.accounting import all_lines, create_journal_entry
from tests.test_cash_basis import (
    _account_id,
    _amount,
    _assert_cash_invariant,
    _bill_payment,
    _bs,
    _bs_amount,
    _each_entry_balances_on_every_basis,
    _invoice,
    _payment,
    _pl,
    _strip_cash_lines,
    _vendor,
)
from tests.test_payroll import _create_employee

D = Decimal
PERIOD = "start_date=2026-07-01&end_date=2026-07-31"


# ---------------------------------------------------------------------------
# Sales Tax Liability on a basis
# ---------------------------------------------------------------------------


def _taxed_invoices(client, cust):
    """One taxed invoice nothing was paid on (tax 10.00) and one paid
    half (tax 20.00, of which 10.00 collected)."""
    unpaid = _invoice(client, cust, "2026-07-02", [{"rate": 100}], tax_rate=0.10)
    part = _invoice(client, cust, "2026-07-05", [{"rate": 200}], tax_rate=0.10)
    assert D(str(part["total"])) == D("220.00")
    _payment(client, cust, "2026-07-20", 110, [(part["id"], 110)])
    return unpaid, part


def _tax(client, basis=None, extra=""):
    qs = PERIOD + (f"&basis={basis}" if basis else "") + extra
    r = client.get(f"/api/reports/sales-tax?{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def test_sales_tax_cash_basis_counts_tax_on_money_received(
    client, seed_accounts, seed_customer
):
    _taxed_invoices(client, seed_customer.id)

    accrual = _tax(client, "accrual")
    assert accrual["basis"] == "accrual" and accrual["total_tax"] == 30.0
    assert accrual["ledger"]["balance"] == 30.0
    # the accrual reading is what it was without the parameter
    plain = _tax(client)
    assert {k: v for k, v in plain.items() if k != "basis"} == {
        k: v for k, v in accrual.items() if k != "basis"
    }

    cash = _tax(client, "cash")
    assert cash["basis"] == "cash"
    assert cash["total_tax"] == 10.0 and cash["tax_on_sales"] == 10.0
    assert cash["total_sales"] == 100.0 and cash["total_taxable"] == 100.0
    assert [i["date"] for i in cash["items"]] == ["2026-07-20"]
    assert cash["ledger"]["tax_posted"] == 10.0
    assert cash["ledger"]["difference"] == 0.0
    assert cash["ledger"]["balance"] == 10.0

    # a remittance is both: it takes the cash balance down, not the accrual's
    # tax collected
    r = client.post(
        "/api/reports/sales-tax/pay",
        json={
            "date": "2026-07-31",
            "amount": 4,
            "pay_from_account_id": _account_id(client, "1000"),
        },
    )
    assert r.status_code == 200, r.text
    cash = _tax(client, "cash")
    assert cash["ledger"]["payments"] == 4.0
    assert cash["ledger"]["tax_posted"] == 10.0
    assert cash["ledger"]["balance"] == 6.0
    assert _tax(client, "accrual")["ledger"]["balance"] == 26.0

    # 2200 on the cash Balance Sheet is the same figure
    assert _bs_amount(_bs(client, "2026-07-31", "cash"), "2200") == D("6.00")


def test_sales_tax_default_follows_the_company_basis(
    client, seed_accounts, seed_customer
):
    _taxed_invoices(client, seed_customer.id)
    assert _tax(client)["basis"] == "accrual"
    client.put("/api/settings", json={"report_basis": "cash"})
    by_default = _tax(client)
    assert by_default["basis"] == "cash" and by_default["total_tax"] == 10.0
    assert _tax(client, "accrual")["total_tax"] == 30.0
    r = client.get(f"/api/reports/sales-tax?{PERIOD}&basis=modified")
    assert r.status_code == 422


def test_sales_tax_cash_basis_ignores_a_voided_payment(
    client, seed_accounts, seed_customer
):
    cust = seed_customer.id
    inv = _invoice(client, cust, "2026-07-05", [{"rate": 200}], tax_rate=0.10)
    pay = _payment(client, cust, "2026-07-20", 220, [(inv["id"], 220)])
    assert _tax(client, "cash")["total_tax"] == 20.0
    assert client.post(f"/api/payments/{pay['id']}/void").status_code == 200
    cash = _tax(client, "cash")
    assert cash["total_tax"] == 0.0 and cash["ledger"]["balance"] == 0.0


def test_the_sales_tax_screen_offers_the_basis():
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "reports.js"
    ).read_text()
    body = js[js.index("async salesTax(prefill)") :]
    body = body[: body.index("view: 'sales-tax'")]
    assert "_basisOpts(prefill)" in body and "_basisQs(params)" in body


# ---------------------------------------------------------------------------
# Retainage held on a bill
# ---------------------------------------------------------------------------


def _retainage_bill(client, db_session, seed_accounts, vendor_id, held=100):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "date": "2026-06-01",
            "retainage_held": held,
            "lines": [
                {
                    "description": "Framing",
                    "amount": 700,
                    "account_id": seed_accounts["5100"].id,
                },
                {
                    "description": "Trim",
                    "amount": 300,
                    "account_id": seed_accounts["6000"].id,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _release(db_session, bill_id, day, amount, bank_number="1000"):
    """What a release of held retainage posts: Dr 2050 / Cr bank (both) with
    the bill's expense recognized beside it (cash). The app has no release
    route yet (progress billing), so the entry is written the way it will."""
    from app.models.accounts import Account

    bill = db_session.get(Bill, bill_id)
    held = db_session.query(Account).filter_by(account_number="2050").one()
    bank = db_session.query(Account).filter_by(account_number=bank_number).one()
    lines = [
        {"account_id": held.id, "debit": D(amount), "credit": D("0")},
        {"account_id": bank.id, "debit": D("0"), "credit": D(amount)},
    ] + cash_basis.retainage_release_cash_lines(db_session, bill, D(amount), "Release")
    txn = create_journal_entry(
        db_session, day, "Retainage release", lines, source_type="retainage_release"
    )
    db_session.commit()
    return txn


def _void_entry(db_session, txn, day):
    lines = [
        {
            "account_id": ln.account_id,
            "debit": ln.credit,
            "credit": ln.debit,
            "description": "VOID",
            "class_id": ln.class_id,
            "job_id": ln.job_id,
            "cost_code_id": ln.cost_code_id,
            "cost_type": ln.cost_type,
            "function": ln.function,
            "basis": ln.basis,
        }
        for ln in all_lines(db_session, txn.id)
    ]
    create_journal_entry(
        db_session, day, "VOID Retainage release", lines, source_type="retainage_void"
    )
    db_session.commit()


def test_retainage_is_not_expensed_until_it_is_released(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _vendor(client)
    bill = _retainage_bill(client, db_session, seed_accounts, vendor["id"])
    assert D(str(bill["balance_due"])) == D("900.00")
    _bill_payment(client, vendor["id"], "2026-06-10", 900, [(bill["id"], 900)])

    # net paid: nine tenths of each expense line, nothing held yet
    june, bs_june, _ = _assert_cash_invariant(client, "2026-06-01", "2026-06-30")
    assert _amount(june, "5100") == D("630.00")
    assert _amount(june, "6000") == D("270.00")
    assert D(str(june["net_income"])) == D("-900.00")
    assert _bs_amount(bs_june, "2000") == 0
    assert _bs_amount(bs_june, "2050") == 0
    # accrual: the whole bill, the held part on 2050
    acc = _pl(client, "2026-06-01", "2026-06-30")
    assert D(str(acc["net_income"])) == D("-1000.00")
    assert _bs_amount(_bs(client, "2026-06-30"), "2050") == D("100.00")

    # the release recognizes the held tenth
    txn = _release(db_session, bill["id"], date(2026, 7, 15), "100.00")
    july, bs_july, _ = _assert_cash_invariant(client, "2026-07-01", "2026-07-31")
    assert _amount(july, "5100") == D("70.00")
    assert _amount(july, "6000") == D("30.00")
    assert D(str(july["net_income"])) == D("-100.00")
    assert _bs_amount(bs_july, "2000") == 0 and _bs_amount(bs_july, "2050") == 0
    assert _bs_amount(_bs(client, "2026-07-31"), "2050") == 0  # accrual: released
    # over the two months the cash expense is the accrual expense
    whole = _pl(client, "2026-06-01", "2026-07-31", "cash")
    assert _amount(whole, "5100") == D("700.00")
    assert _amount(whole, "6000") == D("300.00")

    # voiding the release takes the cash expense back and the held part
    # returns to 2050 on the accrual view
    _void_entry(db_session, txn, date(2026, 7, 15))
    july, bs_july, _ = _assert_cash_invariant(client, "2026-07-01", "2026-07-31")
    assert D(str(july["net_income"])) == 0
    assert _bs_amount(bs_july, "2050") == 0
    assert _bs_amount(_bs(client, "2026-07-31"), "2050") == D("100.00")
    _each_entry_balances_on_every_basis(db_session)


def test_retainage_bill_paid_in_two_parts_and_the_backfill_is_idempotent(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _vendor(client)
    bill = _retainage_bill(client, db_session, seed_accounts, vendor["id"])
    _bill_payment(client, vendor["id"], "2026-06-10", 450, [(bill["id"], 450)])
    _bill_payment(client, vendor["id"], "2026-07-10", 450, [(bill["id"], 450)])
    june = _pl(client, "2026-06-01", "2026-06-30", "cash")
    july = _pl(client, "2026-07-01", "2026-07-31", "cash")
    assert D(str(june["net_income"])) == D("-450.00") == D(str(july["net_income"]))
    assert _amount(june, "5100") == D("315.00")
    assert _bs_amount(_bs(client, "2026-07-31", "cash"), "2000") == 0

    # a file from before the basis column: the backfill gives back the same
    _strip_cash_lines(db_session)
    assert (
        _pl(client, "2026-06-01", "2026-06-30", "cash")["net_income"]
        != june["net_income"]
    )
    first = cash_basis.backfill(db_session)
    assert first["bill_payments"] == 2 and first["failed"] == 0
    assert _pl(client, "2026-06-01", "2026-06-30", "cash") == june
    assert _pl(client, "2026-07-01", "2026-07-31", "cash") == july
    again = cash_basis.backfill(db_session)
    assert again["bill_payments"] == 0 and again["tagged"] == 0
    _each_entry_balances_on_every_basis(db_session)


# ---------------------------------------------------------------------------
# Payroll stays on both bases
# ---------------------------------------------------------------------------


def test_a_pay_run_reads_the_same_on_both_bases(client, db_session, seed_accounts):
    emp = _create_employee(
        client, pay_type="salary", pay_rate=78000, pay_frequency="biweekly"
    )
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": "2026-04-20",
            "stubs": [{"employee_id": emp["id"]}],
        },
    ).json()
    assert client.post(f"/api/payroll/{run['id']}/process").status_code == 200

    accrual = _pl(client, "2026-04-01", "2026-04-30", "accrual")
    cash = _pl(client, "2026-04-01", "2026-04-30", "cash")
    assert accrual["net_income"] == cash["net_income"] < 0
    assert accrual["expenses"] == cash["expenses"]

    bs_cash = _bs(client, "2026-04-30", "cash")
    bs_accrual = _bs(client, "2026-04-30", "accrual")
    assert bs_cash["liabilities"] == bs_accrual["liabilities"]
    payroll_liabilities = [
        ln for ln in bs_cash["liabilities"] if D(str(ln["amount"])) != 0
    ]
    assert payroll_liabilities, "withholdings stay on the cash Balance Sheet"
    assert bs_cash["total_assets"] == bs_cash["total_liabilities"] + (
        bs_cash["total_equity"]
    )
    _assert_cash_invariant(client, "2026-04-01", "2026-04-30")
