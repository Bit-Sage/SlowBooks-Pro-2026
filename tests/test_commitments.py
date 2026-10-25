"""Buyout and commitments (#553): a commitment is a purchase order — a plain
PO or a subcontract — whose lines are its schedule of values and which bills
in parts, optionally holding retainage. Committed cost becomes the remaining
(unbilled) open commitment, with committed_total and billed_to_date alongside.
The buyout endpoint pairs each package's bid against what was bought, and the
bills carry the Sub's compliance flags and a lien-waiver status.

Later issues add sub change orders (#554), progress billing / retainage
release (#555) and the buyout report / job cost by vendor (#556); this one
leaves those seams.
"""

import os
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.models.accounts import Account
from app.models.bills import Bill, BillLine
from app.models.purchase_orders import PurchaseOrder, PurchaseOrderLine
from app.models.transactions import TransactionLine

ROOT = Path(__file__).resolve().parents[1]


# ── Helpers ─────────────────────────────────────────────────────────────


def _gl(db, account_id):
    rows = db.query(TransactionLine).filter_by(account_id=account_id).all()
    return sum(
        (Decimal(str(r.debit)) - Decimal(str(r.credit)) for r in rows), Decimal("0")
    )


def _retainage_account(db):
    return db.query(Account).filter(Account.name == "Retainage Payable").first()


def _vendor(client, name="Ace Concrete", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _package(client, name):
    r = client.post("/api/trade-packages", json={"name": name, "section_ids": []})
    assert r.status_code == 201, r.text
    return r.json()


def _po(client, vendor_id, lines, **extra):
    body = {"vendor_id": vendor_id, "date": "2026-09-01", "lines": lines}
    body.update(extra)
    r = client.post("/api/purchase-orders", json=body)
    return r


def _sent_po(client, vendor_id, lines, **extra):
    """A commitment that has been issued — a PO only counts as committed once
    it has left draft."""
    po = _po(client, vendor_id, lines, **extra).json()
    up = client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    assert up.status_code == 200, up.text
    return up.json()


def _convert(client, po_id, **body):
    return client.post(
        f"/api/purchase-orders/{po_id}/convert-to-bill", json=(body or None)
    )


SUB_LINES = [
    {"description": "Foundations", "quantity": 1, "rate": 10000},
    {"description": "Slab", "quantity": 1, "rate": 5000},
]


# ── PO create/update with the commitment fields ──────────────────────────


def test_po_accepts_commitment_fields_in_both_company_types(client, seed_customer):
    pkg = _package(client, "Concrete")
    v = _vendor(client, vendor_type="sub")
    r = _po(
        client,
        v["id"],
        SUB_LINES,
        commitment_type="subcontract",
        trade_package_id=pkg["id"],
        retainage_pct=10,
        scope_notes="Per plans A-1..A-9",
    )
    assert r.status_code == 201, r.text
    po = r.json()
    assert po["commitment_type"] == "subcontract"
    assert po["trade_package_id"] == pkg["id"]
    assert po["trade_package_name"] == "Concrete"
    assert Decimal(po["retainage_pct"]) == Decimal("10.00")
    assert po["scope_notes"] == "Per plans A-1..A-9"
    # every line starts with nothing billed
    assert all(Decimal(ln["billed_amount"]) == 0 for ln in po["lines"])


def test_po_defaults_keep_todays_behaviour(client, seed_customer):
    v = _vendor(client)
    po = _po(client, v["id"], SUB_LINES).json()
    assert po["commitment_type"] == "po"
    assert po["trade_package_id"] is None
    assert po["retainage_pct"] is None

    up = client.put(
        f"/api/purchase-orders/{po['id']}",
        json={"commitment_type": "subcontract", "retainage_pct": 5},
    )
    assert up.status_code == 200, up.text
    assert up.json()["commitment_type"] == "subcontract"


# ── Partial billing ──────────────────────────────────────────────────────


def test_two_bills_from_one_po_partial_then_closed(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES, job_id=job["id"]).json()
    ids = [ln["id"] for ln in po["lines"]]

    # First period: $4,000 of the $10,000 foundations line only.
    r1 = _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 4000}])
    assert r1.status_code == 200, r1.text
    po_row = db_session.get(PurchaseOrder, po["id"])
    db_session.refresh(po_row)
    assert po_row.status.value == "partial"
    line0 = db_session.get(PurchaseOrderLine, ids[0])
    assert Decimal(str(line0.billed_amount)) == Decimal("4000.00")
    bill1 = db_session.get(Bill, r1.json()["bill_id"])
    assert Decimal(str(bill1.total)) == Decimal("4000.00")
    # the bill line links back to the commitment line
    bl = db_session.query(BillLine).filter_by(bill_id=bill1.id).one()
    assert bl.purchase_order_line_id == ids[0]

    # Second period: everything that remains (both lines), default amounts.
    r2 = _convert(client, po["id"])
    assert r2.status_code == 200, r2.text
    db_session.refresh(po_row)
    assert po_row.status.value == "closed"
    db_session.refresh(line0)
    assert Decimal(str(line0.billed_amount)) == Decimal("10000.00")
    bill2 = db_session.get(Bill, r2.json()["bill_id"])
    assert Decimal(str(bill2.total)) == Decimal("11000.00")  # 6000 + 5000


