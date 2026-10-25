"""Subcontractor vendor fields (#431): vendor_type plus the license and
certificate-of-insurance columns round-trip through the API, and the
migration adds and drops them cleanly."""

import sqlite3
from pathlib import Path

PREV = "f2e3d4c5b6a7"
HEAD = "b7d3f1e2a9c4"
COLS = (
    "vendor_type",
    "license_number",
    "coi_carrier",
    "coi_policy_number",
    "coi_expires",
)


def _cfg(db):
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    return cfg


# ---------------------------------------------------------------------------
# API round trip
# ---------------------------------------------------------------------------


def test_sub_fields_round_trip(client):
    v = client.post(
        "/api/vendors",
        json={
            "name": "Ace Electric",
            "vendor_type": "sub",
            "license_number": "EC13001234",
            "coi_carrier": "Travelers",
            "coi_policy_number": "POL-99812",
            "coi_expires": "2027-03-31",
        },
    )
    assert v.status_code == 201, v.text
    body = v.json()
    assert body["vendor_type"] == "sub"
    assert body["license_number"] == "EC13001234"
    assert body["coi_carrier"] == "Travelers"
    assert body["coi_policy_number"] == "POL-99812"
    assert body["coi_expires"] == "2027-03-31"

    got = client.get(f"/api/vendors/{body['id']}").json()
    assert got["vendor_type"] == "sub"
    assert got["coi_expires"] == "2027-03-31"


def test_ordinary_vendor_leaves_sub_fields_blank(client):
    v = client.post("/api/vendors", json={"name": "Paper Co"})
    assert v.status_code == 201, v.text
    body = v.json()
    for col in COLS:
        assert body[col] is None, col


# ---------------------------------------------------------------------------
# A Sub turns on 1099-NEC, but only when the 1099 type is still blank — the
# API behaves like the vendor form's toggleSubFields (#431, review item 5).
# ---------------------------------------------------------------------------


def test_new_sub_with_blank_1099_type_turns_on_nec(client):
    v = client.post("/api/vendors", json={"name": "Ace Electric", "vendor_type": "sub"})
    assert v.status_code == 201, v.text
    body = v.json()
    assert body["is_1099_vendor"] is True
    assert body["vendor_1099_type"] == "NEC"


def test_sub_already_on_misc_keeps_misc(client):
    # a Sub already flagged 1099-MISC must not be switched to NEC silently
    v = client.post(
        "/api/vendors",
        json={
            "name": "Rent-A-Crane",
            "vendor_type": "sub",
            "is_1099_vendor": True,
            "vendor_1099_type": "MISC",
        },
    )
    assert v.status_code == 201, v.text
    body = v.json()
    assert body["is_1099_vendor"] is True
    assert body["vendor_1099_type"] == "MISC"


def test_marking_an_existing_vendor_a_sub_turns_on_nec(client):
    v = client.post("/api/vendors", json={"name": "Day Labor LLC"})
    assert v.status_code == 201, v.text
    vid = v.json()["id"]
    assert v.json()["vendor_1099_type"] is None

    up = client.put(f"/api/vendors/{vid}", json={"vendor_type": "sub"})
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["is_1099_vendor"] is True
    assert body["vendor_1099_type"] == "NEC"


def test_marking_an_existing_misc_vendor_a_sub_keeps_misc(client):
    v = client.post(
        "/api/vendors",
        json={"name": "Equip Co", "is_1099_vendor": True, "vendor_1099_type": "MISC"},
    )
    assert v.status_code == 201, v.text
    vid = v.json()["id"]

    up = client.put(f"/api/vendors/{vid}", json={"vendor_type": "sub"})
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["vendor_type"] == "sub"
    assert body["vendor_1099_type"] == "MISC"


def test_ordinary_new_vendor_is_not_1099(client):
    # the PR no longer defaults 1099 on for a plain vendor in any mode
    v = client.post("/api/vendors", json={"name": "Paper Co"})
    assert v.status_code == 201, v.text
    body = v.json()
    assert body["is_1099_vendor"] is False
    assert body["vendor_1099_type"] is None


def test_create_sub_with_1099_explicitly_off_stays_off(client):
    # an API create that names the 1099 answer (an incorporated sub) is honoured
    v = client.post(
        "/api/vendors",
        json={"name": "Incorp Sub", "vendor_type": "sub", "is_1099_vendor": False},
    )
    assert v.status_code == 201, v.text
    body = v.json()
    assert body["vendor_type"] == "sub"
    assert body["is_1099_vendor"] is False
    assert body["vendor_1099_type"] is None


def test_1099_fires_on_the_sub_transition_not_every_write(client):
    # a sub with 1099 deliberately off: an unrelated edit must not turn it on
    v = client.post(
        "/api/vendors",
        json={"name": "Incorp Sub", "vendor_type": "sub", "is_1099_vendor": False},
    )
    vid = v.json()["id"]
    up = client.put(f"/api/vendors/{vid}", json={"phone": "555-0100"})
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["is_1099_vendor"] is False
    assert body["vendor_1099_type"] is None


def test_resaving_a_sub_with_1099_off_leaves_it_off(client):
    # the form's own save body on an existing sub (vendor_type sub, 1099 off):
    # re-asserting Sub must not flip 1099 back on
    v = client.post(
        "/api/vendors",
        json={"name": "Incorp Sub", "vendor_type": "sub", "is_1099_vendor": False},
    )
    vid = v.json()["id"]
    up = client.put(
        f"/api/vendors/{vid}",
        json={
            "vendor_type": "sub",
            "is_1099_vendor": False,
            "vendor_1099_type": None,
        },
    )
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["vendor_type"] == "sub"
    assert body["is_1099_vendor"] is False
    assert body["vendor_1099_type"] is None


def test_unmarking_a_sub_clears_license_and_coi(client):
    v = client.post(
        "/api/vendors",
        json={
            "name": "Ace Electric",
            "vendor_type": "sub",
            "license_number": "EC13001234",
            "coi_carrier": "Travelers",
            "coi_policy_number": "POL-99812",
            "coi_expires": "2027-03-31",
        },
    )
    vid = v.json()["id"]
    up = client.put(f"/api/vendors/{vid}", json={"vendor_type": None})
    assert up.status_code == 200, up.text
    body = up.json()
    for col in COLS:
        assert body[col] is None, col


# ---------------------------------------------------------------------------
# Migration up / down
# ---------------------------------------------------------------------------


def test_migration_adds_and_drops_the_columns(tmp_path):
    from alembic import command

    db = tmp_path / "vendors.db"
    cfg = _cfg(db)
    command.upgrade(cfg, PREV)

    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("INSERT INTO vendors (name, is_active, balance) VALUES ('Ace', 1, 0)")
    con.commit()

    def columns():
        return {r[1] for r in cur.execute("PRAGMA table_info(vendors)")}

    assert not (set(COLS) & columns())

    command.upgrade(cfg, HEAD)
    assert set(COLS) <= columns()
    # the row survived and the new columns are nullable (blank on an old row)
    cur.execute(
        "UPDATE vendors SET vendor_type='sub', coi_expires='2027-03-31' WHERE name='Ace'"
    )
    con.commit()
    row = dict(
        zip(
            [c[1] for c in cur.execute("PRAGMA table_info(vendors)")],
            cur.execute("SELECT * FROM vendors WHERE name='Ace'").fetchone(),
        )
    )
    assert row["vendor_type"] == "sub"

    command.downgrade(cfg, PREV)
    assert not (set(COLS) & columns())
    assert cur.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 1
    con.close()
