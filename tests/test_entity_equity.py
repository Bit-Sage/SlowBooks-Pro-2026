"""Entity-aware equity seed (#488, step 2).

The chart is seeded before anyone has said what the company is. When the
entity type is chosen — the setup wizard or Settings → Company — the equity
block follows it, but only while the chart is still the untouched seed and
the books hold no posting. After that nothing is reseeded; the answer says
what to rename instead.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType
from app.seed.chart_of_accounts import (
    CHART_OF_ACCOUNTS,
    EQUITY_SEEDS,
    chart_for_entity,
    equity_family,
)
from app.services.accounting import create_journal_entry
from app.services.entity_equity import apply_entity_equity, current_family
from app.services.settings_service import entity_info

ROOT = Path(__file__).resolve().parent.parent

SOLE = {"3000": "Owner's Equity", "3100": "Owner's Draw", "3200": "Retained Earnings"}
PARTNERSHIP = {
    "3000": "Partner 1 Capital",
    "3010": "Partner 2 Capital",
    "3100": "Partner 1 Distributions",
    "3110": "Partner 2 Distributions",
    "3200": "Retained Earnings",
}
CORPORATION = {
    "3000": "Common Stock",
    "3010": "Additional Paid-in Capital",
    "3100": "Shareholder Distributions",
    "3200": "Retained Earnings",
}


def _equity(db) -> dict[str, str]:
    rows = db.query(Account).filter(Account.account_type == AccountType.EQUITY).all()
    return {a.account_number: a.name for a in rows}


# ---------------------------------------------------------------------------
# The seed tables
# ---------------------------------------------------------------------------


def test_the_chart_equity_block_is_the_sole_proprietor_seed():
    block = [
        (e["account_number"], e["name"])
        for e in CHART_OF_ACCOUNTS
        if e["account_type"] == "equity"
    ]
    assert block == EQUITY_SEEDS["sole_proprietor"]


def test_every_family_keeps_retained_earnings_and_never_seeds_3900():
    for family, seed in EQUITY_SEEDS.items():
        numbers = [n for n, _ in seed]
        assert "3200" in numbers, family
        assert "3900" not in numbers, family
        assert len(numbers) == len(set(numbers)), family


def test_chart_for_entity_swaps_only_the_equity_block():
    for family, expected in (
        ("sole_proprietor", SOLE),
        ("partnership", PARTNERSHIP),
        ("corporation", CORPORATION),
    ):
        chart = chart_for_entity(family)
        equity = {
            e["account_number"]: e["name"]
            for e in chart
            if e["account_type"] == "equity"
        }
        assert equity == expected, family
        others = [e for e in chart if e["account_type"] != "equity"]
        assert others == [e for e in CHART_OF_ACCOUNTS if e["account_type"] != "equity"]
    assert chart_for_entity("") == CHART_OF_ACCOUNTS


@pytest.mark.parametrize(
    "return_form, family",
    [
        ("Schedule C", "sole_proprietor"),
        ("Form 1065", "partnership"),
        ("Form 1120", "corporation"),
        ("Form 1120-S", "corporation"),
        ("Form 990", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_equity_family_follows_the_return(return_form, family):
    assert equity_family(return_form) == family


# ---------------------------------------------------------------------------
# The swap, through Settings → Company
# ---------------------------------------------------------------------------


def test_an_s_corp_gets_stock_apic_and_shareholder_distributions(
    client, db_session, seed_accounts
):
    before = {n: seed_accounts[n].id for n in ("3000", "3100", "3200")}
    r = client.put("/api/settings", json={"entity_type": "s_corp"})
    assert r.status_code == 200, r.text
    equity = r.json()["entity_equity"]
    assert equity["applied"] is True
    assert equity["family"] == "corporation"
    assert equity["note"] == ""
    assert equity["accounts"] == [
        "3000 Common Stock",
        "3010 Additional Paid-in Capital",
        "3100 Shareholder Distributions",
        "3200 Retained Earnings",
    ]
    db_session.expire_all()
    assert _equity(db_session) == CORPORATION
    # 3000 and 3100 are renamed in place: the ids a feature may hold survive
    for number, acct_id in before.items():
        acct = db_session.query(Account).filter(Account.account_number == number).one()
        assert acct.id == acct_id
        assert acct.is_system is True
    assert "Owner's Draw" not in _equity(db_session).values()


def test_a_partnership_gets_partner_capital_and_distributions(
    client, db_session, seed_accounts
):
    r = client.put("/api/settings", json={"entity_type": "general_partnership"})
    assert r.json()["entity_equity"]["applied"] is True
    db_session.expire_all()
    assert _equity(db_session) == PARTNERSHIP


def test_an_llc_follows_its_election(client, db_session, seed_accounts):
    # an LLC with no election: no return, so the chart waits
    r = client.put("/api/settings", json={"entity_type": "llc"})
    assert r.json()["entity_equity"] == {
        "family": "",
        "applied": False,
        "accounts": [],
        "note": "",
    }
    db_session.expire_all()
    assert _equity(db_session) == SOLE
    r = client.put("/api/settings", json={"llc_tax_election": "partnership"})
    assert r.json()["entity_equity"]["applied"] is True
    db_session.expire_all()
    assert _equity(db_session) == PARTNERSHIP


def test_a_change_of_mind_before_any_posting_swaps_again(
    client, db_session, seed_accounts
):
    client.put("/api/settings", json={"entity_type": "general_partnership"})
    r = client.put("/api/settings", json={"entity_type": "c_corp"})
    assert r.json()["entity_equity"]["applied"] is True
    db_session.expire_all()
    assert _equity(db_session) == CORPORATION  # 3110 removed, 3010 renamed
    r = client.put("/api/settings", json={"entity_type": "sole_proprietor"})
    assert r.json()["entity_equity"]["applied"] is True
    db_session.expire_all()
    assert _equity(db_session) == SOLE  # 3010 removed, the originals back
    # the same family again is a no-op, not an error
    r = client.put("/api/settings", json={"entity_type": "sole_proprietor"})
    assert r.json()["entity_equity"]["applied"] is False
    assert r.json()["entity_equity"]["note"] == ""


def test_a_company_with_postings_is_left_alone(client, db_session, seed_accounts):
    # an owner's investment: the one posting is on the equity block itself
    create_journal_entry(
        db_session,
        date(2026, 3, 1),
        "an opening deposit",
        [
            {
                "account_id": seed_accounts["1000"].id,
                "debit": Decimal("100"),
                "credit": 0,
            },
            {
                "account_id": seed_accounts["3000"].id,
                "debit": 0,
                "credit": Decimal("100"),
            },
        ],
    )
    db_session.commit()
    r = client.put("/api/settings", json={"entity_type": "s_corp"})
    assert r.status_code == 200
    equity = r.json()["entity_equity"]
    assert equity["applied"] is False
    assert "already have postings" in equity["note"]
    assert "Shareholder Distributions" in equity["note"]
    assert "Chart of Accounts" in equity["note"]
    db_session.expire_all()
    assert _equity(db_session) == SOLE
    # the entity type itself is saved all the same
    assert r.json()["entity"]["return_form"] == "Form 1120-S"


def test_an_edited_chart_is_left_alone(client, db_session, seed_accounts):
    seed_accounts["3100"].name = "Draws"
    db_session.commit()
    r = client.put("/api/settings", json={"entity_type": "s_corp"})
    equity = r.json()["entity_equity"]
    assert equity["applied"] is False
    assert "have been edited" in equity["note"]
    db_session.expire_all()
    assert _equity(db_session)["3100"] == "Draws"


def test_a_user_added_equity_account_is_left_alone(client, db_session, seed_accounts):
    db_session.add(
        Account(
            account_number="3050",
            name="Investor Capital",
            account_type=AccountType.EQUITY,
        )
    )
    db_session.commit()
    r = client.put("/api/settings", json={"entity_type": "c_corp"})
    assert r.json()["entity_equity"]["applied"] is False
    db_session.expire_all()
    assert _equity(db_session)["3050"] == "Investor Capital"
    assert _equity(db_session)["3100"] == "Owner's Draw"


def test_nonprofit_mode_keeps_its_own_seed(client, db_session, seed_accounts):
    r = client.put(
        "/api/settings", json={"company_type": "nonprofit", "entity_type": "c_corp"}
    )
    assert r.json()["entity_equity"]["applied"] is False
    assert r.json()["entity_equity"]["family"] == ""
    db_session.expire_all()
    equity = _equity(db_session)
    assert equity["3000"] == "Owner's Equity"
    assert "Common Stock" not in equity.values()


def test_a_save_without_the_entity_keys_reports_nothing(client, seed_accounts):
    r = client.put("/api/settings", json={"company_name": "Acme"})
    assert "entity_equity" not in r.json()


def test_current_family_reads_the_chart(db_session, seed_accounts):
    assert current_family(db_session) == "sole_proprietor"
    out = apply_entity_equity(db_session, entity_info({"entity_type": "s_corp"}))
    assert out["applied"] is True
    assert current_family(db_session) == "corporation"
    # 3900, created on demand, is not part of any family and does not spoil it
    db_session.add(
        Account(
            account_number="3900",
            name="Opening Balance Equity",
            account_type=AccountType.EQUITY,
            is_system=True,
        )
    )
    db_session.flush()
    assert current_family(db_session) == "corporation"


def test_an_empty_chart_is_no_family(db_session):
    assert current_family(db_session) == ""
    out = apply_entity_equity(db_session, entity_info({"entity_type": "s_corp"}))
    assert out["applied"] is False
    assert "edited" in out["note"]


# ---------------------------------------------------------------------------
# The setup wizard
# ---------------------------------------------------------------------------


def test_setup_seeds_the_equity_for_the_entity_chosen(
    unauthed_client, db_session, seed_accounts
):
    r = unauthed_client.post(
        "/api/auth/setup",
        json={
            "password": "test-password-123",
            "entity_type": "s_corp",
            # the wizard never sends one for a non-LLC, but an API caller may
            "llc_tax_election": "partnership",
        },
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _equity(db_session) == CORPORATION
    settings = unauthed_client.get("/api/settings").json()
    assert settings["entity_type"] == "s_corp"
    assert settings["llc_tax_election"] == ""


def test_setup_with_an_llc_election(unauthed_client, db_session, seed_accounts):
    r = unauthed_client.post(
        "/api/auth/setup",
        json={
            "password": "test-password-123",
            "entity_type": "llc",
            "llc_tax_election": "partnership",
        },
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _equity(db_session) == PARTNERSHIP
    assert (
        unauthed_client.get("/api/settings").json()["entity"]["return_form"]
        == "Form 1065"
    )


def test_setup_refuses_an_unknown_entity_type(unauthed_client, seed_accounts):
    r = unauthed_client.post(
        "/api/auth/setup",
        json={"password": "test-password-123", "entity_type": "banana"},
    )
    assert r.status_code == 422
    assert "entity_type must be one of" in r.json()["detail"]


def test_setup_without_an_entity_leaves_the_seed(
    unauthed_client, db_session, seed_accounts
):
    r = unauthed_client.post("/api/auth/setup", json={"password": "test-password-123"})
    assert r.status_code == 200
    db_session.expire_all()
    assert _equity(db_session) == SOLE
    assert unauthed_client.get("/api/settings").json()["entity_type"] == ""


# ---------------------------------------------------------------------------
# The UI copy (source assertions)
# ---------------------------------------------------------------------------


def test_the_wizard_offers_the_entity_and_folds_the_llc_election_in():
    js = (ROOT / "app/static/js/auth.js").read_text(encoding="utf-8")
    assert 'id="entity_choice"' in js
    assert '["llc:s_corp", "LLC taxed as an S corporation"]' in js
    assert '["", "Decide later"]' in js
    # the picker's value is split into the two settings the server knows
    assert "out.llc_tax_election = parts[1]" in js
    assert "Object.assign(out, entityPayload(entity.value))" in js


def test_settings_and_payroll_carry_the_s_corp_note():
    settings = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
    tax = (ROOT / "app/static/js/tax.js").read_text(encoding="utf-8")
    payroll = (ROOT / "app/static/js/payroll.js").read_text(encoding="utf-8")
    for src in (settings, tax):
        assert "reasonable compensation as W-2 wages through Payroll" in src
        assert "Shareholder Distributions (equity), never to the profit and loss" in src
    # shown under the entity picker for an S corp, or an LLC taxed as one
    assert 'id="s-corp-note"' in settings
    assert (
        "s.entity_type === 's_corp' || (s.entity_type === 'llc' && s.llc_tax_election === 's_corp')"
        in settings
    )
    # payroll says it once, from TaxPage's sentence, for Form 1120-S only
    assert "entity.return_form !== 'Form 1120-S'" in payroll
    assert "TaxPage.S_CORP_NOTE" in payroll
    # the entity picker's help no longer promises the equity "later"
    assert "(and, later, the equity accounts)" not in settings
    assert "nothing is reseeded" in settings
    # the save toast names what happened to the equity accounts
    assert "saved.entity_equity" in settings
    assert "equity.note" in settings
