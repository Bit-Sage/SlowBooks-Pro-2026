"""Sub-customers with parent billing (#638, from discussion #633).

A property manager keeps each property as a customer and each tenant as a
sub-customer under it: QuickBooks' "Parent:Sub", one level deep. A tenant
is made inactive on move-out without touching the property, everything
rolls up to the property on its page, and a sub billed with its parent has
its invoices on the parent's A/R: the parent's A/R Aging row and statement
carry them, the parent's payments pay them, and the printed invoice bills
the parent naming the sub.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.contacts import Customer

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _js(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def _customer(client, name, **extra):
    r = client.post("/api/customers", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _property_and_tenant(client, bill_with_parent=True):
    """Maple St (the property) with its own billing address, and Unit 4B
    (the tenant) under it."""
    maple = _customer(
        client,
        "Maple St",
        bill_address1="12 Maple St",
        bill_city="Springfield",
        bill_state="IL",
        bill_zip="62704",
    )
    unit = _customer(
        client,
        "Unit 4B",
        parent_customer_id=maple["id"],
        bill_with_parent=bill_with_parent,
        bill_address1="12 Maple St, Apt 4B",
        bill_city="Springfield",
    )
    return maple, unit


def _invoice(client, cid, amount, day, **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": f"2026-09-{day:02d}",
            "tax_rate": 0,
            "lines": [{"description": "Rent", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _payment(client, cid, amount, day, allocations=()):
    return client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": f"2026-09-{day:02d}",
            "amount": amount,
            "method": "Check",
            "allocations": [{"invoice_id": i, "amount": a} for i, a in allocations],
        },
    )


# ── the records ────────────────────────────────────────────────────────────


def test_a_sub_customer_sits_under_its_parent_with_a_full_name(client):
    maple, unit = _property_and_tenant(client)
    assert unit["parent_customer_id"] == maple["id"]
    assert unit["parent_name"] == "Maple St"
    assert unit["full_name"] == "Maple St: Unit 4B"
    assert unit["bill_with_parent"] is True
    # the parent is its own name, and knows it has a sub
    got = client.get(f"/api/customers/{maple['id']}").json()
    assert got["full_name"] == "Maple St" and got["parent_customer_id"] is None
    assert got["sub_count"] == 1
    # a sub is a customer: it has its own page, and the flag is a plain
    # update
    r = client.put(f"/api/customers/{unit['id']}", json={"bill_with_parent": False})
    assert r.status_code == 200 and r.json()["bill_with_parent"] is False


def test_the_tree_lists_subs_under_their_parents_and_a_search_finds_them(client):
    _customer(client, "Zed Co")
    maple, unit = _property_and_tenant(client)
    _customer(client, "Attic", parent_customer_id=maple["id"])
    _customer(client, "Alpha Inc")
    names = [c["full_name"] for c in client.get("/api/customers?tree=1").json()]
    assert names == [
        "Alpha Inc",
        "Maple St",
        "Maple St: Attic",
        "Maple St: Unit 4B",
        "Zed Co",
    ]
    # without tree: plain name order, as before
    plain = [c["name"] for c in client.get("/api/customers").json()]
    assert plain == ["Alpha Inc", "Attic", "Maple St", "Unit 4B", "Zed Co"]
    # the property's name finds its tenants
    found = {c["name"] for c in client.get("/api/customers?search=maple").json()}
    assert found == {"Maple St", "Attic", "Unit 4B"}


def test_one_level_deep_and_the_other_refusals(client):
    maple, unit = _property_and_tenant(client)
    # a sub cannot have subs
    r = client.post(
        "/api/customers", json={"name": "Roommate", "parent_customer_id": unit["id"]}
    )
    assert r.status_code == 422, r.text
    assert "sub-customer cannot have sub-customers" in r.json()["detail"]
    # a parent with subs cannot become a sub
    other = _customer(client, "Oak Ave")
    r = client.put(
        f"/api/customers/{maple['id']}", json={"parent_customer_id": other["id"]}
    )
    assert r.status_code == 422, r.text
    assert "has sub-customers of its own (Unit 4B)" in r.json()["detail"]
    # never its own parent
    r = client.put(
        f"/api/customers/{other['id']}", json={"parent_customer_id": other["id"]}
    )
    assert r.status_code == 422
    # a parent that is not there
    r = client.post(
        "/api/customers", json={"name": "Lost", "parent_customer_id": 999999}
    )
    assert r.status_code == 404
    # bill with parent means nothing without a parent
    r = client.post("/api/customers", json={"name": "Loner", "bill_with_parent": True})
    assert r.status_code == 422 and "needs a parent" in r.json()["detail"]
    # taking the parent away clears the flag
    r = client.put(f"/api/customers/{unit['id']}", json={"parent_customer_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["parent_customer_id"] is None
    assert r.json()["bill_with_parent"] is False


def test_a_sub_is_made_inactive_on_its_own_and_the_parent_asks(client):
    maple, unit = _property_and_tenant(client)
    attic = _customer(client, "Attic", parent_customer_id=maple["id"])
    # the tenant moves out: the property is untouched
    r = client.put(f"/api/customers/{unit['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get(f"/api/customers/{maple['id']}").json()["is_active"] is True
    # the property with an active tenant asks, naming it
    r = client.put(f"/api/customers/{maple['id']}", json={"is_active": False})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "active_subs"
    assert "Attic" in detail["message"] and "Unit 4B" not in detail["message"]
    assert [s["id"] for s in detail["subs"]] == [attic["id"]]
    assert client.get(f"/api/customers/{maple['id']}").json()["is_active"] is True
    # ...and with_subs takes them along
    r = client.put(
        f"/api/customers/{maple['id']}?with_subs=true", json={"is_active": False}
    )
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get(f"/api/customers/{attic['id']}").json()["is_active"] is False
    # DELETE (deactivate) asks the same way
    oak = _customer(client, "Oak Ave")
    _customer(client, "Unit 1", parent_customer_id=oak["id"])
    assert client.delete(f"/api/customers/{oak['id']}").status_code == 409
    assert (
        client.delete(f"/api/customers/{oak['id']}?with_subs=true").status_code == 200
    )
    # the pickers do not list the inactive ones
    active = {c["name"] for c in client.get("/api/customers?active_only=true").json()}
    assert active == set()


# ── bill with parent ───────────────────────────────────────────────────────


def test_an_invoice_to_a_sub_billed_with_parent_is_on_the_parents_ar(
    client, db_session, seed_accounts
):
    from app.routes.reports.receivables import statement_activity

    maple, unit = _property_and_tenant(client)
    own = _invoice(client, maple["id"], 300, 1)
    rent = _invoice(client, unit["id"], 1200, 2)
    # the invoice keeps the sub as its customer, named the pickers' way
    assert rent["customer_id"] == unit["id"]
    assert rent["customer_name"] == "Maple St: Unit 4B"
    # ...and is billed at the parent's address
    assert rent["bill_address1"] == "12 Maple St" and rent["bill_zip"] == "62704"

    # A/R Aging: the parent's row carries it, with the sub's share under it
    aging = client.get("/api/reports/ar-aging?as_of_date=2026-09-30").json()
    rows = {i["customer_name"]: i for i in aging["items"]}
    assert set(rows) == {"Maple St"}
    assert rows["Maple St"]["total"] == 1500.0
    assert rows["Maple St"]["subs"] == [
        {
            "customer_name": "Maple St: Unit 4B",
            "customer_id": unit["id"],
            "current": 1200.0,
            "over_30": 0.0,
            "over_60": 0.0,
            "over_90": 0.0,
            "total": 1200.0,
            "unapplied_credits": 0.0,
        }
    ]
    assert aging["totals"]["total"] == 1500.0

    # the parent's statement carries the sub's invoice, naming the sub
    parent = db_session.get(Customer, maple["id"])
    act = statement_activity(db_session, parent, date(2026, 9, 30))
    assert [(ln["number"], ln["amount"]) for ln in act["lines"]] == [
        (own["invoice_number"], Decimal("300.00")),
        (rent["invoice_number"], Decimal("1200.00")),
    ]
    assert act["lines"][1]["description"].startswith("Unit 4B · Due ")
    assert act["balance_due"] == Decimal("1500.00")

    # Receive Payment for the parent is offered the sub's invoice
    offered = client.get(
        f"/api/invoices?customer_id={maple['id']}&open_only=true&billed_to=true"
    ).json()
    assert {i["id"] for i in offered} == {own["id"], rent["id"]}
    # a plain customer filter is still the customer's own
    mine = client.get(f"/api/invoices?customer_id={maple['id']}").json()
    assert {i["id"] for i in mine} == {own["id"]}

    # a payment from the parent applies to the sub's invoice
    r = _payment(client, maple["id"], 1200, 5, [(rent["id"], 1200)])
    assert r.status_code == 201, r.text
    assert client.get(f"/api/invoices/{rent['id']}").json()["status"] == "paid"
    # ...and so does one received and applied afterwards
    r = _payment(client, maple["id"], 300, 6)
    assert r.status_code == 201, r.text
    r = client.post(
        f"/api/payments/{r.json()['id']}/apply",
        json={"allocations": [{"invoice_id": own["id"], "amount": 300}]},
    )
    assert r.status_code == 200, r.text
    # the parent's own balance is settled, the sub's too, and the aging is
    # clear
    assert client.get("/api/reports/ar-aging").json()["totals"]["total"] == 0.0


def test_the_printed_invoice_bills_the_parent_naming_the_sub(
    client, db_session, seed_accounts
):
    from app.models.invoices import Invoice
    from app.services.donor_documents import invoice_pdf_context
    from app.services.pdf_service import render_invoice_html
    from app.services.settings_service import get_all_settings

    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    inv = db_session.get(Invoice, rent["id"])
    settings = get_all_settings(db_session)
    ctx = invoice_pdf_context(inv, settings)
    assert (ctx["bill_to_name"], ctx["bill_to_for"]) == ("Maple St", "Unit 4B")
    html = render_invoice_html(inv, settings)
    block = html[html.index("Bill To") : html.index('<table class="line-items">')]
    assert "<strong>Maple St</strong>" in block
    assert "12 Maple St" in block and "For: Unit 4B" in block
    # a customer billed on its own prints as before: no For: line
    plain = _invoice(client, maple["id"], 50, 3)
    html = render_invoice_html(db_session.get(Invoice, plain["id"]), settings)
    assert "For:" not in html


def test_a_sub_billed_on_its_own_is_aged_and_paid_on_its_own(
    client, db_session, seed_accounts
):
    from app.routes.reports.receivables import statement_activity

    maple, unit = _property_and_tenant(client, bill_with_parent=False)
    _invoice(client, maple["id"], 300, 1)
    rent = _invoice(client, unit["id"], 1200, 2)
    # the sub's own address, its own aging row under its parent's name
    assert rent["bill_address1"] == "12 Maple St, Apt 4B"
    aging = client.get("/api/reports/ar-aging?as_of_date=2026-09-30").json()
    rows = [(i["customer_name"], i["total"], i["subs"]) for i in aging["items"]]
    assert rows == [("Maple St", 300.0, []), ("Maple St: Unit 4B", 1200.0, [])]
    # the parent's statement is the parent's own
    parent = db_session.get(Customer, maple["id"])
    act = statement_activity(db_session, parent, date(2026, 9, 30))
    assert act["balance_due"] == Decimal("300.00")
    # a payment from the parent does not pay it (#189 stands)
    r = _payment(client, maple["id"], 1200, 5, [(rent["id"], 1200)])
    assert r.status_code == 400, r.text
    # the tenant's own payment does
    r = _payment(client, unit["id"], 1200, 5, [(rent["id"], 1200)])
    assert r.status_code == 201, r.text


def test_the_parents_page_rolls_its_subs_up(client, seed_accounts):
    maple, unit = _property_and_tenant(client, bill_with_parent=False)
    attic = _customer(client, "Attic", parent_customer_id=maple["id"])
    _invoice(client, maple["id"], 300, 1)
    _invoice(client, unit["id"], 1200, 2)
    _invoice(client, attic["id"], 800, 3)
    # the tenant moved out and still owes: on the roll-up all the same
    client.put(f"/api/customers/{attic['id']}", json={"is_active": False})

    page = client.get(f"/api/customers/{maple['id']}/subs").json()
    assert page["own_balance"] == 300.0
    assert page["subs_balance"] == 2000.0
    assert page["rollup_balance"] == 2300.0
    assert [(s["name"], s["is_active"], s["balance"]) for s in page["subs"]] == [
        ("Attic", False, 800.0),
        ("Unit 4B", True, 1200.0),
    ]
    # the sub's page names its parent
    sub = client.get(f"/api/customers/{attic['id']}/subs").json()
    assert sub["parent_customer_id"] == maple["id"]
    assert sub["parent_name"] == "Maple St" and sub["subs"] == []
    # the list carries the same figures on the parent's row
    row = {c["id"]: c for c in client.get("/api/customers?tree=1").json()}
    assert Decimal(row[maple["id"]]["balance"]) == Decimal("300.00")
    assert Decimal(row[maple["id"]]["rollup_balance"]) == Decimal("2300.00")
    assert row[maple["id"]]["sub_count"] == 2
    assert row[unit["id"]]["rollup_balance"] is None


# ── the importers ──────────────────────────────────────────────────────────

TENANTS_IIF = (
    "!CUST\tNAME\tCOMPANYNAME\tPHONE1\n"
    "CUST\tMaple St\tMaple St LLC\t\n"
    "CUST\tMaple St:Unit 4B\t\t555-0104\n"
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!ENDTRNS\n"
    "TRNS\tINVOICE\t09/01/2026\tAccounts Receivable\tMaple St:Unit 4B\t1200\t2001\n"
    "SPL\tINVOICE\t09/01/2026\tSales\tMaple St:Unit 4B\t-1200\t2001\n"
    "ENDTRNS\n"
)


def test_the_iif_import_keeps_parent_sub_as_a_sub_customer_when_told(
    client, db_session, seed_accounts
):
    from app.models.invoices import Invoice
    from app.models.jobs import Job

    r = client.post(
        "/api/iif/import",
        files={"file": ("tenants.iif", TENANTS_IIF.encode("utf-8"), "text/plain")},
        data={"sub_customers": "true"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["customers"] == 2
    maple = db_session.query(Customer).filter(Customer.name == "Maple St").one()
    unit = db_session.query(Customer).filter(Customer.name == "Unit 4B").one()
    assert unit.parent_customer_id == maple.id and unit.full_name == "Maple St: Unit 4B"
    assert unit.phone == "555-0104"  # the row's own contact details
    assert db_session.query(Job).count() == 0
    # the invoice naming Maple St:Unit 4B is the sub-customer's
    inv = db_session.query(Invoice).filter(Invoice.invoice_number == "2001").one()
    assert inv.customer_id == unit.id and inv.job_id is None
    # a second import makes nothing twice
    r = client.post(
        "/api/iif/import",
        files={"file": ("tenants.iif", TENANTS_IIF.encode("utf-8"), "text/plain")},
        data={"sub_customers": "true"},
    )
    assert r.json()["customers"] == 0
    assert db_session.query(Customer).count() == 2
    # the validation report counts the Customer:Job names for the choice
    r = client.post(
        "/api/iif/validate",
        files={"file": ("tenants.iif", TENANTS_IIF.encode("utf-8"), "text/plain")},
    )
    assert r.json()["customer_job_names"] == 1


def test_the_iif_import_still_makes_a_job_unless_told(
    client, db_session, seed_accounts
):
    from app.models.jobs import Job

    r = client.post(
        "/api/iif/import",
        files={"file": ("jobs.iif", TENANTS_IIF.encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 200, r.text
    names = {c.name for c in db_session.query(Customer).all()}
    assert names == {"Maple St"}
    assert db_session.query(Job).filter(Job.name == "Unit 4B").count() == 1


def test_a_sub_customer_on_file_takes_a_later_transaction_named_for_it(
    db_session, seed_accounts
):
    """A file imported as jobs after the sub-customer exists: the name
    resolves to the sub-customer, with no job made beside it."""
    from app.models.jobs import Job
    from app.services.jobs_service import resolve_customer_and_job

    maple = Customer(name="Maple St", is_active=True)
    db_session.add(maple)
    db_session.flush()
    unit = Customer(name="Unit 4B", parent_customer_id=maple.id, is_active=True)
    db_session.add(unit)
    db_session.flush()
    customer, job = resolve_customer_and_job(db_session, "Maple St:Unit 4B")
    assert customer.id == unit.id and job is None
    assert db_session.query(Job).count() == 0
    # a child never seen before is still a job, as before
    customer, job = resolve_customer_and_job(db_session, "Maple St:Roof 2026")
    assert customer.id == maple.id and job is not None and job.name == "Roof 2026"


def test_the_iif_export_spells_a_sub_customer_as_parent_sub(client):
    maple, unit = _property_and_tenant(client)
    body = client.get("/api/iif/export/customers").text
    names = [
        line.split("\t")[1] for line in body.splitlines() if line.startswith("CUST\t")
    ]
    assert names == ["Maple St", "Maple St:Unit 4B"]


def test_the_qbo_customer_list_makes_parent_sub_a_sub_customer(db_session):
    """QuickBooks Online's Customer Contact List carries no parent column:
    a sub-customer is the "Parent:Sub" spelling of its name (a colon is
    not allowed in a QuickBooks name otherwise), in the fixture's shape."""
    from app.services import qbo_export_import as qbo

    csv_text = (
        ",Sutehk Realty Group,,,,,\n"
        ",Customer Contact List,,,,,\n"
        ",,,,,,\n"
        ",Customer,Phone Numbers,Email,Full Name,Billing Address,Shipping Address\n"
        ",Maple St,,owner@example.com,,,\n"
        ",Maple St:Unit 4B,Mobile: (555) 010-2004,tenant@example.com,Dana Reyes,,\n"
    )
    created, _vendors = qbo._import_contacts(db_session, {"customers": csv_text})
    assert created == 2
    maple = db_session.query(Customer).filter(Customer.name == "Maple St").one()
    unit = db_session.query(Customer).filter(Customer.name == "Unit 4B").one()
    assert unit.parent_customer_id == maple.id
    assert unit.email == "tenant@example.com" and unit.mobile == "(555) 010-2004"
    assert maple.email == "owner@example.com"
    # the A/R Aging Detail's "Parent:Sub" names the sub-customer
    assert qbo._customer_names(db_session, {})["maple st:unit 4b"] == "Maple St:Unit 4B"
    # a second pass makes nothing twice
    again, _ = qbo._import_contacts(db_session, {"customers": csv_text})
    assert again == 0


