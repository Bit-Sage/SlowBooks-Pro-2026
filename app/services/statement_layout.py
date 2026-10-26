"""How the statements lay their accounts out (#390, #416).

The figures come from the report functions; this module only decides the
headings a line sits under, so the screen, the PDF and the CSV of a
statement print the same layout from one place.

Balance Sheet groups (#390): Current / Fixed / Other Assets and Current /
Long Term Liabilities, by the account's detail type. A group with no line
is left out; Total Assets and Total Liabilities always print. A sub-account
still at its default detail type prints in its parent's group
(bs_print_details, 2.28.0).
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


def bs_print_details(db=None, index: dict | None = None) -> dict:
    """{account id: the detail type it groups under on the Balance Sheet},
    for the sub-accounts that print with their parent rather than by their
    own detail type (2.28.0, the owner's call). Display only: the account's
    stored detail type, its cash flow placement and every figure stay; a
    group's total changes only by the moved line's amount.

    A sub-account moves to its parent's group when its detail type is still
    its default — what the code gives an account nobody typed: the type's
    default for its number (`default_detail_type`), the number and name
    rule a new account takes (2.28.0), or the one the upgrade's backfill
    gave it (`legacy_placement.backfill_detail_type`, e.g. an Other Asset
    for a "1520 Trucks") — and its parent is of the same base type and not a
    bank, card, Accounts Receivable or Payable account (under those a
    sub-account is set on its own, QuickBooks' rule). The parent's own group
    is where it prints, itself moved or not, walking up; a parent loop or a
    chain past MAX_DEPTH stops the walk. A sub-account whose detail type was
    set to something else keeps its own group.

    `index` is the chart a report has read ({id: (number, name, parent_id,
    account_type, detail_type)}, financial._chart_index), else one query."""
    from app.models.accounts import (
        BASE_TYPE_OF,
        PINNED_DETAIL_TYPES,
        Account,
        AccountType,
        DetailType,
        _enum,
        default_detail_type,
    )
    from app.services import legacy_placement

    if index is None:
        index = {
            a.id: (
                a.account_number or "",
                a.name or "",
                a.parent_id,
                a.account_type,
                a.detail_type,
            )
            for a in db.query(
                Account.id,
                Account.account_number,
                Account.name,
                Account.parent_id,
                Account.account_type,
                Account.detail_type,
            )
        }

    def base(detail):
        try:
            return BASE_TYPE_OF[DetailType(detail)]
        except ValueError:
            return None

    def still_default(row) -> bool:
        number, name, _parent, atype, detail = row
        if not detail or detail in PINNED_DETAIL_TYPES:
            return False
        try:
            atype = _enum(AccountType, atype)
            defaults = {
                default_detail_type(atype, None, number).value,
                default_detail_type(atype, None, number, name=name).value,
                legacy_placement.backfill_detail_type(atype, name, number),
            }
        except (KeyError, ValueError):
            return False
        return detail in defaults

    memo: dict = {}

    def printed(aid, depth=0):
        """The detail type `aid` groups under; None for an unknown id."""
        if aid in memo:
            return memo[aid]
        row = index.get(aid)
        if row is None:
            return None
        own = row[4]
        memo[aid] = own  # a loop back to here reads the account's own
        parent = row[2]
        if (
            parent is not None
            and parent != aid
            and parent in index
            and depth < MAX_DEPTH
            and still_default(row)
        ):
            up = printed(parent, depth + 1)
            if (
                up
                and up != own
                and up not in PINNED_DETAIL_TYPES
                and index[parent][4] not in PINNED_DETAIL_TYPES
                and base(up) is not None
                and base(up) == base(own)
            ):
                memo[aid] = up
        return memo[aid]

    out = {}
    for aid, row in index.items():
        detail = printed(aid)
        if detail and detail != row[4]:
            out[aid] = detail
    return out


def mark_bs_lines(lines: list[dict], placed: dict) -> list[dict]:
    """Note on each Balance Sheet line the detail type it groups under when
    that is not its own (`group_detail_type`, from bs_print_details); the
    line's `detail_type` stays the account's."""
    for line in lines:
        detail = placed.get(line.get("account_id"))
        if detail:
            line["group_detail_type"] = detail
    return lines


def _group_detail(line: dict):
    return line.get("group_detail_type") or line.get("detail_type")


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
        enumerate(lines), key=lambda p: (_bs_rank(side, _group_detail(p[1])), p[0])
    )
    out = []
    for g, (heading, key, _details) in enumerate(BS_GROUPS[side]):
        members = [
            line for _i, line in ranked if _bs_rank(side, _group_detail(line))[0] == g
        ]
        if members:
            out.append((heading, key, members))
    return out


def bs_group_of(account_type, detail) -> tuple[str | None, tuple[int, int]]:
    """(side, (group, place inside it)) an account prints in on the Balance
    Sheet, from its type and the detail type it groups under (its own, or
    bs_print_details' for a default-typed sub-account); equity is one
    group, and a P&L account has no side. What family_rows asks to know
    whether a parent gathers a sub-account (#565)."""
    from app.models.accounts import AccountType

    try:
        atype = AccountType(account_type)
    except ValueError:
        return None, (0, 0)
    if atype == AccountType.EQUITY:
        return "equity", (0, 0)
    side = {AccountType.ASSET: "assets", AccountType.LIABILITY: "liabilities"}.get(
        atype
    )
    if side is None:
        return None, (0, 0)
    return side, _bs_rank(side, detail)


def bs_group_totals(lines: list[dict], side: str) -> dict:
    """Every subtotal of one side, zero for a group with no line."""
    totals = {key: Decimal("0") for _h, key, _d in BS_GROUPS[side]}
    for line in lines:
        g, _ = _bs_rank(side, _group_detail(line))
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
            {
                "kind": "heading",
                "depth": depth,
                "label": label,
                "account_id": node,
                # the parent's number and path, for an export's columns
                "account_number": chart[node]["number"],
                "account_path": account_path(chart, node),
            }
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
