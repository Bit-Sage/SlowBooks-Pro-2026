"""Construction job reports (#556): the WIP schedule with over/under billing,
cost-to-complete, the change-order log, the buyout report and job cost by
vendor.

The service math is checked with exact Decimals on hand-built books; the routes
(JSON / PDF HTML stage / CSV) are checked through the client for a 200 and the
expected header row. The Report Center cards are driven in node — they show in
construction mode only, and the pins still work.
"""

import json
import shutil
import subprocess
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType
from app.models.bills import (
    Bill,
    BillLine,
    BillPayment,
    BillPaymentAllocation,
    BillStatus,
)
from app.models.change_orders import ChangeOrder, ChangeOrderLine
from app.models.contacts import Customer, Vendor
from app.models.cost_codes import CostCode
from app.models.job_costing import JobBudget
from app.models.jobs import Job, SovLine
from app.models.pay_applications import PayApplication, PayApplicationLine
from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine
from app.services import job_reports
from app.services.accounting import create_journal_entry
from app.services.job_costing import ensure_default_cost_types

ROOT = Path(__file__).resolve().parents[1]
needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


# ── builders ────────────────────────────────────────────────────────────


def _customer(db, name="Owner Co"):
    c = Customer(name=name, is_active=True)
    db.add(c)
    db.flush()
    return c


def _cogs(db):
    return db.query(Account).filter(Account.account_type == AccountType.COGS).first()


def _liability(db):
    return (
        db.query(Account).filter(Account.account_type == AccountType.LIABILITY).first()
    )


def _income(db):
    return db.query(Account).filter(Account.account_type == AccountType.INCOME).first()


def _post_cost(db, job_id, account, amount, when, cost_code_id=None, vendor_id=None):
    txn = create_journal_entry(
        db,
        when,
        "job cost",
        [
            {
                "account_id": account.id,
                "debit": Decimal(str(amount)),
                "job_id": job_id,
                "cost_code_id": cost_code_id,
            },
            {"account_id": _liability(db).id, "credit": Decimal(str(amount))},
        ],
    )
    if vendor_id is not None:
        txn.vendor_id = vendor_id
        db.flush()


def _post_cost_split(db, job_amounts, vendor_id):
    """One journal entry with a COGS debit per (job_id, amount), balanced by a
    single credit, with the vendor on the header (as a bill posts)."""
    cogs = _cogs(db)
    total = sum(Decimal(str(a)) for _j, a in job_amounts)
    lines = [
        {"account_id": cogs.id, "debit": Decimal(str(a)), "job_id": jid}
        for jid, a in job_amounts
    ]
    lines.append({"account_id": _liability(db).id, "credit": total})
    txn = create_journal_entry(db, date(2026, 3, 1), "split cost", lines)
    txn.vendor_id = vendor_id
    db.flush()


def _wip_job(db, customer):
    """The spec scenario: contract 100,000 + 10,000 approved CO (revised
    110,000); budget 80,000 + 8,000 change (88,000); cost to date 44,000; one
    approved application billing 60,000; 10% retainage."""
    ensure_default_cost_types(db)
    job = Job(
        customer_id=customer.id,
        name="Tower",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("110000"),
        original_contract_amount=Decimal("100000"),
        retainage_pct=Decimal("10"),
    )
    db.add(job)
    db.flush()
    db.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("80000"),
            revenue_amount=Decimal("0"),
            source="manual",
        )
    )
    db.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("8000"),
            revenue_amount=Decimal("0"),
            source="change",
            locked=True,
        )
    )
    _post_cost(db, job.id, _cogs(db), "44000", date(2026, 3, 1))

    sov = SovLine(
        job_id=job.id,
        line_no=1,
        item_no="01",
        description="Work",
        kind="work",
        scheduled_value=Decimal("110000"),
    )
    db.add(sov)
    db.flush()
    app = PayApplication(
        job_id=job.id,
        application_no=1,
        number="APP-1",
        status="approved",
        retainage_pct=Decimal("10"),
        date=date(2026, 3, 31),
    )
    db.add(app)
    db.flush()
    db.add(
        PayApplicationLine(
            pay_application_id=app.id,
            sov_line_id=sov.id,
            line_no=1,
            item_no="01",
            description="Work",
            scheduled_value=Decimal("110000"),
            this_period=Decimal("60000"),
            stored=Decimal("0"),
        )
    )
    db.commit()
    return job


