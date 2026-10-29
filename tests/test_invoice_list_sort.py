"""The Invoices list sorts by any column (#650, from the 2.31.1 gate).

The shared list helper (renderListPage in utils.js) already sorted a column
that carries a `key`; the Invoices list never gave its columns one. Sortable
headings are buttons now, so the keyboard reaches them, and each header cell
says how its column is sorted (aria-sort). The order last chosen on a list is
kept when the list is drawn again."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _js(name):
    return (ROOT / "app/static/js" / name).read_text(encoding="utf-8")


def test_every_invoices_column_but_actions_sorts():
    js = _js("invoices.js")
    render = js[js.index("    async render() {") :]
    render = render[: render.index("\n    },")]
    for key in (
        "invoice_number",
        "customer_name",
        "date",
        "due_date",
        "status",
        "total",
        "balance_due",
    ):
        assert f"key: '{key}'" in render, key
    assert "sort: { id: 'inv-list' }" in render
    assert "'Actions'," in render


def _helper_source():
    utils = _js("utils.js")
    start = utils.index("const _listSortState = {};")
    end = utils.index("function filterRows(")
    return utils[start:end]


def _run(script):
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_sortable_heading_is_a_button_with_aria_sort():
    src = _helper_source()
    script = src + """
const cols = [{ label: '#', key: 'n' }, { label: 'Total', cls: 'amount', key: 't' }, 'Actions'];
const items = [{ n: '1002', t: '5' }, { n: '999', t: '20' }, { n: '1001', t: '7.5' }];
const row = i => `<tr><td>${i.n}</td><td>${i.t}</td><td></td></tr>`;
const none = _listTableHtml({ columns: cols, items, row, sort: { id: 'x', column: null, direction: 'asc' } });
const desc = _listTableHtml({ columns: cols, items, row, sort: { id: 'x', column: 't', direction: 'desc' } });
const asc = _listTableHtml({ columns: cols, items, row, sort: { id: 'x', column: 'n', direction: 'asc' } });
console.log(JSON.stringify({ none, desc, asc }));
"""
    got = _run(script)
    none, desc, asc = got["none"], got["desc"], got["asc"]
    # a button in each sortable heading, none in Actions
    assert none.count('<button type="button" class="th-sort"') == 2
    assert '<th scope="col" class="">Actions</th>' in none
    assert none.count('aria-sort="none"') == 2
    # the sorted column says which way, the other says none
    assert 'class="amount sortable sort-active" aria-sort="descending"' in desc
    assert (
        desc.index("<td>20</td>")
        < desc.index("<td>7.5</td>")
        < desc.index("<td>5</td>")
    )
    # invoice numbers compare as numbers when they are numbers
    assert 'aria-sort="ascending"' in asc
    assert (
        asc.index("<td>999</td>")
        < asc.index("<td>1001</td>")
        < asc.index("<td>1002</td>")
    )
    assert 'aria-hidden="true"' in asc  # the arrow is not read out


def test_the_order_chosen_is_kept_when_the_list_is_drawn_again():
    src = _helper_source()
    assert "const kept = _listSortState[sort.id]?.sort;" in src
    assert "wrap.querySelector(`th.sort-active .th-sort`)?.focus();" in src
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    assert "thead th .th-sort {" in css and "thead th .th-sort:focus-visible {" in css


def test_the_changelog_and_docs_say_so():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "#650" in changelog
    features = (ROOT / "docs/features.md").read_text(encoding="utf-8")
    assert "#650" in features
