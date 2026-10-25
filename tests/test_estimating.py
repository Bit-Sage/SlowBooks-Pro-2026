"""Construction estimating (#551): the bid math, pricing from cost, the
accept-locks-the-budget flow, and the convert carve-outs.

The bid the customer pays is the sum of the line PRICES. Beside it sits the
contractor's cost view: a base cost, the four markups that compound in order
(general conditions → insurance → fee → bond), a markup target, the
directly-priced lines, and the drift between the priced bid and the target —
which "Price from cost" closes to the rounding. Accepting an estimate turns
its seeded budget into the contract: those rows lock, and only a change order
(a later issue) moves them.
"""

import os
import subprocess
import sys
from pathlib import Path

from app.models.invoices import InvoiceLine

ROOT = Path(__file__).resolve().parents[1]


def _section(client, code, name, cost_type="material"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _estimate(client, customer_id, lines, **extra):
    body = {"customer_id": customer_id, "date": "2026-02-01", "lines": lines}
    body.update(extra)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _totals(client, est_id):
    return client.get(f"/api/estimates/{est_id}").json()["totals"]


# ── The compounding markups (the cost view) ────────────────────────────────


def test_markups_compound_in_order_to_exact_cents(client, seed_customer):
    # base cost 100,000; 8 / 1.5 / 5 / 1 percent, compounding
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100000}],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    t = _totals(client, est["id"])
    assert float(t["cost_subtotal"]) == 100000.0
    assert float(t["gc"]) == 8000.0
    assert float(t["insurance"]) == 1620.0
    assert float(t["fee"]) == 5481.0
    assert float(t["bond"]) == 1151.01
    assert float(t["markup_target"]) == 116252.01


def test_a_rate_priced_line_with_no_unit_cost_is_the_bid(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Lump sum", "quantity": 1, "rate": 500}],
        gc_pct=8,
        fee_pct=5,
    )
    t = _totals(client, est["id"])
    assert float(t["cost_subtotal"]) == 0.0  # nothing to mark up
    assert float(t["markup_target"]) == 0.0
    assert float(t["priced_direct"]) == 500.0
    assert float(t["bid_total"]) == 500.0
    assert float(t["drift"]) == 0.0


def test_allowances_are_in_the_bid_but_not_the_cost_view(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Shell",
                "quantity": 1,
                "rate": 116252.01,
                "unit_cost": 100000,
            },
            {
                "description": "Appliance allowance",
                "quantity": 1,
                "rate": 5000,
                "line_kind": "allowance",
            },
        ],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    t = _totals(client, est["id"])
    assert float(t["markup_target"]) == 116252.01  # allowance adds no markup
    assert float(t["allowance_total"]) == 5000.0
    assert float(t["bid_total"]) == 116252.01 + 5000.0  # price of both lines
    assert abs(float(t["drift"])) <= 0.01


def test_alternates_count_only_once_accepted(client, seed_customer):
    base = [{"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100000}]
    alt = {
        "description": "Upgrade alt",
        "quantity": 1,
        "rate": 2000,
        "unit_cost": 1500,
        "line_kind": "alternate",
    }

    pending = _estimate(client, seed_customer.id, base + [dict(alt)])
    tp = _totals(client, pending["id"])
    assert float(tp["cost_subtotal"]) == 100000.0  # not accepted → not counted
    assert float(tp["bid_total"]) == 0.0  # base rate 0, alt excluded
    assert tp["alternates"][0]["accepted"] is False

    accepted = _estimate(
        client, seed_customer.id, base + [dict(alt, alternate_accepted=True)]
    )
    ta = _totals(client, accepted["id"])
    assert float(ta["cost_subtotal"]) == 101500.0  # folded into the base scope
    assert float(ta["bid_total"]) == 2000.0  # accepted alt's price joins the bid
    assert ta["alternates"][0]["accepted"] is True


def test_unit_price_lines_are_in_no_total(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Shell", "quantity": 1, "rate": 5000, "unit_cost": 4000},
            {
                "description": "Rock excavation",
                "quantity": 1,
                "rate": 85,
                "unit": "CY",
                "line_kind": "unit_price",
            },
        ],
    )
    t = _totals(client, est["id"])
    assert float(t["bid_total"]) == 5000.0  # unit price excluded
    assert len(t["unit_prices"]) == 1
    assert t["unit_prices"][0]["unit"] == "CY"
    assert float(t["unit_prices"][0]["rate"]) == 85.0


def test_bid_total_is_pre_tax_with_tax_beside_it(client, seed_customer):
    # the contract sum / schedule of values (#552) are pre-tax; tax rides each
    # invoice. bid_total is the pre-tax price sum, with bid_tax and
    # bid_total_with_tax beside it mirroring the estimate's stored figures.
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Work", "quantity": 1, "rate": 1000}],
        tax_rate=0.07,
    )
    full = client.get(f"/api/estimates/{est['id']}").json()
    t = full["totals"]
    assert float(t["bid_total"]) == 1000.0  # pre-tax
    assert float(t["bid_tax"]) == 70.0
    assert float(t["bid_total_with_tax"]) == 1070.0
    assert float(t["bid_total"]) + float(t["bid_tax"]) == float(t["bid_total_with_tax"])
    assert float(t["bid_total_with_tax"]) == float(full["total"])