# ── 1. WIP math ───────────────────────────────────────────────────────────


def test_wip_math_exact(db_session, seed_accounts):
    customer = _customer(db_session)
    job = _wip_job(db_session, customer)
    data = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))
    assert data["method"] == "cost_to_cost"
    assert len(data["rows"]) == 1
    r = data["rows"][0]
    assert r["job_id"] == job.id
    assert r["has_contract"] is True
    assert r["billed_basis"] == "pay_applications"
    assert r["contract_original"] == 100000.0
    assert r["approved_change_orders"] == 10000.0
    assert r["revised_contract"] == 110000.0
    assert r["estimated_total_cost"] == 88000.0
    assert r["estimated_gross_profit"] == 22000.0
    assert r["margin_pct"] == 20.0
    assert r["cost_to_date"] == 44000.0
    assert r["percent_complete"] == 50.0
    assert r["earned_revenue"] == 55000.0
    assert r["billed_to_date"] == 60000.0
    assert r["over_billing"] == 5000.0
    assert r["under_billing"] == 0.0
    assert r["retainage_outstanding"] == 6000.0  # 60,000 × 10%
    assert r["cost_to_complete"] == 44000.0
    assert r["projected_gross_profit"] == 22000.0
    # totals mirror the one row
    t = data["totals"]
    assert t["revised_contract"] == 110000.0
    assert t["over_billing"] == 5000.0
    assert data["adjustment_accounts"]["over_billing"]
    assert data["adjustment_accounts"]["under_billing"]


def test_wip_percent_capped_at_100(db_session, seed_accounts):
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    job = Job(
        customer_id=customer.id,
        name="Overrun",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("100000"),
        original_contract_amount=Decimal("100000"),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("50000"),
            source="manual",
        )
    )
    _post_cost(db_session, job.id, _cogs(db_session), "60000", date(2026, 3, 1))
    db_session.commit()
    r = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))["rows"][0]
    assert r["percent_complete"] == 100.0  # 60,000 / 50,000 is capped
    assert r["earned_revenue"] == 100000.0  # revised × 100%


def test_wip_job_with_no_contract_is_blank(db_session, seed_accounts):
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Bare", status="in_progress")
    db_session.add(job)
    db_session.commit()
    r = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))["rows"][0]
    assert r["has_contract"] is False
    assert r["revised_contract"] is None
    assert r["percent_complete"] is None
    assert r["over_billing"] is None


def test_wip_closed_jobs_excluded_by_default_and_included_on_request(
    db_session, seed_accounts
):
    customer = _customer(db_session)
    _wip_job(db_session, customer)
    closed = Job(
        customer_id=customer.id,
        name="Done",
        status="closed",
        contract_type="lump_sum",
        contract_amount=Decimal("5000"),
        original_contract_amount=Decimal("5000"),
    )
    db_session.add(closed)
    db_session.commit()
    default = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))
    assert [r["job_name"] for r in default["rows"]] == ["Tower"]
    with_closed = job_reports.wip_schedule(
        db_session, as_of=date(2026, 6, 1), include_closed=True
    )
    assert "Done" in [r["job_name"] for r in with_closed["rows"]]


# ── 2. cost-to-complete ─────────────────────────────────────────────────────


