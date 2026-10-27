"""How an account is named on screen, in a report, a PDF, a picker (#567).

One rule for every label: "6100 - Auto & Truck Expense" when the company
uses account numbers and the account has one, else the name alone. The
JS twin is accountLabel() in app/static/js/utils.js.

A caller with a session passes `numbered=use_account_numbers(db)`
(app.services.settings_service); a report that builds its chart once
carries the flag on the chart (financial._chart_index), so the helpers
that label rows from it need no session of their own.
"""


def account_label(number, name, numbered: bool = True, sep: str = " - ") -> str:
    """The account's words: number and name joined by `sep` when the
    company numbers its accounts and this one has a number, else the name.
    An account with neither reads "Uncategorized" (the P&L's word for a
    line with no account, 2.22.3 gate NEW-74)."""
    number = str(number or "").strip() if numbered else ""
    name = str(name or "").strip()
    if number and name:
        return f"{number}{sep}{name}"
    return number or name or "Uncategorized"


def label_for(account, db=None, sep: str = " ") -> str:
    """An Account row named for a sentence ("1000 Checking", or "Checking"
    when the company does not use account numbers): the one call a message
    that holds an account needs. `db` defaults to the account's own session."""
    from sqlalchemy.orm import object_session

    from app.services.settings_service import use_account_numbers

    db = db or object_session(account)
    numbered = use_account_numbers(db) if db is not None else True
    return account_label(account.account_number, account.name, numbered, sep)
