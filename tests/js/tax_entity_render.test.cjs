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

test('an S corp keeps Pay Sales Tax but loses the Schedule C controls', async () => {
    const html = await taxPage({
        entity_type: 's_corp', label: 'an S corporation',
        return_form: 'Form 1120-S', schedule_c: false, nonprofit: false,
    }).render();
    assert.match(html, PAY_SALES_TAX);          // the way into showPaySalesTax survives
    assert.doesNotMatch(html, GENERATE);        // the Schedule C report controls are gone
    assert.match(html, /files Form 1120-S, not Schedule C/);
    assert.match(html, /Change the entity type/);  // an S corp is pointed at the entity type
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
    const html = await taxPage({
        entity_type: 'sole_proprietor', label: 'a sole proprietor',
        return_form: 'Schedule C', schedule_c: true, nonprofit: false,
    }).render();
    assert.match(html, GENERATE);
    assert.match(html, PAY_SALES_TAX);
    assert.match(html, /files Schedule C/);
});

test('an unset entity type renders exactly as before', async () => {
    const html = await taxPage(null).render();
    assert.match(html, GENERATE);
    assert.match(html, PAY_SALES_TAX);
    assert.match(html, /Profit or Loss from Business/);
});