def test_a_deduct_alternate_subtracts(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Base", "quantity": 1, "rate": 100},
            {
                "description": "Deduct skylight",
                "quantity": 1,
                "rate": -20,
                "line_kind": "alternate",
                "alternate_accepted": True,
            },
        ],
        tax_rate=0,
    )
    full = client.get(f"/api/estimates/{est['id']}").json()
    assert float(full["totals"]["bid_total"]) == 80.0  # 100 − 20
    assert float(full["subtotal"]) == 80.0  # the stored total foots the bid


def test_by_package_groups_by_id_not_name(client, seed_customer):
    conc = _section(client, "03 30 00", "Cast-in-Place Concrete")
    pkg = client.post(
        "/api/trade-packages", json={"name": "Concrete", "section_ids": [conc["id"]]}
    ).json()
    # a package literally named "Unassigned" must NOT merge with the bucket
    ghost = client.post("/api/trade-packages", json={"name": "Unassigned"}).json()
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Slab",
                "quantity": 100,
                "rate": 60,
                "unit_cost": 50,
                "trade_package_id": pkg["id"],
            },
            {
                "description": "Named-Unassigned work",
                "quantity": 1,
                "rate": 700,
                "unit_cost": 500,
                "trade_package_id": ghost["id"],
            },
            {"description": "No package", "quantity": 1, "rate": 400, "unit_cost": 300},
        ],
    )
    t = _totals(client, est["id"])
    by = {(g["package_id"], g["package_name"]): g for g in t["by_package"]}
    assert (pkg["id"], "Concrete") in by
    assert (ghost["id"], "Unassigned") in by  # the real package
    assert (None, "Unassigned") in by  # the no-package bucket, separate
    assert float(by[(pkg["id"], "Concrete")]["cost"]) == 5000.0
    assert float(by[(pkg["id"], "Concrete")]["price"]) == 6000.0


# ── Price from cost ─────────────────────────────────────────────────────────


def test_reprice_sets_rates_and_leaves_the_right_lines_alone(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Cost line", "quantity": 1, "rate": 0, "unit_cost": 100},
            {"description": "No cost", "quantity": 1, "rate": 50},
            {
                "description": "Allowance",
                "quantity": 1,
                "rate": 5000,
                "unit_cost": 999,
                "line_kind": "allowance",
            },
            {
                "description": "Unit price",
                "quantity": 1,
                "rate": 10,
                "unit_cost": 8,
                "line_kind": "unit_price",
            },
        ],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    r = client.post(f"/api/estimates/{est['id']}/reprice")
    assert r.status_code == 200, r.text
    lines = {line["description"]: line for line in r.json()["lines"]}
    # 100 × 1.08 × 1.015 × 1.05 × 1.01 = 116.25201 → 116.25
    assert float(lines["Cost line"]["rate"]) == 116.25
    assert float(lines["No cost"]["rate"]) == 50.0  # no unit cost → untouched
    assert float(lines["Allowance"]["rate"]) == 5000.0  # allowances never repriced
    assert float(lines["Unit price"]["rate"]) == 10.0  # unit prices never repriced