def test_cost_to_complete_tree_and_totals(db_session, seed_accounts):
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    division = CostCode(
        code="03", name="Concrete", cost_type="material", level="division"
    )
    db_session.add(division)
    db_session.flush()
    section = CostCode(
        code="03 30 00",
        name="Cast-in-Place",
        cost_type="material",
        level="section",
        parent_id=division.id,
    )
    db_session.add(section)
    db_session.flush()
    job = Job(customer_id=customer.id, name="Slab", status="in_progress")
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_code_id=section.id,
            cost_type="material",
            amount=Decimal("10000"),
            source="manual",
        )
    )
    # a committed (open) PO line on the section: 4,000 ordered, 1,000 billed
    vendor = Vendor(name="Concrete Co")
    db_session.add(vendor)
    db_session.flush()
    po = PurchaseOrder(
        po_number="PO-1",
        vendor_id=vendor.id,
        status=POStatus.SENT,
        date=date(2026, 2, 1),
        job_id=job.id,
    )
    db_session.add(po)
    db_session.flush()
    db_session.add(
        PurchaseOrderLine(
            purchase_order_id=po.id,
            cost_code_id=section.id,
            cost_type="material",
            amount=Decimal("4000"),
            billed_amount=Decimal("1000"),
            job_id=job.id,
        )
    )
    _post_cost(
        db_session, job.id, _cogs(db_session), "3000", date(2026, 3, 1), section.id
    )
    db_session.commit()

    data = job_reports.cost_to_complete(db_session, as_of=date(2026, 6, 1))
    jobrow = data["jobs"][0]
    # the tree is division › section
    div = jobrow["tree"][0]
    assert div["code"] == "03"
    sec = div["children"][0]
    assert sec["code"] == "03 30 00"
    f = sec["figures"]
    assert f["budget_revised"] == 10000.0
    assert f["committed"] == 3000.0  # 4,000 − 1,000 billed
    assert f["actual"] == 3000.0
    # remaining to budget = max(10000 − 3000 − 3000, 0) = 4000; + committed 3000
    assert f["cost_to_complete"] == 7000.0
    assert f["projected_final"] == 10000.0  # actual 3000 + cost to complete 7000
    assert f["variance"] == 0.0
    assert f["pct_complete"] == 30.0
    # the division rolls the section up
    assert div["figures"]["budget_revised"] == 10000.0
    assert data["totals"]["budget_revised"] == 10000.0
    assert data["totals"]["actual"] == 3000.0


# ── 3. change-order log ─────────────────────────────────────────────────────


def _co(db, job, number, kind, status, funding, lines, **extra):
    co = ChangeOrder(
        job_id=job.id,
        number=number,
        kind=kind,
        status=status,
        funding=funding,
        date=date(2026, 3, 1),
        **extra,
    )
    db.add(co)
    db.flush()
    for i, (cost, sell, ctype) in enumerate(lines):
        co.lines.append(
            ChangeOrderLine(
                line_order=i,
                cost_type=ctype,
                quantity=Decimal("1"),
                unit_cost=Decimal(str(cost)),
                amount=Decimal(str(cost)),
                sell_amount=Decimal(str(sell)),
                markup_pct=Decimal("0"),
            )
        )
    db.flush()
    return co


def test_change_order_log_running_totals(db_session, seed_accounts):
    customer = _customer(db_session)
    job = Job(
        customer_id=customer.id,
        name="Addition",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("110000"),
        original_contract_amount=Decimal("100000"),
    )
    db_session.add(job)
    db_session.flush()
    # an approved contract-funded owner CO that grew the contract by 10,000
    _co(
        db_session,
        job,
        "CO-1",
        "owner",
        "approved",
        "contract",
        [("8000", "10000", "material")],
        approved_at=date(2026, 3, 2),
    )
    # a voided CO (no effect on the running total)
    _co(
        db_session,
        job,
        "CO-2",
        "owner",
        "void",
        "contract",
        [("5000", "6000", "labor")],
    )
    # a pending CO (counts into the pending total, not the running approved)
    _co(
        db_session,
        job,
        "CO-3",
        "owner",
        "pending",
        "contract",
        [("2000", "2000", "material")],
    )
    db_session.commit()

    data = job_reports.change_order_log(db_session)
    jobrow = data["jobs"][0]
    assert jobrow["contract"]["original"] == 100000.0
    assert jobrow["contract"]["approved_changes"] == 10000.0
    assert jobrow["contract"]["revised"] == 110000.0
    running = {r["number"]: r["running_approved"] for r in jobrow["rows"]}
    assert running["CO-1"] == 10000.0
    assert running["CO-2"] == 10000.0  # the void did not move it
    assert running["CO-3"] == 10000.0  # pending does not either
    assert jobrow["pending_total"] == 2000.0
    assert jobrow["counts"]["approved"] == 1
    assert jobrow["counts"]["void"] == 1
    assert jobrow["counts"]["pending"] == 1


