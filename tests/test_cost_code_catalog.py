"""Division/section cost codes (#550): code normalization and level, the
standard divisions + common sections loader, and a few-hundred-section import
through POST /api/cost-codes/import (code,name only) that auto-parents and
normalizes in one call."""

import pytest

from app.services.cost_code_catalog import (
    DIVISIONS,
    SECTIONS,
    code_level,
    division_of,
    normalize_code,
)

# ── Normalization + level ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected,level",
    [
        ("033000", "03 30 00", "section"),
        ("03-3000", "03 30 00", "section"),
        ("03 3000", "03 30 00", "section"),
        ("03.30.00", "03 30 00", "section"),
        ("07 42 13.16", "07 42 13.16", "section"),
        ("0742 1316", "07 42 13.16", "section"),
        ("03", "03", "division"),
        ("L-100", "L-100", None),
        ("EQ", "EQ", None),
        # a numeric custom chart is never mangled (#550 review)
        ("1.5", "1.5", None),
        ("1000", "1000", None),
        ("0330", "0330", None),
        ("2000", "2000", None),
    ],
)
def test_normalize_and_level(raw, expected, level):
    norm = normalize_code(raw)
    assert norm == expected
    assert code_level(norm) == level


def test_division_of_a_section():
    assert division_of("03 30 00") == "03"
    assert division_of("07 42 13.16") == "07"
    assert division_of("03") is None  # a division has no parent division
    assert division_of("L-100") is None


# ── Loader ───────────────────────────────────────────────────────────────


def test_standard_loader_creates_divisions_and_sections_with_parents(client):
    std = client.post("/api/cost-codes/standard").json()
    by_code = {c["code"]: c for c in std}
    # every division and section lands
    for code, _name in DIVISIONS:
        assert code in by_code, code
        assert by_code[code]["level"] == "division"
    for raw, _name in SECTIONS:
        code = normalize_code(raw)
        assert code in by_code, code
        row = by_code[code]
        assert row["level"] == "section"
        assert row["parent_code"] == division_of(code)
    # seeded codes default to "other"
    assert {c["cost_type"] for c in std} == {"other"}


def test_standard_loader_is_idempotent_and_skips_existing(client):
    # a hand-made "03 30 00" is kept, not duplicated or renamed by the loader
    mine = client.post(
        "/api/cost-codes",
        json={"code": "033000", "name": "My concrete", "cost_type": "subcontract"},
    ).json()
    assert mine["code"] == "03 30 00"
    assert mine["level"] == "section"

    first = client.post("/api/cost-codes/standard").json()
    second = client.post("/api/cost-codes/standard").json()
    assert len(first) == len(second)
    rows = client.get("/api/cost-codes?include_inactive=true").json()
    concrete = [c for c in rows if c["code"] == "03 30 00"]
    assert len(concrete) == 1
    assert concrete[0]["name"] == "My concrete"  # the loader left it alone


# ── Import: a few hundred sections, code,name only, one call ──────────────


