// A register line opens its posting over the register (App.openOver), not at
// the posting's own address: going there drew the document's whole list
// first (Journal Entries loads every entry in the company, minutes on years
// of imported history) and Close left that list on screen instead of the
// register the line was clicked in.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function app(startHash) {
    const calls = [];
    const pageContent = { innerHTML: '' };
    const stub = name => new Proxy({}, {
        get: (_t, prop) => (prop === 'then' ? undefined : (...args) => {
            calls.push([`${name}.${String(prop)}`, ...args.map(String)]);
            return prop === 'render' ? `<${name}>` : '';
        }),
    });
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    const ctx = {
        console, setTimeout, clearTimeout, Promise, URLSearchParams,
        window: {},
        document: { addEventListener() {}, querySelectorAll: () => [] },
        location: { hash: startHash },
        history: {
            state: null,
            pushState: (_s, _t, url) => { calls.push(['pushState', url]); ctx.location.hash = url; },
            replaceState: (_s, _t, url) => { calls.push(['replaceState', url]); ctx.location.hash = url; },
        },
        $: sel => (sel === '#page-content' ? pageContent : null), $$: () => [],
        escapeHtml: s => String(s ?? ''),
        toast: msg => calls.push(['toast', String(msg)]),
        T: s => s,
        Terms: { text: s => s, isNonprofit: () => false, isConstruction: () => false },
    };
    for (const name of new Set(src.match(/\b[A-Z][A-Za-z]*Page\b/g) || [])) ctx[name] = stub(name);
    vm.createContext(ctx);
    vm.runInContext(src + '\nthis.App = App;', ctx);
    return { App: ctx.App, calls, ctx };
}

const settle = () => new Promise(r => setTimeout(r, 5));

test('a journal posting opens over the register without drawing Journal Entries', async () => {
    const a = app('#/banking/3');
    const followed = a.App.openOver({ button: 0 }, '/#/journal/42');
    await settle();
    assert.equal(followed, false, 'the link is cancelled');
    assert.deepEqual(a.calls.filter(c => c[0].endsWith('.render')), [], 'no list drawn');
    assert.ok(a.calls.some(c => c[0] === 'JournalPage.view' && c[1] === '42'));
    assert.equal(a.ctx.location.hash, '#/journal/42', 'the posting has its address');
    // Close puts the register's address back; the register was never replaced
    a.App.dialogClosed();
    assert.equal(a.ctx.location.hash, '#/banking/3');
});

test('a document with its own page (an invoice) opens over the register too', async () => {
    const a = app('#/banking/3');
    assert.equal(a.App.openOver({ button: 0 }, '/#/invoices/9'), false);
    await settle();
    assert.deepEqual(a.calls.filter(c => c[0] === 'InvoicesPage.render'), []);
    assert.ok(a.calls.some(c => c[0] === 'InvoicesPage.view' && c[1] === '9'));
    a.App.dialogClosed();
    assert.equal(a.ctx.location.hash, '#/banking/3');
});

test('a modified click or a page address follows the link as before', () => {
    const a = app('#/banking/3');
    assert.equal(a.App.openOver({ button: 0, ctrlKey: true }, '/#/journal/42'), true);
    assert.equal(a.App.openOver({ button: 1 }, '/#/journal/42'), true);
    assert.equal(a.App.openOver({ button: 0 }, '/#/banking/5'), true, 'a register is a page, not a document');
    assert.equal(a.App.openOver({ button: 0 }, '/#/reports/profit-loss'), true, 'a report view is not a document');
    assert.deepEqual(a.calls, []);
});

test('opening a document by its address still draws its list behind it', async () => {
    const a = app('#/journal/42');
    await a.App.navigate('#/journal/42');
    await settle();
    assert.ok(a.calls.some(c => c[0] === 'JournalPage.render'));
    assert.ok(a.calls.some(c => c[0] === 'JournalPage.view' && c[1] === '42'));
});