# ── 4. buyout totals across jobs ────────────────────────────────────────────


def _bought_po(db, job, vendor, amount, package_id=None):
    po = PurchaseOrder(
        po_number=f"PO-{job.id}-{vendor.id}",
        vendor_id=vendor.id,
        status=POStatus.SENT,
        date=date(2026, 2, 1),
        job_id=job.id,
        subtotal=Decimal(str(amount)),
        trade_package_id=package_id,
    )
    db.add(po)
    db.flush()
    db.add(
        PurchaseOrderLine(
            purchase_order_id=po.id,
            amount=Decimal(str(amount)),
            billed_amount=Decimal("0"),
            job_id=job.id,
        )
    )
    db.flush()
    return po


def test_buyout_totals_across_two_jobs(db_session, seed_accounts):
    customer = _customer(db_session)
    vendor = Vendor(name="Sub Co")
    db_session.add(vendor)
    db_session.flush()
    job1 = Job(customer_id=customer.id, name="J1", status="in_progress")
    job2 = Job(customer_id=customer.id, name="J2", status="in_progress")
    db_session.add_all([job1, job2])
    db_session.flush()
    _bought_po(db_session, job1, vendor, "12000")
    _bought_po(db_session, job2, vendor, "8000")
    db_session.commit()

    data = job_reports.buyout_report(db_session)
    assert len(data["jobs"]) == 2
    assert data["totals"]["bought"] == 20000.0
    # each job keeps its Unassigned package row
    for jobrow in data["jobs"]:
        assert any(r["package_name"] == "Unassigned" for r in jobrow["rows"])


# ── 5. job cost by vendor ───────────────────────────────────────────────────


def test_job_cost_by_vendor(db_session, seed_accounts):
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Mixed", status="in_progress")
    db_session.add(job)
    db_session.flush()
    v1 = Vendor(name="Alpha Supply")
    v2 = Vendor(name="Beta Labor")
    db_session.add_all([v1, v2])
    db_session.flush()

    # vendor 1: a bill with a payment and retainage held
    bill = Bill(
        bill_number="B-1",
        vendor_id=v1.id,
        status=BillStatus.PARTIAL,
        date=date(2026, 3, 1),
        job_id=job.id,
        subtotal=Decimal("10000"),
        total=Decimal("10000"),
        amount_paid=Decimal("4000"),
        balance_due=Decimal("5000"),
        retainage_held=Decimal("1000"),
        retainage_released=Decimal("0"),
    )
    db_session.add(bill)
    db_session.flush()
    db_session.add(BillLine(bill_id=bill.id, amount=Decimal("10000"), job_id=job.id))
    payment = BillPayment(
        vendor_id=v1.id, date=date(2026, 3, 15), amount=Decimal("4000")
    )
    db_session.add(payment)
    db_session.flush()
    db_session.add(
        BillPaymentAllocation(
            bill_payment_id=payment.id, bill_id=bill.id, amount=Decimal("4000")
        )
    )
    # the posted cost for each vendor (header-level vendor_id) and a no-vendor line
    _post_cost(
        db_session,
        job.id,
        _cogs(db_session),
        "10000",
        date(2026, 3, 1),
        vendor_id=v1.id,
    )
    _post_cost(
        db_session, job.id, _cogs(db_session), "6000", date(2026, 3, 1), vendor_id=v2.id
    )
    _post_cost(db_session, job.id, _cogs(db_session), "500", date(2026, 3, 1))
    db_session.commit()

    data = job_reports.job_cost_by_vendor(
        db_session, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
    )
    rows = {r["vendor_name"]: r for r in data["jobs"][0]["rows"]}
    assert rows["Alpha Supply"]["cost"] == 10000.0
    assert rows["Alpha Supply"]["bills_count"] == 1
    assert rows["Alpha Supply"]["paid_to_date"] == 4000.0
    assert rows["Alpha Supply"]["retainage_outstanding"] == 1000.0
    assert rows["Alpha Supply"]["open_ap"] == 5000.0
    assert rows["Beta Labor"]["cost"] == 6000.0
    assert rows["Beta Labor"]["bills_count"] == 0
    assert rows["No vendor"]["cost"] == 500.0
    assert data["totals"]["cost"] == 16500.0
    # No vendor is last
    assert data["jobs"][0]["rows"][-1]["vendor_name"] == "No vendor"


