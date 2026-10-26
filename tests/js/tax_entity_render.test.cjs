// Legal entity type (#488) — a real render of the Tax Reports page, so the
// test exercises TaxPage.render() rather than matching strings in the source.
// Two things the maintainer's review called out:
//   - an entity that does not file Schedule C (an S corp, a nonprofit on Form
//     990, …) keeps the header and the Pay Sales Tax button; only the Schedule
//     C controls are swapped for the one sentence. Pay Sales Tax is the only
//     way into showPaySalesTax, and every entity can owe sales tax.
//   - a nonprofit is told Form 990, never Form 1120.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

// TaxPage is a bare top-level `const`, not window.TaxPage, so run the file and
// take the trailing expression's value as the object under test.
function taxPage(entity) {
    const context = {
        window: {},
        App: { settings: entity ? { entity } : {} },
        escapeHtml: value => String(value ?? ''),
        formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
    };
    vm.createContext(context);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/tax.js'), 'utf8');
    return vm.runInContext(src + '\nTaxPage;', context);
}

const PAY_SALES_TAX = /onclick="TaxPage\.showPaySalesTax\(\)"/;
const GENERATE = /onclick="TaxPage\.generate\(\)"/;

test('an S corp gets the Form 1120-S page, with the W-2 wages note (#488 step 2)', async () => {
    const page = taxPage({
        entity_type: 's_corp', label: 'an S corporation',
        return_form: 'Form 1120-S', schedule_c: false, nonprofit: false,
    });
    const html = await page.render();
    assert.match(html, PAY_SALES_TAX);
    assert.match(html, GENERATE);               // the report controls, for the entity return
    assert.match(html, /Tax Reports — Form 1120-S/);
    assert.doesNotMatch(html, /not Schedule C/);
    assert.match(html, /reasonable compensation as W-2 wages/);
    assert.match(html, /Shareholder Distributions/);
    assert.equal(page._reportPath(page._entityInfo()), '/tax/return');
});

test('a partnership and a C corp get their returns, without the S corp note', async () => {
    for (const [form, label] of [['Form 1065', 'a general partnership'], ['Form 1120', 'a C corporation']]) {
        const page = taxPage({ entity_type: 'x', label, return_form: form, schedule_c: false, nonprofit: false });
        const html = await page.render();
        assert.match(html, GENERATE);
        assert.match(html, new RegExp(`Tax Reports — ${form}`));
        assert.doesNotMatch(html, /W-2 wages/);
        assert.equal(page._reportPath(page._entityInfo()), '/tax/return');
    }
});

test('the summary rows come from the report, in the form\'s words', () => {
    const page = taxPage(null);
    const rows = page._summaryRows({
        summary: [
            { key: 'gross_receipts', label: 'Gross receipts', line: '1a' },
            { key: 'returns_and_allowances', label: 'Returns and allowances', line: '1b' },
            { key: 'cost_of_goods_sold', label: 'Cost of goods sold', line: '2' },
            { key: 'net_profit', label: 'Ordinary business income (loss)', line: '22' },
        ],
        gross_receipts: 1000, returns_and_allowances: 0, cost_of_goods_sold: 250, net_profit: 750,
    });
    assert.match(rows, /Gross receipts \(line 1a\)/);
    assert.doesNotMatch(rows, /Returns and allowances/);     // zero: left out
    assert.match(rows, /Cost of goods sold \(line 2\).*\$-250\.00/);  // a subtraction
    assert.match(rows, /grand-total.*Ordinary business income \(loss\) \(line 22\)/);
});

test('a nonprofit is told Form 990, not Form 1120, and keeps Pay Sales Tax', async () => {
    const html = await taxPage({
        entity_type: 'c_corp', label: 'a nonprofit',
        return_form: 'Form 990', schedule_c: false, nonprofit: true,
    }).render();
    assert.match(html, /files Form 990, not Schedule C/);
    assert.doesNotMatch(html, /Form 1120/);
    assert.match(html, /Change the company type/);  // a nonprofit's return follows company type
    assert.doesNotMatch(html, /Change the entity type/);
    assert.match(html, PAY_SALES_TAX);
});

test('a sole proprietor gets the full Schedule C page', async () => {
    const page = taxPage({
        entity_type: 'sole_proprietor', label: 'a sole proprietor',
        return_form: 'Schedule C', schedule_c: true, nonprofit: false,
    });
    const html = await page.render();
    assert.match(html, GENERATE);
    assert.match(html, PAY_SALES_TAX);
    assert.match(html, /files Schedule C/);
    assert.match(html, /Tax Reports — Schedule C/);
    assert.doesNotMatch(html, /W-2 wages/);
    assert.equal(page._reportPath(page._entityInfo()), '/tax/schedule-c');
});

test('an unset entity type renders exactly as before', async () => {
    const page = taxPage(null);
    const html = await page.render();
    assert.match(html, GENERATE);
    assert.match(html, PAY_SALES_TAX);
    assert.match(html, /Profit or Loss from Business/);
    assert.match(html, /Tax Reports — Schedule C/);
    assert.equal(page._reportPath(page._entityInfo()), '/tax/schedule-c');
});