# ── the pages ──────────────────────────────────────────────────────────────


def test_every_customer_picker_lists_subs_under_parents_by_full_name():
    assert "function customerLabel(c)" in _js("utils.js")
    pickers = {
        "invoices.js": 1,
        "estimates.js": 1,
        "payments.js": 1,
        "credit_memos.js": 1,
        "sales_receipts.js": 1,
        "recurring.js": 1,
        "in_kind.js": 1,
        "jobs.js": 1,
        "app.js": 1,  # quick entry
        "banking.js": 2,
        "reports.js": 2,
    }
    for name, fetches in pickers.items():
        js = _js(name)
        assert js.count("/customers?active_only=true&tree=1") == fetches, name
        assert "/customers?active_only=true'" not in js, name
        assert "customerLabel(c)" in js, name
    permits = _js("reseller_permits.js")
    assert "/customers?tree=1" in permits and "customerLabel(c)" in permits


def test_receive_payment_offers_what_is_billed_to_the_customer():
    js = _js("payments.js")
    assert "open_only=true&billed_to=true" in js


def test_the_customer_center_shows_the_hierarchy_and_asks_about_subs():
    js = _js("customers.js")
    assert "tree=1" in js
    # the form: the parent picker and the flag
    form = js[js.index("async showForm(id = null)") : js.index("_confirmDuplicate(")]
    assert 'name="parent_customer_id"' in form and "cust-parent-select" in form
    assert 'name="bill_with_parent"' in form
    assert "data.parent_customer_id = parentSel.value" in js
    assert "?with_subs=true" in js and "err.detail.error === 'active_subs'" in js
    # the page: the subs with their balances and the roll-up, the parent link
    page = js[js.index("async showDetails(id)") : js.index("_reportsRow(id)")]
    assert "/customers/${id}/subs" in page
    assert "Sub-customers (${subList.length})" in page and "Roll-up" in page
    assert "Sub-customer of" in page
    # Make Inactive on the list asks the same question
    assert "active_subs" in _js("active_lists.js")
    assert "?with_subs=true" in _js("active_lists.js")


def test_the_aging_screen_shows_each_sub_under_its_parent():
    js = _js("reports.js")
    assert "(i.subs || []).map(subRow)" in js


def test_the_iif_page_offers_the_sub_customer_choice():
    js = _js("iif.js")
    assert "report.customer_job_names > 0" in js
    assert "formData.append('sub_customers'" in js


def test_the_migration_follows_the_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision("c1e5a7b9d2f4").down_revision == "b8d4f2a6c1e3"
