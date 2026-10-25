// #551: in construction mode the estimate line row grows a Package, a Kind
// and a Unit cell, the form grows a Markups block, and the view modal grows
// an Accept button (guarded like the other write actions). A Business or
// Nonprofit company sees none of it. Drives the real EstimatesPage helpers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function estimatesContext(construction) {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        API: { get: async () => [] },
        Terms: { mode: construction ? 'construction' : 'business', isConstruction() { return this.mode === 'construction'; } },
        escapeHtml: (s) => String(s ?? ''),
        T: (s) => s,
        formatCurrency: (v) => '$' + v,
        ItemQuickAdd: { options: () => '' },
        TaxDefault: { checked: () => '' },
        SalesLines: { rate: (v) => '$' + v },
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8') +
            '\nthis.CostCodes = CostCodes; this.CostTypes = CostTypes; this.TradePackages = TradePackages;',
        context,
    );
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/estimates.js'), 'utf8') +
            '\nthis.EstimatesPage = EstimatesPage;',
        context,
    );
    // a package and a cost code so the construction cells have something to show
    context.CostCodes._list = [{ id: 1, code: '03', name: 'Concrete', label: '03 Concrete' }];
    context.CostTypes._list = [{ code: 'labor', name: 'Labor' }];
    context.TradePackages._list = [{ id: 7, name: 'Concrete' }];
    return context;
}

test('a construction line row carries the package, kind and unit cells', () => {
    const ctx = estimatesContext(true);
    const row = ctx.EstimatesPage.lineRowHtml(0, { line_kind: 'alternate', unit: 'SF' }, []);
    assert.match(row, /class="line-package"/);
    assert.match(row, /class="line-kind"/);
    assert.match(row, /class="line-unit"/);
    assert.match(row, /class="line-alt-accepted"/); // hidden accept flag rides along
    // the existing cells are still there in their pinned order
    assert.match(row, /class="line-item"/);
    assert.match(row, /class="line-unit-cost money"/);
});

test('a business line row carries none of the construction cells', () => {
    const ctx = estimatesContext(false);
    const row = ctx.EstimatesPage.lineRowHtml(0, {}, []);
    assert.doesNotMatch(row, /class="line-package"/);
    assert.doesNotMatch(row, /class="line-kind"/);
    assert.doesNotMatch(row, /class="line-unit"/);
    // today's form is intact
    assert.match(row, /class="line-item"/);
    assert.match(row, /class="line-unit-cost money"/);
});

test('the markups block shows only in construction mode', () => {
    assert.match(estimatesContext(true).EstimatesPage.markupsBlockHtml({}), /Markups/);
    assert.match(estimatesContext(true).EstimatesPage.markupsBlockHtml({}), /name="gc_pct"/);
    assert.equal(estimatesContext(false).EstimatesPage.markupsBlockHtml({}), '');
});

test('the Accept button shows only for a pending estimate with a job', () => {
    const page = estimatesContext(true).EstimatesPage;
    assert.match(page.acceptButtonHtml({ id: 5, job_id: 3, status: 'pending' }), /EstimatesPage\.accept\(5\)/);
    assert.equal(page.acceptButtonHtml({ id: 5, job_id: null, status: 'pending' }), '');
    assert.equal(page.acceptButtonHtml({ id: 5, job_id: 3, status: 'accepted' }), '');
});

test('Accept and Price-from-cost are registered write actions (hidden for read-only)', () => {
    const app = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    const block = app.slice(app.indexOf('WRITE_ACTIONS:'), app.indexOf('hideWriteControls(root)'));
    assert.match(block, /'EstimatesPage\.accept'/);
    assert.match(block, /'EstimatesPage\.repriceFromCost'/);
});
