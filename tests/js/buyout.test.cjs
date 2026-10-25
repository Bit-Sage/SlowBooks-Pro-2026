// #553: construction mode grows commitment fields on the purchase-order form
// (Type / Package / Retainage % / scope), a Buyout tab on the job, and Sub
// compliance flags plus a lien-waiver select on Pay Bills. A business company
// sees none of it. Drives the real page helpers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function baseContext(construction) {
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
        formatDate: (v) => String(v ?? ''),
        reversedRangeRefused: () => false,
    };
    vm.createContext(context);
    return context;
}

function load(context, file, name) {
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js', file), 'utf8') +
            `\nthis.${name} = ${name};`,
        context,
    );
}

function poContext(construction) {
    const context = baseContext(construction);
    load(context, 'utils.js', 'TradePackages');
    load(context, 'purchase_orders.js', 'PurchaseOrdersPage');
    context.TradePackages._list = [{ id: 7, name: 'Concrete' }];
    return context;
}

function jobsContext(construction) {
    const context = baseContext(construction);
    load(context, 'utils.js', 'TradePackages');
    load(context, 'jobs.js', 'JobsPage');
    context.TradePackages._list = [{ id: 7, name: 'Concrete' }];
    return context;
}

function billsContext(construction) {
    const context = baseContext(construction);
    load(context, 'bills.js', 'BillsPage');
    return context;
}

// ── PO commitment fields ────────────────────────────────────────────────

test('the PO form shows commitment fields in construction mode only', () => {
    const con = poContext(true).PurchaseOrdersPage;
    const html = con.commitmentFieldsHtml({ commitment_type: 'subcontract', trade_package_id: 7, retainage_pct: 5 });
    assert.match(html, /name="commitment_type"/);
    assert.match(html, /value="subcontract" selected/);
    assert.match(html, /name="trade_package_id"/);
    assert.match(html, /Concrete/);
    assert.match(html, /name="retainage_pct"/);
    assert.match(html, /name="scope_notes"/);

    const biz = poContext(false).PurchaseOrdersPage;
    assert.equal(biz.commitmentFieldsHtml({ commitment_type: 'po' }), '');
});

test('a subcontract to a vendor not marked a Sub shows the note, no block', () => {
    const page = poContext(true).PurchaseOrdersPage;
    page._vendors = [{ id: 1, vendor_type: null }, { id: 2, vendor_type: 'sub' }];
    assert.match(page._vendorNote(1, 'subcontract'), /not marked a Sub/);
    assert.equal(page._vendorNote(2, 'subcontract'), '');
    // a plain PO never shows the note, whatever the vendor
    assert.equal(page._vendorNote(1, 'po'), '');
});

// ── Buyout tab ────────────────────────────────────────────────────────────

test('the Buyout tab shows only in construction mode, after Contract', () => {
    const labels = Object.keys(jobsContext(true).JobsPage.tabLabels());
    assert.ok(labels.includes('buyout'));
    assert.equal(labels[labels.indexOf('buyout') - 1], 'contract');
    assert.ok(!('buyout' in jobsContext(false).JobsPage.tabLabels()));
});

test('the buyout table carries its columns, variance and the compliance flag', () => {
    const page = jobsContext(true).JobsPage;
    const html = page.buyoutTableHtml({
        rows: [
            {
                package_id: 7, package_name: 'Concrete', bid_cost: 14000, bought: 13000,
                variance: 1000, committed_remaining: 3000, billed_to_date: 10000,
                commitments: [{
                    id: 3, number: 'PO-0003', vendor_name: 'Ace', type: 'subcontract',
                    total: 13000, billed: 10000, status: 'partial',
                    compliance: { is_sub: true, w9_on_file: false, coi_expired: true },
                }],
            },
        ],
        totals: { bid_cost: 14000, bought: 13000, variance: 1000, committed_remaining: 3000, billed_to_date: 10000 },
    });
    for (const col of ['Package', 'Bid', 'Bought', 'Variance', 'Committed left', 'Billed to date']) {
        assert.match(html, new RegExp(col));
    }
    assert.match(html, /PO-0003/);
    assert.match(html, /W-9 missing/);
    assert.match(html, /COI expired/);
});

test('the buyout table is empty-stated with no rows', () => {
    const page = jobsContext(true).JobsPage;
    assert.match(page.buyoutTableHtml({ rows: [], totals: {} }), /No commitments or bid yet/);
});

// ── Pay Bills flags + lien waiver ──────────────────────────────────────────

test('Pay Bills flags a Sub with a missing W-9 or expired COI, warn-only', () => {
    const page = billsContext(true).BillsPage;
    assert.match(
        page.complianceFlagHtml({ is_sub: true, w9_on_file: false, coi_expired: true }),
        /W-9 missing, COI expired/,
    );
    // a clean Sub, and a plain vendor, show nothing
    assert.equal(page.complianceFlagHtml({ is_sub: true, w9_on_file: true, coi_expired: false }), '');
    assert.equal(page.complianceFlagHtml({ is_sub: false, w9_on_file: false, coi_expired: true }), '');
});

test('the lien-waiver select is write-guarded and saves the current value', () => {
    const page = billsContext(true).BillsPage;
    const html = page.lienWaiverSelectHtml({ id: 9, bill_number: 'B-9', lien_waiver_status: 'conditional' });
    assert.match(html, /class="lien-waiver" data-write/);
    assert.match(html, /value="conditional" selected/);
    assert.match(html, /BillsPage\.saveLienWaiver\(9/);
});

test('saveLienWaiver is a registered write action', () => {
    const app = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    const block = app.slice(app.indexOf('WRITE_ACTIONS:'), app.indexOf('hideWriteControls(root)'));
    assert.match(block, /'BillsPage\.saveLienWaiver'/);
});