def test_over_remaining_is_422_and_nothing_left_is_409(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES).json()
    ids = [ln["id"] for ln in po["lines"]]

    over = _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 99999}])
    assert over.status_code == 422, over.text

    zero = _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 0}])
    assert zero.status_code == 422, zero.text

    # Bill it all, then a second convert finds nothing to bill.
    done = _convert(client, po["id"])
    assert done.status_code == 200, done.text
    again = _convert(client, po["id"])
    # a closed PO is refused up front; a partial with nothing left is the 409
    assert again.status_code in (400, 409), again.text


# ── Retainage posting ─────────────────────────────────────────────────────


def test_retainage_splits_ap_and_2050_with_exact_decimals(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES, retainage_pct=10).json()
    r = _convert(client, po["id"])
    assert r.status_code == 200, r.text
    bill = db_session.get(Bill, r.json()["bill_id"])
    # subtotal 15,000; 10% held
    assert Decimal(str(bill.retainage_held)) == Decimal("1500.00")
    assert Decimal(str(bill.balance_due)) == Decimal("13500.00")
    ap = seed_accounts["2000"].id
    assert _gl(db_session, ap) == Decimal("-13500.00")  # A/P credited net
    ret = _retainage_account(db_session)
    assert ret is not None
    assert _gl(db_session, ret.id) == Decimal("-1500.00")  # 2050 credited


def test_void_restores_billed_amount_and_reverses_2050(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, v["id"], SUB_LINES, retainage_pct=10).json()
    ids = [ln["id"] for ln in po["lines"]]
    r = _convert(client, po["id"])
    bill_id = r.json()["bill_id"]

    vr = client.post(f"/api/bills/{bill_id}/void")
    assert vr.status_code == 200, vr.text
    line0 = db_session.get(PurchaseOrderLine, ids[0])
    db_session.refresh(line0)
    assert Decimal(str(line0.billed_amount)) == Decimal("0.00")
    po_row = db_session.get(PurchaseOrder, po["id"])
    db_session.refresh(po_row)
    assert po_row.status.value != "closed"
    # 2050 nets to zero after the reversing entry
    ret = _retainage_account(db_session)
    assert _gl(db_session, ret.id) == Decimal("0.00")


def test_hand_entered_bill_carries_retainage(
    client, db_session, seed_accounts, seed_customer
):
    v = _vendor(client)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-10",
            "retainage_held": 500,
            "lines": [
                {
                    "description": "Framing draw",
                    "amount": 5000,
                    "account_id": seed_accounts["5100"].id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    assert Decimal(bill["retainage_held"]) == Decimal("500.00")
    assert Decimal(bill["balance_due"]) == Decimal("4500.00")
    ret = _retainage_account(db_session)
    assert _gl(db_session, ret.id) == Decimal("-500.00")

    # over the subtotal is refused
    bad = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-11",
            "retainage_held": 99999,
            "lines": [{"description": "x", "amount": 100}],
        },
    )
    assert bad.status_code == 422, bad.text