# ── routes: JSON / PDF / CSV answer 200 with the header row ─────────────────


def _setup_books(client, db_session, seed_accounts):
    customer = _customer(db_session)
    _wip_job(db_session, customer)
    return customer


_REPORTS = {
    "wip-schedule": "Revised",
    "cost-to-complete": "Cost code",
    "change-order-log": "Number",
    "buyout": "Package",
    "job-cost-by-vendor": "Vendor",
}


def test_report_routes_json_and_csv(client, db_session, seed_accounts):
    _setup_books(client, db_session, seed_accounts)
    for name, header in _REPORTS.items():
        j = client.get(f"/api/reports/{name}")
        assert j.status_code == 200, (name, j.text)
        body = j.json()
        assert "columns" in body and "totals" in body
        labels = [c["label"] for c in body["columns"]]
        assert header in labels, (name, labels)
        # CSV carries the header row
        csv = client.get(f"/api/reports/{name}/csv")
        assert csv.status_code == 200, (name, csv.text)
        assert header in csv.text, (name, csv.text[:200])


def test_report_routes_pdf(client, db_session, seed_accounts):
    # Skipped where WeasyPrint's native stack is missing (conftest), but the
    # HTML section builders still run to the render boundary.
    _setup_books(client, db_session, seed_accounts)
    for name in _REPORTS:
        pdf = client.get(f"/api/reports/{name}/pdf")
        assert pdf.status_code == 200, (name, pdf.text)


def test_existing_reports_export_csv(client, db_session, seed_accounts):
    _setup_books(client, db_session, seed_accounts)
    for name, header in (
        ("job-profitability", "Income"),
        ("job-budget-vs-actual", "Budget"),
    ):
        csv = client.get(f"/api/reports/{name}/csv")
        assert csv.status_code == 200, (name, csv.text)
        assert header in csv.text, (name, csv.text[:200])


def test_existing_reports_export_pdf(client, db_session, seed_accounts):
    _setup_books(client, db_session, seed_accounts)
    for name in ("job-profitability", "job-budget-vs-actual"):
        r = client.get(f"/api/reports/{name}/pdf")
        assert r.status_code == 200, (name, r.text)


# ── Report Center cards: construction only, pins still work ─────────────────


