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


# ── Sub-accounts under their parents (#416) ──────────────────────────────
# The P&L and the Statement of Activities print a parent's family together:
#
#     Travel                       heading (the parent, even with no figure)
#         Hotel           100
#         Flight          200
#         Direct postings to Travel  50   (the parent's own lines, if any)
#     Total Travel        350
#
# Each line is printed once, so the families' totals and the loose lines
# add up to the section's total. The flat presentation (the CSV) keeps the
# full "Travel: Hotel" path instead.

# Deeper than any real chart; a parent chain this long is bad data (a loop
# the walk has not closed), and the account prints at the top level.
MAX_DEPTH = 64


def account_chart(db=None, index: dict | None = None) -> dict:
    """{id: {number, name, parent_id}} for every account: from `index`, the
    chart a report has already read ({id: (number, name, parent_id, …)},
    financial._chart_index), else one query."""
    from app.models.accounts import Account

    if index is not None:
        return {
            aid: {"number": row[0], "name": row[1], "parent_id": row[2]}
            for aid, row in index.items()
        }
    return {
        a.id: {
            "number": a.account_number or "",
            "name": a.name or "",
            "parent_id": a.parent_id,
        }
        for a in db.query(
            Account.id, Account.account_number, Account.name, Account.parent_id
        )
    }


def account_path(chart: dict, account_id) -> str:
    """ "Travel: Hotel": the names from the top parent down, stopping at a
    loop or MAX_DEPTH so bad data cannot hang a report."""
    names, seen, cur = [], set(), account_id
    while cur in chart and cur not in seen and len(seen) < MAX_DEPTH:
        seen.add(cur)
        names.append(chart[cur]["name"])
        cur = chart[cur]["parent_id"]
    return ": ".join(reversed(names))


def _parent_in_section(chart: dict, node, belongs) -> int | None:
    """The parent a node prints under: its own parent when that account
    prints in the same section and the node is not on a parent loop (a
    walk up from the parent that comes back to the node, or runs past
    MAX_DEPTH); otherwise None and the node prints at the top level."""
    parent = chart.get(node, {}).get("parent_id")
    if parent is None or parent == node or parent not in chart or not belongs(parent):
        return None
    cur, steps = parent, 0
    while cur is not None:
        if cur == node or steps > MAX_DEPTH:
            return None
        cur = chart.get(cur, {}).get("parent_id")
        steps += 1
        if cur is not None and cur not in chart:
            break
    return parent


def family_rows(
    lines: list[dict],
    chart: dict,
    belongs,
    keys: tuple,
    order,
    numbered: bool = True,
) -> list[dict]:
    """A section's lines as printed rows, sub-accounts beneath their
    parents. `belongs(id)` says whether an account prints in this section
    (a parent of another type or detail type does not gather the line);
    `keys` are the figures to add up (amount, prior_amount, …); `order(id)`
    the accounts' order. Each row is a dict with `kind`:

    - "account": a line with no sub-account lines under it (the line's own
      keys, plus `depth` and `label`);
    - "heading": a parent with sub-account lines, no figures;
    - "direct": the parent's own line, one level in, labelled "Direct
      postings to …";
    - "total": the family's total, "Total …", at the heading's level.

    A line with no account (a computed one) prints last, as it came."""
    by_id = {i["account_id"]: i for i in lines if i.get("account_id") in chart}
    loose = [i for i in lines if i.get("account_id") not in by_id]

    parent_of: dict = {}
    todo = list(by_id)
    while todo:
        node = todo.pop()
        if node in parent_of:
            continue
        parent_of[node] = _parent_in_section(chart, node, belongs)
        if parent_of[node] is not None:
            todo.append(parent_of[node])

    children: dict = {}
    roots = []
    for node, parent in parent_of.items():
        (children.setdefault(parent, []) if parent is not None else roots).append(node)
    for kids in children.values():
        kids.sort(key=order)
    roots.sort(key=order)

    def leaf(node) -> str:
        entry = chart[node]
        return (
            f"{entry['number']} - {entry['name']}"
            if numbered and entry["number"]
            else entry["name"]
        )

    def top(node) -> str:
        """At the top level the full path, as the flat P&L printed it: an
        account whose parent prints elsewhere is still told apart."""
        line = by_id.get(node)
        if line and line.get("account_label"):
            return line["account_label"]
        path = account_path(chart, node)
        number = chart[node]["number"]
        return f"{number} - {path}" if numbered and number else path

    def sums(node) -> dict:
        total = {k: Decimal("0") for k in keys}
        stack, seen = [node], set()
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            line = by_id.get(n)
            if line:
                for k in keys:
                    total[k] += Decimal(str(line.get(k) or 0))
            stack.extend(children.get(n, ()))
        return {k: float(v) for k, v in total.items()}

    rows: list[dict] = []

    def emit(node, depth):
        label = top(node) if depth == 0 else leaf(node)
        line = by_id.get(node)
        kids = children.get(node)
        if not kids:
            rows.append({**line, "kind": "account", "depth": depth, "label": label})
            return
        rows.append(
            {"kind": "heading", "depth": depth, "label": label, "account_id": node}
        )
        for kid in kids:
            emit(kid, depth + 1)
        if line:
            rows.append(
                {
                    **line,
                    "kind": "direct",
                    "depth": depth + 1,
                    "label": f"Direct postings to {chart[node]['name']}",
                }
            )
        rows.append(
            {
                "kind": "total",
                "depth": depth,
                "label": f"Total {label}",
                "account_id": node,
                **sums(node),
            }
        )

    for root in roots:
        emit(root, 0)
    for line in loose:
        rows.append(
            {
                **line,
                "kind": "account",
                "depth": 0,
                "label": line.get("account_label") or line.get("account_name") or "",
            }
        )
    return rows
