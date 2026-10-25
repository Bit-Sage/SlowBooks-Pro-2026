"""Per-line cost type (#550): bill / PO / estimate / vendor-credit lines
accept and return cost_type, the server fills it from the code when blank, an
unknown type is refused the way the cost code's type check is, and the job
cost tree buckets each cost by its EFFECTIVE type (the line's own, else the
code's) — so a coded line lands under its own type's division › section
branch, and a blank one under the code's."""

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction


def _expense_income_bank(db):
    income = (
        db.query(Account).filter(Account.account_type == AccountType.INCOME).first()
    )
    expense = (
        db.query(Account).filter(Account.account_type == AccountType.EXPENSE).first()
    )
    bank = db.query(Account).filter(Account.account_type == AccountType.ASSET).first()
    return expense, income, bank


def _code(client, code, name, cost_type="subcontract"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _job(client, customer_id):
    r = client.post("/api/jobs", json={"customer_id": customer_id, "name": "Tower"})
    assert r.status_code == 201, r.text
    return r.json()


# ── Each line type accepts and returns cost_type ───────────────────────────


def test_bill_line_accepts_returns_and_fills_cost_type(
    client, db_session, seed_accounts, seed_customer
):
    expense, _income, _bank = _expense_income_bank(db_session)
    code = _code(client, "03 30 00", "Cast-in-Place Concrete", "subcontract")
    vendor = client.post("/api/vendors", json={"name": "Ready Mix"}).json()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "RM-1",
            "date": "2026-07-04",
            "lines": [
                # explicit type overrides the code's
                {
                    "account_id": expense.id,
                    "rate": 100,
                    "cost_code_id": code["id"],
                    "cost_type": "material",
                },
                # blank type fills from the code (subcontract)
                {"account_id": expense.id, "rate": 50, "cost_code_id": code["id"]},
            ],
        },
    )
    assert bill.status_code in (200, 201), bill.text
    lines = bill.json()["lines"]
    assert lines[0]["cost_type"] == "material"
    assert lines[1]["cost_type"] == "subcontract"

    # the posted ledger line carries the line's type, not just the code's
    txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "bill",
            Transaction.source_id == bill.json()["id"],
        )
        .first()
    )
    by_amt = {
        float(ln.debit): ln.cost_type for ln in txn.lines if ln.debit and ln.debit > 0
    }
    assert by_amt[100.0] == "material"  # line's type wins
    assert by_amt[50.0] == "subcontract"  # fell back to the code


def test_po_estimate_vendor_credit_lines_carry_cost_type(
    client, seed_accounts, seed_customer, db_session
):
    expense, _income, _bank = _expense_income_bank(db_session)
    code = _code(client, "26 50 00", "Lighting", "subcontract")
    vendor = client.post("/api/vendors", json={"name": "Sparky"}).json()

    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-06",
            "lines": [
                {
                    "description": "fixtures",
                    "quantity": 1,
                    "rate": 200,
                    "cost_code_id": code["id"],
                    "cost_type": "material",
                }
            ],
        },
    )
    assert po.status_code in (200, 201), po.text
    assert po.json()["lines"][0]["cost_type"] == "material"
    # blank fills from the code on the PO too
    po2 = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-06",
            "lines": [
                {
                    "description": "wire",
                    "quantity": 1,
                    "rate": 20,
                    "cost_code_id": code["id"],
                }
            ],
        },
    )
    assert po2.json()["lines"][0]["cost_type"] == "subcontract"

    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-06",
            "lines": [
                {
                    "description": "lights",
                    "quantity": 1,
                    "rate": 300,
                    "cost_code_id": code["id"],
                    "cost_type": "material",
                }
            ],
        },
    )
    assert est.status_code in (200, 201), est.text
    assert est.json()["lines"][0]["cost_type"] == "material"

    vc = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-06",
            "lines": [
                {
                    "account_id": expense.id,
                    "rate": 40,
                    "cost_code_id": code["id"],
                    "cost_type": "material",
                }
            ],
        },
    )
    assert vc.status_code in (200, 201), vc.text
    assert vc.json()["lines"][0]["cost_type"] == "material"


