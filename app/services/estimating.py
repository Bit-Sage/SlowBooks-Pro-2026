# ============================================================================
# Construction estimating (#551) — the bid math, in one pure place.
#
# A contractor's estimate is more than a priced list: lines belong to bid
# packages, some are allowances or add/deduct alternates or unit prices, and
# the bid carries four markups that compound in order (general conditions,
# insurance, fee, bond). This module turns an estimate into the figures the
# route, the PDF and the tests all read, with no database of its own — it
# reads only the estimate's own lines and markup percents.
# ============================================================================

from decimal import Decimal

from app.services.accounting import _q


# A line's cost is quantity × unit cost (the budget side); a line with no
# unit cost carries no cost. Rounded to the cent, as the budget seeder does.
def line_cost(line) -> Decimal:
    if line.unit_cost is None:
        return Decimal("0")
    qty = Decimal(str(line.quantity or 0))
    return _q(qty * Decimal(str(line.unit_cost)))


# A line's price is its stored amount (quantity × rate), already to the cent.
def line_price(line) -> Decimal:
    return _q(line.amount or 0)


def _pct(value) -> Decimal:
    """A stored percent (5.5 = 5.5%) as a fraction."""
    return Decimal(str(value or 0)) / Decimal("100")


# An accepted alternate folds into the base scope; a plain base line always
# does. Allowances, unit prices and un-accepted alternates do not.
def _counts_as_base(line) -> bool:
    kind = line.line_kind or "base"
    return kind == "base" or (kind == "alternate" and line.alternate_accepted)


def reprice_factor(estimate) -> Decimal:
    """The multiplier that turns a line's unit cost into a marked-up rate:
    (1+gc)(1+ins)(1+fee)(1+bond). Unrounded — the caller rounds the rate."""
    one = Decimal("1")
    return (
        (one + _pct(estimate.gc_pct))
        * (one + _pct(estimate.insurance_pct))
        * (one + _pct(estimate.fee_pct))
        * (one + _pct(estimate.bond_pct))
    )


def estimate_totals(estimate) -> dict:
    """Every bid figure for an estimate, from its own lines and markups.

    - cost_subtotal — the base cost the markups compound on (base lines plus
      accepted alternates).
    - the four markups, each rounded to the cent, compounding in order:
      gc on the base cost, insurance on base+gc, fee on base+gc+ins, bond on
      base+gc+ins+fee. Allowances carry no markup.
    - allowance_total — the allowance lines, added to the bid after markup.
    - bid_total — base cost + the four markups + allowances.
    - by_package — cost, price and line count per bid package (lines with no
      package group under "Unassigned"), for the base-scope lines.
    - alternates / unit_prices — the lines held out of the totals, listed so
      the customer can choose them.

    Unit-price lines and un-accepted alternates are in no total.
    """
    cost_subtotal = Decimal("0")
    allowance_total = Decimal("0")
    by_package: dict = {}
    alternates: list = []
    unit_prices: list = []

    for line in estimate.lines:
        kind = line.line_kind or "base"
        cost = line_cost(line)
        price = line_price(line)

        if kind == "unit_price":
            unit_prices.append(
                {
                    "id": line.id,
                    "description": line.description,
                    "unit": line.unit,
                    "rate": _q(line.rate or 0),
                    "quantity": Decimal(str(line.quantity or 0)),
                }
            )
            continue

        if kind == "alternate":
            alternates.append(
                {
                    "id": line.id,
                    "description": line.description,
                    "amount": price,
                    "accepted": bool(line.alternate_accepted),
                }
            )
            if not line.alternate_accepted:
                continue

        if kind == "allowance":
            allowance_total += price
        elif _counts_as_base(line):
            cost_subtotal += cost

        # by_package covers the lines that count toward the bid.
        key = line.trade_package_id
        bucket = by_package.get(key)
        if bucket is None:
            bucket = {
                "package_id": key,
                "package_name": (
                    line.trade_package.name
                    if line.trade_package is not None
                    else "Unassigned"
                ),
                "cost": Decimal("0"),
                "price": Decimal("0"),
                "line_count": 0,
            }
            by_package[key] = bucket
        bucket["cost"] += cost
        bucket["price"] += price
        bucket["line_count"] += 1

    gc = _q(cost_subtotal * _pct(estimate.gc_pct))
    insurance = _q((cost_subtotal + gc) * _pct(estimate.insurance_pct))
    fee = _q((cost_subtotal + gc + insurance) * _pct(estimate.fee_pct))
    bond = _q((cost_subtotal + gc + insurance + fee) * _pct(estimate.bond_pct))
    bid_total = cost_subtotal + gc + insurance + fee + bond + allowance_total

    # Named packages by name, Unassigned last.
    packages = sorted(
        by_package.values(),
        key=lambda b: (b["package_id"] is None, b["package_name"].lower()),
    )

    return {
        "cost_subtotal": cost_subtotal,
        "gc_pct": Decimal(str(estimate.gc_pct or 0)),
        "insurance_pct": Decimal(str(estimate.insurance_pct or 0)),
        "fee_pct": Decimal(str(estimate.fee_pct or 0)),
        "bond_pct": Decimal(str(estimate.bond_pct or 0)),
        "gc": gc,
        "insurance": insurance,
        "fee": fee,
        "bond": bond,
        "allowance_total": allowance_total,
        "bid_total": bid_total,
        "by_package": packages,
        "alternates": alternates,
        "unit_prices": unit_prices,
    }
