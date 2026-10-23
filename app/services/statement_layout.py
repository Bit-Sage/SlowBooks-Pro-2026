"""How the statements lay their accounts out (#390, #416).

The figures come from the report functions; this module only decides the
headings a line sits under, so the screen, the PDF and the CSV of a
statement print the same layout from one place.

Balance Sheet groups (#390): Current / Fixed / Other Assets and Current /
Long Term Liabilities, by the account's detail type. A group with no line
is left out; Total Assets and Total Liabilities always print.
"""

from __future__ import annotations

from decimal import Decimal

# side -> ((heading, subtotal key, detail types in print order), ...)
BS_GROUPS = {
    "assets": (
        (
            "Current Assets",
            "total_current_assets",
            ("bank", "accounts_receivable", "other_current_asset"),
        ),
        ("Fixed Assets", "total_fixed_assets", ("fixed_asset",)),
        ("Other Assets", "total_other_assets", ("other_asset",)),
    ),
    "liabilities": (
        (
            "Current Liabilities",
            "total_current_liabilities",
            ("accounts_payable", "credit_card", "other_current_liability"),
        ),
        (
            "Long Term Liabilities",
            "total_long_term_liabilities",
            ("long_term_liability",),
        ),
    ),
}
BS_GROUP_TOTALS = tuple(key for groups in BS_GROUPS.values() for _h, key, _d in groups)


def _bs_rank(side: str, detail) -> tuple[int, int]:
    """(group, place inside the group) of a detail type on one side; one
    the side does not know (bad data) counts as the first group's last."""
    for g, (_heading, _key, details) in enumerate(BS_GROUPS[side]):
        if detail in details:
            return g, details.index(detail)
    return 0, len(BS_GROUPS[side][0][2])


def bs_groups(lines: list[dict], side: str) -> list[tuple[str, str, list[dict]]]:
    """[(heading, subtotal key, lines)] for the assets or the liabilities
    of a Balance Sheet, in print order. Inside a group the detail types
    print in QuickBooks' order (Bank, Accounts Receivable, Other Current
    Asset; Accounts Payable, Credit Card, Other Current Liability) and the
    lines of one detail type keep the order they came in (the `sort`
    choice). A group with no line is left out."""
    ranked = sorted(
        enumerate(lines), key=lambda p: (_bs_rank(side, p[1].get("detail_type")), p[0])
    )
    out = []
    for g, (heading, key, _details) in enumerate(BS_GROUPS[side]):
        members = [
            line
            for _i, line in ranked
            if _bs_rank(side, line.get("detail_type"))[0] == g
        ]
        if members:
            out.append((heading, key, members))
    return out


def bs_group_totals(lines: list[dict], side: str) -> dict:
    """Every subtotal of one side, zero for a group with no line."""
    totals = {key: Decimal("0") for _h, key, _d in BS_GROUPS[side]}
    for line in lines:
        g, _ = _bs_rank(side, line.get("detail_type"))
        key = BS_GROUPS[side][g][1]
        totals[key] += Decimal(str(line.get("amount") or 0))
    return {k: float(v) for k, v in totals.items()}