@needs_node
def test_cards_show_in_construction_only_and_pins_work(tmp_path):
    probe = tmp_path / "cardprobe.cjs"
    probe.write_text(
        """
const fs=require('fs'),vm=require('vm');
const src=fs.readFileSync(process.argv[2],'utf8');
function run(isCon){
  const ctx={console,URLSearchParams,JSON,Object,
    T:s=>s, Terms:{text:s=>s,isNonprofit:()=>false,isConstruction:()=>isCon},
    escapeHtml:s=>String(s==null?'':s), document:{}, window:{}, location:{hash:'#/'}};
  vm.createContext(ctx);
  vm.runInContext(src+'\\nthis.ReportsPage=ReportsPage;',ctx);
  const views=ctx.ReportsPage._cards().map(c=>c.view);
  // pins reorder: pin a card and confirm it precedes an unpinned one
  ctx.ReportsPage._pins=['job-cost-by-vendor'];
  const grid=ctx.ReportsPage._cardGrid();
  const pinned=grid.indexOf('report-card-job-cost-by-vendor');
  const unpinned=grid.indexOf('report-card-wip-schedule');
  return {views, pinned, unpinned};
}
console.log(JSON.stringify({con:run(true), biz:run(false)}));
""",
        encoding="utf-8",
    )
    out = subprocess.run(
        ["node", str(probe), str(ROOT / "app/static/js/reports.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    five = [
        "wip-schedule",
        "cost-to-complete",
        "change-order-log",
        "buyout",
        "job-cost-by-vendor",
    ]
    assert all(v in res["con"]["views"] for v in five)
    assert all(v not in res["biz"]["views"] for v in five)
    # the Construction group leads: three existing, then the five new
    assert (
        res["con"]["views"][:8]
        == [
            "job-profitability",
            "job-budget-vs-actual",
            "profit-loss-by-job",
        ]
        + five
    )
    # a pinned card precedes an unpinned one in the rendered grid
    assert res["con"]["pinned"] >= 0 and res["con"]["unpinned"] >= 0
    assert res["con"]["pinned"] < res["con"]["unpinned"]


# ── Fixes (#556 review round) ───────────────────────────────────────────────


def test_wip_as_of_before_billing_reads_zero(db_session, seed_accounts):
    """As of a day before the cost, the billing and the change order, the row
    shows no changes, no billing and no over-billing."""
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    job = Job(
        customer_id=customer.id,
        name="Early",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("110000"),  # after the CO below
        original_contract_amount=Decimal("100000"),
        retainage_pct=Decimal("10"),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("80000"),
            source="manual",
        )
    )
    _post_cost(db_session, job.id, _cogs(db_session), "44000", date(2026, 3, 1))
    # an owner, contract-funded CO approved after the as_of date
    _co(
        db_session,
        job,
        "CO-1",
        "owner",
        "approved",
        "contract",
        [("8000", "10000", "material")],
        approved_at=datetime(2026, 4, 1, 9, 0),
    )
    # an approved application billing 60,000, dated after the as_of date
    sov = SovLine(
        job_id=job.id,
        line_no=1,
        item_no="01",
        kind="work",
        scheduled_value=Decimal("110000"),
    )
    db_session.add(sov)
    db_session.flush()
    app = PayApplication(
        job_id=job.id,
        application_no=1,
        number="E-1",
        status="approved",
        retainage_pct=Decimal("10"),
        date=date(2026, 3, 31),
    )
    db_session.add(app)
    db_session.flush()
    db_session.add(
        PayApplicationLine(
            pay_application_id=app.id,
            sov_line_id=sov.id,
            line_no=1,
            scheduled_value=Decimal("110000"),
            this_period=Decimal("60000"),
            stored=Decimal("0"),
        )
    )
    db_session.commit()

    r = job_reports.wip_schedule(db_session, as_of=date(2026, 2, 1))["rows"][0]
    assert r["billed_to_date"] == 0.0
    assert r["over_billing"] == 0.0
    assert r["under_billing"] == 0.0  # nothing earned yet either
    assert r["approved_change_orders"] == 0.0
    assert r["revised_contract"] == 100000.0  # the CO is backed out as of the date
    assert r["cost_to_date"] == 0.0
    assert r["billed_basis"] == "posted_revenue"

    # and as of today it reads the full, current figures
    today = job_reports.wip_schedule(db_session)["rows"][0]
    assert today["approved_change_orders"] == 10000.0
    assert today["revised_contract"] == 110000.0
    assert today["billed_to_date"] == 60000.0
    assert today["billed_basis"] == "pay_applications"


def test_wip_overrun_projected_gp_diverges(db_session, seed_accounts):
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    job = Job(
        customer_id=customer.id,
        name="Overrun2",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("100000"),
        original_contract_amount=Decimal("100000"),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("50000"),
            source="manual",
        )
    )
    _post_cost(db_session, job.id, _cogs(db_session), "60000", date(2026, 3, 1))
    db_session.commit()
    r = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))["rows"][0]
    assert r["percent_complete"] == 100.0
    assert r["cost_to_complete"] == 0.0  # floored
    assert r["estimated_gross_profit"] == 50000.0  # revised 100k − estimate 50k
    assert r["projected_gross_profit"] == 40000.0  # revised 100k − cost 60k
    assert r["projected_gross_profit"] != r["estimated_gross_profit"]


def test_wip_zero_budget_row(db_session, seed_accounts):
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    job = Job(
        customer_id=customer.id,
        name="NoBudget",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("20000"),
        original_contract_amount=Decimal("20000"),
    )
    db_session.add(job)
    db_session.flush()
    _post_cost(db_session, job.id, _cogs(db_session), "5000", date(2026, 3, 1))
    db_session.commit()
    r = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))["rows"][0]
    assert r["estimated_total_cost"] == 0.0
    assert r["percent_complete"] == 0.0
    assert r["cost_to_complete"] == 0.0  # floored, no budget to finish against
    assert r["projected_gross_profit"] == 15000.0  # 20,000 − 5,000 cost