def test_reprice_prices_alternates_accepted_or_not(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Pending alt",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 200,
                "line_kind": "alternate",
            },
            {
                "description": "Accepted alt",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 200,
                "line_kind": "alternate",
                "alternate_accepted": True,
            },
        ],
        gc_pct=10,
    )
    r = client.post(f"/api/estimates/{est['id']}/reprice")
    lines = {line["description"]: line for line in r.json()["lines"]}
    assert float(lines["Pending alt"]["rate"]) == 220.0  # 200 × 1.10
    assert float(lines["Accepted alt"]["rate"]) == 220.0


def test_reprice_closes_the_drift_to_cents(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "A", "quantity": 1, "rate": 0, "unit_cost": 40000},
            {"description": "B", "quantity": 1, "rate": 0, "unit_cost": 33333},
        ],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    before = _totals(client, est["id"])
    assert abs(float(before["drift"])) > 1  # rate 0, so the bid is far below target
    client.post(f"/api/estimates/{est['id']}/reprice")
    after = _totals(client, est["id"])
    assert abs(float(after["drift"])) <= 0.05  # repriced bid foots the target


def test_reprice_409s_once_accepted(client, seed_customer):
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 100}],
        job_id=job["id"],
    )
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    assert client.post(f"/api/estimates/{est['id']}/reprice").status_code == 409


# ── Accept locks the budget ─────────────────────────────────────────────────


def test_accept_seeds_only_bought_scope_and_locks(client, seed_customer):
    job = _job(client, seed_customer.id)
    coded = _section(client, "06 10 00", "Rough Carpentry", "material")
    skipped = _section(client, "26 00 00", "Electrical", "subcontract")
    other = _section(client, "09 90 00", "Painting", "subcontract")
    # a manual budget row on a code the estimate never touches survives
    client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_code_id": other["id"], "amount": 1200}]},
    )
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Framing",
                "quantity": 10,
                "rate": 300,
                "unit_cost": 200,
                "cost_code_id": coded["id"],
            },
            # a unit-price line with a code must NOT seed a budget row
            {
                "description": "Unit",
                "quantity": 1,
                "rate": 5,
                "unit_cost": 4,
                "cost_code_id": skipped["id"],
                "line_kind": "unit_price",
            },
        ],
        job_id=job["id"],
    )
    accepted = client.post(f"/api/estimates/{est['id']}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["accepted_at"] is not None

    rows = {
        r["cost_code_id"]: r
        for r in client.get(f"/api/jobs/{job['id']}/budgets").json()
    }
    assert skipped["id"] not in rows  # unit-price line seeded nothing
    assert (
        rows[coded["id"]]["source"] == "estimate"
        and rows[coded["id"]]["locked"] is True
    )
    assert (
        rows[other["id"]]["source"] == "manual" and rows[other["id"]]["locked"] is False
    )
    assert float(rows[other["id"]]["amount"]) == 1200.0


def test_post_accept_needs_a_job(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 80}],
    )
    r = client.post(f"/api/estimates/{est['id']}/accept", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_job"


def test_accept_refuses_a_second_time(client, seed_customer):
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 80}],
        job_id=job["id"],
    )
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 200
    assert client.post(f"/api/estimates/{est['id']}/accept", json={}).status_code == 409


def test_accept_refuses_another_estimate_while_locked(client, seed_customer):
    job = _job(client, seed_customer.id)
    first = _estimate(
        client,
        seed_customer.id,
        [{"description": "A", "quantity": 1, "rate": 100, "unit_cost": 80}],
        job_id=job["id"],
    )
    assert (
        client.post(f"/api/estimates/{first['id']}/accept", json={}).status_code == 200
    )
    second = _estimate(
        client,
        seed_customer.id,
        [{"description": "B", "quantity": 1, "rate": 200, "unit_cost": 150}],
        job_id=job["id"],
    )
    r = client.post(f"/api/estimates/{second['id']}/accept", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "budget_locked"


def test_put_accepted_locks_with_a_job_and_is_idempotent(client, seed_customer):
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 80}],
        job_id=job["id"],
    )
    r = client.put(f"/api/estimates/{est['id']}", json={"status": "accepted"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
    assert any(b["locked"] for b in client.get(f"/api/jobs/{job['id']}/budgets").json())
    # re-PUT accepted is a no-op, not a 409
    again = client.put(f"/api/estimates/{est['id']}", json={"status": "accepted"})
    assert again.status_code == 200 and again.json()["status"] == "accepted"


def test_put_accepted_without_a_job_is_a_plain_status_change(client, seed_customer):
    # a business company with no job accepts the estimate, nothing to lock
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100}],
    )
    r = client.put(f"/api/estimates/{est['id']}", json={"status": "accepted"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"


def test_a_settled_estimate_refuses_scope_edits(client, seed_customer):
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 80}],
        job_id=job["id"],
    )
    client.post(f"/api/estimates/{est['id']}/accept", json={})
    for edit in (
        {"lines": [{"description": "X", "quantity": 1, "rate": 1}]},
        {"gc_pct": 9},
        {"tax_rate": 0.05},
        {"customer_id": seed_customer.id},
    ):
        r = client.put(f"/api/estimates/{est['id']}", json=edit)
        assert r.status_code == 409, (edit, r.text)
    # notes and expiration still edit
    ok = client.put(
        f"/api/estimates/{est['id']}",
        json={"notes": "signed", "expiration_date": "2026-12-31"},
    )
    assert ok.status_code == 200 and ok.json()["notes"] == "signed"


