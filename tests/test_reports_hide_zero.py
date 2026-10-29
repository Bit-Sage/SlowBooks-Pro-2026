"""Hide zero balances (#625, from Discussion #354): the Profit & Loss, the
Balance Sheet, the Trial Balance and the P&L Detail take `hide_zero`, which
leaves out an account that had activity but nets to nothing for the period
(a Balance Sheet account that stands at zero on the date; a Trial Balance
row with nothing in either column), its family heading with it when every
sub-account is zero. The totals are the same either way, the choice rides
on the CSV and PDF routes, and each screen's toolbar offers it the way it
offers the Basis.
"""

import io
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType

APRIL = "start_date=2026-04-01&end_date=2026-04-30"
ROOT = Path(__file__).resolve().parents[1]


def _add(db, number, name, atype, parent=None):
    acct = Account(
        account_number=number,
        name=name,
        account_type=atype,
        parent_id=parent.id if parent else None,
    )
    db.add(acct)
    db.flush()
    return acct


def _je(client, when, dr, cr, amount, what="hide zero"):
    r = client.post(
        "/api/journal",
        json={
            "date": when,
            "description": what,
            "lines": [
                {"account_id": dr.id, "debit": amount},
                {"account_id": cr.id, "credit": amount},
            ],
        },
    )
    assert r.status_code == 201, r.text


@pytest.fixture(name="books")
def books_fixture(client, db_session, seed_accounts):
    """April 2026: $1,000 of income, $250 of advertising, office supplies
    bought and refunded ($100 each way), a hotel night under Travel bought
    and refunded ($80 each way), and a $500 loan drawn and repaid. April
    2025: $300 of rent, for the prior-year comparison."""
    a = dict(seed_accounts)
    a["travel"] = _add(db_session, "7000", "Travel", AccountType.EXPENSE)
    a["hotel"] = _add(db_session, "7010", "Hotel", AccountType.EXPENSE, a["travel"])
    db_session.commit()
    cash = a["1000"]
    _je(client, "2026-04-02", cash, a["4000"], "1000.00", "sale")
    _je(client, "2026-04-03", a["6000"], cash, "250.00", "ads")
    _je(client, "2026-04-05", a["6400"], cash, "100.00", "supplies")
    _je(client, "2026-04-20", cash, a["6400"], "100.00", "supplies refunded")
    _je(client, "2026-04-06", a["hotel"], cash, "80.00", "hotel")
    _je(client, "2026-04-21", cash, a["hotel"], "80.00", "hotel refunded")
    _je(client, "2026-04-08", cash, a["2400"], "500.00", "loan drawn")
    _je(client, "2026-04-25", a["2400"], cash, "500.00", "loan repaid")
    _je(client, "2025-04-10", a["6500"], cash, "300.00", "last year's rent")
    return a


def _get(client, path, qs):
    r = client.get(f"/api/reports/{path}?{qs}")
    assert r.status_code == 200, (path, r.text)
    return r


def _numbers(lines):
    return [i.get("account_number") for i in lines]


def _tree_ids(rows):
    return {r.get("account_id") for r in rows}


# ---------------------------------------------------------------------------
# Profit & Loss
# ---------------------------------------------------------------------------


def test_pl_leaves_out_an_account_that_nets_to_zero(client, books):
    shown = _get(client, "profit-loss", APRIL).json()
    hidden = _get(client, "profit-loss", f"{APRIL}&hide_zero=true").json()

    # without the choice the offsetting accounts print at $0.00, with it
    # they go; the family whose only sub-account is zero loses its heading
    assert _numbers(shown["expenses"]) == ["6000", "6400", "7010"]
    assert _numbers(hidden["expenses"]) == ["6000"]
    assert books["travel"].id in _tree_ids(shown["tree"]["expenses"])
    assert books["hotel"].id in _tree_ids(shown["tree"]["expenses"])
    assert _tree_ids(hidden["tree"]["expenses"]) == {books["6000"].id}
    assert [r["kind"] for r in hidden["tree"]["expenses"]] == ["account"]

    # the totals are the same either way
    for key in ("total_income", "total_expenses", "net_income", "gross_profit"):
        assert hidden[key] == shown[key], key
    assert hidden["total_operating_expenses"] == 250.0
    assert hidden["net_income"] == 750.0
    assert shown["hide_zero"] is False and hidden["hide_zero"] is True

    # the address writes hide_zero=1, which reads the same as true
    as_one = _get(client, "profit-loss", f"{APRIL}&hide_zero=1").json()
    assert as_one["expenses"] == hidden["expenses"]


