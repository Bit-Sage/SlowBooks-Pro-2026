"""The Balance Sheet rounds its current-period net income and total equity
to the cent, the same way the Profit & Loss does (PR #413 review, point 6).

Before, the P&L rounded its totals but the Balance Sheet summed the account
floats straight into net income and equity, so 0.1 + 0.2 produced
0.30000000000000004 in equity beside the P&L's 0.30. The two reports are now
rounded the same way and agree to the cent.
"""

from datetime import date
from decimal import Decimal

from app.services.accounting import create_journal_entry

PERIOD = {"start_date": "2026-01-01", "end_date": "2026-12-31"}
AS_OF = {"as_of_date": "2026-12-31"}


def _post_income(db, seed_accounts, splits):
    """Credit each income account its share, debit checking the total."""
    total = sum(splits.values())
    lines = [
        {
            "account_id": seed_accounts["1000"].id,
            "debit": total,
            "credit": Decimal("0"),
            "description": "cash in",
        }
    ]
    for number, amount in splits.items():
        lines.append(
            {
                "account_id": seed_accounts[number].id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": number,
            }
        )
    create_journal_entry(db, date(2026, 3, 1), "income", lines)
    db.commit()


def test_balance_sheet_net_income_matches_the_pl_and_carries_no_float_noise(
    client, db_session, seed_accounts
):
    # Two income accounts whose floats sum with error: 0.1 + 0.2 == 0.30000000000000004.
    _post_income(
        db_session, seed_accounts, {"4000": Decimal("0.10"), "4100": Decimal("0.20")}
    )

    pl = client.get("/api/reports/profit-loss", params=PERIOD).json()
    bs = client.get("/api/reports/balance-sheet", params=AS_OF).json()

    assert pl["net_income"] == 0.3
    net_rows = [e for e in bs["equity"] if "Net Income" in e["account_name"]]
    assert len(net_rows) == 1
    # The equity line and the totals are rounded, so they equal the P&L exactly
    # (before the fix these were 0.30000000000000004).
    assert net_rows[0]["amount"] == pl["net_income"]
    assert bs["total_equity"] == pl["net_income"]
    assert repr(bs["total_equity"]) == "0.3"
