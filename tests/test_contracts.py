"""The contract on the job (#552): the three contract types and their terms,
the summary math, and the owner's schedule of values built from an accepted
estimate.

A construction job can carry a lump-sum, GMP or cost-plus contract, with a
fee, a cap, contingency and retainage. The SOV breaks the contract sum into
the lines a pay application bills against, and it must balance to the sum —
the summary's sov_balance shows the difference until it does. Later issues
move the contract (change orders, #554) and bill it (progress billing, #555);
this one sets it and builds the schedule.
"""

import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.accounts import Account, AccountType
from app.models.estimates import Estimate
from app.models.jobs import SovLine
from app.services.accounting import create_journal_entry
from app.services.contracts import sov_from_estimate
from app.services.estimating import estimate_totals

ROOT = Path(__file__).resolve().parents[1]


# ── Helpers ─────────────────────────────────────────────────────────────


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _section(client, code, name, cost_type="material"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _package(client, name, section_ids):
    return client.post(
        "/api/trade-packages", json={"name": name, "section_ids": section_ids}
    ).json()


def _estimate(client, customer_id, lines, **extra):
    body = {"customer_id": customer_id, "date": "2026-02-01", "lines": lines}
    body.update(extra)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _income_expense(db):
    income = (
        db.query(Account).filter(Account.account_type == AccountType.INCOME).first()
    )
    expense = (
        db.query(Account).filter(Account.account_type == AccountType.EXPENSE).first()
    )
    assert income and expense
    return income, expense


def _post_cost(db, job_id, cost=Decimal("0"), revenue=Decimal("0")):
    """A posted journal entry carrying the job: a cost (expense debit) and a
    revenue (income credit), balanced against the other."""
    income, expense = _income_expense(db)
    create_journal_entry(
        db,
        date(2026, 7, 1),
        "job activity",
        [
            # the cost and revenue carry the job on the line; the balancing
            # lines are genuinely "No job" (no header job to inherit), so
            # profitability still ties to the P&L
            {"account_id": expense.id, "debit": cost, "credit": 0, "job_id": job_id},
            {"account_id": income.id, "debit": 0, "credit": revenue, "job_id": job_id},
            {"account_id": income.id, "debit": revenue, "credit": 0},
            {"account_id": expense.id, "debit": 0, "credit": cost},
        ],
        source_type="manual",
    )
    db.commit()


# ── PUT contract terms ────────────────────────────────────────────────────


def test_put_each_contract_type(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 500000},
    )
    assert r.status_code == 200, r.text
    c = r.json()["contract"]
    assert c["contract_type"] == "lump_sum"
    assert c["contract_amount"] == 500000.0
    assert c["original_contract_amount"] == 500000.0  # stamped on the first set
    assert c["fee"] == 0.0  # lump sum earns no separate fee

    job2 = _job(client, seed_customer.id, name="CP")
    r = client.put(
        f"/api/jobs/{job2['id']}/contract",
        json={"contract_type": "cost-plus", "contract_amount": 100, "fee_pct": 10},
    )
    assert r.status_code == 200, r.text
    assert r.json()["contract"]["contract_type"] == "cost_plus"


def test_gmp_defaults_the_cap_to_the_contract_amount(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "gmp", "contract_amount": 750000},
    )
    assert r.status_code == 200, r.text
    c = r.json()["contract"]
    assert c["cap_amount"] == 750000.0  # the guaranteed maximum defaults to the sum

    # a cap given explicitly is kept
    job2 = _job(client, seed_customer.id, name="GMP2")
    r = client.put(
        f"/api/jobs/{job2['id']}/contract",
        json={"contract_type": "gmp", "contract_amount": 800000, "cap_amount": 820000},
    )
    assert r.json()["contract"]["cap_amount"] == 820000.0

    # a GMP with no contract amount to default from is refused
    job3 = _job(client, seed_customer.id, name="GMP3")
    r = client.put(f"/api/jobs/{job3['id']}/contract", json={"contract_type": "gmp"})
    assert r.status_code == 422


def test_original_is_set_once_and_not_overwritten(client, seed_customer):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 100000},
    )
    r = client.put(f"/api/jobs/{job['id']}/contract", json={"contract_amount": 125000})
    c = r.json()["contract"]
    assert c["contract_amount"] == 125000.0  # the current/revised sum moves
    assert c["original_contract_amount"] == 100000.0  # the original does not
    assert c["changes"] == 25000.0