def test_pl_prior_year_keeps_an_account_with_a_figure_in_either_year(client, books):
    data = _get(
        client, "profit-loss", f"{APRIL}&compare=prior_year&hide_zero=true"
    ).json()
    by_number = {i["account_number"]: i for i in data["expenses"]}
    # rent: nothing this year, $300 last year; it stays with last year's figure
    assert by_number["6500"]["amount"] == 0.0
    assert by_number["6500"]["prior_amount"] == 300.0
    assert by_number["6000"]["prior_amount"] == 0.0
    # zero in both years: gone
    assert set(by_number) == {"6000", "6500"}
    assert data["total_expenses"] == 250.0 and data["prior"]["total_expenses"] == 300.0


# ---------------------------------------------------------------------------
# Balance Sheet
# ---------------------------------------------------------------------------


def test_balance_sheet_leaves_out_an_account_that_ends_at_zero(client, books):
    shown = _get(client, "balance-sheet", "as_of_date=2026-04-30").json()
    hidden = _get(
        client, "balance-sheet", "as_of_date=2026-04-30&hide_zero=true"
    ).json()

    assert _numbers(shown["liabilities"]) == ["2400"]
    assert shown["liabilities"][0]["amount"] == 0.0
    assert hidden["liabilities"] == []
    # the group whose only account is zero prints no heading, as one with
    # no account never did; its subtotal is still there and unchanged
    assert [g["heading"] for g in shown["groups"]["liabilities"]] == [
        "Long Term Liabilities"
    ]
    assert hidden["groups"]["liabilities"] == []
    for key in (
        "total_assets",
        "total_liabilities",
        "total_equity",
        "total_long_term_liabilities",
        "total_current_assets",
    ):
        assert hidden[key] == shown[key], key
    assert hidden["total_assets"] == 450.0
    assert hidden["total_liabilities"] == 0.0
    # the account with a balance stays
    assert _numbers(hidden["assets"]) == ["1000"]


# ---------------------------------------------------------------------------
# Trial Balance
# ---------------------------------------------------------------------------


def test_trial_balance_leaves_out_a_row_with_nothing_in_either_column(client, books):
    shown = _get(client, "trial-balance", APRIL).json()
    hidden = _get(client, "trial-balance", f"{APRIL}&hide_zero=true").json()

    assert "2400" in _numbers(shown["items"])
    assert "2400" not in _numbers(hidden["items"])
    # an expense whose debits and credits offset keeps both figures: each
    # column still adds up to its total
    supplies = {i["account_number"]: i for i in hidden["items"]}["6400"]
    assert (supplies["total_debit"], supplies["total_credit"]) == (100.0, 100.0)
    for key in ("total_debit", "total_credit", "difference"):
        assert hidden[key] == shown[key], key
    assert hidden["difference"] == 0.0
    assert (
        round(sum(i["total_debit"] for i in hidden["items"]), 2)
        == hidden["total_debit"]
    )
    assert (
        round(sum(i["total_credit"] for i in hidden["items"]), 2)
        == hidden["total_credit"]
    )


# ---------------------------------------------------------------------------
# P&L Detail
# ---------------------------------------------------------------------------


def test_pl_detail_leaves_out_an_account_that_nets_to_zero(client, books):
    shown = _get(client, "profit-loss-detail", APRIL).json()
    hidden = _get(client, "profit-loss-detail", f"{APRIL}&hide_zero=true").json()

    assert _numbers(shown["sections"]["expenses"]) == ["6000", "6400", "7010"]
    assert _numbers(hidden["sections"]["expenses"]) == ["6000"]
    assert books["travel"].id in _tree_ids(shown["tree"]["expenses"])
    assert _tree_ids(hidden["tree"]["expenses"]) == {books["6000"].id}
    for key in ("total_income", "total_expenses", "net_income"):
        assert hidden[key] == shown[key], key
    assert hidden["net_income"] == 750.0
    # a hidden account's lines go with it
    assert shown["line_count"] == 6
    assert hidden["line_count"] == 2


# ---------------------------------------------------------------------------
# CSV and PDF
# ---------------------------------------------------------------------------

