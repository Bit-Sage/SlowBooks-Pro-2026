"""A deposit that is really several gifts (#606): each income line of a
register or bank-feed deposit carries the donor (customer) it came from and
its own class, the donor reports count each line for its donor, the
register names them, and an old entry can have a donor added in place."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.banking import BankAccount
from app.models.transactions import TransactionLine
from app.services.donor_documents import collect_gifts
from app.services.ofx_import import import_transactions

ROOT = Path(__file__).resolve().parents[1]


def _customer(client, name, **over):
    r = client.post("/api/customers", json={"name": name, **over})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _class(client, name):
    r = client.post("/api/classes", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _two_gifts(seed_accounts, ann, ben, **over):
    """$1,500 into Checking: two $750 gifts."""
    body = {
        "account_id": seed_accounts["1000"].id,
        "date": "2026-03-05",
        "amount": "1500.00",
        "payee": "Sunday offering",
        "splits": [
            {
                "category_account_id": seed_accounts["4000"].id,
                "amount": "750.00",
                "customer_id": ann,
            },
            {
                "category_account_id": seed_accounts["4000"].id,
                "amount": "750.00",
                "customer_id": ben,
            },
        ],
    }
    body.update(over)
    return body


def _post(client, body):
    r = client.post("/api/banking/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _income_lines(db, txn_id, seed_accounts):
    db.expire_all()
    return (
        db.query(TransactionLine)
        .filter_by(transaction_id=txn_id, account_id=seed_accounts["4000"].id)
        .order_by(TransactionLine.id)
        .all()
    )


def test_a_split_deposit_keeps_a_donor_and_class_per_line(
    client, db_session, seed_accounts
):
    ann, ben = _customer(client, "Ann Lee"), _customer(client, "Ben Ortiz")
    fund_a, fund_b = _class(client, "Annual Fund"), _class(client, "Building")
    body = _two_gifts(seed_accounts, ann, ben)
    body["splits"][0]["class_id"] = fund_a
    body["splits"][1]["class_id"] = fund_b
    posted = _post(client, body)

    lines = _income_lines(db_session, posted["id"], seed_accounts)
    assert [(ln.customer_id, ln.class_id) for ln in lines] == [
        (ann, fund_a),
        (ben, fund_b),
    ]
    got = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert [(s["customer_id"], s["class_id"]) for s in got["splits"]] == [
        (ann, fund_a),
        (ben, fund_b),
    ]


def test_the_entry_name_is_the_default_for_lines_that_leave_it_blank(
    client, db_session, seed_accounts
):
    ann, ben = _customer(client, "Ann Lee"), _customer(client, "Ben Ortiz")
    body = _two_gifts(seed_accounts, ann, ben, customer_id=ann)
    body["splits"][1].pop("customer_id")
    posted = _post(client, body)
    lines = _income_lines(db_session, posted["id"], seed_accounts)
    assert [ln.customer_id for ln in lines] == [ann, ann]
    # a line's own name beats the entry's
    body["splits"][1]["customer_id"] = ben
    posted = _post(client, body)
    lines = _income_lines(db_session, posted["id"], seed_accounts)
    assert [ln.customer_id for ln in lines] == [ann, ben]


def test_a_plain_deposit_names_its_income_line(client, db_session, seed_accounts):
    ann = _customer(client, "Ann Lee")
    posted = _post(
        client,
        {
            "account_id": seed_accounts["1000"].id,
            "date": "2026-03-05",
            "amount": "200.00",
            "category_account_id": seed_accounts["4000"].id,
            "customer_id": ann,
        },
    )
    assert posted["customer_id"] == ann and posted["customer_name"] == "Ann Lee"
    [line] = _income_lines(db_session, posted["id"], seed_accounts)
    assert line.customer_id == ann
    bank = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=posted["id"], account_id=seed_accounts["1000"].id)
        .one()
    )
    assert bank.customer_id is None


def test_an_unknown_or_inactive_name_is_refused(client, seed_accounts):
    ann, ben = _customer(client, "Ann Lee"), _customer(client, "Ben Ortiz")
    assert (
        client.put(f"/api/customers/{ben}", json={"is_active": False}).status_code
        == 200
    )
    for bad, status in ((99999, 404), (ben, 422)):
        r = client.post(
            "/api/banking/transactions", json=_two_gifts(seed_accounts, ann, bad)
        )
        assert r.status_code == status, r.text
        r = client.post(
            "/api/banking/transactions",
            json={
                "account_id": seed_accounts["1000"].id,
                "date": "2026-03-05",
                "amount": "5.00",
                "category_account_id": seed_accounts["4000"].id,
                "customer_id": bad,
            },
        )
        assert r.status_code == status, r.text


def test_an_old_entry_gets_a_donor_added_in_place(client, db_session, seed_accounts):
    ann = _customer(client, "Ann Lee")
    posted = _post(
        client,
        {
            "account_id": seed_accounts["1000"].id,
            "date": "2025-03-05",
            "amount": "300.00",
            "category_account_id": seed_accounts["4000"].id,
        },
    )
    r = client.put(f"/api/banking/entries/{posted['id']}", json={"customer_id": ann})
    assert r.status_code == 200, r.text
    assert r.json()["customer_id"] == ann and Decimal(str(r.json()["amount"])) == 300
    [line] = _income_lines(db_session, posted["id"], seed_accounts)
    assert line.customer_id == ann
    # a later edit that leaves the name out keeps it; null clears it
    client.put(f"/api/banking/entries/{posted['id']}", json={"payee": "Gift"})
    assert _income_lines(db_session, posted["id"], seed_accounts)[0].customer_id == ann
    client.put(f"/api/banking/entries/{posted['id']}", json={"customer_id": None})
    assert _income_lines(db_session, posted["id"], seed_accounts)[0].customer_id is None


def test_a_reconciled_entry_takes_a_donor_without_moving_money(
    client, db_session, seed_accounts
):
    ann, ben = _customer(client, "Ann Lee"), _customer(client, "Ben Ortiz")
    bank = seed_accounts["1000"]
    body = _two_gifts(seed_accounts, ann, ben)
    for part in body["splits"]:
        part.pop("customer_id")
    posted = _post(client, body)
    bank_line = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=posted["id"], account_id=bank.id)
        .one()
    )
    r = client.post(
        "/api/banking/reconciliations",
        json={
            "account_id": bank.id,
            "statement_date": "2026-03-31",
            "statement_balance": "1500.00",
        },
    )
    assert r.status_code == 201, r.text
    recon = r.json()
    client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{bank_line.id}")
    assert (
        client.post(f"/api/banking/reconciliations/{recon['id']}/complete").status_code
        == 200
    )

    got = client.get(f"/api/banking/entries/{posted['id']}").json()
    got["splits"][0]["customer_id"] = ann
    got["splits"][1]["customer_id"] = ben
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json={"splits": [{k: v for k, v in s.items()} for s in got["splits"]]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["uncleared"] == []
    db_session.expire_all()
    assert [
        ln.customer_id for ln in _income_lines(db_session, posted["id"], seed_accounts)
    ] == [ann, ben]
    held = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=posted["id"], account_id=bank.id)
        .one()
    )
    assert held.cleared is True and held.reconciliation_id == recon["id"]
    assert held.debit == Decimal("1500.00")


def test_one_deposit_of_two_gifts_counts_for_each_donor_and_a_void_drops_out(
    client, seed_accounts, db_session
):
    ann, ben = _customer(client, "Ann Lee"), _customer(client, "Ben Ortiz")
    posted = _post(client, _two_gifts(seed_accounts, ann, ben))
    for donor in (ann, ben):
        gifts = collect_gifts(db_session, donor, 2026)
        assert [g["amount"] for g in gifts["cash"]] == [Decimal("750.00")]
        assert gifts["cash"][0]["kind"] == "bank-entry"
        assert gifts["totals"]["amount"] == Decimal("750.00")
        assert gifts["totals"]["deductible"] == Decimal("750.00")
        assert collect_gifts(db_session, donor, 2025)["cash"] == []
    # and the year-end statements print for both
    one = client.get(f"/api/donors/{ann}/giving-statement/pdf?year=2026")
    assert one.status_code == 200 and one.content[:5] == b"%PDF-"
    both = client.get("/api/donors/giving-statements/pdf?year=2026")
    assert both.status_code == 200 and both.content[:5] == b"%PDF-"

    assert client.post(f"/api/banking/entries/{posted['id']}/void").status_code == 200
    db_session.expire_all()
    for donor in (ann, ben):
        assert collect_gifts(db_session, donor, 2026)["cash"] == []


def test_an_expense_line_with_a_name_is_not_a_gift(client, db_session, seed_accounts):
    ann = _customer(client, "Ann Lee")
    _post(
        client,
        {
            "account_id": seed_accounts["1000"].id,
            "date": "2026-03-05",
            "amount": "-40.00",
            "category_account_id": seed_accounts["6000"].id,
            "customer_id": ann,
        },
    )
    assert collect_gifts(db_session, ann, 2026)["cash"] == []


def test_a_bank_feed_row_books_as_a_deposit_split_across_two_donors(
    client, db_session, seed_accounts
):
    ann, ben = _customer(client, "Ann Lee"), _customer(client, "Ben Ortiz")
    feed = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    ).json()
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": "F1",
                "date": date(2026, 3, 5),
                "amount": Decimal("1500.00"),
                "payee": "DEPOSIT",
                "memo": "",
            }
        ],
    )
    bt = db_session.query(BankAccount).one().transactions[0]
    income = seed_accounts["4000"].id
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={
            "splits": [
                {"category_account_id": income, "amount": "750", "customer_id": ann},
                {"category_account_id": income, "amount": "750", "customer_id": ben},
            ]
        },
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    lines = (
        db_session.query(TransactionLine)
        .filter_by(account_id=income)
        .order_by(TransactionLine.id)
        .all()
    )
    assert [(ln.customer_id, ln.credit) for ln in lines] == [
        (ann, Decimal("750.00")),
        (ben, Decimal("750.00")),
    ]
    assert len(collect_gifts(db_session, ben, 2026)["cash"]) == 1


def test_the_register_names_the_donor_or_counts_them(client, seed_accounts):
    ann, ben = _customer(client, "Ann Lee"), _customer(client, "Ben Ortiz")
    bank = seed_accounts["1000"].id
    one = _post(
        client,
        {
            "account_id": bank,
            "date": "2026-03-04",
            "amount": "50.00",
            "category_account_id": seed_accounts["4000"].id,
            "customer_id": ann,
        },
    )
    two = _post(client, _two_gifts(seed_accounts, ann, ben))
    typed = _post(
        client,
        {
            "account_id": bank,
            "date": "2026-03-06",
            "amount": "5.00",
            "payee": "Cash box",
            "category_account_id": seed_accounts["4000"].id,
            "customer_id": ann,
        },
    )
    plain = _post(
        client,
        {
            "account_id": bank,
            "date": "2026-03-07",
            "amount": "6.00",
            "category_account_id": seed_accounts["4000"].id,
        },
    )
    rows = {
        r["transaction_id"]: r
        for r in client.get(f"/api/banking/check-register?account_id={bank}").json()[
            "entries"
        ]
    }
    assert rows[one["id"]]["payee"] == "Ann Lee"
    # a typed payee is the person's own words; the donors count when none is
    assert rows[two["id"]]["payee"] == "Sunday offering"
    assert rows[typed["id"]]["payee"] == "Cash box"
    assert rows[plain["id"]]["payee"] == ""
    blank = _post(client, _two_gifts(seed_accounts, ann, ben, payee=None))
    rows = client.get(f"/api/banking/check-register?account_id={bank}").json()[
        "entries"
    ]
    assert (
        next(r for r in rows if r["transaction_id"] == blank["id"])["payee"]
        == "Split (2 names)"
    )


def test_the_entry_dialog_gives_each_split_line_a_name_and_a_class():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    form = js[js.index("async showEntryForm(") :]
    form = form[: form.index("async showTransferForm(")]
    # the table has a name column, and a class column where the journal form has one
    assert "<th scope=\"col\">${T('Customer')}</th>${Nonprofit.classHeadHtml()}" in form
    row = js[js.index("    addSplitRow() {") :]
    row = row[: row.index("\n    },\n")]
    assert "BankingPage.nameCellHtml('split-name'" in row
    assert "Nonprofit.classCellHtml('split', null)" in row
    # the plain entry has its own name picker, offering "+ New Customer"
    assert 'id="entry-name-group"' in form and "nameSelectHtml('entry-name'" in form
    assert "+ ${T('New Customer')}" in js
    # a line sends its name, and a class only with its picker (as the journal form does)
    lines = js[js.index("    _splitLines(form) {") :]
    lines = lines[: lines.index("\n    },\n")]
    assert "customer_id: BankingPage.nameFromRow(r)" in lines
    assert "r.querySelector('.split-fund')" in lines
    # an edit opens each line on its own name and class
    fill = js[js.index("    _fillEntryForm(entry) {") :]
    fill = fill[: fill.index("\n    },\n")]
    assert (
        "BankingPage._setName(row.querySelector('.split-name'), sp.customer_id" in fill
    )
    assert "BankingPage._setSplitClass(row, sp.class_id)" in fill


def _gift_line_ids(db, txn_id, seed_accounts):
    return [ln.id for ln in _income_lines(db, txn_id, seed_accounts)]


def test_a_split_deposit_line_gets_an_acknowledgment_letter(
    client, db_session, seed_accounts
):
    ann, ben = _customer(client, "Ann Lee"), _customer(client, "Ben Ortiz")
    posted = _post(client, _two_gifts(seed_accounts, ann, ben, check_number="D-9"))
    line_ids = _gift_line_ids(db_session, posted["id"], seed_accounts)
    base = "/api/donors/gifts/bank-entry"
    r = client.get(f"{base}/{line_ids[0]}/acknowledgment/preview")
    assert r.json() == {"eligible": True, "amount": 750.0, "reason": None}
    pdf = client.get(f"{base}/{line_ids[1]}/acknowledgment/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    # the register entry offers one letter per donor line
    got = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert [g["line_id"] for g in got["gift_letters"]] == line_ids
    # gifts from collect_gifts carry the id the letter route needs
    [gift] = collect_gifts(db_session, ann, 2026)["cash"]
    assert (gift["kind"], gift["id"], gift["number"]) == (
        "bank-entry",
        line_ids[0],
        "D-9",
    )
    assert client.get(f"{base}/999999/acknowledgment/preview").status_code == 404

    client.post(f"/api/banking/entries/{posted['id']}/void")
    r = client.get(f"{base}/{line_ids[0]}/acknowledgment/preview").json()
    assert r["eligible"] is False and "void" in r["reason"]
    assert client.get(f"{base}/{line_ids[0]}/acknowledgment/pdf").status_code == 400


def test_an_expense_or_unnamed_line_gets_no_letter(client, db_session, seed_accounts):
    ann = _customer(client, "Ann Lee")
    bank = seed_accounts["1000"].id
    spend = _post(
        client,
        {
            "account_id": bank,
            "date": "2026-03-05",
            "amount": "-40.00",
            "category_account_id": seed_accounts["6000"].id,
            "customer_id": ann,
        },
    )
    unnamed = _post(
        client,
        {
            "account_id": bank,
            "date": "2026-03-05",
            "amount": "40.00",
            "category_account_id": seed_accounts["4000"].id,
        },
    )
    for txn_id, acct in ((spend["id"], "6000"), (unnamed["id"], "4000")):
        line = (
            db_session.query(TransactionLine)
            .filter_by(transaction_id=txn_id, account_id=seed_accounts[acct].id)
            .one()
        )
        r = client.get(f"/api/donors/gifts/bank-entry/{line.id}/acknowledgment/preview")
        assert r.status_code == 200 and r.json()["eligible"] is False
        r = client.get(f"/api/donors/gifts/bank-entry/{line.id}/acknowledgment/pdf")
        assert r.status_code == 400