def test_validation_422s(client, seed_customer):
    job = _job(client, seed_customer.id)
    base = f"/api/jobs/{job['id']}/contract"
    assert client.put(base, json={"contract_type": "fixed_bid"}).status_code == 422
    assert client.put(base, json={"contract_amount": -5}).status_code == 422
    assert client.put(base, json={"retainage_pct": 150}).status_code == 422
    assert client.put(base, json={"fee_amount": -1}).status_code == 422


# ── Summary math ────────────────────────────────────────────────────────


def test_cost_plus_fee_on_a_posted_cost(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "cost_plus", "contract_amount": 0, "fee_pct": 10},
    )
    _post_cost(db_session, job["id"], cost=Decimal("40000"), revenue=Decimal("30000"))
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["cost_to_date"] == 40000.0
    assert c["fee"] == 4000.0  # 10% of actual cost
    assert c["billed_to_date"] == 30000.0


def test_a_fixed_fee_wins_over_the_percent(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "cost_plus",
            "contract_amount": 0,
            "fee_pct": 10,
            "fee_amount": 7500,
        },
    )
    _post_cost(db_session, job["id"], cost=Decimal("40000"))
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["fee"] == 7500.0


def test_cap_and_contingency_remaining(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "gmp",
            "contract_amount": 500000,
            "fee_pct": 5,
            "contingency_amount": 20000,
        },
    )
    _post_cost(db_session, job["id"], cost=Decimal("100000"))
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["cap_amount"] == 500000.0
    # cap_remaining = cap − (cost + fee) = 500000 − (100000 + 5000)
    assert c["cap_remaining"] == 395000.0
    assert c["contingency_amount"] == 20000.0
    assert c["contingency_used"] == 0.0
    assert c["contingency_remaining"] == 20000.0


# ── SOV from an accepted estimate ───────────────────────────────────────


def _build_estimate_with_shape(client, seed_customer):
    """Two packages, an unassigned line, an allowance, an excluded and an
    accepted alternate. Zero markups and rate == unit_cost, so each line's
    price equals its cost and the SOV balances to the bid total."""
    conc = _section(client, "03 30 00", "Cast-in-Place Concrete")
    elec = _section(client, "26 05 00", "Electrical")
    pkg_c = _package(client, "Concrete", [conc["id"]])
    pkg_e = _package(client, "Electrical", [elec["id"]])
    return (
        _estimate(
            client,
            seed_customer.id,
            [
                {
                    "description": "Slab",
                    "quantity": 1,
                    "rate": 10000,
                    "unit_cost": 10000,
                    "trade_package_id": pkg_c["id"],
                },
                {
                    "description": "Service",
                    "quantity": 1,
                    "rate": 20000,
                    "unit_cost": 20000,
                    "trade_package_id": pkg_e["id"],
                },
                {
                    "description": "General",
                    "quantity": 1,
                    "rate": 5000,
                    "unit_cost": 5000,
                },
                {
                    "description": "Flooring allowance",
                    "quantity": 1,
                    "rate": 3000,
                    "line_kind": "allowance",
                },
                {
                    "description": "Alt roof (declined)",
                    "quantity": 1,
                    "rate": 2000,
                    "unit_cost": 2000,
                    "line_kind": "alternate",
                },
                {
                    "description": "Alt concrete finish",
                    "quantity": 1,
                    "rate": 4000,
                    "unit_cost": 4000,
                    "line_kind": "alternate",
                    "alternate_accepted": True,
                    "trade_package_id": pkg_c["id"],
                },
            ],
        ),
        pkg_c,
        pkg_e,
    )