CSV_CASES = (
    ("profit-loss", APRIL, "Office Supplies"),
    ("balance-sheet", "as_of_date=2026-04-30", "Loan Payable"),
    ("trial-balance", APRIL, "Loan Payable"),
    ("profit-loss-detail", APRIL, "Office Supplies"),
)


@pytest.mark.parametrize("path,qs,name", CSV_CASES)
def test_the_csv_takes_the_choice(client, books, path, qs, name):
    shown = _get(client, f"{path}/csv", qs).text
    hidden = _get(client, f"{path}/csv", f"{qs}&hide_zero=true").text
    assert name in shown
    assert name not in hidden
    # the preamble's period line says so
    assert "(zero balances hidden)" in hidden
    assert "zero balances hidden" not in shown


def test_the_pdf_takes_the_choice(client, books):
    pytest.importorskip("weasyprint")
    pypdf = pytest.importorskip("pypdf")

    def text(path, qs):
        r = _get(client, f"{path}/pdf", qs)
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:5] == b"%PDF-"
        pages = pypdf.PdfReader(io.BytesIO(r.content)).pages
        return " ".join(" ".join(p.extract_text() for p in pages).split())

    for path, qs, name in CSV_CASES:
        assert name in text(path, qs), path
        hidden = text(path, f"{qs}&hide_zero=true")
        assert name not in hidden, path
        assert "zero balances hidden" in hidden, path


# ---------------------------------------------------------------------------
# The screens' toolbars
# ---------------------------------------------------------------------------


def test_each_reports_toolbar_offers_hide_zero_balances():
    js = (ROOT / "app" / "static" / "js" / "reports.js").read_text(encoding="utf-8")

    def body(start, end):
        part = js[js.index(start) :]
        return part[: part.index(end)]

    # the box, its param on the address and the query string it adds
    opts = body("    _hideZeroOpts(prefill) {", "\n    },")
    assert 'id="report-hide-zero"' in opts
    assert "Hide zero balances" in opts
    assert "ReportsPage.setParam('hide_zero', this.checked ? '1' : '')" in opts
    assert "const params = on ? { hide_zero: '1' } : {};" in opts
    qs = body("    _hideZeroQs(params) {", "\n    },")
    assert "'&hide_zero=1'" in qs

    # the P&L and the P&L Detail take the statements' toolbar, which has
    # the box, and build their query (the API call and the PDF / CSV
    # links) with _sortQs, which carries it
    statement = body("    _statementOpts(prefill) {", "\n    },")
    assert "ReportsPage._hideZeroOpts(prefill)" in statement
    assert "...zero.params" in statement and "+ zero.toolbar" in statement
    sort_qs = body("    _sortQs(params) {", "\n    },")
    assert "ReportsPage._hideZeroQs(params)" in sort_qs
    pl = body("    async profitLoss(prefill) {", "\n    },")
    assert "ReportsPage._statementOpts(prefill)" in pl
    assert (
        "ReportsPage._exportButtons('profit-loss', `start_date=${range.start}&end_date=${range.end}${ReportsPage._sortQs(params)}"
        in pl
    )
    detail = body("    async profitLossDetail(prefill) {", "\n    },")
    assert "ReportsPage._statementOpts(prefill)" in detail
    assert "${ReportsPage._sortQs(p)}" in detail

    # the Balance Sheet draws the box beside Sort by and Basis
    bs = body("    async balanceSheet(prefill) {", "\n    },")
    assert "const zeroOpts = ReportsPage._hideZeroOpts(prefill);" in bs
    assert "...zeroOpts.params" in bs and "+ zeroOpts.toolbar" in bs
    assert (
        "ReportsPage._exportButtons('balance-sheet', `as_of_date=${asOf}${ReportsPage._sortQs(live)}"
        in bs
    )

    # the Trial Balance draws it after the adjusting box, and one query
    # string feeds the API call and the export links
    tb = body("    async trialBalance(prefill) {", "\n    },")
    assert "const tbZero = ReportsPage._hideZeroOpts(prefill);" in tb
    assert "...tbZero.params" in tb and "+ tbZero.toolbar" in tb
    assert "${ReportsPage._hideZeroQs(live)}`;" in tb
    assert "API.get(`/reports/trial-balance?${tbQs}`)" in tb
    assert "ReportsPage._exportButtons('trial-balance', tbQs)" in tb

    # Back from a drill-down returns to the statement with the choice kept
    keep = body("ReportsPage._cmpKeep = function (params) {", "\n};")
    assert "'hide_zero'" in keep
