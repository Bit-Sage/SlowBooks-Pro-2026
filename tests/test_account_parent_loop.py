"""An account can't become a sub-account of its own sub-account
(SlowBooks-Pro-Testing #101, found in PNWImport's fork). Only an account
named as its own parent was refused; A under B, then B under A, was saved,
and every walk up the tree (the report rows' paths, the IIF export's full
names) went round forever."""

import io
from datetime import date
from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.services.accounting import create_journal_entry


def _new(client, name, parent=None):
    r = client.post(
        "/api/accounts",
        json={"name": name, "account_type": "expense", "parent_id": parent},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _move(client, account_id, parent_id):
    return client.put(f"/api/accounts/{account_id}", json={"parent_id": parent_id})


def test_two_accounts_cannot_be_each_others_parent(client, seed_accounts):
    a = _new(client, "Vehicles")
    b = _new(client, "Truck", parent=a)
    r = _move(client, a, b)
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "That would make Vehicles a sub-account of itself (through Truck)."
    )


def test_a_loop_two_levels_down_is_refused(client, seed_accounts):
    a = _new(client, "Vehicles")
    b = _new(client, "Truck", parent=a)
    c = _new(client, "Fuel", parent=b)
    r = _move(client, a, c)
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "That would make Vehicles a sub-account of itself (through Fuel)."
    )


def test_a_legal_reparent_still_works(client, seed_accounts):
    a = _new(client, "Vehicles")
    b = _new(client, "Equipment")
    c = _new(client, "Truck", parent=a)
    assert _move(client, c, b).json()["parent_id"] == b
    assert _move(client, a, b).json()["parent_id"] == b  # Truck's old parent
    assert _move(client, c, None).json()["parent_id"] is None


def test_the_chart_stops_at_max_depth_on_create_and_move(client, seed_accounts):
    """A chain built to MAX_DEPTH with POST is fine; one deeper is refused on
    create, and re-parenting another account onto the deepest one is refused
    the same way — by its depth, not the misleading loop message (#416)."""
    from app.services.account_tree import MAX_DEPTH

    prev = None
    deepest = None
    for i in range(MAX_DEPTH):
        row = _new(client, f"D{i}", parent=prev)
        prev = deepest = row

    # the next one down is one level too deep
    r = client.post(
        "/api/accounts",
        json={"name": "TooDeep", "account_type": "expense", "parent_id": deepest},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        f"TooDeep would be {MAX_DEPTH + 1} levels deep; "
        f"the chart stops at {MAX_DEPTH}."
    )

    # moving a separate account onto the deepest one is the same depth refusal
    other = _new(client, "Elsewhere")
    r = _move(client, other, deepest)
    assert r.status_code == 400
    assert "the chart stops at" in r.json()["detail"]
    assert "sub-account of itself" not in r.json()["detail"]

    # a real loop within the limit still gets the loop message
    a = _new(client, "Vehicles")
    b = _new(client, "Truck", parent=a)
    r = _move(client, a, b)
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "That would make Vehicles a sub-account of itself (through Truck)."
    )


def test_an_account_still_cannot_be_its_own_parent(client, seed_accounts):
    a = _new(client, "Vehicles")
    r = _move(client, a, a)
    assert r.status_code == 400
    assert r.json()["detail"] == "An account cannot be its own parent."


def test_a_parent_that_does_not_exist_is_refused(client, seed_accounts):
    a = _new(client, "Vehicles")
    assert _move(client, a, 999999).status_code == 400
    r = client.post(
        "/api/accounts",
        json={"name": "Orphan", "account_type": "expense", "parent_id": 999999},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "That parent account does not exist."


def test_a_chart_file_cannot_make_a_loop(client, db_session, seed_accounts):
    text = "Name,Type,Parent\nRig One,expense,Rig Two\nRig Two,expense,Rig One\n"
    out = client.post(
        "/api/csv/import/accounts?dry_run=0",
        files={"file": ("chart.csv", io.BytesIO(text.encode()), "text/csv")},
    ).json()
    assert out["created"] == 2
    assert any("sub-account of itself" in (r.get("note") or "") for r in out["rows"])
    db_session.expire_all()
    by_name = {a.name: a for a in db_session.query(Account)}
    one, two = by_name["Rig One"], by_name["Rig Two"]
    assert not (one.parent_id == two.id and two.parent_id == one.id)


def test_a_chart_file_loop_shows_in_the_dry_run_preview(client, seed_accounts):
    """The note belongs in the preview, not only once the import is applied —
    the page shows the dry run before anyone commits to it."""
    text = "Name,Type,Parent\nRig One,expense,Rig Two\nRig Two,expense,Rig One\n"
    out = client.post(
        "/api/csv/import/accounts?dry_run=1",
        files={"file": ("chart.csv", io.BytesIO(text.encode()), "text/csv")},
    ).json()
    assert out["dry_run"] is True
    assert out["created"] == 2
    assert any(
        "sub-account of itself" in (r.get("note") or "")
        and "left off" in (r.get("note") or "")
        for r in out["rows"]
    )


def test_a_loop_deeper_than_the_walk_limit_is_still_refused(
    client, db_session, seed_accounts
):
    """A chain longer than the walk's depth cap used to exhaust the walk and
    then be allowed, saving a loop the tree-walkers spin on. Refuse it — now
    by its depth (#416: the depth guard runs before the loop guard), so the
    message names the depth, not a false loop."""
    from app.services.account_tree import MAX_DEPTH

    prev = None
    chain = []
    for i in range(MAX_DEPTH + 5):
        a = Account(
            name=f"L{i}", account_type=AccountType.EXPENSE, balance=Decimal("0")
        )
        db_session.add(a)
        db_session.flush()
        a.parent_id = prev
        prev = a.id
        chain.append(a.id)
    db_session.commit()
    # chain[0] is the root, chain[-1] the deepest leaf: hanging the root off
    # the leaf is both a loop and too deep; the depth guard catches it first.
    r = _move(client, chain[0], chain[-1])
    assert r.status_code == 400
    assert "the chart stops at" in r.json()["detail"]


def test_a_loop_already_saved_does_not_hang_the_chart_or_reports(
    client, db_session, seed_accounts
):
    """Written straight to the database, as a loop saved before the fix."""
    a = Account(name="Vehicles", account_type=AccountType.EXPENSE, balance=Decimal("0"))
    b = Account(name="Truck", account_type=AccountType.EXPENSE, balance=Decimal("0"))
    db_session.add_all([a, b])
    db_session.flush()
    a.parent_id, b.parent_id = b.id, a.id
    lines = [
        {"account_id": b.id, "debit": Decimal("10"), "credit": Decimal("0")},
        {
            "account_id": seed_accounts["1000"].id,
            "debit": Decimal("0"),
            "credit": Decimal("10"),
        },
    ]
    create_journal_entry(db_session, date(2026, 3, 1), "loop", lines)
    db_session.commit()

    assert client.get("/api/accounts").status_code == 200
    period = {"start_date": "2026-01-01", "end_date": "2026-12-31"}
    pl = client.get("/api/reports/profit-loss", params=period)
    assert pl.status_code == 200
    assert client.get("/api/reports/trial-balance").status_code == 200
    iif = client.get("/api/iif/export/accounts")
    assert iif.status_code == 200 and "Truck" in iif.text
    # And the loop can be undone from the form.
    assert _move(client, a.id, None).status_code == 200