def test_unknown_cost_type_is_refused_like_the_cost_code_check(
    client, seed_accounts, db_session
):
    expense, _income, _bank = _expense_income_bank(db_session)
    vendor = client.post("/api/vendors", json={"name": "Yard"}).json()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "Y-9",
            "date": "2026-07-04",
            "lines": [{"account_id": expense.id, "rate": 10, "cost_type": "magic"}],
        },
    )
    # consistent with the cost code's own unknown-type check (422)
    assert r.status_code in (400, 422), r.text


# ── Job cost tree: line's type and division roll-up ───────────────────────


def test_job_cost_tree_reads_line_type_and_rolls_section_under_division(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    expense, _income, _bank = _expense_income_bank(db_session)
    # a division and a section beneath it, both subcontract
    client.post("/api/cost-codes/standard")  # seeds 03 and 03 30 00 with parent
    rows = {c["code"]: c for c in client.get("/api/cost-codes").json()}
    section = rows["03 30 00"]
    assert section["parent_code"] == "03"

    vendor = client.post("/api/vendors", json={"name": "Ready Mix"}).json()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "RM-2",
            "date": "2026-07-04",
            "job_id": job["id"],
            "lines": [
                # coded to the section, no type → the code's "other"
                {"account_id": expense.id, "rate": 1000, "cost_code_id": section["id"]},
                # uncoded but typed material → the line's own type drives it
                {"account_id": expense.id, "rate": 200, "cost_type": "material"},
            ],
        },
    )
    assert bill.status_code in (200, 201), bill.text

    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()
    by_type = {t["cost_type"]: t for t in tree["types"]}
    # the uncoded line lands under the material type by its own cost_type
    assert by_type["material"]["uncoded"]["figures"]["actual"] == 200.0
    # the section cost rolls up under division 03 in the "other" type tree
    other = by_type["other"]
    div = next(c for c in other["codes"] if c["code"] == "03")
    assert div["figures"]["actual"] == 1000.0  # division aggregates its sections
    sec = next(c for c in div["children"] if c["code"] == "03 30 00")
    assert sec["figures"]["actual"] == 1000.0


def test_estimate_budget_carries_the_line_type(client, seed_accounts, seed_customer):
    # budgets seeded from an estimate carry the line's cost type (#550)
    job = _job(client, seed_customer.id)
    code = _code(client, "03 30 00", "Concrete", "subcontract")
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "job_id": job["id"],
            "lines": [
                {
                    "description": "slab",
                    "quantity": 1,
                    "rate": 1000,
                    "unit_cost": 400,
                    "cost_code_id": code["id"],
                    "cost_type": "labor",
                },
            ],
        },
    )
    assert est.status_code in (200, 201), est.text
    seeded = client.post(
        f"/api/jobs/{job['id']}/budgets/from-estimate/{est.json()['id']}"
    )
    assert seeded.status_code == 200, seeded.text
    row = next(r for r in seeded.json() if r["cost_code_id"] == code["id"])
    assert row["cost_type"] == "labor"


def test_coded_line_rolls_up_under_its_own_type_not_the_codes(
    client, db_session, seed_accounts, seed_customer
):
    # a 03 30 00 (default "other") code with two lines: one typed subcontract,
    # one blank. Each cost lands under its effective type's division branch.
    job = _job(client, seed_customer.id)
    expense, _income, _bank = _expense_income_bank(db_session)
    client.post("/api/cost-codes/standard")
    section = {c["code"]: c for c in client.get("/api/cost-codes").json()}["03 30 00"]
    vendor = client.post("/api/vendors", json={"name": "Ready Mix"}).json()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "RM-3",
            "date": "2026-07-04",
            "job_id": job["id"],
            "lines": [
                {
                    "account_id": expense.id,
                    "rate": 1000,
                    "cost_code_id": section["id"],
                    "cost_type": "subcontract",
                },
                {"account_id": expense.id, "rate": 500, "cost_code_id": section["id"]},
            ],
        },
    )
    assert bill.status_code in (200, 201), bill.text

    by_type = {
        t["cost_type"]: t
        for t in client.get(f"/api/jobs/{job['id']}/cost-tree").json()["types"]
    }

    def section_actual(type_code):
        div = next(c for c in by_type[type_code]["codes"] if c["code"] == "03")
        sec = next(c for c in div["children"] if c["code"] == "03 30 00")
        return sec["figures"]["actual"], div["figures"]["actual"]

    # the typed line is under subcontract, the blank one falls back to "other"
    assert section_actual("subcontract") == (1000.0, 1000.0)
    assert section_actual("other") == (500.0, 500.0)
