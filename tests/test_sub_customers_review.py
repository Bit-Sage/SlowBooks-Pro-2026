"""The review of sub-customers (#638): what the probes found, fixed.

A bare name in an import is a top-level customer, never a sub-customer
that shares it; a statement's payment and credit lines are what relieved
the invoices on it, whoever paid; the IIF export spells a sub-customer
"Parent:Sub" on every row; a parent is not made inactive over a
sub-customer's open balance billed to it; a three-level QuickBooks name
is flattened out loud; a second property's Unit 1 is not a duplicate; a
credit memo is routed the way a payment is; an invoice's customer_name is
the same name everywhere; and the printed invoice prints the billed
customer's country.
"""

from datetime import date
from decimal import Decimal

from app.models.contacts import Customer
from app.models.invoices import Invoice
from app.models.jobs import Job
from app.models.payments import Payment


def _customer(client, name, **extra):
    r = client.post("/api/customers", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _property_and_tenant(client, bill_with_parent=True, **parent_extra):
    maple = _customer(
        client,
        "Maple St",
        bill_address1="12 Maple St",
        bill_city="Springfield",
        bill_state="IL",
        bill_zip="62704",
        **parent_extra,
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


def _credit_memo(client, cid, amount, day, **extra):
    return client.post(
        "/api/credit-memos",
        json={
            "customer_id": cid,
            "date": f"2026-09-{day:02d}",
            "lines": [{"description": "Credit", "quantity": 1, "rate": amount}],
            **extra,
        },
    )


def _import(client, text, **data):
    return client.post(
        "/api/iif/import",
        files={"file": ("books.iif", text.encode("utf-8"), "text/plain")},
        data=data,
    )


def _balance(client, cid) -> Decimal:
    return Decimal(str(client.get(f"/api/customers/{cid}").json()["balance"]))


def _statement(db_session, cid, day=30):
    from app.routes.reports.receivables import statement_activity

    return statement_activity(
        db_session, db_session.get(Customer, cid), date(2026, 9, day)
    )


# ── 1. a bare name is a top-level customer ───────────────────────────────

SMITH_IIF = (
    "!CUST\tNAME\tCOMPANYNAME\tPHONE1\n"
    "CUST\tSmith\t\t555-0100\n"
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!ENDTRNS\n"
    "TRNS\tINVOICE\t09/01/2026\tAccounts Receivable\tSmith\t500\t3001\n"
    "SPL\tINVOICE\t09/01/2026\tSales\tSmith\t-500\t3001\n"
    "ENDTRNS\n"
    "TRNS\tPAYMENT\t09/05/2026\tUndeposited Funds\tSmith\t500\t\n"
    "SPL\tPAYMENT\t09/05/2026\tAccounts Receivable\tSmith\t-500\t3001\n"
    "ENDTRNS\n"
)

UNIT_2_IIF = (
    "!CUST\tNAME\tCOMPANYNAME\tPHONE1\n"
    "CUST\tSmith:Unit 2\t\t555-0102\n"
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!ENDTRNS\n"
    "TRNS\tINVOICE\t09/01/2026\tAccounts Receivable\tSmith:Unit 2\t700\t3002\n"
    "SPL\tINVOICE\t09/01/2026\tSales\tSmith:Unit 2\t-700\t3002\n"
    "ENDTRNS\n"
)


def _oak_st_with_tenant_smith(client):
    oak = _customer(client, "Oak St")
    smith = _customer(
        client, "Smith", parent_customer_id=oak["id"], bill_with_parent=True
    )
    return oak, smith


def test_an_imported_bare_name_is_a_top_level_customer_not_the_tenant(
    client, db_session, seed_accounts
):
    """A file's "Smith" posted its invoice and payment to the tenant
    "Oak St:Smith". A bare name is a top-level customer: it is made, and
    the documents are its."""
    oak, tenant = _oak_st_with_tenant_smith(client)
    r = _import(client, SMITH_IIF)
    assert r.status_code == 200, r.text
    assert r.json()["customers"] == 1
    assert r.json()["invoices"] == 1 and r.json()["payments"] == 1
    smiths = db_session.query(Customer).filter(Customer.name == "Smith").all()
    assert len(smiths) == 2
    top = next(c for c in smiths if c.parent_customer_id is None)
    assert top.phone == "555-0100"
    inv = db_session.query(Invoice).filter(Invoice.invoice_number == "3001").one()
    assert inv.customer_id == top.id
    pay = db_session.query(Payment).one()
    assert pay.customer_id == top.id
    # the tenant has nothing, and the property's roll-up says so
    assert _balance(client, tenant["id"]) == 0
    assert client.get(f"/api/customers/{oak['id']}/subs").json()["rollup_balance"] == 0
    assert _balance(client, top.id) == 0
    # a second import makes nothing twice
    r = _import(client, SMITH_IIF)
    assert r.json()["customers"] == 0 and r.json()["invoices"] == 0
    assert db_session.query(Customer).filter(Customer.name == "Smith").count() == 2


def test_a_sub_customer_under_a_bare_name_lands_under_the_top_level_one(
    client, db_session, seed_accounts
):
    """ "Smith:Unit 2", with sub-customers on, made a third level under the
    tenant "Oak St:Smith". It lands under a top-level Smith, made for it;
    never three levels."""
    oak, tenant = _oak_st_with_tenant_smith(client)
    r = _import(client, UNIT_2_IIF, sub_customers="true")
    assert r.status_code == 200, r.text
    assert r.json()["customers"] == 1
    unit = db_session.query(Customer).filter(Customer.name == "Unit 2").one()
    smith = db_session.get(Customer, unit.parent_customer_id)
    assert smith.name == "Smith" and smith.parent_customer_id is None
    assert smith.id != tenant["id"]
    # one level deep, everywhere
    for c in db_session.query(Customer).all():
        if c.parent_customer_id:
            assert (
                db_session.get(Customer, c.parent_customer_id).parent_customer_id
                is None
            )
    assert client.get(f"/api/customers/{tenant['id']}/subs").json()["subs"] == []
    inv = db_session.query(Invoice).filter(Invoice.invoice_number == "3002").one()
    assert inv.customer_id == unit.id
    # as jobs (the default) the same: a job under the top-level Smith
    r = _import(client, UNIT_2_IIF.replace("Unit 2", "Roof 2026"))
    assert r.status_code == 200, r.text
    job = db_session.query(Job).filter(Job.name == "Roof 2026").one()
    assert job.customer_id == smith.id


def test_a_bare_name_lookup_never_answers_a_sub_customer(db_session):
    from app.services.jobs_service import find_customer, resolve_customer_and_job

    oak = Customer(name="Oak St", is_active=True)
    db_session.add(oak)
    db_session.flush()
    db_session.add(Customer(name="Smith", parent_customer_id=oak.id, is_active=True))
    db_session.flush()
    assert find_customer(db_session, "Smith") is None
    assert find_customer(db_session, "SMITH") is None
    assert find_customer(db_session, "Oak St").id == oak.id
    assert resolve_customer_and_job(db_session, "Smith", create=False) == (None, None)
    # through its parent it is found, in any case
    customer, job = resolve_customer_and_job(db_session, "Oak St:smith", create=False)
    assert (
        customer is not None and customer.parent_customer_id == oak.id and job is None
    )


# ── 2. the statement follows what relieved its invoices ─────────────────


def test_a_subs_own_statement_shows_the_parents_payment_that_relieved_it(
    client, db_session, seed_accounts
):
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    r = _payment(client, maple["id"], 1200, 5, [(rent["id"], 1200)])
    assert r.status_code == 201, r.text
    # the sub owes nothing, and its statement says so
    assert _balance(client, unit["id"]) == 0
    act = _statement(db_session, unit["id"])
    assert [(ln["type"], ln["amount"], ln["balance"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1200.00"), Decimal("1200.00")),
        ("Payment", Decimal("-1200.00"), Decimal("0.00")),
    ]
    assert act["lines"][1]["description"] == (
        f"from Maple St · Check · applied to #{rent['invoice_number']}"
    )
    assert act["balance_due"] == 0 and act["total_payments"] == Decimal("1200.00")
    # the parent's statement carries the sub's invoice and its own payment
    act = _statement(db_session, maple["id"])
    assert [(ln["type"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1200.00")),
        ("Payment", Decimal("-1200.00")),
    ]
    assert act["lines"][1]["description"] == (
        f"Check · applied to #{rent['invoice_number']}"
    )
    assert act["balance_due"] == 0 == _balance(client, maple["id"])


def test_the_iif_shape_a_parent_payment_on_a_sub_billed_on_its_own(
    client, db_session, seed_accounts
):
    """The IIF import allocates a parent's payment by invoice number while
    an imported sub-customer is billed on its own: the sub's statement
    shows the invoice and the payment that paid it, the parent's statement
    neither (the payment relieved nothing on it), and both read what the
    customers owe."""
    text = (
        "!CUST\tNAME\tCOMPANYNAME\tPHONE1\n"
        "CUST\tMaple St\tMaple St LLC\t\n"
        "CUST\tMaple St:Unit 4B\t\t555-0104\n"
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!ENDTRNS\n"
        "TRNS\tINVOICE\t09/01/2026\tAccounts Receivable\tMaple St:Unit 4B\t1200\t2001\n"
        "SPL\tINVOICE\t09/01/2026\tSales\tMaple St:Unit 4B\t-1200\t2001\n"
        "ENDTRNS\n"
        "TRNS\tPAYMENT\t09/05/2026\tUndeposited Funds\tMaple St\t1200\t\n"
        "SPL\tPAYMENT\t09/05/2026\tAccounts Receivable\tMaple St\t-1200\t2001\n"
        "ENDTRNS\n"
    )
    r = _import(client, text, sub_customers="true")
    assert r.status_code == 200, r.text
    assert r.json()["invoices"] == 1 and r.json()["payments"] == 1
    maple = db_session.query(Customer).filter(Customer.name == "Maple St").one()
    unit = db_session.query(Customer).filter(Customer.name == "Unit 4B").one()
    assert unit.bill_with_parent is False
    assert _balance(client, unit.id) == 0 and _balance(client, maple.id) == 0
    act = _statement(db_session, unit.id)
    assert [(ln["type"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1200.00")),
        ("Payment", Decimal("-1200.00")),
    ]
    assert act["lines"][1]["description"].startswith("from Maple St")
    assert act["balance_due"] == 0
    act = _statement(db_session, maple.id)
    assert act["lines"] == [] and act["balance_due"] == 0
    assert client.get("/api/reports/ar-aging").json()["totals"]["total"] == 0.0


def test_a_sub_stays_billed_with_its_parent_while_the_parents_money_is_on_it(
    client, db_session, seed_accounts
):
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    r = _payment(client, maple["id"], 600, 5, [(rent["id"], 600)])
    assert r.status_code == 201, r.text
    other = _customer(client, "Oak Ave")
    for body in (
        {"bill_with_parent": False},
        {"parent_customer_id": None},
        {"parent_customer_id": other["id"]},
    ):
        r = client.put(f"/api/customers/{unit['id']}", json=body)
        assert r.status_code == 409, (body, r.text)
        detail = r.json()["detail"]
        assert detail["error"] == "parent_money_on_open_invoices"
        assert f"#{rent['invoice_number']} ($600.00)" in detail["message"]
        assert "stays billed with Maple St" in detail["message"]
        assert detail["invoices"] == [
            {
                "invoice_id": rent["id"],
                "invoice_number": rent["invoice_number"],
                "amount": 600.0,
            }
        ]
    got = client.get(f"/api/customers/{unit['id']}").json()
    assert got["bill_with_parent"] is True and got["parent_customer_id"] == maple["id"]
    # a parent's credit on it counts the same way
    cm = _credit_memo(client, maple["id"], 100, 6)
    assert cm.status_code == 201, cm.text
    r = client.post(
        f"/api/credit-memos/{cm.json()['id']}/apply",
        json={"invoice_id": rent["id"], "amount": 100},
    )
    assert r.status_code == 200, r.text
    r = client.put(f"/api/customers/{unit['id']}", json={"bill_with_parent": False})
    assert r.status_code == 409
    assert r.json()["detail"]["invoices"][0]["amount"] == 700.0
    # settled, the flag comes off
    r = _payment(client, maple["id"], 500, 7, [(rent["id"], 500)])
    assert r.status_code == 201, r.text
    r = client.put(f"/api/customers/{unit['id']}", json={"bill_with_parent": False})
    assert r.status_code == 200, r.text
    assert r.json()["bill_with_parent"] is False
    # ...and the statements still read what each owes: nothing
    assert _statement(db_session, unit["id"])["balance_due"] == 0
    assert _statement(db_session, maple["id"])["balance_due"] == 0


def test_a_customer_with_no_sub_customers_reads_as_before(
    client, db_session, seed_accounts
):
    acme = _customer(client, "Acme Diner")
    first = _invoice(client, acme["id"], 100, 1)
    r = _payment(client, acme["id"], 60, 2, [(first["id"], 60)])
    assert r.status_code == 201, r.text
    r = _payment(client, acme["id"], 25, 6)  # nothing applied
    assert r.status_code == 201, r.text
    cm = _credit_memo(client, acme["id"], 10, 5)
    assert cm.status_code == 201, cm.text
    act = _statement(db_session, acme["id"])
    assert [(ln["type"], ln["amount"], ln["balance"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("100.00"), Decimal("100.00")),
        ("Payment", Decimal("-60.00"), Decimal("40.00")),
        ("Credit Memo", Decimal("-10.00"), Decimal("30.00")),
        ("Payment", Decimal("-25.00"), Decimal("5.00")),
    ]
    assert (
        act["lines"][1]["description"]
        == f"Check · applied to #{first['invoice_number']}"
    )
    assert act["lines"][2]["description"] == "credit"
    assert act["lines"][3]["description"] == "Check · not applied to an invoice yet"
    assert act["total_payments"] == Decimal("85.00")
    assert act["total_credits"] == Decimal("10.00")
    assert act["balance_due"] == Decimal("5.00") == _balance(client, acme["id"])


# ── 3. the IIF export spells a sub-customer one way ─────────────────────


def _names(text):
    return [
        (row[0], row[1], row[4])
        for row in (line.split("\t") for line in text.splitlines())
        if row[0] in ("TRNS", "SPL")
    ]


def test_the_iif_export_names_a_sub_customer_parent_sub_on_every_row(
    client, seed_accounts
):
    maple, unit = _property_and_tenant(client, bill_with_parent=False)
    rent = _invoice(client, unit["id"], 1200, 2, tax_rate=0.05)
    r = _payment(client, unit["id"], 1260, 5, [(rent["id"], 1260)])
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": unit["id"],
            "date": "2026-09-02",
            "lines": [{"description": "Paint", "quantity": 1, "rate": 50}],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": unit["id"],
            "date": "2026-09-03",
            "lines": [{"description": "Key", "quantity": 1, "rate": 20}],
        },
    )
    assert r.status_code == 201, r.text
    for path in ("invoices", "payments", "estimates", "sales-receipts"):
        rows = _names(client.get(f"/api/iif/export/{path}").text)
        assert rows, path
        assert {name for _row, _kind, name in rows} == {"Maple St:Unit 4B"}, (
            path,
            rows,
        )


def test_the_customer_list_keeps_its_order_and_puts_a_sub_after_its_parent(
    client,
):
    """The top-level rows go out in the database's name order, as they
    always have (a company with no sub-customers exports as before); a
    sub-customer follows its parent."""
    for name in ("apple co", "Banana Ltd", "Cherry Inc"):
        _customer(client, name)

    def cust_names():
        body = client.get("/api/iif/export/customers").text
        return [
            line.split("\t")[1]
            for line in body.splitlines()
            if line.startswith("CUST\t")
        ]

    r = client.get("/api/customers").json()  # the database's order
    assert (
        cust_names()
        == [c["name"] for c in r]
        == [
            "Banana Ltd",
            "Cherry Inc",
            "apple co",
        ]
    )
    banana = next(c for c in r if c["name"] == "Banana Ltd")
    _customer(client, "Unit 1", parent_customer_id=banana["id"])
    assert cust_names() == ["Banana Ltd", "Banana Ltd:Unit 1", "Cherry Inc", "apple co"]


# ── 4. inactivation ────────────────────────────────────────────────────


def test_a_parent_is_not_made_inactive_over_a_sub_customers_open_balance(
    client, seed_accounts
):
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    for how in (
        lambda: client.put(f"/api/customers/{maple['id']}", json={"is_active": False}),
        lambda: client.put(
            f"/api/customers/{maple['id']}?with_subs=true", json={"is_active": False}
        ),
        lambda: client.delete(f"/api/customers/{maple['id']}?with_subs=true"),
    ):
        r = how()
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "subs_billed_with_parent_owe"
        assert (
            f"Unit 4B is billed with Maple St and still owes $1,200.00 "
            f"(#{rent['invoice_number']})" in detail["message"]
        )
        assert "Receive the payment or write the balance off" in detail["message"]
        assert "take Bill with parent off" in detail["message"]
        assert detail["subs"] == [
            {
                "id": unit["id"],
                "name": "Unit 4B",
                "balance": 1200.0,
                "invoices": [rent["invoice_number"]],
            }
        ]
    assert client.get(f"/api/customers/{maple['id']}").json()["is_active"] is True
    assert client.get(f"/api/customers/{unit['id']}").json()["is_active"] is True
    # a tenant who moved out still owing is the same case
    r = client.put(f"/api/customers/{unit['id']}", json={"is_active": False})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/customers/{maple['id']}", json={"is_active": False})
    assert r.status_code == 409 and r.json()["detail"]["subs"][0]["balance"] == 1200.0
    # paid, the property can go, with its sub-customers
    r = _payment(client, maple["id"], 1200, 5, [(rent["id"], 1200)])
    assert r.status_code == 201, r.text
    r = client.put(
        f"/api/customers/{maple['id']}?with_subs=true", json={"is_active": False}
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/reports/ar-aging").json()["items"] == []


def test_the_question_about_active_subs_names_each_ones_open_balance(
    client, seed_accounts
):
    maple, unit = _property_and_tenant(client, bill_with_parent=False)
    attic = _customer(client, "Attic", parent_customer_id=maple["id"])
    _invoice(client, attic["id"], 800, 3)
    r = client.put(f"/api/customers/{maple['id']}", json={"is_active": False})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "active_subs"
    assert "Attic ($800.00 open), Unit 4B" in detail["message"]
    assert detail["subs"] == [
        {"id": attic["id"], "name": "Attic", "balance": 800.0},
        {"id": unit["id"], "name": "Unit 4B", "balance": 0.0},
    ]
    # billed on its own, the tenant's balance is its own: the property can
    # go with its sub-customers, and the balance ages on the tenant's row
    r = client.put(
        f"/api/customers/{maple['id']}?with_subs=true", json={"is_active": False}
    )
    assert r.status_code == 200, r.text
    rows = client.get("/api/reports/ar-aging").json()["items"]
    assert [(i["customer_name"], i["total"]) for i in rows] == [
        ("Maple St: Attic", 800.0)
    ]


def test_a_sub_is_not_made_active_again_under_an_inactive_parent(client):
    maple, unit = _property_and_tenant(client)
    r = client.put(
        f"/api/customers/{maple['id']}?with_subs=true", json={"is_active": False}
    )
    assert r.status_code == 200, r.text
    r = client.put(f"/api/customers/{unit['id']}", json={"is_active": True})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "parent_inactive"
    assert detail["parent_id"] == maple["id"]
    assert "Maple St, which is inactive" in detail["message"]
    assert "Make Maple St active first" in detail["message"]
    assert client.get(f"/api/customers/{unit['id']}").json()["is_active"] is False
    # a new sub-customer under it, or one moved under it, the same
    r = client.post(
        "/api/customers", json={"name": "Attic", "parent_customer_id": maple["id"]}
    )
    assert r.status_code == 409 and r.json()["detail"]["error"] == "parent_inactive"
    loner = _customer(client, "Oak Ave")
    r = client.put(
        f"/api/customers/{loner['id']}", json={"parent_customer_id": maple["id"]}
    )
    assert r.status_code == 409 and r.json()["detail"]["error"] == "parent_inactive"
    # the parent first, then the tenant; an edit of the inactive tenant
    # that keeps its parent is fine meanwhile
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={"parent_customer_id": maple["id"], "phone": "555-0104"},
    )
    assert r.status_code == 200, r.text
    assert (
        client.put(
            f"/api/customers/{maple['id']}", json={"is_active": True}
        ).status_code
        == 200
    )
    r = client.put(f"/api/customers/{unit['id']}", json={"is_active": True})
    assert r.status_code == 200 and r.json()["is_active"] is True


# ── 5. three-level names ───────────────────────────────────────────────

DEEP_IIF = (
    "!CUST\tNAME\tCOMPANYNAME\tPHONE1\n"
    "CUST\tMaple St\t\t\n"
    "CUST\tMaple St:Unit 4B:Bedroom\t\t555-0104\n"
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!ENDTRNS\n"
    "TRNS\tINVOICE\t09/01/2026\tAccounts Receivable\tMaple St:Unit 4B:Bedroom\t300\t2001\n"
    "SPL\tINVOICE\t09/01/2026\tSales\tMaple St:Unit 4B:Bedroom\t-300\t2001\n"
    "ENDTRNS\n"
)


def test_a_three_level_name_is_reported_and_flattened_out_loud(
    client, db_session, seed_accounts
):
    r = client.post(
        "/api/iif/validate",
        files={"file": ("deep.iif", DEEP_IIF.encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["customer_job_names"] == 1
    assert report["nested_customer_job_names"] == ["Maple St:Unit 4B:Bedroom"]
    assert any(
        "Maple St:Unit 4B:Bedroom' has more than one level" in w
        and "'Maple St' with job or sub-customer 'Unit 4B:Bedroom'" in w
        for w in report["warnings"]
    ), report["warnings"]

    r = _import(client, DEEP_IIF, sub_customers="true")
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == [
        "Customer 'Maple St:Unit 4B:Bedroom' has more than one level; imported "
        "as 'Maple St' with sub-customer 'Unit 4B:Bedroom' (one level deep)"
    ]
    maple = db_session.query(Customer).filter(Customer.name == "Maple St").one()
    sub = (
        db_session.query(Customer).filter(Customer.parent_customer_id == maple.id).one()
    )
    assert sub.name == "Unit 4B:Bedroom" and sub.phone == "555-0104"
    inv = db_session.query(Invoice).filter(Invoice.invoice_number == "2001").one()
    assert inv.customer_id == sub.id


def test_a_three_level_name_as_a_job_says_so_too(client, db_session, seed_accounts):
    r = _import(client, DEEP_IIF)
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == [
        "Customer 'Maple St:Unit 4B:Bedroom' has more than one level; imported "
        "as 'Maple St' with job 'Unit 4B:Bedroom' (one level deep)"
    ]
    assert db_session.query(Job).filter(Job.name == "Unit 4B:Bedroom").count() == 1
    # said once per import, however many rows name it, on a re-import too
    r = _import(client, DEEP_IIF.replace("2001", "2002"))
    assert len(r.json()["warnings"]) == 1
    # a transaction alone naming three levels is said as well
    text = "\n".join(
        line for line in DEEP_IIF.splitlines() if not line.startswith("CUST")
    )
    r = _import(client, text.replace("Bedroom", "Closet").replace("2001", "2003"))
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == [
        "Customer 'Maple St:Unit 4B:Closet' has more than one level; imported "
        "as 'Maple St' with job 'Unit 4B:Closet' (one level deep)"
    ]


# ── 6. duplicates: siblings and top-level names ─────────────────────────


def test_a_second_propertys_unit_1_is_not_a_duplicate(client):
    oak = _customer(client, "Oak St")
    _customer(client, "Unit 1", parent_customer_id=oak["id"])
    maple = _customer(client, "Maple St")
    # the same name under another parent: fine
    r = client.post(
        "/api/customers", json={"name": "Unit 1", "parent_customer_id": maple["id"]}
    )
    assert r.status_code == 201, r.text
    # a sibling: a likely duplicate, as before
    r = client.post(
        "/api/customers", json={"name": "Unit 1", "parent_customer_id": oak["id"]}
    )
    assert r.status_code == 409 and r.json()["detail"]["error"] == "possible_duplicate"
    # a sub-customer named like a top-level customer is still caught
    r = client.post(
        "/api/customers", json={"name": "Maple St", "parent_customer_id": oak["id"]}
    )
    assert r.status_code == 409
    # the standalone check takes the parent the same way
    found = lambda qs: [  # noqa: E731
        d["name"]
        for d in client.get(f"/api/customers/check-duplicate?{qs}").json()["duplicates"]
    ]
    assert found(f"name=Unit%201&parent_customer_id={oak['id']}") == ["Unit 1"]
    assert found(f"name=Unit%201&parent_customer_id={maple['id']}") == ["Unit 1"]
    assert found("name=Oak%20St") == ["Oak St"]
    assert found("name=Unit%201") == []
    # a bare name is a top-level customer: checked against those
    assert client.post("/api/customers", json={"name": "Oak St"}).status_code == 409
    assert client.post("/api/customers", json={"name": "Unit 1"}).status_code == 201


# ── 7. credit memos route like payments ─────────────────────────────────


def test_a_parents_credit_memo_applies_to_a_sub_billed_with_it(
    client, db_session, seed_accounts
):
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    cm = _credit_memo(client, maple["id"], 200, 3)
    assert cm.status_code == 201, cm.text
    r = client.post(
        f"/api/credit-memos/{cm.json()['id']}/apply",
        json={"invoice_id": rent["id"], "amount": 200},
    )
    assert r.status_code == 200, r.text
    got = client.get(f"/api/invoices/{rent['id']}").json()
    assert Decimal(got["balance_due"]) == 1000 and got["status"] == "partial"
    # a memo issued against the sub's invoice is the parent's too
    r = _credit_memo(client, maple["id"], 50, 4, original_invoice_id=rent["id"])
    assert r.status_code == 201, r.text
    # the sub's statement shows the credit that relieved its invoice, and
    # ties to what it owes; the parent's carries the memo whole
    act = _statement(db_session, unit["id"])
    assert [(ln["type"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1200.00")),
        ("Credit Memo", Decimal("-200.00")),
    ]
    assert act["lines"][1]["description"] == (
        f"from Maple St · applied to #{rent['invoice_number']}"
    )
    assert act["balance_due"] == Decimal("1000.00") == _balance(client, unit["id"])
    act = _statement(db_session, maple["id"])
    assert [(ln["type"], ln["amount"]) for ln in act["lines"]] == [
        ("Invoice", Decimal("1200.00")),
        ("Credit Memo", Decimal("-200.00")),
        ("Credit Memo", Decimal("-50.00")),
    ]
    # ...which is the parent's A/R Aging row: the sub's open item, net of
    # the parent's unapplied credit (the parent's own balance is -50)
    assert act["balance_due"] == Decimal("950.00")
    rows = {
        i["customer_name"]: i
        for i in client.get("/api/reports/ar-aging").json()["items"]
    }
    assert (
        rows["Maple St"]["total"] == 950.0
        and rows["Maple St"]["unapplied_credits"] == 50.0
    )
    assert _balance(client, maple["id"]) == Decimal("-50.00")


def test_a_credit_memo_does_not_reach_a_sub_billed_on_its_own(client, seed_accounts):
    maple, unit = _property_and_tenant(client, bill_with_parent=False)
    rent = _invoice(client, unit["id"], 1200, 2)
    cm = _credit_memo(client, maple["id"], 200, 3)
    assert cm.status_code == 201, cm.text
    r = client.post(
        f"/api/credit-memos/{cm.json()['id']}/apply",
        json={"invoice_id": rent["id"], "amount": 200},
    )
    assert r.status_code == 400, r.text
    assert "belongs to a different customer than this credit memo" in r.json()["detail"]
    r = _credit_memo(client, maple["id"], 50, 4, original_invoice_id=rent["id"])
    assert r.status_code == 400, r.text
    # the pages offer what the API takes
    for name in ("payments.js", "credit_memos.js"):
        js = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "app"
            / "static"
            / "js"
            / name
        ).read_text(encoding="utf-8")
        assert "open_only=true&billed_to=true" in js, name
        assert "kind === 'payment' ? '&billed_to=true'" not in js, name


# ── 8. customer_name, one name everywhere ───────────────────────────────


def test_every_invoice_response_names_the_customer_the_same_way(client, seed_accounts):
    maple, unit = _property_and_tenant(client)
    rent = _invoice(client, unit["id"], 1200, 2)
    full = "Maple St: Unit 4B"
    assert rent["customer_name"] == full
    r = client.put(f"/api/invoices/{rent['id']}", json={"notes": "September"})
    assert r.status_code == 200 and r.json()["customer_name"] == full, r.text
    r = client.post(f"/api/invoices/{rent['id']}/send")
    assert r.status_code == 200 and r.json()["customer_name"] == full, r.text
    r = client.post(f"/api/invoices/{rent['id']}/duplicate")
    assert r.status_code == 201 and r.json()["customer_name"] == full, r.text
    copy_id = r.json()["id"]
    r = client.post(
        f"/api/invoices/{copy_id}/write-off", json={"date": "2026-09-20", "amount": 10}
    )
    assert r.status_code == 201 and r.json()["customer_name"] == full, r.text
    r = client.post(f"/api/invoices/{rent['id']}/void")
    assert r.status_code == 200 and r.json()["customer_name"] == full, r.text
    # the small-balance list from A/R Aging says the same
    rows = client.get("/api/invoices/write-offs/candidates?under=5000").json()
    assert rows and {row["customer_name"] for row in rows} == {full}


# ── 9. the printed country is the billed customer's ─────────────────────


def test_the_printed_invoice_prints_the_billed_customers_country(
    client, db_session, seed_accounts
):
    from app.services.donor_documents import invoice_pdf_context
    from app.services.pdf_service import render_invoice_html
    from app.services.settings_service import get_all_settings

    maple, unit = _property_and_tenant(client, bill_country="CA")
    rent = _invoice(client, unit["id"], 1200, 2)
    settings = get_all_settings(db_session)
    inv = db_session.get(Invoice, rent["id"])
    ctx = invoice_pdf_context(inv, settings)
    assert ctx["bill_to_country"] == "CA"
    assert (ctx["bill_to_name"], ctx["bill_to_for"]) == ("Maple St", "Unit 4B")
    html = render_invoice_html(inv, settings)
    block = html[html.index("Bill To") : html.index('<table class="line-items">')]
    assert "12 Maple St" in block and "Canada" in block and "For: Unit 4B" in block
    # billed on its own, the sub-customer's own country prints
    r = client.put(
        f"/api/customers/{unit['id']}",
        json={"bill_with_parent": False, "bill_country": "DE"},
    )
    assert r.status_code == 200, r.text
    own = _invoice(client, unit["id"], 50, 3)
    inv = db_session.get(Invoice, own["id"])
    assert invoice_pdf_context(inv, settings)["bill_to_country"] == "DE"
    html = render_invoice_html(inv, settings)
    block = html[html.index("Bill To") : html.index('<table class="line-items">')]
    assert "Germany" in block and "Canada" not in block and "For:" not in block