def _division(db, code, name):
    c = CostCode(code=code, name=name, cost_type="material", level="division")
    db.add(c)
    db.flush()
    return c


def _section(db, code, name, parent):
    c = CostCode(
        code=code, name=name, cost_type="material", level="section", parent_id=parent.id
    )
    db.add(c)
    db.flush()
    return c


def test_cost_to_complete_empty_sibling_and_footed_totals(db_session, seed_accounts):
    """Two divisions, one with an empty sibling section: no crash, and the job
    totals foot the clamped rows (0 + 8,000 = 8,000; variance −5,000)."""
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    div_a = _division(db_session, "03", "Concrete")
    a1 = _section(db_session, "03 30 00", "Cast-in-Place", div_a)
    _section(db_session, "03 40 00", "Precast", div_a)  # empty sibling
    div_b = _division(db_session, "26", "Electrical")
    b1 = _section(db_session, "26 05 00", "Basic Materials", div_b)
    job = Job(customer_id=customer.id, name="Two", status="in_progress")
    db_session.add(job)
    db_session.flush()
    # division A: no budget, 5,000 actual → over, cost to complete 0
    _post_cost(db_session, job.id, _cogs(db_session), "5000", date(2026, 3, 1), a1.id)
    # division B: 8,000 budget, no actual → cost to complete 8,000
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_code_id=b1.id,
            cost_type="material",
            amount=Decimal("8000"),
            source="manual",
        )
    )
    db_session.commit()

    data = job_reports.cost_to_complete(db_session, as_of=date(2026, 6, 1))
    job_data = data["jobs"][0]
    # the empty sibling is dropped, not crashed on
    div_a_node = next(n for n in job_data["tree"] if n["code"] == "03")
    assert [c["code"] for c in div_a_node["children"]] == ["03 30 00"]
    t = job_data["totals"]
    assert t["budget_revised"] == 8000.0
    assert t["actual"] == 5000.0
    assert t["cost_to_complete"] == 8000.0  # 0 (div A) + 8,000 (div B)
    assert t["variance"] == -5000.0  # −5,000 (div A) + 0 (div B)
    # grand totals (one job) match
    assert data["totals"]["cost_to_complete"] == 8000.0
    assert data["totals"]["variance"] == -5000.0


def test_job_cost_by_vendor_prorates_a_split_bill(db_session, seed_accounts):
    customer = _customer(db_session)
    j1 = Job(customer_id=customer.id, name="J1", status="in_progress")
    j2 = Job(customer_id=customer.id, name="J2", status="in_progress")
    db_session.add_all([j1, j2])
    db_session.flush()
    vendor = Vendor(name="Split Co")
    db_session.add(vendor)
    db_session.flush()
    # one bill split 6,000 to J1 and 4,000 to J2 (header carries no job)
    bill = Bill(
        bill_number="S-1",
        vendor_id=vendor.id,
        status=BillStatus.PARTIAL,
        date=date(2026, 3, 1),
        subtotal=Decimal("10000"),
        total=Decimal("10000"),
        amount_paid=Decimal("5000"),
        balance_due=Decimal("10000"),
        retainage_held=Decimal("1000"),
        retainage_released=Decimal("0"),
    )
    db_session.add(bill)
    db_session.flush()
    db_session.add(BillLine(bill_id=bill.id, amount=Decimal("6000"), job_id=j1.id))
    db_session.add(BillLine(bill_id=bill.id, amount=Decimal("4000"), job_id=j2.id))
    payment = BillPayment(
        vendor_id=vendor.id, date=date(2026, 3, 15), amount=Decimal("5000")
    )
    db_session.add(payment)
    db_session.flush()
    db_session.add(
        BillPaymentAllocation(
            bill_payment_id=payment.id, bill_id=bill.id, amount=Decimal("5000")
        )
    )
    # posted cost, one JE with a line per job, the bill's vendor on the header
    _post_cost_split(db_session, [(j1.id, "6000"), (j2.id, "4000")], vendor.id)
    db_session.commit()

    data = job_reports.job_cost_by_vendor(
        db_session, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
    )
    by_job = {j["job_name"]: j for j in data["jobs"]}
    r1 = by_job["J1"]["rows"][0]
    r2 = by_job["J2"]["rows"][0]
    assert r1["bills_count"] == 0.6 and r2["bills_count"] == 0.4
    assert r1["paid_to_date"] == 3000.0 and r2["paid_to_date"] == 2000.0
    assert r1["retainage_outstanding"] == 600.0 and r2["retainage_outstanding"] == 400.0
    assert r1["open_ap"] == 6000.0 and r2["open_ap"] == 4000.0
    # the grand totals foot the whole bill
    g = data["totals"]
    assert g["cost"] == 10000.0
    assert g["bills_count"] == 1.0
    assert g["paid_to_date"] == 5000.0
    assert g["retainage_outstanding"] == 1000.0
    assert g["open_ap"] == 10000.0