# ── Committed / billed figures ────────────────────────────────────────────


def test_committed_and_billed_figures_track_partial_billing(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(client, v["id"], SUB_LINES, job_id=job["id"])
    ids = [ln["id"] for ln in po["lines"]]

    # Before billing: committed is the whole $15,000 open, nothing billed.
    detail = client.get(f"/api/jobs/{job['id']}").json()["summary"]
    assert detail["committed_cost"] == 15000.0
    assert detail["committed_total"] == 15000.0
    assert detail["billed_to_date"] == 0.0
    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()["totals"]
    assert tree["committed"] == 15000.0
    assert tree["committed_total"] == 15000.0
    assert tree["billed_to_date"] == 0.0

    # Bill $4,000 of the foundations line.
    _convert(client, po["id"], lines=[{"line_id": ids[0], "amount": 4000}])
    detail = client.get(f"/api/jobs/{job['id']}").json()["summary"]
    assert detail["committed_cost"] == 11000.0  # remaining open
    assert detail["committed_total"] == 15000.0  # the whole commitment
    assert detail["billed_to_date"] == 4000.0
    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()["totals"]
    assert tree["committed"] == 11000.0
    assert tree["billed_to_date"] == 4000.0


# ── Buyout endpoint ────────────────────────────────────────────────────────


def _accepted_two_package_estimate(client, customer_id, job_id):
    pkg_c = _package(client, "Concrete")
    pkg_e = _package(client, "Electrical")
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": "2026-02-01",
            "job_id": job_id,
            "lines": [
                {
                    "description": "Concrete",
                    "quantity": 1,
                    "rate": 16000,
                    "unit_cost": 14000,
                    "trade_package_id": pkg_c["id"],
                },
                {
                    "description": "Electrical",
                    "quantity": 1,
                    "rate": 9000,
                    "unit_cost": 8000,
                    "trade_package_id": pkg_e["id"],
                },
            ],
        },
    ).json()
    acc = client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": job_id})
    assert acc.status_code == 200, acc.text
    return pkg_c, pkg_e


def test_buyout_rows_and_totals(client, db_session, seed_accounts, seed_customer):
    job = _job(client, seed_customer.id)
    pkg_c, pkg_e = _accepted_two_package_estimate(client, seed_customer.id, job["id"])
    v = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)

    # Concrete bought in full at 13,000 (under the 14,000 bid), billed partly.
    po_c = _sent_po(
        client,
        v["id"],
        [{"description": "Concrete sub", "quantity": 1, "rate": 13000}],
        job_id=job["id"],
        commitment_type="subcontract",
        trade_package_id=pkg_c["id"],
    )
    _convert(
        client, po_c["id"], lines=[{"line_id": po_c["lines"][0]["id"], "amount": 5000}]
    )

    # Electrical bought partly (4,000 of the 8,000 bid), nothing billed.
    _sent_po(
        client,
        v["id"],
        [{"description": "Elec sub", "quantity": 1, "rate": 4000}],
        job_id=job["id"],
        commitment_type="subcontract",
        trade_package_id=pkg_e["id"],
    )

    # An unassigned commitment (no package).
    _sent_po(
        client,
        v["id"],
        [{"description": "General conditions", "quantity": 1, "rate": 1000}],
        job_id=job["id"],
    )

    data = client.get(f"/api/jobs/{job['id']}/buyout").json()
    rows = {r["package_name"]: r for r in data["rows"]}

    assert rows["Concrete"]["bid_cost"] == 14000.0
    assert rows["Concrete"]["bought"] == 13000.0
    assert rows["Concrete"]["variance"] == 1000.0
    assert rows["Concrete"]["committed_remaining"] == 8000.0  # 13000 - 5000 billed
    assert rows["Concrete"]["billed_to_date"] == 5000.0
    assert len(rows["Concrete"]["commitments"]) == 1

    assert rows["Electrical"]["bid_cost"] == 8000.0
    assert rows["Electrical"]["bought"] == 4000.0
    assert rows["Electrical"]["variance"] == 4000.0

    # Unassigned gathers the commitment with no package.
    assert rows["Unassigned"]["bought"] == 1000.0

    totals = data["totals"]
    assert totals["bid_cost"] == 22000.0  # 14000 + 8000
    assert totals["bought"] == 18000.0  # 13000 + 4000 + 1000