def test_from_estimate_builds_and_balances(client, db_session, seed_customer):
    est, pkg_c, pkg_e = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id)
    accepted = client.post(
        f"/api/estimates/{est['id']}/accept", json={"job_id": job["id"]}
    )
    assert accepted.status_code == 200, accepted.text

    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    sov = body["sov"]
    work = [line for line in sov if line["kind"] == "work"]
    allow = [line for line in sov if line["kind"] == "allowance"]
    # one work line per package in order (Concrete, Electrical) + Unassigned last
    assert [line["description"] for line in work] == [
        "Concrete",
        "Electrical",
        "Unassigned",
    ]
    by_desc = {line["description"]: line for line in work}
    # Concrete carries its base slab (10000) + the accepted alternate (4000)
    assert float(by_desc["Concrete"]["scheduled_value"]) == 14000.0
    assert float(by_desc["Electrical"]["scheduled_value"]) == 20000.0
    assert float(by_desc["Unassigned"]["scheduled_value"]) == 5000.0
    # the declined alternate is in no line
    assert all("declined" not in line["description"].lower() for line in sov)
    # the allowance is its own line at its amount
    assert len(allow) == 1 and float(allow[0]["scheduled_value"]) == 3000.0
    # line_no is 1..n, item_no zero-padded by default
    assert [line["line_no"] for line in sov] == list(range(1, len(sov) + 1))
    assert sov[0]["item_no"] == "01"

    c = body["contract"]
    bid_total = float(estimate_totals(db_session.get(Estimate, est["id"]))["bid_total"])
    assert c["contract_type"] == "lump_sum"  # defaulted
    assert c["contract_amount"] == bid_total
    assert c["sov_total"] == bid_total  # 14000 + 20000 + 5000 + 3000 = 42000
    assert c["sov_total"] == 42000.0
    assert c["sov_balance"] == 0.0  # balances when the sum came from the estimate
    assert c["contract_estimate_id"] == est["id"]


def test_from_estimate_adds_a_contingency_line_when_set(client, seed_customer):
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id, name="WithContingency")
    client.put(f"/api/jobs/{job['id']}/contract", json={"contingency_amount": 6000})
    client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": job["id"]})
    body = client.post(
        f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}"
    ).json()
    cont = [line for line in body["sov"] if line["kind"] == "contingency"]
    assert len(cont) == 1 and float(cont[0]["scheduled_value"]) == 6000.0
    # the contingency pushes the SOV past the estimate's bid total, so the
    # balance is negative until the operator reconciles it (the seam #554 fills)
    assert body["contract"]["sov_balance"] == -6000.0


def test_from_estimate_409_when_not_accepted_or_foreign(client, seed_customer):
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    job = _job(client, seed_customer.id)
    # not accepted yet
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "estimate_not_accepted"

    # accepted onto another job → mismatch for this one
    other = _job(client, seed_customer.id, name="Other")
    client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": other["id"]})
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "estimate_job_mismatch"


def test_sov_from_estimate_is_pure(client, db_session, seed_customer):
    """The pure builder returns the line dicts without touching the DB."""
    est, _, _ = _build_estimate_with_shape(client, seed_customer)
    estimate = db_session.get(Estimate, est["id"])
    lines = sov_from_estimate(estimate, estimate_totals(estimate))
    assert [line["kind"] for line in lines] == ["work", "work", "work", "allowance"]
    assert db_session.query(SovLine).count() == 0  # nothing written


# ── PUT /sov ────────────────────────────────────────────────────────────


def test_put_sov_renumbers_and_defaults_item_no(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Sitework", "kind": "work", "scheduled_value": 10000},
                {
                    "item_no": "A",
                    "description": "Allowance",
                    "kind": "allowance",
                    "scheduled_value": 2000,
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["sov_total"] == 12000.0
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    assert [line["line_no"] for line in sov] == [1, 2]
    assert sov[0]["item_no"] == "01"  # blank defaults to zero-padded line_no
    assert sov[1]["item_no"] == "A"  # a given item_no is kept


def test_put_sov_refuses_a_negative_value(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={"lines": [{"description": "Bad", "scheduled_value": -1}]},
    )
    assert r.status_code == 422

    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={"lines": [{"description": "Bad kind", "kind": "profit"}]},
    )
    assert r.status_code == 422


# ── Detail response + migration ─────────────────────────────────────────


def test_detail_response_carries_contract(client, seed_customer):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 250000},
    )
    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert detail["contract"]["contract_type"] == "lump_sum"
    assert detail["contract"]["contract_amount"] == 250000.0


def test_job_profitability_rows_gain_contract_type_and_fee(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "cost_plus", "contract_amount": 0, "fee_pct": 10},
    )
    _post_cost(db_session, job["id"], cost=Decimal("40000"), revenue=Decimal("30000"))
    from app.services.jobs_service import job_profitability

    rows = job_profitability(db_session, job_ids=[job["id"]], include_no_job=False)
    row = next(r for r in rows if r["job_id"] == job["id"])
    assert row["contract_type"] == "cost_plus"
    assert row["fee"] == 4000.0


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
