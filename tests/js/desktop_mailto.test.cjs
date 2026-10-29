// A mailto: link in the desktop shell (Contact support's Send) goes to the
// launcher bridge's open_external: WKWebView can't load the scheme and
// pywebview's delegate lets it drop, so on a Mac the click did nothing
// (2.30.0 gate NEW-185). Without the bridge the click is left alone.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load({ bridge = true } = {}) {
    const listeners = {};
    const listen = (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); };
    const opened = [];
    const api = bridge ? { open_external: (href) => { opened.push(href); return Promise.resolve({ success: true }); } } : {};
    const context = {
        window: { open() {}, addEventListener: listen, pywebview: { api } },
        navigator: { userAgent: 'WebView2' },
        document: { addEventListener: listen, documentElement: { style: {} } },
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        setTimeout() {},
        URL,
    };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/desktop_shim.js'), 'utf8');
    vm.runInContext(js, context);
    return { opened, click: (event) => (listeners.click || []).forEach(fn => fn(event)) };
}

// an anchor as the click's target: closest() answers the selectors the shim asks
function anchorClick(href, attrs = {}) {
    const a = Object.assign({ href, closest: null }, attrs);
    a.closest = (selector) => {
        if (selector === 'a[href^="mailto:"]') return /^mailto:/i.test(href) ? a : null;
        if (selector === 'a[target="_blank"], a[download]') return (a.target === '_blank' || 'download' in a) ? a : null;
        return null;
    };
    return { target: a, prevented: false, preventDefault() { this.prevented = true; } };
}

test('a mailto: click goes to open_external and the web view never sees it', () => {
    const { opened, click } = load();
    const href = 'mailto:support@slowbookspro.com?subject=%5BSBP%5D%20v2.30.0&body=hi';
    const e = anchorClick(href);
    click(e);
    assert.equal(e.prevented, true);
    assert.deepEqual(opened, [href]);
});

test('without the bridge the click is left to the web view', () => {
    const { opened, click } = load({ bridge: false });
    const e = anchorClick('mailto:support@slowbookspro.com');
    click(e);
    assert.equal(e.prevented, false);
    assert.deepEqual(opened, []);
});

test('an ordinary same-page link is not a mailto', () => {
    const { opened, click } = load();
    const e = anchorClick('http://127.0.0.1:8000/#/customers');
    click(e);
    assert.equal(e.prevented, false);
    assert.deepEqual(opened, []);
});
