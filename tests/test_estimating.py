"""Construction estimating (#551): the bid math, pricing from cost, the
accept-locks-the-budget flow, and the convert carve-outs.

The compounding markups are the heart of it — general conditions, insurance,
fee and bond stack in that order on the base cost, each on the running total
before it, and allowances ride along with no markup. Accepting an estimate
turns its seeded budget into the contract: those rows lock, and only a change
order moves them (a later issue).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    body = {
        "customer_id": customer_id,
        "date": "2026-02-01",
        "lines": lines,
    }
    body.update(extra)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _totals(client, est_id):
    return client.get(f"/api/estimates/{est_id}").json()["totals"]


# ── The compounding markups ────────────────────────────────────────────────


def test_markups_compound_in_order_to_exact_cents(client, seed_customer):
    # base cost 100,000; 8 / 1.5 / 5 / 1 percent, compounding
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1000, "rate": 0, "unit_cost": 100}],
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
    assert float(t["bid_total"]) == 116252.01


def test_allowances_carry_no_markup_but_are_in_the_bid(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Shell", "quantity": 1000, "rate": 0, "unit_cost": 100},
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
    # markups are unchanged by the allowance; the allowance is added after
    assert float(t["gc"]) == 8000.0
    assert float(t["allowance_total"]) == 5000.0
    assert float(t["bid_total"]) == 116252.01 + 5000.0


def test_alternates_count_only_once_accepted(client, seed_customer):
    base = [{"description": "Shell", "quantity": 1000, "rate": 0, "unit_cost": 100}]
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
    assert len(tp["alternates"]) == 1 and tp["alternates"][0]["accepted"] is False

    accepted = _estimate(
        client, seed_customer.id, base + [dict(alt, alternate_accepted=True)]
    )
    ta = _totals(client, accepted["id"])
    assert float(ta["cost_subtotal"]) == 101500.0  # folded into the base scope
    assert ta["alternates"][0]["accepted"] is True


def test_unit_price_lines_are_in_no_total(client, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Shell", "quantity": 1000, "rate": 0, "unit_cost": 100},
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
    assert float(t["cost_subtotal"]) == 100000.0
    assert len(t["unit_prices"]) == 1
    assert t["unit_prices"][0]["unit"] == "CY"
    assert float(t["unit_prices"][0]["rate"]) == 85.0


def test_by_package_groups_including_unassigned(client, seed_customer):
    conc = _section(client, "03 30 00", "Cast-in-Place Concrete")
    pkg = client.post(
        "/api/trade-packages", json={"name": "Concrete", "section_ids": [conc["id"]]}
    ).json()
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
            {"description": "General", "quantity": 1, "rate": 400, "unit_cost": 300},
        ],
    )
    t = _totals(client, est["id"])
    by = {g["package_name"]: g for g in t["by_package"]}
    assert set(by) == {"Concrete", "Unassigned"}
    assert float(by["Concrete"]["cost"]) == 5000.0
    assert float(by["Concrete"]["price"]) == 6000.0
    assert by["Unassigned"]["line_count"] == 1
    # each line also names its package on the GET response
    line = next(
        line
        for line in client.get(f"/api/estimates/{est['id']}").json()["lines"]
        if line["trade_package_id"] == pkg["id"]
    )
    assert line["trade_package_name"] == "Concrete"


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
    assert float(lines["Cost line"]["amount"]) == 116.25
    assert float(lines["No cost"]["rate"]) == 50.0  # no unit cost → untouched
    assert float(lines["Allowance"]["rate"]) == 5000.0  # allowances never repriced


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


def test_accept_locks_the_estimate_rows_and_keeps_manual(client, seed_customer):
    job = _job(client, seed_customer.id)
    coded = _section(client, "06 10 00", "Rough Carpentry", "material")
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
            }
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
    assert (
        rows[coded["id"]]["source"] == "estimate"
        and rows[coded["id"]]["locked"] is True
    )
    assert (
        rows[other["id"]]["source"] == "manual" and rows[other["id"]]["locked"] is False
    )
    assert float(rows[other["id"]]["amount"]) == 1200.0


def test_accept_needs_a_job(client, seed_customer):
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


def test_accept_also_works_through_a_put(client, seed_customer):
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


def test_locked_budget_refuses_put_and_from_estimate(client, seed_customer):
    job = _job(client, seed_customer.id)
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Shell", "quantity": 1, "rate": 100, "unit_cost": 80}],
        job_id=job["id"],
    )
    client.post(f"/api/estimates/{est['id']}/accept", json={})

    put = client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_type": "labor", "amount": 999}]},
    )
    assert put.status_code == 409 and put.json()["detail"]["code"] == "budget_locked"

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


@pytest.mark.skipif(__import__("shutil").which is None, reason="never")
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
