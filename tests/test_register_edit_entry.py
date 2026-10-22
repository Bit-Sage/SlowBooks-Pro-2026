"""A register entry opens for editing, with a warning when it is reconciled
(#388, from a bookkeeper's notes): GET gives the + Entry form's shape, PUT
rewrites the entry in place on the same journal entry — lines, balances and
the audit trail following — refuses a void one, and asks first about one a
completed reconciliation holds."""

from decimal import Decimal
from pathlib import Path

from app.models.accounts import Account
from app.models.audit import AuditLog
from app.models.transactions import Transaction, TransactionLine

ROOT = Path(__file__).resolve().parents[1]


def _entry(client, seed_accounts, account="1000", **over):
    body = {
        "account_id": seed_accounts[account].id,
        "date": "2026-09-02",
        "amount": "-125.00",
        "category_account_id": seed_accounts["6900"].id,
        "payee": "City Water",
        "description": "August water",
        "check_number": "1042",
    }
    body.update(over)
    r = client.post("/api/banking/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _lines(db, txn_id):
    return {
        ln.account_id: (Decimal(str(ln.debit or 0)), Decimal(str(ln.credit or 0)))
        for ln in db.query(TransactionLine).filter(
            TransactionLine.transaction_id == txn_id
        )
    }


def _balance(db, acct):
    db.expire_all()
    return Decimal(str(db.get(Account, acct.id).balance or 0))


def _form(entry, **over):
    """The PUT body as the form sends it: the GET's fields back."""
    body = {
        "account_id": entry["account_id"],
        "date": entry["date"],
        "amount": entry["amount"],
        "category_account_id": entry["category_account_id"],
        "payee": entry["payee"] or None,
        "description": entry["description"] or None,
        "check_number": entry["reference"] or None,
        "class_id": entry["class_id"],
        "job_id": entry["job_id"],
    }
    if entry.get("splits"):
        body["splits"] = entry["splits"]
    body.update(over)
    return body


def _reconcile(client, seed_accounts, balance, day=30):
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
    s = client.get(f"/api/banking/reconciliations/{recon['id']}/transactions").json()
    for ln in s["transactions"]:
        client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{ln['id']}")
    r = client.post(f"/api/banking/reconciliations/{recon['id']}/complete")
    assert r.status_code == 200, r.text
    return recon


# ---------------------------------------------------------------- GET


def test_get_returns_the_entry_in_the_forms_shape(client, seed_accounts):
    posted = _entry(client, seed_accounts)
    r = client.get(f"/api/banking/entries/{posted['id']}")
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["id"] == posted["id"] and e["account_id"] == seed_accounts["1000"].id
    assert e["date"] == "2026-09-02" and e["amount"] == "-125.00"
    assert e["category_account_id"] == seed_accounts["6900"].id
    assert e["payee"] == "City Water" and e["description"] == "August water"
    assert e["reference"] == "1042" and e["status"] == "recorded"
    assert e["class_id"] is None and e["job_id"] is None
    assert e["splits"] is None and e["reconciled_on"] is None
    # no memo typed: the line carries the payee, the form's Memo stays blank
    blank = _entry(client, seed_accounts, description=None, check_number=None)
    e = client.get(f"/api/banking/entries/{blank['id']}").json()
    assert e["payee"] == "City Water" and e["description"] == ""
    assert client.get("/api/banking/entries/999999").status_code == 404


# ---------------------------------------------------------------- PUT


def test_put_rewrites_amount_and_category_on_the_same_entry(
    client, db_session, seed_accounts
):
    bank, old_cat, new_cat = (
        seed_accounts["1000"],
        seed_accounts["6900"],
        seed_accounts["6400"],
    )
    posted = _entry(client, seed_accounts)
    before = db_session.query(Transaction).count()
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(
            e,
            amount="-152.00",
            category_account_id=new_cat.id,
            payee="City Water Dept",
            description="August water, corrected",
            check_number="1043",
            date="2026-09-03",
        ),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == posted["id"] and out["amount"] == "-152.00"
    assert out["category_account_id"] == new_cat.id and out["uncleared"] == []
    assert out["note"] is None
    # the same journal entry, its lines rewritten, the balances following
    assert db_session.query(Transaction).count() == before
    txn = db_session.get(Transaction, posted["id"])
    db_session.refresh(txn)
    assert txn.source_type == "bank_entry" and txn.reference == "1043"
    assert txn.description == "City Water Dept" and str(txn.date) == "2026-09-03"
    assert _lines(db_session, txn.id) == {
        new_cat.id: (Decimal("152.00"), Decimal("0")),
        bank.id: (Decimal("0"), Decimal("152.00")),
    }
    assert _balance(db_session, old_cat) == 0
    assert _balance(db_session, new_cat) == Decimal("152.00")
    assert _balance(db_session, bank) == Decimal("-152.00")
    reg = client.get(f"/api/banking/check-register?account_id={bank.id}").json()
    [row] = reg["entries"]
    assert row["transaction_id"] == posted["id"] and row["payment"] == 152.0
    assert row["payee"] == "City Water Dept" and row["reference"] == "1043"
    assert row["editable"] is True and row["voidable"] is True
    # the trail: the header's UPDATE, each old line a DELETE, each new one an INSERT
    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.table_name == "transactions", AuditLog.record_id == txn.id)
        .all()
    )
    assert [a.action for a in rows] == ["INSERT", "UPDATE"]
    assert "description" in rows[-1].changed_fields
    lines = db_session.query(AuditLog).filter_by(table_name="transaction_lines").all()
    actions = sorted(a.action for a in lines)
    assert actions == ["DELETE", "DELETE", "INSERT", "INSERT", "INSERT", "INSERT"]
    deleted = [a for a in lines if a.action == "DELETE"]
    assert {a.old_values["account_id"] for a in deleted} == {old_cat.id, bank.id}
    assert {a.old_values["debit"] for a in deleted} == {"125.00", "0.00"}


