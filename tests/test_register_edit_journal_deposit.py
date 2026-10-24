"""Journal entries and deposits open for editing from the register (#388,
the second half, after the bank-entry edit shipped in 2.27.0). A manual
journal entry opens the journal form filled in and PUT rewrites it in
place; a deposit opens the deposit form and PUT reposts it, moving which
undeposited payments it takes. Both follow the bank-entry rules: an entry a
completed reconciliation holds keeps its amount and date (400), a change
that moves an amount between accounts asks first (409 for the journal
entry), and the edit is on the same journal entry, its audit trail and
statement match following."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import func

from app.models.audit import AuditLog
from app.models.contacts import Customer
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------- helpers


def _je(client, seed_accounts, **over):
    """A hand-entered journal entry with a Checking line, so it shows in
    the register: CR Checking 100 / DR an expense 100."""
    body = {
        "date": "2026-09-02",
        "description": "Owner draw",
        "reference": "JE-1",
        "lines": [
            {
                "account_id": seed_accounts["1000"].id,
                "debit": 0,
                "credit": 100,
                "description": "cash out",
            },
            {
                "account_id": seed_accounts["6900"].id,
                "debit": 100,
                "credit": 0,
                "description": "expense",
            },
        ],
    }
    body.update(over)
    r = client.post("/api/journal", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _je_form(entry, **over):
    """The PUT body as the journal form sends it: the GET's fields back."""
    body = {
        "date": entry["date"],
        "description": entry["description"],
        "reference": entry["reference"] or None,
        "class_id": entry["class_id"],
        "job_id": entry["job_id"],
        "is_adjusting": entry["is_adjusting"],
        "lines": [
            {
                "account_id": ln["account_id"],
                "debit": str(ln["debit"]),
                "credit": str(ln["credit"]),
                "description": ln["description"],
            }
            for ln in entry["lines"]
        ],
    }
    body.update(over)
    return body


def _reconcile_lines(client, db, seed_accounts, line_ids, balance, day=30):
    r = client.post(
        "/api/banking/reconciliations",
        json={
            "account_id": seed_accounts["1000"].id,
            "statement_date": f"2026-09-{day:02d}",
            "statement_balance": balance,
        },
    )
    assert r.status_code == 201, r.text
    recon = r.json()
    for line_id in line_ids:
        r = client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{line_id}")
        assert r.status_code == 200, r.text
    r = client.post(f"/api/banking/reconciliations/{recon['id']}/complete")
    assert r.status_code == 200, r.text
    return recon


def _bank_line(db, txn_id, acct):
    db.expire_all()
    return (
        db.query(TransactionLine)
        .filter_by(transaction_id=txn_id, account_id=acct.id)
        .one()
    )


def _gl(db, acct):
    db.expire_all()
    dr, cr = (
        db.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == acct.id)
        .one()
    )
    return Decimal(str(dr)) - Decimal(str(cr))


# ---------------------------------------------------------- journal entry


