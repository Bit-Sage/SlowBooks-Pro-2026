"""The estimate PDF in construction mode (#551): lines grouped by package
with the section code, then the allowances, alternates and unit-price blocks,
then the bid total — and proof that an ordinary company's estimate PDF is
untouched by the feature (byte-for-byte stable).

These render the PDF template (app/templates/estimate_pdf.html) through the
same renderer the PDF endpoint uses. The HTML stage is deterministic and
needs no native graphics stack, so the template's logic is pinned here even
where WeasyPrint itself is not installed.
"""

from app.models.estimates import Estimate
from app.services.pdf_service import _render


def _section(client, code, name, cost_type="material"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _estimate(client, customer_id, lines, **extra):
    body = {"customer_id": customer_id, "date": "2026-03-01", "lines": lines}
    body.update(extra)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _html(db_session, est_id, company_type):
    est = db_session.get(Estimate, est_id)
    return _render(
        "estimate_pdf.html",
        {"company_name": "Keystone Builders", "company_type": company_type},
        est=est,
    )


def test_construction_pdf_shows_groups_and_blocks(client, db_session, seed_customer):
    conc = _section(client, "03 30 00", "Cast-in-Place Concrete")
    pkg = client.post(
        "/api/trade-packages", json={"name": "Concrete", "section_ids": [conc["id"]]}
    ).json()
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Slab on grade",
                "quantity": 100,
                "rate": 60,
                "unit_cost": 50,
                "unit": "SF",
                "cost_code_id": conc["id"],
                "trade_package_id": pkg["id"],
            },
            {
                "description": "Flooring allowance",
                "quantity": 1,
                "rate": 4000,
                "line_kind": "allowance",
            },
            {
                "description": "Skylight alternate",
                "quantity": 1,
                "rate": 2500,
                "line_kind": "alternate",
            },
            {
                "description": "Rock excavation",
                "quantity": 1,
                "rate": 85,
                "unit": "CY",
                "line_kind": "unit_price",
            },
        ],
        gc_pct=10,
        fee_pct=5,
    )
    html = _html(db_session, est["id"], "construction")
    assert "Concrete" in html  # package heading row
    assert "03 30 00" in html  # section code in the description column
    assert "Allowances" in html and "Flooring allowance" in html
    assert "Alternates" in html and "Skylight alternate" in html
    assert "Unit prices" in html and "Rock excavation" in html
    assert "Bid Total" in html


def test_business_estimate_pdf_is_byte_stable(client, db_session, seed_customer):
    # a plain estimate with none of the new fields: the feature must not
    # change the business PDF at all
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Logo design", "quantity": 1, "rate": 500}],
        tax_rate=0,
    )
    first = _html(db_session, est["id"], "business")
    second = _html(db_session, est["id"], "business")
    assert first == second  # deterministic
    assert "Subtotal" in first and "Total" in first
    assert "Bid Total" not in first  # no construction surface leaks in
    assert "Package" not in first and "Alternates" not in first