def test_existing_report_csv_figures_agree_with_json(client, db_session, seed_accounts):
    """The Job Profitability CSV carries the same money figures the JSON does,
    including a negative-margin job (income under cost)."""
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Loser", status="in_progress")
    db_session.add(job)
    db_session.flush()
    # income 10,000, cost 44,000 → net −34,000, margin negative
    create_journal_entry(
        db_session,
        date(2026, 3, 1),
        "revenue",
        [
            {"account_id": _liability(db_session).id, "debit": Decimal("10000")},
            {
                "account_id": _income(db_session).id,
                "credit": Decimal("10000"),
                "job_id": job.id,
            },
        ],
    )
    _post_cost(db_session, job.id, _cogs(db_session), "44000", date(2026, 3, 1))
    db_session.commit()

    jp = client.get(
        "/api/reports/job-profitability?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    row = next(r for r in jp["jobs"] if r["job_name"] == "Loser")
    assert row["net_income"] == -34000.0
    assert row["margin_pct"] is not None and row["margin_pct"] < 0
    csv = client.get(
        "/api/reports/job-profitability/csv?start_date=2026-01-01&end_date=2026-12-31"
    ).text
    assert "-34000.00" in csv  # the net money cell, not just the header word
    assert "10000.00" in csv  # income


def test_budget_vs_actual_csv_figure_agrees_with_service(
    client, db_session, seed_accounts
):
    from app.services.job_costing import budget_vs_actual_all_jobs

    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="BVA", status="in_progress")
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("12000"),
            source="manual",
        )
    )
    _post_cost(db_session, job.id, _cogs(db_session), "3000", date(2026, 3, 1))
    db_session.commit()

    svc = budget_vs_actual_all_jobs(db_session, date(2026, 1, 1), date(2026, 12, 31))
    row = next(r for r in svc if r["job_name"] == "BVA")
    assert row["revised"] == 12000.0 and row["actual"] == 3000.0
    csv = client.get(
        "/api/reports/job-budget-vs-actual/csv?start_date=2026-01-01&end_date=2026-12-31"
    ).text
    assert "12000.00" in csv and "3000.00" in csv


def test_budget_vs_actual_exports_honour_customer_filter(
    client, db_session, seed_accounts
):
    c1 = _customer(db_session, "C1")
    c2 = _customer(db_session, "C2")
    j1 = Job(customer_id=c1.id, name="C1 Job", status="in_progress")
    j2 = Job(customer_id=c2.id, name="C2 Job", status="in_progress")
    db_session.add_all([j1, j2])
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=j1.id, cost_type="material", amount=Decimal("7777"), source="manual"
        )
    )
    db_session.add(
        JobBudget(
            job_id=j2.id, cost_type="material", amount=Decimal("8888"), source="manual"
        )
    )
    db_session.commit()
    csv = client.get(
        f"/api/reports/job-budget-vs-actual/csv?start_date=2026-01-01&end_date=2026-12-31&customer_id={c1.id}"
    ).text
    assert "7777.00" in csv
    assert "8888.00" not in csv  # the other customer's job is filtered out


def test_job_cost_by_vendor_reversed_dates_refused(client, db_session, seed_accounts):
    _customer(db_session)
    db_session.commit()
    for ext in ("", "/csv"):
        r = client.get(
            f"/api/reports/job-cost-by-vendor{ext}?start_date=2026-12-31&end_date=2026-01-01"
        )
        assert r.status_code == 400, (ext, r.text)