def test_get_opens_a_journal_entry_as_editable(client, seed_accounts):
    posted = _je(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    assert e["editable"] is True
    assert e["reconciled_on"] is None and e["reconciled_note"] is None
    assert len(e["lines"]) == 2


def test_put_rewrites_a_journal_entry_on_the_same_id(client, db_session, seed_accounts):
    posted = _je(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    r = client.put(
        f"/api/journal/{posted['id']}",
        json=_je_form(e, description="Owner draw — July", reference="JE-9"),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == posted["id"]
    assert out["description"] == "Owner draw — July" and out["reference"] == "JE-9"
    # one journal entry, still two lines, still balanced
    assert out["total_debit"] == out["total_credit"] == 100.0
    assert db_session.query(Transaction).filter_by(source_type="manual").count() == 1


def test_a_void_journal_entry_is_not_edited(client, seed_accounts):
    posted = _je(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    assert client.post(f"/api/journal/{posted['id']}/void").status_code == 200
    r = client.put(f"/api/journal/{posted['id']}", json=_je_form(e, reference="X"))
    assert r.status_code == 400 and "void" in r.json()["detail"], r.text


def test_a_memo_fix_on_a_reconciled_journal_entry_is_allowed(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    posted = _je(client, seed_accounts)
    line = _bank_line(db_session, posted["id"], bank)
    recon = _reconcile_lines(client, db_session, seed_accounts, [line.id], "-100.00")
    e = client.get(f"/api/journal/{posted['id']}").json()
    assert e["reconciled_on"] == "2026-09-30"
    assert "amount and date stay as they are" in e["reconciled_note"]
    # a memo/reference change keeps every line's account and amount
    r = client.put(
        f"/api/journal/{posted['id']}",
        json=_je_form(e, description="Owner draw (corrected)", reference="JE-2"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["uncleared"] == []
    line = _bank_line(db_session, posted["id"], bank)
    assert line.cleared is True and line.reconciliation_id == recon["id"]


def test_an_amount_or_date_change_on_a_reconciled_journal_entry_is_refused(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    posted = _je(client, seed_accounts)
    line = _bank_line(db_session, posted["id"], bank)
    recon = _reconcile_lines(client, db_session, seed_accounts, [line.id], "-100.00")
    e = client.get(f"/api/journal/{posted['id']}").json()
    refused = (
        "This entry was reconciled on the Sep 30, 2026 statement, so its amount "
        "and date cannot be changed, just as it cannot be voided. Correct the "
        "difference with a new entry."
    )
    # the amount changed (both lines to 130) and, separately, the date
    bigger = _je_form(e, confirm_reconciled=True)
    bigger["lines"][0]["credit"] = "130"
    bigger["lines"][1]["debit"] = "130"
    for body in (bigger, _je_form(e, confirm_reconciled=True, date="2026-10-02")):
        r = client.put(f"/api/journal/{posted['id']}", json=body)
        assert r.status_code == 400 and r.json()["detail"] == refused, r.text
    # nothing moved; still on the statement
    line = _bank_line(db_session, posted["id"], bank)
    assert Decimal(str(line.credit)) == Decimal("100.00")
    assert line.reconciliation_id == recon["id"]


def test_an_account_change_on_a_reconciled_journal_entry_asks_first(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    posted = _je(client, seed_accounts)
    line = _bank_line(db_session, posted["id"], bank)
    recon = _reconcile_lines(client, db_session, seed_accounts, [line.id], "-100.00")
    e = client.get(f"/api/journal/{posted['id']}").json()
    # move the expense from 6900 to 6400 — same amounts, same date
    body = _je_form(e)
    body["lines"][1]["account_id"] = seed_accounts["6400"].id
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "reconciled"
    assert r.json()["detail"]["statement_date"] == "2026-09-30"
    # confirmed, it goes through and the Checking line keeps its mark
    r = client.put(
        f"/api/journal/{posted['id']}", json={**body, "confirm_reconciled": True}
    )
    assert r.status_code == 200, r.text
    line = _bank_line(db_session, posted["id"], bank)
    assert line.cleared is True and line.reconciliation_id == recon["id"]
    assert _gl(db_session, seed_accounts["6400"]) == Decimal("100.00")
    assert _gl(db_session, seed_accounts["6900"]) == Decimal("0")
    # the move within a reconciled period is in the trail, as the bank edit's
    marks = [
        a
        for a in db_session.query(AuditLog).filter_by(
            table_name="transactions", record_id=posted["id"], action="UPDATE"
        )
        if a.changed_fields == ["confirm_reconciled"]
    ]
    assert len(marks) == 1
    assert marks[0].old_values == {"reconciled_on": "2026-09-30"}
    assert marks[0].new_values["reconciliation_id"] == recon["id"]
    # the lines were replaced the way a bill's are on edit (#289)
    actions = {
        a.action
        for a in db_session.query(AuditLog).filter_by(table_name="transaction_lines")
    }
    assert {"DELETE", "INSERT"} <= actions


# --------------------------------------------------------------- deposits


def _customer(db):
    c = Customer(name="Acme Diner", is_active=True)
    db.add(c)
    db.commit()
    return c.id


def _receive(client, cid, amount, check=None):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-10",
            "amount": amount,
            "method": "Check",
            "check_number": check,
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pending(client):
    return client.get("/api/deposits/pending").json()


def _line_of(client, db, payment_id):
    txn_id = db.get(Payment, payment_id).transaction_id
    return next(p for p in _pending(client) if p["transaction_id"] == txn_id)


def _make_deposit(client, seed_accounts, pend_lines, ref="DEP-1", day=12):
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": f"2026-09-{day:02d}",
            "total": float(sum(Decimal(str(p["amount"])) for p in pend_lines)),
            "reference": ref,
            "line_ids": [p["transaction_line_id"] for p in pend_lines],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["transaction_id"]


def test_put_changes_a_deposits_date_and_reference(client, db_session, seed_accounts):
    cid = _customer(db_session)
    pay = _receive(client, cid, 300, check="4420")
    dep = _make_deposit(
        client, seed_accounts, [_line_of(client, db_session, pay["id"])]
    )
    r = client.put(
        f"/api/deposits/{dep}", json={"date": "2026-09-15", "reference": "DEP-9"}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["date"] == "2026-09-15" and out["reference"] == "DEP-9"
    assert out["amount"] == "300.00" and out["items"] == 1
    assert db_session.query(Transaction).filter_by(source_type="deposit").count() == 1


def test_a_receipt_swap_reposts_and_ties(client, db_session, seed_accounts):
    bank, uf = seed_accounts["1000"], seed_accounts["1200"]
    cid = _customer(db_session)
    first = _receive(client, cid, 300, check="4420")
    second = _receive(client, cid, 200, check="4421")
    l1 = _line_of(client, db_session, first["id"])
    l2 = _line_of(client, db_session, second["id"])
    dep = _make_deposit(client, seed_accounts, [l1, l2])
    assert _pending(client) == []
    assert _gl(db_session, bank) == Decimal("500.00")
    # drop the second payment from the deposit
    r = client.put(
        f"/api/deposits/{dep}", json={"line_ids": [l1["transaction_line_id"]]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == "300.00" and r.json()["items"] == 1
    # the register and the GL follow; Undeposited Funds nets to the 200 left
    assert _gl(db_session, bank) == Decimal("300.00")
    assert _gl(db_session, uf) == Decimal("200.00")
    # the dropped payment is back on the Make Deposits list, the other gone
    waiting = {p["transaction_id"] for p in _pending(client)}
    assert waiting == {db_session.get(Payment, second["id"]).transaction_id}
    reg = client.get(f"/api/banking/check-register?account_id={bank.id}").json()
    dep_rows = [e for e in reg["entries"] if e["source_type"] == "deposit"]
    assert [e["deposit"] for e in dep_rows] == [300.0]


def test_a_reconciled_deposit_keeps_its_amount_and_date(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    cid = _customer(db_session)
    first = _receive(client, cid, 300, check="4420")
    second = _receive(client, cid, 200, check="4421")
    l1 = _line_of(client, db_session, first["id"])
    l2 = _line_of(client, db_session, second["id"])
    dep = _make_deposit(client, seed_accounts, [l1, l2])
    bank_line = _bank_line(db_session, dep, bank)
    recon = _reconcile_lines(
        client, db_session, seed_accounts, [bank_line.id], "500.00"
    )
    # dropping a payment changes the amount: refused
    r = client.put(
        f"/api/deposits/{dep}", json={"line_ids": [l1["transaction_line_id"]]}
    )
    assert r.status_code == 400 and "cannot be changed" in r.json()["detail"], r.text
    # so does a new date
    r = client.put(f"/api/deposits/{dep}", json={"date": "2026-10-01"})
    assert r.status_code == 400, r.text
    # a reference fix still goes through
    r = client.put(f"/api/deposits/{dep}", json={"reference": "DEP-RE"})
    assert r.status_code == 200 and r.json()["reference"] == "DEP-RE", r.text
    line = _bank_line(db_session, dep, bank)
    assert line.cleared is True and line.reconciliation_id == recon["id"]
    assert _gl(db_session, bank) == Decimal("500.00")


def test_the_register_marks_journal_and_deposit_rows_editable(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    je = _je(client, seed_accounts)
    cid = _customer(db_session)
    pay = _receive(client, cid, 300, check="4420")
    dep = _make_deposit(
        client, seed_accounts, [_line_of(client, db_session, pay["id"])]
    )
    reg = client.get(f"/api/banking/check-register?account_id={bank.id}").json()
    by_id = {e["transaction_id"]: e for e in reg["entries"]}
    assert by_id[je["id"]]["editable"] is True
    assert by_id[je["id"]]["source_type"] == "manual"
    assert by_id[dep]["editable"] is True
    assert by_id[dep]["source_type"] == "deposit"


# ----------------------------------------------------------------- the pages


def test_the_journal_form_opens_for_editing():
    js = (ROOT / "app/static/js/journal.js").read_text(encoding="utf-8")
    assert "async showForm(entry = null)" in js
    assert "async editEntry(entryId)" in js
    assert "API.get(`/journal/${entryId}`)" in js
    assert "API.put(`/journal/${entryId}`, data)" in js
    assert "JournalPage.saveAnyway()" in js


def test_the_deposit_form_opens_for_editing():
    js = (ROOT / "app/static/js/deposits.js").read_text(encoding="utf-8")
    assert "async editDeposit(id)" in js
    assert "API.put(`/deposits/${id}`, body)" in js


def test_the_register_routes_journal_and_deposit_rows_to_their_editors():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "JournalPage.editEntry(${e.transaction_id})" in js
    assert "DepositsPage.editDeposit(${e.transaction_id})" in js