def test_locked_budget_is_row_level(client, seed_customer):
    job = _job(client, seed_customer.id)
    free = _section(client, "09 90 00", "Painting", "subcontract")
    # an uncoded line seeds a whole-job budget row — a key a manual save can
    # also address, so the row-level lock is exercisable (a coded+typed row
    # can't be re-sent: the save schema forbids both keys on one row)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Whole job", "quantity": 1, "rate": 300, "unit_cost": 200}],
        job_id=job["id"],
    )
    client.post(f"/api/estimates/{est['id']}/accept", json={})
    locked = client.get(f"/api/jobs/{job['id']}/budgets").json()
    assert locked[0]["cost_code_id"] is None and locked[0]["locked"] is True

    # a manual row on a specific code still saves after acceptance
    add = client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_code_id": free["id"], "amount": 500}]},
    )
    assert add.status_code == 200, add.text
    rows = {r["cost_code_id"]: r for r in add.json()}
    assert float(rows[free["id"]]["amount"]) == 500.0
    assert rows[None]["locked"] is True  # the whole-job estimate row untouched

    # overwriting the LOCKED whole-job row's key is refused
    hit = client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"amount": 1}]},
    )
    assert hit.status_code == 409 and hit.json()["detail"]["code"] == "budget_locked"

    # re-seeding from another estimate stays a job-wide 409
    other = _estimate(
        client,
        seed_customer.id,
        [{"description": "Other", "quantity": 1, "rate": 10, "unit_cost": 5}],
        job_id=job["id"],
    )
    seed = client.post(f"/api/jobs/{job['id']}/budgets/from-estimate/{other['id']}")
    assert seed.status_code == 409 and seed.json()["detail"]["code"] == "budget_locked"


# ── Convert carries the cost basis and drops what isn't scope ───────────────


def test_convert_excludes_unit_price_and_pending_alternates(
    client, db_session, seed_accounts, seed_customer
):
    coded = _section(client, "26 00 00", "Electrical", "subcontract")
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Base work",
                "quantity": 2,
                "rate": 500,
                "unit_cost": 350,
                "cost_code_id": coded["id"],
            },
            {
                "description": "Accepted alt",
                "quantity": 1,
                "rate": 800,
                "unit_cost": 600,
                "line_kind": "alternate",
                "alternate_accepted": True,
            },
            {
                "description": "Pending alt",
                "quantity": 1,
                "rate": 900,
                "line_kind": "alternate",
            },
            {
                "description": "Unit price",
                "quantity": 1,
                "rate": 45,
                "unit": "LF",
                "line_kind": "unit_price",
            },
        ],
    )
    inv = client.post(f"/api/estimates/{est['id']}/convert")
    assert inv.status_code == 200, inv.text
    descs = [line["description"] for line in inv.json()["lines"]]
    assert descs == ["Base work", "Accepted alt"]  # unit price + pending alt dropped

    base_line = (
        db_session.query(InvoiceLine)
        .filter(InvoiceLine.description == "Base work")
        .one()
    )
    assert base_line.cost_type == "subcontract"
    assert float(base_line.unit_cost) == 350.0


# ── Migration up/down ───────────────────────────────────────────────────────


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

    up = run("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = run("downgrade", "-1")
    assert down.returncode == 0, down.stderr
    again = run("upgrade", "head")
    assert again.returncode == 0, again.stderr
