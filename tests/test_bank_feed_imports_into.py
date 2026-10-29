"""A bank feed's "Imports into" list offers the chart's own bank accounts.

A support email (2.31.1): SimpleFIN connected, the bank's accounts listed, and
the "Imports into" dropdown beside each feed empty. It listed only bank feed
records (BankAccount), which exist once an account is added with + New Bank
Account on the Banking page; a company whose bank accounts are in the chart saw
an empty list and no word why (Mercury's section had the hint, SimpleFIN's did
not). Both lists now offer every bank account in the chart (SimpleFIN its
credit-card accounts too), and choosing one with no feed record makes it."""

from pathlib import Path

from app.models.accounts import Account, AccountType
from app.models.banking import BankAccount
from app.services.settings_service import set_setting

from tests.test_mercury import (  # noqa: F401  (fixtures)
    ACCOUNT,
    TOKEN,
    transport,
)
from tests.test_simplefin import ACCESS_URL

ROOT = Path(__file__).resolve().parent.parent


def _ledger(db, name, kind="bank", active=True):
    a = Account(
        name=name,
        bank_kind=kind,
        account_type=(
            AccountType.LIABILITY if kind == "credit_card" else AccountType.ASSET
        ),
        is_active=active,
    )
    db.add(a)
    db.commit()
    return a


def _feeds_for(db, account_id):
    return db.query(BankAccount).filter(BankAccount.account_id == account_id).all()


def test_simplefin_maps_a_chart_bank_account_with_no_feed_record(
    authed_client, db_session
):
    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    db_session.commit()
    checking = _ledger(db_session, "Operating Checking")
    card = _ledger(db_session, "Company Visa", kind="credit_card")
    assert _feeds_for(db_session, checking.id) == []

    r = authed_client.post(
        "/api/simplefin/map",
        json={"ledger_mapping": {"ACT-1": checking.id, "ACT-2": card.id}},
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    (feed,) = _feeds_for(db_session, checking.id)
    (card_feed,) = _feeds_for(db_session, card.id)
    assert feed.name == "Operating Checking" and feed.is_active
    assert r.json()["account_map"] == {"ACT-1": feed.id, "ACT-2": card_feed.id}

    # mapped again: the same feed record, never a second one
    r = authed_client.post(
        "/api/simplefin/map", json={"ledger_mapping": {"ACT-1": checking.id}}
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert len(_feeds_for(db_session, checking.id)) == 1
    assert r.json()["account_map"]["ACT-1"] == feed.id
    # an existing feed record still maps by its own id, as before
    r = authed_client.post("/api/simplefin/map", json={"mapping": {"ACT-1": feed.id}})
    assert r.status_code == 200 and r.json()["account_map"] == {"ACT-1": feed.id}


def test_simplefin_refuses_an_account_that_is_not_a_bank_account(
    authed_client, db_session, seed_accounts
):
    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    db_session.commit()
    income = seed_accounts["4000"]
    r = authed_client.post(
        "/api/simplefin/map", json={"ledger_mapping": {"ACT-1": income.id}}
    )
    assert r.status_code == 400, r.text
    assert "is not a bank or credit-card account" in r.json()["detail"]
    retired = _ledger(db_session, "Old Savings", active=False)
    r = authed_client.post(
        "/api/simplefin/map", json={"ledger_mapping": {"ACT-1": retired.id}}
    )
    assert r.status_code == 400 and "inactive" in r.json()["detail"]
    db_session.expire_all()
    assert _feeds_for(db_session, income.id) == []
    assert _feeds_for(db_session, retired.id) == []


def test_mercury_maps_a_chart_bank_account_and_refuses_a_card(
    client, db_session, seed_accounts, transport  # noqa: F811
):
    assert (
        client.post("/api/mercury/connect", json={"api_token": TOKEN}).status_code
        == 200
    )
    checking = _ledger(db_session, "Mercury Checking")
    r = client.post("/api/mercury/map", json={"ledger_mapping": {ACCOUNT: checking.id}})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    (feed,) = _feeds_for(db_session, checking.id)
    assert r.json()["account_map"] == {ACCOUNT: feed.id}

    card = _ledger(db_session, "Mercury Card", kind="credit_card")
    r = client.post("/api/mercury/map", json={"ledger_mapping": {ACCOUNT: card.id}})
    assert r.status_code == 400
    assert "bank accounts only" in r.json()["detail"]
    db_session.expire_all()
    assert _feeds_for(db_session, card.id) == []


def test_the_page_offers_the_chart_and_says_what_to_do_when_it_is_empty():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "_importOptions(rows, feeds, mapped, kinds) {" in js
    assert 'opts.push(`<option value="acct:${a.account_id}"' in js
    assert (
        "BankingPage._importOptions(rows, feeds, feed.account_map[a.id], ['bank'])"
        in js
    )
    assert (
        "BankingPage._importOptions(\n                rows, feeds, feed.account_map[sfId], ['bank', 'credit_card'])"
        in js
    )
    # both saves send a chart account as ledger_mapping
    assert (
        "await API.post('/simplefin/map', { mapping, ledger_mapping, reversed });" in js
    )
    assert "await API.post('/mercury/map', { mapping, ledger_mapping });" in js
    # the hint for a chart with no bank account, on both sections
    assert js.count("BankingPage._noBankAccountHint(") == 2
    assert "Add one with <strong>+ New Bank Account</strong> above" in js