# ── Vendor compliance + lien waiver ───────────────────────────────────────


def test_vendor_compliance_flags_a_sub(client, db_session, seed_accounts):
    expired = (date.today() - timedelta(days=1)).isoformat()
    sub = _vendor(
        client,
        name="Ace Electric",
        vendor_type="sub",
        coi_expires=expired,
        default_expense_account_id=seed_accounts["5100"].id,
    )
    plain = _vendor(
        client,
        name="Lumber Yard",
        default_expense_account_id=seed_accounts["5100"].id,
    )

    r = client.post(
        "/api/bills",
        json={
            "vendor_id": sub["id"],
            "date": "2026-09-01",
            "lines": [{"description": "work", "amount": 100}],
        },
    )
    comp = r.json()["vendor_compliance"]
    assert comp["is_sub"] is True
    assert comp["w9_on_file"] is False
    assert comp["coi_expired"] is True

    r2 = client.post(
        "/api/bills",
        json={
            "vendor_id": plain["id"],
            "date": "2026-09-01",
            "lines": [
                {
                    "description": "paper",
                    "amount": 50,
                    "account_id": seed_accounts["5100"].id,
                }
            ],
        },
    )
    comp2 = r2.json()["vendor_compliance"]
    assert comp2["is_sub"] is False
    assert comp2["coi_expired"] is False


def test_lien_waiver_put_round_trip(client, seed_accounts):
    v = _vendor(
        client,
        name="Ace",
        vendor_type="sub",
        default_expense_account_id=seed_accounts["5100"].id,
    )
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-01",
            "lines": [{"description": "work", "amount": 100}],
        },
    ).json()
    assert bill["lien_waiver_status"] is None
    up = client.put(
        f"/api/bills/{bill['id']}", json={"lien_waiver_status": "conditional"}
    )
    assert up.status_code == 200, up.text
    assert up.json()["lien_waiver_status"] == "conditional"
    # a bad value is refused
    bad = client.put(f"/api/bills/{bill['id']}", json={"lien_waiver_status": "nope"})
    assert bad.status_code == 422, bad.text


# ── Migration up/down incl. backfill ──────────────────────────────────────


def test_migration_upgrades_downgrades_and_backfills(tmp_path):
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

    # Up to the revision just before this one, seed a closed PO with a bill.
    assert run("upgrade", "e2a4c6d8f0b1").returncode == 0
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript("""
        INSERT INTO vendors (id, name, is_active) VALUES (1, 'V', 1);
        INSERT INTO purchase_orders (id, po_number, vendor_id, status, date, subtotal, total)
            VALUES (1, 'PO-1', 1, 'CLOSED', '2026-01-01', 100, 100);
        INSERT INTO purchase_order_lines (id, purchase_order_id, quantity, rate, amount)
            VALUES (1, 1, 1, 100, 100);
        INSERT INTO bills (id, bill_number, vendor_id, status, po_id, date, total, balance_due)
            VALUES (1, 'BILL-PO-1', 1, 'UNPAID', 1, '2026-01-01', 100, 100);
        """)
    con.commit()
    con.close()

    up = run("upgrade", "head")
    assert up.returncode == 0, up.stderr
    con = sqlite3.connect(db)
    billed = con.execute(
        "SELECT billed_amount FROM purchase_order_lines WHERE id = 1"
    ).fetchone()[0]
    con.close()
    assert Decimal(str(billed)) == Decimal("100")  # backfilled

    assert run("downgrade", "-1").returncode == 0
    assert run("upgrade", "head").returncode == 0