def test_a_void_entry_is_not_edited(client, seed_accounts):
    posted = _entry(client, seed_accounts)
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert client.post(f"/api/banking/entries/{posted['id']}/void").status_code == 200
    r = client.put(f"/api/banking/entries/{posted['id']}", json=_form(e, amount="-5"))
    assert r.status_code == 400 and "void" in r.json()["detail"], r.text
    assert client.get(f"/api/banking/entries/{posted['id']}").json()["status"] == "void"
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()
    assert [x["editable"] for x in reg["entries"]] == [False, False]


def test_a_reconciled_entry_asks_first_and_keeps_its_mark_when_unchanged(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    _entry(
        client,
        seed_accounts,
        amount="1000",
        category_account_id=seed_accounts["4000"].id,
        payee="walk-in",
        date="2026-09-01",
        description=None,
        check_number=None,
    )
    posted = _entry(client, seed_accounts)
    recon = _reconcile(client, seed_accounts, "875.00")
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert e["reconciled_on"] == "2026-09-30"
    # the register still offers Edit on it (not Void)
    reg = client.get(f"/api/banking/check-register?account_id={bank.id}").json()
    row = next(x for x in reg["entries"] if x["transaction_id"] == posted["id"])
    assert row["editable"] is True and row["voidable"] is False
    # a typo in the payee: refused until the person says so
    body = _form(e, payee="City Water Dept")
    r = client.put(f"/api/banking/entries/{posted['id']}", json=body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {
        "code": "reconciled",
        "message": "This entry was reconciled on the Sep 30, 2026 statement. "
        "Changing it moves that reconciled balance.",
        "statement_date": "2026-09-30",
    }
    # nothing moved
    db_session.expire_all()
    assert db_session.get(Transaction, posted["id"]).description == "City Water"
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json={**body, "confirm_reconciled": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["payee"] == "City Water Dept" and r.json()["uncleared"] == []
    # the amount did not change: the bank line is still cleared on that statement
    db_session.expire_all()
    bank_line = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=posted["id"], account_id=bank.id)
        .one()
    )
    assert bank_line.cleared is True and bank_line.reconciliation_id == recon["id"]
    # the trail says the edit moved past a reconciliation
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
    assert marks[0].new_values["lines_uncleared"] == 0
    # a changed amount loses the mark, and the answer says so
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json={**body, "amount": "-130.00", "confirm_reconciled": True},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["uncleared"] == [
        {"account_id": bank.id, "account_name": "Checking", "amount": "-125.00"}
    ]
    assert out["note"] == (
        "The Checking line changed, so it is no longer marked cleared; tick it "
        "again on the next reconciliation."
    )
    db_session.expire_all()
    bank_line = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=posted["id"], account_id=bank.id)
        .one()
    )
    assert bank_line.cleared is False and bank_line.reconciliation_id is None
    assert Decimal(str(bank_line.credit)) == Decimal("130.00")
    reg = client.get(f"/api/banking/check-register?account_id={bank.id}").json()
    row = next(x for x in reg["entries"] if x["transaction_id"] == posted["id"])
    assert row["reconciliation_id"] is None and row["voidable"] is True


def test_the_closing_date_holds_for_the_old_date_and_the_new(client, seed_accounts):
    posted = _entry(client, seed_accounts)  # Sep 2
    later = _entry(client, seed_accounts, date="2026-10-02")
    assert (
        client.put("/api/settings", json={"closing_date": "2026-09-15"}).status_code
        == 200
    )
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    r = client.put(f"/api/banking/entries/{posted['id']}", json=_form(e, amount="-1"))
    assert r.status_code == 403, r.text
    e = client.get(f"/api/banking/entries/{later['id']}").json()
    # an open entry cannot be moved into the closed period either
    r = client.put(
        f"/api/banking/entries/{later['id']}", json=_form(e, date="2026-09-10")
    )
    assert r.status_code == 403, r.text
    r = client.put(
        f"/api/banking/entries/{later['id']}", json=_form(e, date="2026-10-03")
    )
    assert r.status_code == 200, r.text


