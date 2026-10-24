"""A register deposit splits like a check, with a fee line (#387, from a
bookkeeper's notes). A card processor's deposit is the gross it collected
less its fee, and only the net reaches the bank: the gross on an income
line, the fee on an expense line going the other way, the net posted to
the bank as one entry. The same rule lets a check carry a refund netted
against it. Before, a split line had to take the entry's sign (400), and
the posting would have made a negative credit of it anyway."""

from decimal import Decimal
from pathlib import Path

from app.models.transactions import Transaction, TransactionLine

ROOT = Path(__file__).resolve().parents[1]
PERIOD = {"start_date": "2026-03-01", "end_date": "2026-03-31"}


def _lines(db, txn_id):
    return {
        ln.account_id: (Decimal(str(ln.debit or 0)), Decimal(str(ln.credit or 0)))
        for ln in db.query(TransactionLine).filter(
            TransactionLine.transaction_id == txn_id
        )
    }


def _balanced(db, txn_id):
    lines = _lines(db, txn_id)
    return sum(d for d, _ in lines.values()) == sum(c for _, c in lines.values())


def _bank_balance(client, account_id):
    return Decimal(
        str(
            client.get(f"/api/banking/check-register?account_id={account_id}").json()[
                "balance"
            ]
        )
    )


def test_a_square_deposit_is_gross_income_less_the_fee(
    client, db_session, seed_accounts
):
    bank, income, fees = (
        seed_accounts["1000"],
        seed_accounts["4000"],
        seed_accounts["6200"],
    )
    before = _bank_balance(client, bank.id)
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank.id,
            "date": "2026-03-05",
            "amount": "97.00",
            "payee": "Square",
            "splits": [
                {"category_account_id": income.id, "amount": "100.00"},
                {
                    "category_account_id": fees.id,
                    "amount": "-3.00",
                    "description": "processing fee",
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    txn_id = r.json()["id"]
    assert Decimal(str(r.json()["amount"])) == Decimal("97.00")
    # CR Income 100.00, DR Processing Fees 3.00, DR Bank 97.00
    lines = _lines(db_session, txn_id)
    assert lines[income.id] == (Decimal("0"), Decimal("100.00"))
    assert lines[fees.id] == (Decimal("3.00"), Decimal("0"))
    assert lines[bank.id] == (Decimal("97.00"), Decimal("0"))
    assert len(lines) == 3 and _balanced(db_session, txn_id)
    txn = db_session.get(Transaction, txn_id)
    assert txn.source_type == "bank_entry" and txn.description == "Square"
    fee_line = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == txn_id,
            TransactionLine.account_id == fees.id,
        )
        .one()
    )
    assert fee_line.description == "processing fee"
    # the bank is up by the net
    assert _bank_balance(client, bank.id) - before == Decimal("97.00")
    # the P&L shows the gross and the fee, not the net
    pl = client.get("/api/reports/profit-loss", params=PERIOD).json()
    by_account = {
        row["account_id"]: Decimal(str(row["amount"]))
        for row in pl["income"] + pl["expenses"]
    }
    assert by_account[income.id] == Decimal("100.00")
    assert by_account[fees.id] == Decimal("3.00")
    assert Decimal(str(pl["total_income"])) == Decimal("100.00")
    assert Decimal(str(pl["net_income"])) == Decimal("97.00")


def test_the_lines_must_still_add_up_to_the_net(client, db_session, seed_accounts):
    bank = seed_accounts["1000"]
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank.id,
            "date": "2026-03-05",
            "amount": "97.00",
            "splits": [
                {"category_account_id": seed_accounts["4000"].id, "amount": "100.00"},
                {"category_account_id": seed_accounts["6200"].id, "amount": "-2.50"},
            ],
        },
    )
    assert r.status_code == 400 and "must agree" in r.json()["detail"]
    assert "97.50" in r.json()["detail"] and "97.00" in r.json()["detail"]
    assert db_session.query(Transaction).count() == 0


def test_a_zero_line_is_refused_not_dropped(client, db_session, seed_accounts):
    bank = seed_accounts["1000"]
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank.id,
            "date": "2026-03-05",
            "amount": "100.00",
            "splits": [
                {"category_account_id": seed_accounts["4000"].id, "amount": "100.00"},
                {"category_account_id": seed_accounts["6200"].id, "amount": "0.00"},
            ],
        },
    )
    assert r.status_code == 400 and "0.00" in r.json()["detail"]
    assert db_session.query(Transaction).count() == 0


