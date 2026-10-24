"""#176: the estimate's line-item cells did not follow the header.

Header: Item, Description, Cost code, Cost, Qty, Rate, Tax, Amount.
Cells:  Item, Description, Qty, Cost code, Cost, Rate, ...  — so what a
person typed as a quantity sat under "Cost code". The cell order is pinned
to the header order here, in the source that renders both.
"""

import re
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "app/static/js/estimates.js").read_text(
    encoding="utf-8"
)


def _order(pattern_pairs):
    return [name for name, pat in pattern_pairs if re.search(pat, JS)]


def test_estimate_line_cells_follow_the_header():
    head = JS[JS.index('<th scope="col">Item</th>') :]
    head = head[: head.index("</tr></thead>")]
    head_order = [
        m
        for m in re.findall(
            r"<th[^>]*>([^<]*)</th>|\$\{(CostCodes\.headHtml)\(\)\}", head
        )
    ]
    head_names = [a or b for a, b in head_order]
    assert head_names[:5] == [
        "Item",
        "Description",
        "CostCodes.headHtml",
        "Cost",
        "Qty",
    ]

    row = JS[JS.index("lineRowHtml(idx, line, items) {") :]
    row = row[: row.index("</tr>`;")]
    cells = re.findall(
        r'class="(line-item|line-desc|line-unit-cost|line-qty|line-rate)(?: money)?"|(CostCodes\.cellHtml)',
        row,
    )
    cell_names = [a or b for a, b in cells]
    assert cell_names == [
        "line-item",
        "line-desc",
        "CostCodes.cellHtml",
        "line-unit-cost",
        "line-qty",
        "line-rate",
    ]


def test_picking_an_item_fills_its_cost_and_the_form_opens_wide():
    assert "cost.value = item.cost && Number(item.cost) !== 0 ? item.cost : ''" in JS
    assert "`, { wide: true });" in JS
    assert (
        'class="table-container table-container--scroll"><table class="line-items-table">'
        in JS
    )
    assert 'class="line-item" style="min-width:150px"' in JS