def test_a_split_entry_round_trips(client, db_session, seed_accounts):
    bank, a, b, c = (
        seed_accounts["1000"],
        seed_accounts["2400"],
        seed_accounts["6200"],
        seed_accounts["6900"],
    )
    posted = _entry(
        client,
        seed_accounts,
        amount="-500.00",
        category_account_id=a.id,
        payee="Bank loan",
        description="September payment",
        check_number="1050",
        splits=[
            {"category_account_id": a.id, "amount": "-450.00", "description": None},
            {
                "category_account_id": b.id,
                "amount": "-50.00",
                "description": "interest",
            },
        ],
    )
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert e["amount"] == "-500.00" and e["category_account_id"] == a.id
    assert e["splits"] == [
        {"category_account_id": a.id, "amount": "-450.00", "description": None},
        {"category_account_id": b.id, "amount": "-50.00", "description": "interest"},
    ]
    # the interest was 45, not 50, and a bank fee was on the same check
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(
            e,
            amount="-505.00",
            splits=[
                {"category_account_id": a.id, "amount": "-450.00", "description": None},
                {
                    "category_account_id": b.id,
                    "amount": "-45.00",
                    "description": "interest",
                },
                {"category_account_id": c.id, "amount": "-10.00", "description": "fee"},
            ],
        ),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == posted["id"] and out["amount"] == "-505.00"
    assert [s["amount"] for s in out["splits"]] == ["-450.00", "-45.00", "-10.00"]
    assert _lines(db_session, posted["id"]) == {
        a.id: (Decimal("450.00"), Decimal("0")),
        b.id: (Decimal("45.00"), Decimal("0")),
        c.id: (Decimal("10.00"), Decimal("0")),
        bank.id: (Decimal("0"), Decimal("505.00")),
    }
    assert _balance(db_session, bank) == Decimal("-505.00")
    assert _balance(db_session, b) == Decimal("45.00")
    # the split rules hold on an edit as on a post
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(
            e,
            splits=[
                {"category_account_id": a.id, "amount": "-450.00", "description": None},
                {"category_account_id": b.id, "amount": "-40.00", "description": None},
            ],
        ),
    )
    assert r.status_code == 400 and "must agree" in r.json()["detail"]


def test_an_edit_that_would_make_a_transfer_or_move_accounts_is_refused(
    client, seed_accounts
):
    posted = _entry(client, seed_accounts)
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(e, category_account_id=seed_accounts["1010"].id),
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        "Savings is a bank or card account, so this would be a transfer. A "
        "transfer is its own entry: void this one and post the transfer."
    )
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(e, account_id=seed_accounts["1010"].id),
    )
    assert r.status_code == 400 and "void it and enter it there" in r.json()["detail"]
    # a transfer row is not a register entry
    t = client.post(
        "/api/transfers",
        json={
            "date": "2026-09-04",
            "from_account_id": seed_accounts["1000"].id,
            "to_account_id": seed_accounts["1010"].id,
            "amount": "20",
        },
    )
    assert t.status_code == 201, t.text
    assert client.get(f"/api/banking/entries/{t.json()['id']}").status_code == 404


# ---------------------------------------------------------------- the page


def test_the_register_row_opens_the_entry_for_editing():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "${BankingPage._printCheckButton(e)}${BankingPage._editButton(e, id)}" in js
    assert "if (!e.editable) return '';" in js
    assert (
        'onclick="BankingPage.editEntry(${e.transaction_id}, ${accountId})">Edit</button>'
        in js
    )
    assert "async showEntryForm(accountId, entry = null)" in js
    assert "if (entry) BankingPage._fillEntryForm(entry);" in js
    assert "title.textContent = 'Edit entry'" in js
    assert "API.get(`/banking/entries/${txnId}`)" in js
    assert "API.put(`/banking/entries/${entryId}`, data)" in js


def test_a_reconciled_refusal_is_asked_in_the_form_not_a_browser_dialog():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "err.status === 409 && err.detail && err.detail.code === 'reconciled'" in js
    assert "Change it anyway" in js and "id = 'entry-reconciled-warning'" in js
    assert "confirm_reconciled: true" in js
    start = js.index("async _putEntry(")
    end = js.index("async saveEntryAnyway(")
    assert "confirm(" not in js[start:end].replace("confirm_reconciled", "")


def test_the_edit_form_shows_a_fee_line_as_typed():
    """A deposit's fee line (#387) reopens as its own number with Taken out
    ticked, not as a negative amount."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/banking.js").read_text()
    fill = js[js.index("_fillEntryForm(entry) {") :]
    fill = fill[: fill.index("\n    },")]
    assert ".split-fee').checked = against" in fill
    assert "(against ? -part : part)" in fill