def test_import_three_hundred_sections_code_name_only(client):
    divisions = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 21, 22, 23, 25, 26, 27, 28]
    lines = []
    for dd in divisions:
        for ss in range(5, 95, 5):
            # compact "DDSS00" so normalization is exercised through import
            lines.append(f"{dd:02d}{ss:02d}00,Section {dd}-{ss}")
            if len(lines) == 300:
                break
        if len(lines) == 300:
            break
    assert len(lines) == 300

    r = client.post("/api/cost-codes/import", json={"csv": "\n".join(lines)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == []
    # 300 sections + the divisions they parent to (created from the titles)
    assert body["created"] >= 300

    rows = client.get("/api/cost-codes?include_inactive=true").json()
    by_code = {c["code"]: c for c in rows}
    # a sample normalized section landed, parented to its division
    assert "03 30 00" in by_code
    sec = by_code["03 30 00"]
    assert sec["level"] == "section"
    assert sec["parent_code"] == "03"
    assert by_code["03"]["level"] == "division"
    # every imported section is normalized and auto-parented
    sections = [c for c in rows if c["level"] == "section"]
    assert len(sections) >= 300
    assert all(c["parent_code"] == c["code"][:2] for c in sections)
    assert all(" " in c["code"] for c in sections)  # "DD SS SS", not "DDSS00"


def test_reimporting_the_same_csv_is_idempotent(client):
    divisions = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 21, 22, 23, 25, 26, 27, 28]
    lines = []
    for dd in divisions:
        for ss in range(5, 95, 5):
            lines.append(f"{dd:02d}{ss:02d}00,Section {dd}-{ss}")
            if len(lines) == 300:
                break
        if len(lines) == 300:
            break
    csv = "\n".join(lines)

    first = client.post("/api/cost-codes/import", json={"csv": csv}).json()
    assert first["created"] >= 300 and first["errors"] == []
    # the second pass over the same rows creates nothing (every code already
    # exists in its normalized form)
    second = client.post("/api/cost-codes/import", json={"csv": csv}).json()
    assert second["created"] == 0 and second["errors"] == []


def test_reimport_keeps_a_numeric_custom_chart_as_typed(client):
    # a company on a 1000/2000 chart: codes keep their form across imports,
    # no divisions are invented
    csv = "1000,Direct Costs\n1100,Labor\n2000,Overhead"
    first = client.post("/api/cost-codes/import", json={"csv": csv}).json()
    assert first["created"] == 3
    second = client.post("/api/cost-codes/import", json={"csv": csv}).json()
    assert second["created"] == 0
    rows = {c["code"]: c for c in client.get("/api/cost-codes").json()}
    assert set(rows) == {"1000", "1100", "2000"}
    assert all(rows[c]["level"] is None for c in rows)


def test_import_does_not_wipe_a_pinned_level(client):
    # a level named on import is kept; re-importing the row without one (and
    # whose shape yields no level) leaves the pinned level alone
    client.post(
        "/api/cost-codes/import",
        json={"rows": [{"code": "L-100", "name": "Labor", "level": "section"}]},
    )
    rows = {c["code"]: c for c in client.get("/api/cost-codes").json()}
    assert rows["L-100"]["level"] == "section"
    client.post(
        "/api/cost-codes/import",
        json={"rows": [{"code": "L-100", "name": "Labor crew"}]},
    )
    rows = {c["code"]: c for c in client.get("/api/cost-codes").json()}
    assert rows["L-100"]["level"] == "section"  # not wiped
    assert rows["L-100"]["name"] == "Labor crew"  # name still updated


def test_import_creates_a_missing_division_from_the_title_table(client):
    # no "05" yet; importing a section under it creates "05 Metals"
    r = client.post("/api/cost-codes/import", json={"csv": "05 12 00,My metals"})
    assert r.status_code == 200, r.text
    rows = {c["code"]: c for c in client.get("/api/cost-codes").json()}
    assert "05" in rows
    assert rows["05"]["name"] == "Metals"  # from DIVISION_TITLES
    assert rows["05"]["level"] == "division"
    assert rows["05 12 00"]["parent_code"] == "05"


# ── Ordering ──────────────────────────────────────────────────────────────


def test_list_and_tree_are_ordered_by_code(client):
    client.post("/api/cost-codes/standard")
    codes = [c["code"] for c in client.get("/api/cost-codes").json()]
    assert codes == sorted(codes)
    tree = client.get("/api/cost-codes/tree").json()
    roots = [n["code"] for n in tree]
    assert roots == sorted(roots)
    for node in tree:
        child_codes = [c["code"] for c in node["children"]]
        assert child_codes == sorted(child_codes)


# ── Migration up / down ────────────────────────────────────────────────────

PREV = "b7d3f1e2a9c4"
HEAD = "c8e4a2f1b3d5"
LINE_TABLES = (
    "bill_lines",
    "estimate_lines",
    "purchase_order_lines",
    "vendor_credit_lines",
)


def _cfg(db):
    from pathlib import Path

    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    return cfg


def test_migration_adds_and_drops_the_columns_and_tables(tmp_path):
    import sqlite3

    from alembic import command

    db = tmp_path / "mf.db"
    cfg = _cfg(db)
    command.upgrade(cfg, PREV)
    con = sqlite3.connect(db)
    cur = con.cursor()

    def cols(table):
        return {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}

    def tables():
        return {
            r[0]
            for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert "level" not in cols("cost_codes")
    assert not ({"trade_packages", "trade_package_sections"} & tables())

    command.upgrade(cfg, HEAD)
    assert "level" in cols("cost_codes")
    for table in LINE_TABLES:
        assert "cost_type" in cols(table), table
    assert {"trade_packages", "trade_package_sections"} <= tables()

    command.downgrade(cfg, PREV)
    assert "level" not in cols("cost_codes")
    for table in LINE_TABLES:
        assert "cost_type" not in cols(table), table
    assert not ({"trade_packages", "trade_package_sections"} & tables())
    con.close()