def test_a_check_with_a_refund_netted_against_it_balances(
    client, db_session, seed_accounts
):
    bank, insurance, supplies = (
        seed_accounts["1000"],
        seed_accounts["6300"],
        seed_accounts["6000"],
    )
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank.id,
            "date": "2026-03-06",
            "amount": "-500.00",
            "check_number": "1042",
            "splits": [
                {"category_account_id": insurance.id, "amount": "-620.00"},
                {
                    "category_account_id": supplies.id,
                    "amount": "120.00",
                    "description": "credit for the returned order",
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    lines = _lines(db_session, r.json()["id"])
    assert lines[insurance.id] == (Decimal("620.00"), Decimal("0"))
    assert lines[supplies.id] == (Decimal("0"), Decimal("120.00"))
    assert lines[bank.id] == (Decimal("0"), Decimal("500.00"))
    assert _balanced(db_session, r.json()["id"])


def test_the_check_stub_lists_a_refund_as_a_negative_line(
    client, db_session, seed_accounts
):
    # The print stub lists every category line, a credit (refund) as a
    # negative amount, so the lines add up to the check (#387 review): a
    # 620.00 expense less a 120.00 refund is a 500.00 check, not a 620.00
    # stub with the refund dropped. This is register_check_data alone, no
    # PDF, so it runs without WeasyPrint.
    from app.routes.checks import register_check_data

    bank, insurance, supplies = (
        seed_accounts["1000"],
        seed_accounts["6300"],
        seed_accounts["6000"],
    )
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank.id,
            "date": "2026-03-06",
            "amount": "-500.00",
            "check_number": "1042",
            "splits": [
                {"category_account_id": insurance.id, "amount": "-620.00"},
                {
                    "category_account_id": supplies.id,
                    "amount": "120.00",
                    "description": "credit for the returned order",
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    data = register_check_data(db_session, r.json()["id"])
    by_name = {d["description"]: Decimal(str(d["amount"])) for d in data["details"]}
    insurance_line = next(v for k, v in by_name.items() if k.startswith("Insurance"))
    refund_line = next(
        v for k, v in by_name.items() if "returned order" in k or k.startswith("Adver")
    )
    assert insurance_line == Decimal("620.00")
    assert refund_line == Decimal("-120.00")
    assert sum(by_name.values()) == Decimal("500.00")
    assert Decimal(str(data["amount"])) == Decimal("500.00")


def test_a_split_check_still_posts_as_before(client, db_session, seed_accounts):
    bank = seed_accounts["1000"]
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank.id,
            "date": "2026-03-01",
            "amount": "-500.00",
            "splits": [
                {"category_account_id": seed_accounts["6300"].id, "amount": "-120.00"},
                {"category_account_id": seed_accounts["6000"].id, "amount": "-380.00"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    lines = _lines(db_session, r.json()["id"])
    assert lines[seed_accounts["6300"].id] == (Decimal("120.00"), Decimal("0"))
    assert lines[seed_accounts["6000"].id] == (Decimal("380.00"), Decimal("0"))
    assert lines[bank.id] == (Decimal("0"), Decimal("500.00"))


def test_a_bank_account_is_still_never_a_split_line_of_a_deposit(client, seed_accounts):
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": "2026-03-05",
            "amount": "97.00",
            "splits": [
                {"category_account_id": seed_accounts["1010"].id, "amount": "100.00"},
                {"category_account_id": seed_accounts["6200"].id, "amount": "-3.00"},
            ],
        },
    )
    assert r.status_code == 400 and "transfer" in r.json()["detail"]


def test_the_entry_dialog_splits_a_deposit_with_a_fee_line():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    form = js[js.index("async showEntryForm(") :]
    form = form[: form.index("async showTransferForm(")]
    # the split control is not gated on the sign of the amount
    assert "Split this entry…" in form
    assert "amount < 0" not in form and "amount > 0" not in form
    # a "Taken out" box per line makes it go the other way from the entry,
    # so a fee is typed as the positive number the statement shows
    assert '<th scope="col">Taken out</th>' in form
    assert 'class="split-fee" type="checkbox"' in form
    # the line follows the entry's direction, flipped by "Taken out"; only
    # the magnitude typed counts, so 620 and −620 read the same (#387 review)
    assert "const dir = row.querySelector('.split-fee').checked ? -sign : sign;" in form
    assert "return dir * mag;" in form
    assert "return amt < 0 ? -1 : 1;" in form
    # the running "left to place" and the payload both use the signed line
    assert "(BankingPage._splitAmount(r, sign) || 0)" in form
    assert "amount: BankingPage._splitAmount(r, sign).toFixed(2)," in form
    assert "each line takes the entry's sign" not in form
    assert "tick Taken out" in form
