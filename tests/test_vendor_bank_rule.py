"""A bank-feed rule set from the vendor form (#493): the vendor's
"Bank feed: lines from this payee post to <default account>" makes or
updates a bank rule with its vendor_id and default expense account; the
rules engine honours vendor_id (the vendor's name matches, and a line it
categorises is added for that vendor); the Bank Rule form shows the vendor."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.bank_rules import BankRule
from app.models.banking import BankTransaction
from app.models.transactions import Transaction
from app.services.ofx_import import import_transactions

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def feed(client, seed_accounts):
    r = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _vendor(client, seed_accounts, **extra):
    r = client.post(
        "/api/vendors",
        json={
            "name": "NT Suites",
            "default_expense_account_id": seed_accounts["6000"].id,
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _import(db, feed, *rows):
    import_transactions(
        db,
        feed["id"],
        [
            {
                "fitid": f"R{i}-{p}",
                "date": date(2026, 9, d),
                "amount": Decimal(a),
                "payee": p,
                "memo": "",
            }
            for i, (d, a, p) in enumerate(rows)
        ],
    )
    return {bt.payee: bt for bt in db.query(BankTransaction).all()}


def test_the_vendor_form_makes_the_rule_and_the_response_says_so(
    client, db_session, seed_accounts
):
    v = _vendor(client, seed_accounts, bank_feed_rule=True)
    assert v["bank_feed_rule"] is True and v["bank_rule_id"]
    [rule] = db_session.query(BankRule).all()
    assert (rule.vendor_id, rule.account_id, rule.pattern, rule.rule_type) == (
        v["id"],
        seed_accounts["6000"].id,
        "NT Suites",
        "contains",
    )
    assert rule.is_active
    # the Bank Rules page names the vendor and the account
    [listed] = client.get("/api/bank-rules").json()
    assert listed["vendor_name"] == "NT Suites"
    assert listed["account_name"].endswith(seed_accounts["6000"].name)


def test_changing_the_default_account_updates_the_rule_and_unticking_turns_it_off(
    client, db_session, seed_accounts
):
    v = _vendor(client, seed_accounts, bank_feed_rule=True)
    r = client.put(
        f"/api/vendors/{v['id']}",
        json={
            "default_expense_account_id": seed_accounts["5000"].id,
            "bank_feed_rule": True,
        },
    )
    assert r.status_code == 200, r.text
    [rule] = db_session.query(BankRule).all()
    db_session.refresh(rule)
    assert rule.account_id == seed_accounts["5000"].id
    # a pattern tuned on the Bank Rules page is kept
    client.put(f"/api/bank-rules/{rule.id}", json={"pattern": "NT SUITE"})
    client.put(f"/api/vendors/{v['id']}", json={"bank_feed_rule": True})
    db_session.refresh(rule)
    assert rule.pattern == "NT SUITE"
    # unticked: off, not deleted; ticked again: the same rule back on
    r = client.put(f"/api/vendors/{v['id']}", json={"bank_feed_rule": False})
    assert r.json()["bank_feed_rule"] is False
    db_session.refresh(rule)
    assert not rule.is_active
    r = client.put(f"/api/vendors/{v['id']}", json={"bank_feed_rule": True})
    assert r.json()["bank_rule_id"] == rule.id
    assert db_session.query(BankRule).count() == 1
    # left out, the rule is left as it is
    client.put(f"/api/vendors/{v['id']}", json={"phone": "555-0100"})
    db_session.refresh(rule)
    assert rule.is_active


def test_the_rule_needs_a_default_account(client, db_session, seed_accounts):
    r = client.post("/api/vendors", json={"name": "No Account", "bank_feed_rule": True})
    assert r.status_code == 400 and "default" in r.json()["detail"]
    assert client.get("/api/vendors").json() == []
    v = client.post("/api/vendors", json={"name": "No Account"}).json()
    assert v["bank_feed_rule"] is False
    r = client.put(f"/api/vendors/{v['id']}", json={"bank_feed_rule": True})
    assert r.status_code == 400
    assert db_session.query(BankRule).count() == 0


def test_a_vendor_rule_categorises_and_adds_the_line_for_its_vendor(
    client, db_session, seed_accounts, feed
):
    v = _vendor(client, seed_accounts, bank_feed_rule=True)
    lines = _import(
        db_session,
        feed,
        (3, "-450.00", "POS NT SUITES #1234 CHICAGO"),
        (4, "-20.00", "OTHER SHOP"),
    )
    nt = lines["POS NT SUITES #1234 CHICAGO"]
    db_session.refresh(nt)
    assert nt.category_account_id == seed_accounts["6000"].id
    assert lines["OTHER SHOP"].category_account_id is None
    out = client.post(f"/api/banking/accounts/{feed['id']}/feed/add-all").json()
    assert out["added"] == 1 and out["without_vendor"] == []
    db_session.refresh(nt)
    txn = db_session.query(Transaction).get(nt.transaction_id)
    assert txn.vendor_id == v["id"]
    assert [
        e["transaction_id"]
        for e in client.get(f"/api/vendors/{v['id']}/bank-entries").json()
    ] == [txn.id]


def test_a_rule_naming_a_vendor_matches_the_vendors_name_after_a_rename(
    client, db_session, seed_accounts, feed
):
    landlord = client.post("/api/vendors", json={"name": "Oak Street LLC"}).json()
    r = client.post(
        "/api/bank-rules",
        json={
            "name": "Rent",
            "pattern": "ACH RENT 0042",
            "rule_type": "contains",
            "account_id": seed_accounts["6000"].id,
            "vendor_id": landlord["id"],
        },
    )
    assert r.status_code == 201 and r.json()["vendor_name"] == "Oak Street LLC"
    lines = _import(
        db_session,
        feed,
        (1, "-1800.00", "ACH RENT 0042"),
        (2, "-5.00", "oak street llc"),
    )
    for bt in lines.values():
        db_session.refresh(bt)
        assert bt.category_account_id == seed_accounts["6000"].id
    added = client.post(
        f"/api/banking/transactions/{lines['ACH RENT 0042'].id}/add"
    ).json()
    assert added["vendor_name"] == "Oak Street LLC"
    # an explicit vendor still wins over the rule's
    other = client.post("/api/vendors", json={"name": "Someone Else"}).json()
    added = client.post(
        f"/api/banking/transactions/{lines['oak street llc'].id}/add",
        json={"vendor_id": other["id"]},
    ).json()
    assert added["vendor_name"] == "Someone Else"


def test_a_rule_with_an_unknown_vendor_or_account_is_refused(client, seed_accounts):
    r = client.post(
        "/api/bank-rules",
        json={"name": "x", "pattern": "x", "vendor_id": 99999},
    )
    assert r.status_code == 404
    r = client.post(
        "/api/bank-rules",
        json={"name": "x", "pattern": "x", "account_id": 99999},
    )
    assert r.status_code == 404


def test_csv_vendor_import_is_untouched_by_the_form_only_field(client):
    from app.schemas.contacts import VendorCreate

    assert (
        "bank_feed_rule" not in VendorCreate(name="A", bank_feed_rule=True).model_dump()
    )


def test_the_forms_carry_the_fields():
    vendors = (ROOT / "app/static/js/vendors.js").read_text(encoding="utf-8")
    assert 'name="bank_feed_rule"' in vendors and "bankRuleAccountChanged" in vendors
    rules = (ROOT / "app/static/js/bank_rules.js").read_text(encoding="utf-8")
    assert 'name="vendor_id"' in rules and "r.vendor_name" in rules
