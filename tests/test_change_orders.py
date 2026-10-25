"""Change orders (#554): the three kinds (potential / owner / sub), material
and labor priced separately with markup (and none when an allowance pays),
the lifecycle and its 409s, and the effects on the contract, the schedule of
values, the budget and a commitment — every one reversed on void.

Progress billing against the revised SOV and the change-order log report are
later issues (#555, #556); this one leaves those seams.
"""

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from app.models.change_orders import ChangeOrder
from app.models.job_costing import JobBudget
from app.models.jobs import SovLine
from app.services.change_orders import co_totals, line_sell, effective_markup, line_cost
from app.services.job_costing import job_cost_tree
from app.services.pdf_service import render_change_order_html
from app.services.settings_service import get_all_settings

ROOT = Path(__file__).resolve().parents[1]


# ── Helpers ─────────────────────────────────────────────────────────────


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _contract(client, job_id, **terms):
    r = client.put(f"/api/jobs/{job_id}/contract", json=terms)
    assert r.status_code == 200, r.text
    return r.json()


def _section(client, code, name, cost_type="material"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _co(client, job_id, lines, kind="owner", funding="contract", status=201, **extra):
    body = {"job_id": job_id, "kind": kind, "funding": funding, "lines": lines}
    body.update(extra)
    r = client.post("/api/change-orders", json=body)
    assert r.status_code == status, r.text
    return r.json()


LABOR = {
    "description": "Crew",
    "cost_type": "labor",
    "quantity": 10,
    "unit_cost": 50,
    "markup_pct": 15,
}
MATERIAL = {
    "description": "Steel",
    "cost_type": "material",
    "quantity": 1,
    "unit_cost": 1000,
    "markup_pct": 10,
}


def _submit_approve(client, co_id):
    assert client.post(f"/api/change-orders/{co_id}/submit").status_code == 200
    r = client.post(f"/api/change-orders/{co_id}/approve")
    return r


# ── Math (pure, exact Decimals) ────────────────────────────────────────────


def test_line_math_per_cost_type():
    # labor 10h × 50 at 15% → cost 500, sell 575.00
    cost = line_cost(10, 50)
    assert cost == Decimal("500.00")
    assert line_sell(cost, Decimal("15"), "owner") == Decimal("575.00")
    # material 1,000 at 10% → 1,100.00
    mcost = line_cost(1, 1000)
    assert line_sell(mcost, Decimal("10"), "owner") == Decimal("1100.00")


def test_allowance_forces_zero_markup():
    assert effective_markup(Decimal("15"), None, None, "allowance") == Decimal("0")
    assert effective_markup(Decimal("15"), None, None, "contract") == Decimal("15")
    # sell = cost when there is no markup
    assert line_sell(Decimal("500.00"), Decimal("0"), "owner") == Decimal("500.00")


def test_sub_co_carries_no_markup():
    # a commitment moves by cost
    assert line_sell(Decimal("500.00"), Decimal("15"), "sub") == Decimal("500.00")


def test_markup_fallback_order():
    # line wins, then CO, then job, then 0
    assert effective_markup(
        Decimal("5"), Decimal("10"), Decimal("20"), "contract"
    ) == Decimal("5")
    assert effective_markup(None, Decimal("10"), Decimal("20"), "contract") == Decimal(
        "10"
    )
    assert effective_markup(None, None, Decimal("20"), "contract") == Decimal("20")
    assert effective_markup(None, None, None, "contract") == Decimal("0")


def test_co_totals_per_cost_type(client, seed_customer):
    job = _job(client, seed_customer.id)
    co = _co(client, job["id"], [LABOR, MATERIAL])
    assert co["by_cost_type"]["labor"]["sell"] == 575.0
    assert co["by_cost_type"]["material"]["sell"] == 1100.0
    assert co["cost_total"] == 1500.0
    assert co["sell_total"] == 1675.0
    assert co["markup_total"] == 175.0


# ── Numbering per kind ──────────────────────────────────────────────────────


def test_numbering_per_kind(client, seed_customer):
    job = _job(client, seed_customer.id)
    pco = _co(client, job["id"], [LABOR], kind="pco")
    owner = _co(client, job["id"], [LABOR], kind="owner")
    assert pco["number"] == "PCO-0001"
    assert owner["number"] == "CO-0001"
    owner2 = _co(client, job["id"], [LABOR], kind="owner")
    assert owner2["number"] == "CO-0002"


# ── Lifecycle + 409s ────────────────────────────────────────────────────────


def test_edit_after_approve_is_409(client, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    co = _co(client, job["id"], [LABOR])
    assert _submit_approve(client, co["id"]).status_code == 200
    r = client.put(f"/api/change-orders/{co['id']}", json={"title": "late edit"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "co_locked"


def test_approve_twice_is_409(client, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    co = _co(client, job["id"], [LABOR])
    assert _submit_approve(client, co["id"]).status_code == 200
    r = client.post(f"/api/change-orders/{co['id']}/approve")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "co_not_pending"


def test_void_unapproved_is_409(client, seed_customer):
    job = _job(client, seed_customer.id)
    co = _co(client, job["id"], [LABOR])
    r = client.post(f"/api/change-orders/{co['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "co_not_approved"


def test_pco_approve_says_promote_instead(client, seed_customer):
    job = _job(client, seed_customer.id)
    pco = _co(client, job["id"], [LABOR], kind="pco")
    client.post(f"/api/change-orders/{pco['id']}/submit")
    r = client.post(f"/api/change-orders/{pco['id']}/approve")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "promote_instead"


def test_delete_only_draft(client, seed_customer):
    job = _job(client, seed_customer.id)
    co = _co(client, job["id"], [LABOR])
    client.post(f"/api/change-orders/{co['id']}/submit")
    r = client.delete(f"/api/change-orders/{co['id']}")
    assert r.status_code == 409  # pending is not draft
    # a fresh draft deletes
    co2 = _co(client, job["id"], [LABOR])
    assert client.delete(f"/api/change-orders/{co2['id']}").status_code == 200


# ── Owner CO on contract funding ────────────────────────────────────────────


def test_owner_co_moves_contract_and_appends_sov(client, db_session, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    # SOV with one work line balancing the contract
    client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Base", "kind": "work", "scheduled_value": 100000}
            ]
        },
    )
    co = _co(client, job["id"], [LABOR, MATERIAL], title="Extra steel")
    assert _submit_approve(client, co["id"]).status_code == 200

    contract = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert contract["contract_amount"] == 101675.0  # 100000 + sell_total 1675
    assert contract["original_contract_amount"] == 100000.0  # unchanged
    assert contract["changes"] == 1675.0  # equals the delta
    assert contract["changes"] == contract["change_orders"]["approved_total"]
    assert contract["change_orders"]["approved_count"] == 1
    # the SOV gained a read-only change_order line and still balances
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()
    co_line = [line for line in sov["sov"] if line["kind"] == "change_order"]
    assert len(co_line) == 1
    assert float(co_line[0]["scheduled_value"]) == 1675.0
    assert co_line[0]["description"].startswith("CO-0001")
    assert sov["contract"]["sov_balance"] == 0.0  # 101675 sum vs 101675 contract


def test_owner_co_writes_change_budget_rows(client, db_session, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    # an original (manual) budget on a DIFFERENT key than the CO's lines
    client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={
            "rows": [{"cost_type": "other", "amount": 40000, "revenue_amount": 50000}]
        },
    )
    co = _co(client, job["id"], [LABOR, MATERIAL])
    assert _submit_approve(client, co["id"]).status_code == 200

    rows = db_session.query(JobBudget).filter(JobBudget.job_id == job["id"]).all()
    change_rows = [r for r in rows if r.source == "change"]
    assert change_rows and all(r.locked for r in change_rows)
    assert sum(Decimal(str(r.amount)) for r in change_rows) == Decimal("1500.00")

    totals = job_cost_tree(db_session, job["id"])["totals"]
    assert totals["original"] == 40000.0
    assert totals["changes"] == 1500.0
    assert totals["revised"] == 41500.0  # original + changes


def test_void_reverses_contract_sov_and_budget(client, db_session, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    co = _co(client, job["id"], [LABOR, MATERIAL])
    assert _submit_approve(client, co["id"]).status_code == 200
    r = client.post(f"/api/change-orders/{co['id']}/void")
    assert r.status_code == 200

    contract = client.get(f"/api/jobs/{job['id']}/contract").json()
    assert contract["contract"]["contract_amount"] == 100000.0  # back to original
    assert not [line for line in contract["sov"] if line["kind"] == "change_order"]
    change_rows = (
        db_session.query(JobBudget)
        .filter(JobBudget.job_id == job["id"], JobBudget.source == "change")
        .all()
    )
    assert change_rows == []


# ── Contingency funding ─────────────────────────────────────────────────────


def test_contingency_used_and_exceeded(client, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(
        client,
        job["id"],
        contract_type="lump_sum",
        contract_amount=100000,
        contingency_amount=2000,
    )
    co = _co(client, job["id"], [LABOR, MATERIAL], funding="contingency")  # sell 1675
    assert _submit_approve(client, co["id"]).status_code == 200
    contract = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert contract["contract_amount"] == 100000.0  # contingency does not add
    assert contract["contingency_used"] == 1675.0
    assert contract["contingency_remaining"] == 325.0

    # a second contingency CO would pass the 2000 limit → 409
    co2 = _co(client, job["id"], [MATERIAL], funding="contingency")  # sell 1100
    client.post(f"/api/change-orders/{co2['id']}/submit")
    r = client.post(f"/api/change-orders/{co2['id']}/approve")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "contingency_exceeded"


# ── Allowance funding ───────────────────────────────────────────────────────


def _allowance_job(client, customer_id):
    job = _job(client, customer_id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100000)
    client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Base", "kind": "work", "scheduled_value": 98000},
                {
                    "description": "Finishes allowance",
                    "kind": "allowance",
                    "scheduled_value": 2000,
                },
            ]
        },
    )
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    allowance_id = next(line["id"] for line in sov if line["kind"] == "allowance")
    return job, allowance_id


def test_allowance_used_remaining_and_exceeded(client, seed_customer):
    job, allowance_id = _allowance_job(client, seed_customer.id)
    # allowance-funded CO carries no markup: sell == cost == 1500
    co = _co(
        client,
        job["id"],
        [LABOR, MATERIAL],
        funding="allowance",
        allowance_sov_line_id=allowance_id,
    )
    assert co["sell_total"] == 1500.0  # no markup under an allowance
    assert _submit_approve(client, co["id"]).status_code == 200

    contract = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert contract["contract_amount"] == 100000.0  # allowance does not add
    row = next(a for a in contract["allowances"] if a["sov_line_id"] == allowance_id)
    assert row["used"] == 1500.0
    assert row["remaining"] == 500.0

    # a second allowance CO beyond the 2000 allowance → 409
    co2 = _co(
        client,
        job["id"],
        [MATERIAL],
        funding="allowance",
        allowance_sov_line_id=allowance_id,
    )
    client.post(f"/api/change-orders/{co2['id']}/submit")
    r = client.post(f"/api/change-orders/{co2['id']}/approve")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "allowance_exceeded"


def test_allowance_funding_needs_a_line(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "funding": "allowance",
            "lines": [LABOR],
        },
    )
    assert r.status_code == 422


# ── Promote a PCO ───────────────────────────────────────────────────────────


def test_promote_copies_lines_and_marks_promoted(client, seed_customer):
    job = _job(client, seed_customer.id)
    pco = _co(client, job["id"], [LABOR, MATERIAL], kind="pco")
    r = client.post(f"/api/change-orders/{pco['id']}/promote")
    assert r.status_code == 200, r.text
    owner = r.json()
    assert owner["kind"] == "owner"
    assert owner["source_pco_id"] == pco["id"]
    assert len(owner["lines"]) == 2
    assert owner["sell_total"] == 1675.0
    # the PCO is now promoted
    assert client.get(f"/api/change-orders/{pco['id']}").json()["status"] == "promoted"


# ── Sub CO against a commitment ─────────────────────────────────────────────


def _vendor(client, name="Ace", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _sent_commitment(client, vendor_id, job_id, lines, **extra):
    body = {
        "vendor_id": vendor_id,
        "date": "2026-09-01",
        "job_id": job_id,
        "lines": lines,
    }
    body.update(extra)
    po = client.post("/api/purchase-orders", json=body).json()
    up = client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    assert up.status_code == 200, up.text
    return up.json()


def test_sub_co_appends_commitment_lines(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    vendor = _vendor(client)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base scope", "quantity": 1, "rate": 10000}],
        commitment_type="subcontract",
    )
    assert float(po["total"]) == 10000.0
    # an owner CO with subcontract scope
    owner = _co(
        client,
        job["id"],
        [
            {
                "description": "Added scope",
                "cost_type": "subcontract",
                "quantity": 1,
                "unit_cost": 2500,
            },
        ],
    )
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order",
        json={"commitment_id": po["id"]},
    ).json()
    assert sub["kind"] == "sub"
    assert sub["sell_total"] == 2500.0  # no markup on a sub CO
    assert _submit_approve(client, sub["id"]).status_code == 200

    fresh = client.get(f"/api/purchase-orders/{po['id']}").json()
    assert float(fresh["total"]) == 12500.0  # the commitment grew by cost
    assert len(fresh["lines"]) == 2
    # the existing line kept its billed_amount
    base = db_session.query(SovLine)  # noqa: F841 — just forcing a fresh read below
    db_session.expire_all()
    from app.models.purchase_orders import PurchaseOrderLine

    lines = (
        db_session.query(PurchaseOrderLine).filter_by(purchase_order_id=po["id"]).all()
    )
    assert all(Decimal(str(line.billed_amount)) == Decimal("0") for line in lines)


def test_sub_co_reopens_a_closed_commitment_and_buyout_grows(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    pkg = client.post(
        "/api/trade-packages", json={"name": "Concrete", "section_ids": []}
    ).json()
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Slab", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
        trade_package_id=pkg["id"],
    )
    # fully bill it → closed
    conv = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill", json=None)
    assert conv.status_code == 200, conv.text
    assert client.get(f"/api/purchase-orders/{po['id']}").json()["status"] == "closed"

    owner = _co(
        client,
        job["id"],
        [
            {
                "description": "More slab",
                "cost_type": "subcontract",
                "quantity": 1,
                "unit_cost": 1500,
            },
        ],
    )
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order",
        json={"commitment_id": po["id"]},
    ).json()
    assert _submit_approve(client, sub["id"]).status_code == 200

    fresh = client.get(f"/api/purchase-orders/{po['id']}").json()
    assert fresh["status"] == "partial"  # reopened — there is more to bill
    assert float(fresh["total"]) == 6500.0

    buyout = client.get(f"/api/jobs/{job['id']}/buyout").json()
    row = next(r for r in buyout["rows"] if r["package_id"] == pkg["id"])
    assert row["bought"] == 6500.0


def test_void_sub_co_refuses_when_billed(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    owner = _co(
        client,
        job["id"],
        [
            {
                "description": "Extra",
                "cost_type": "subcontract",
                "quantity": 1,
                "unit_cost": 2000,
            },
        ],
    )
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order", json={"commitment_id": po["id"]}
    ).json()
    assert _submit_approve(client, sub["id"]).status_code == 200
    # bill the whole commitment (both lines) → the added line is now billed
    conv = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill", json=None)
    assert conv.status_code == 200, conv.text
    r = client.post(f"/api/change-orders/{sub['id']}/void")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "co_billed"


# ── PDF (HTML stage) ────────────────────────────────────────────────────────


def test_pdf_html_renders_for_an_owner_co(client, db_session, seed_customer):
    job = _job(client, seed_customer.id)
    co = _co(
        client,
        job["id"],
        [LABOR, MATERIAL],
        title="Steel upgrade",
        description="Owner-requested",
    )
    row = db_session.get(ChangeOrder, co["id"])
    html = render_change_order_html(
        row, co_totals(row, row.job), get_all_settings(db_session)
    )
    assert "CHANGE ORDER" in html
    assert "CO-0001" in html
    assert "Steel upgrade" in html
    assert "Total" in html


# ── Migration ───────────────────────────────────────────────────────────────


def test_migration_upgrades_and_downgrades(tmp_path):
    db = tmp_path / "mig.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}"}

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    assert run("upgrade", "head").returncode == 0
    assert run("downgrade", "-1").returncode == 0
    assert run("upgrade", "head").returncode == 0
