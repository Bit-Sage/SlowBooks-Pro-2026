"""A deposit QuickBooks prints under several numbers is one journal.

A bank deposit of two customer checks shows its bank line with no number and
each income line with that check's own number. Grouping by number split it
into an unnumbered half and two numbered halves that only balance together,
and the import refused the file (a real 2013 deposit on discussion #349:
182.86 = 137.82 + 45.04). The shape below is that one, with names changed.
"""

from datetime import date
from decimal import Decimal

from app.services.qb_ledger_import import group_journals

D = date(2013, 12, 19)


def _line(row, path, num, name, debit="0", credit="0"):
    return {
        "path": path,
        "type": "Deposit",
        "date": D,
        "num": num,
        "name": name,
        "memo": "",
        "debit": Decimal(debit),
        "credit": Decimal(credit),
        "row": row,
    }


def _deposit():
    return [
        _line(10, "Checking", "", "", debit="182.86"),
        _line(20, "License Fees", "8935", "Example Publisher", credit="137.82"),
        _line(21, "License Fees", "9007", "Example Publisher", credit="45.04"),
    ]


def test_the_halves_that_add_up_are_one_journal():
    journals, errors = group_journals(_deposit())
    assert errors == []
    assert len(journals) == 1
    assert sorted(ln["row"] for ln in journals[0]) == [10, 20, 21]


def test_halves_that_do_not_add_up_are_still_named():
    lines = _deposit()
    lines[2]["credit"] = Decimal("45.00")
    journals, errors = group_journals(lines)
    assert journals == []
    assert len(errors) == 3 and all("does not balance" in e for e in errors)


def test_a_one_to_one_pair_is_unchanged():
    lines = [
        _line(10, "Checking", "", "", debit="137.82"),
        _line(20, "License Fees", "8935", "Example Publisher", credit="137.82"),
    ]
    journals, errors = group_journals(lines)
    assert errors == [] and len(journals) == 1
