// #554: in construction mode the job detail grows a Change orders tab, after
// Buyout. The line form prices material and labor separately with markup
// (none under an allowance or on a sub CO), and the SOV grid shows a
// change_order-kind line read-only (its value is owned by the CO). Drives the
// real JobsPage helpers.
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

test('the Change orders tab shows only in construction mode, after Buyout', () => {
    const labels = jobsContext(true).JobsPage.tabLabels();
    assert.ok('changeorders' in labels);
    const keys = Object.keys(labels);
    assert.ok(keys.indexOf('changeorders') > keys.indexOf('buyout'));
    assert.ok(!('changeorders' in jobsContext(false).JobsPage.tabLabels()));
});

test('the line form math prices with markup and forces zero under an allowance', () => {
    const page = jobsContext(true).JobsPage;
    // labor 10h × 50 at 15% → cost 500, sell 575
    let r = page.coLineMath(10, 50, 15, null, null, 'contract', 'owner');
    assert.equal(r.cost, 500);
    assert.equal(r.sell, 575);
    // an allowance pays for it → no markup, sell = cost
    r = page.coLineMath(10, 50, 15, null, null, 'allowance', 'owner');
    assert.equal(r.sell, 500);
    // a sub CO carries no markup either
    r = page.coLineMath(10, 50, 15, null, null, 'contract', 'sub');
    assert.equal(r.sell, 500);
    // markup falls back to the CO's then the job's
    assert.equal(page.coLineMath(1, 1000, '', 10, 20, 'contract', 'owner').sell, 1100);
    assert.equal(page.coLineMath(1, 1000, '', '', 20, 'contract', 'owner').sell, 1200);
});

test('the change-order log carries its columns and the New button is guarded', () => {
    const page = jobsContext(true).JobsPage;
    const log = page.coLogHtml([
        { id: 1, number: 'CO-0001', kind: 'owner', status: 'pending', title: 'Extra', funding: 'contract', cost_total: 1500, markup_total: 175, sell_total: 1675, date: '2026-03-01' },
    ]);
    for (const col of ['Number', 'Kind', 'Status', 'Title', 'Funding', 'Cost', 'Markup', 'Total']) {
        assert.match(log, new RegExp(col));
    }
    // the New button sits inside a write-guarded action bar
    assert.match(log, /data-write[\s\S]*onclick="JobsPage\.coNew\(\)"/);
    assert.match(log, /JobsPage\.coApprove\(1\)/);
    assert.match(log, /change-orders\/1\/pdf/);
});

test('the SOV grid shows a change_order line read-only', () => {
    const page = jobsContext(true).JobsPage;
    const grid = page.sovGridHtml(
        [
            { id: 1, item_no: '01', description: 'Base', kind: 'work', scheduled_value: 100000 },
            { id: 2, item_no: '02', description: 'CO-0001 Extra', kind: 'change_order', scheduled_value: 1675 },
        ],
        { contract_amount: 101675, allowances: [] },
        [], [], true,
    );
    assert.match(grid, /sov-co-row/);
    assert.match(grid, /read-only/);
    // the read-only row carries no editable value input
    assert.match(grid, /sov-covalue/);
});

test('an allowance SOV line shows its change-order draw-down', () => {
    const page = jobsContext(true).JobsPage;
    const grid = page.sovGridHtml(
        [{ id: 5, description: 'Finishes', kind: 'allowance', scheduled_value: 2000 }],
        { contract_amount: 2000, allowances: [{ sov_line_id: 5, used: 1500, remaining: 500 }] },
        [], [], false,
    );
    assert.match(grid, /Used \$1,500\.00/);
    assert.match(grid, /\$500\.00 left/);
});

test('the change-order write actions are registered', () => {
    const app = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    const block = app.slice(app.indexOf('WRITE_ACTIONS:'), app.indexOf('hideWriteControls(root)'));
    for (const fn of ['coNew', 'coSave', 'coApprove', 'coVoid', 'coPromote', 'coMakeSub']) {
        assert.match(block, new RegExp(`'JobsPage\\.${fn}'`));
    }
});
