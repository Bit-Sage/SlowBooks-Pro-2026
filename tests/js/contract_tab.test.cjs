// #552: in construction mode the job detail grows a Contract tab — the terms
// block, the summary tiles and the schedule-of-values grid, every write
// guarded with data-write like the budget grid. A Business or Nonprofit
// company sees today's five tabs with no Contract tab. Drives the real
// JobsPage helpers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function jobsContext(construction) {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        API: { get: async () => [] },
        Terms: {
            mode: construction ? 'construction' : 'business',
            isConstruction() { return this.mode === 'construction'; },
            text: (s) => s,
        },
        escapeHtml: (s) => String(s ?? ''),
        T: (s) => s,
        formatCurrency: (v) => '$' + Number(v || 0).toFixed(2),
        reversedRangeRefused: () => false,
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8') +
            '\nthis.TradePackages = TradePackages;',
        context,
    );
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/jobs.js'), 'utf8') +
            '\nthis.JobsPage = JobsPage;',
        context,
    );
    context.TradePackages._list = [{ id: 7, name: 'Concrete' }];
    return context;
}

test('the Contract tab shows only in construction mode', () => {
    assert.ok('contract' in jobsContext(true).JobsPage.tabLabels());
    assert.ok(!('contract' in jobsContext(false).JobsPage.tabLabels()));
});

test('the SOV grid carries its columns and every write is guarded', () => {
    const page = jobsContext(true).JobsPage;
    const grid = page.sovGridHtml(
        [{ item_no: '01', description: 'Concrete', kind: 'work', scheduled_value: 14000, trade_package_id: 7 }],
        { contract_amount: 14000 },
        [{ id: 7, name: 'Concrete' }],
        [{ id: 1, code: '03 30 00' }],
        true,
    );
    // the schedule-of-values columns the spec names
    for (const col of ['Item #', 'Description', 'Package', 'Section', 'Kind', 'Scheduled value']) {
        assert.match(grid, new RegExp(col));
    }
    // the grid's inputs and buttons are write-guarded
    assert.match(grid, /class="sov-item" data-write/);
    assert.match(grid, /class="sov-value money" data-write/);
    assert.match(grid, /JobsPage\.saveSov\(\)/);
    assert.match(grid, /JobsPage\.buildSov\(\)/);
    // Build is enabled when the job has an accepted estimate
    assert.doesNotMatch(grid, /disabled[^>]*JobsPage\.buildSov/);
});

test('Build from accepted estimate is disabled without one', () => {
    const page = jobsContext(true).JobsPage;
    const grid = page.sovGridHtml([], { contract_amount: 0 }, [], [], false);
    assert.match(grid, /disabled[^>]*JobsPage\.buildSov/);
});

test('the balance line highlights until the SOV balances to the contract sum', () => {
    const page = jobsContext(true).JobsPage;
    const off = page.sovGridHtml(
        [{ description: 'A', kind: 'work', scheduled_value: 9000 }],
        { contract_amount: 10000 },
        [], [], false,
    );
    assert.match(off, /id="sov-balance" style="color:var\(--text-danger\)/);
    const even = page.sovGridHtml(
        [{ description: 'A', kind: 'work', scheduled_value: 10000 }],
        { contract_amount: 10000 },
        [], [], false,
    );
    assert.match(even, /id="sov-balance" style="color:var\(--text-success\)/);
});

test('the terms block carries the type select and the guarded form', () => {
    const page = jobsContext(true).JobsPage;
    const terms = page.contractTermsHtml({ contract_type: 'gmp', contract_amount: 500000 });
    assert.match(terms, /id="contract-terms" data-write/);
    assert.match(terms, /name="contract_type"/);
    assert.match(terms, /value="gmp" selected/);
    assert.match(terms, /name="retainage_pct"/);
    assert.match(terms, /JobsPage\.saveContract/);
});

test('saveContract and saveSov and buildSov are registered write actions', () => {
    const app = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    const block = app.slice(app.indexOf('WRITE_ACTIONS:'), app.indexOf('hideWriteControls(root)'));
    assert.match(block, /'JobsPage\.saveContract'/);
    assert.match(block, /'JobsPage\.saveSov'/);
    assert.match(block, /'JobsPage\.buildSov'/);
});
