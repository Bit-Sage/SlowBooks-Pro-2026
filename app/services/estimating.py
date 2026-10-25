# ============================================================================
# Construction estimating (#551) — the bid math, in one pure place.
#
# A contractor's estimate is more than a priced list: lines belong to bid
# packages, some are allowances or add/deduct alternates or unit prices, and
# the bid carries four markups that compound in order (general conditions,
# insurance, fee, bond). This module turns an estimate into the figures the
# route, the PDF and the tests all read, with no database of its own — it
# reads only the estimate's own lines and markup percents.
#
# The bid the customer sees is the sum of the LINE PRICES — what they pay.
# The markups are the contractor's own view of how a cost-based price was
# built; "Price from cost" writes them into the rates, and the totals panel
# shows cost → markups → target against the priced total so any drift is
# visible. Tax applies to the bid like any estimate.
# ============================================================================

from decimal import Decimal

from app.services.accounting import _q

# A package with no id groups here; a real package named "Unassigned" keeps
# its own id, so grouping is by id, never by this label.
UNASSIGNED = -1


def is_included_line(line) -> bool:
    """A line that is part of the bought scope: a base line, an allowance, or
    an ACCEPTED alternate. Unit-price lines (a rate card) and un-accepted
    alternates are not — they convert, seed and total as if absent."""
    kind = getattr(line, "line_kind", None) or "base"
    if kind == "unit_price":
        return False
    if kind == "alternate" and not getattr(line, "alternate_accepted", False):
        return False
    return True


def included_lines(estimate):
    """The bought-scope lines, in order (convert, accept and totals share
    this one predicate)."""
    return [ln for ln in estimate.lines if is_included_line(ln)]


# A line's cost is quantity × unit cost (the budget side); a line with no unit
# cost carries no cost. Rounded to the cent, as the budget seeder does.
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


# A base line or an accepted alternate is markup-able scope; allowances and
# everything excluded are not.
def _markupable(line) -> bool:
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

    The bid the customer pays is the sum of the included line PRICES:

        bid_total = Σ amount of base lines + accepted alternates + allowances

    The contractor's cost view sits beside it:

    - cost_subtotal — Σ qty×unit_cost over the markup-able lines (base +
      accepted alternates) that HAVE a unit cost; allowances never count.
    - the four markups compound in order on cost_subtotal (gc on it,
      insurance on cost+gc, fee on cost+gc+ins, bond on cost+gc+ins+fee).
    - markup_target — cost_subtotal + the four markups (what a cost-based
      price should foot to).
    - priced_direct — Σ amount of base / accepted-alternate lines priced with
      NO unit cost (a flat price, never marked up).
    - drift — bid_total − allowance_total − priced_direct − markup_target:
      the gap between the priced bid and the cost-built target, which
      "Price from cost" closes to the rounding.

    by_package carries price and cost per package (allowances contribute
    price only, never a unit cost); alternates and unit_prices list the lines
    held out of the bid so the customer can choose them.
    """
    bid_total = Decimal("0")
    allowance_total = Decimal("0")
    cost_subtotal = Decimal("0")
    priced_direct = Decimal("0")
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

        # From here the line is included (base, allowance, or accepted alt).
        bid_total += price
        if kind == "allowance":
            allowance_total += price
        else:  # base or accepted alternate
            if line.unit_cost is None:
                priced_direct += price
            else:
                cost_subtotal += cost

        key = line.trade_package_id if line.trade_package_id is not None else UNASSIGNED
        bucket = by_package.get(key)
        if bucket is None:
            bucket = {
                "package_id": line.trade_package_id,
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
        # An allowance's unit cost (if any was typed) is not a bid cost.
        bucket["cost"] += Decimal("0") if kind == "allowance" else cost
        bucket["price"] += price
        bucket["line_count"] += 1

    gc = _q(cost_subtotal * _pct(estimate.gc_pct))
    insurance = _q((cost_subtotal + gc) * _pct(estimate.insurance_pct))
    fee = _q((cost_subtotal + gc + insurance) * _pct(estimate.fee_pct))
    bond = _q((cost_subtotal + gc + insurance + fee) * _pct(estimate.bond_pct))
    markup_target = cost_subtotal + gc + insurance + fee + bond
    drift = bid_total - allowance_total - priced_direct - markup_target

    # Named packages by name, the unassigned bucket last.
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
        "markup_target": markup_target,
        "priced_direct": priced_direct,
        "allowance_total": allowance_total,
        "drift": drift,
        # The contract sum and schedule of values (#552) are PRE-TAX, so
        # bid_total is the pre-tax price sum; tax is added on each invoice.
        # bid_tax / bid_total_with_tax mirror the estimate's stored tax and
        # total (what the recompute put there over the included lines).
        "bid_total": bid_total,
        "bid_tax": _q(getattr(estimate, "tax_amount", 0) or 0),
        "bid_total_with_tax": _q(getattr(estimate, "total", 0) or 0),
        "by_package": packages,
        "alternates": alternates,
        "unit_prices": unit_prices,
    }
