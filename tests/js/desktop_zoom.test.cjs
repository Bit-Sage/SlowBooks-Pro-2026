// The desktop zoom keys (#597): Ctrl + / Ctrl - / Ctrl 0 (Cmd on a Mac) set
// the document's CSS zoom by tenths between 0.5 and 2, and the level is
// kept in localStorage so it holds across pages and launches. pywebview's
// zoomable=True only gives Ctrl + wheel, so desktop_shim.js does the keys.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load({ desktop = true, stored = {}, userAgent } = {}) {
    const storage = Object.assign({}, stored);
    const listeners = {};
    const listen = (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); };
    const context = {
        window: { open() {}, addEventListener: listen, pywebview: desktop ? { api: {} } : undefined },
        navigator: { userAgent: userAgent || (desktop ? 'WebView2' : 'Firefox') },
        document: { addEventListener: listen, documentElement: { style: {} } },
        localStorage: {
            getItem: k => (k in storage ? storage[k] : null),
            setItem: (k, v) => { storage[k] = String(v); },
            removeItem: k => { delete storage[k]; },
        },
        setTimeout() {},
        URL,
    };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/desktop_shim.js'), 'utf8');
    vm.runInContext(js, context);
    return {
        zoom: context.window.SlowbooksDesktop.zoom,
        style: context.document.documentElement.style,
        storage,
        window: context.window,
        fire: (name, event) => (listeners[name] || []).forEach(fn => fn(event)),
    };
}

function wheel(deltaY, mods = {}) {
    return Object.assign({ deltaY, prevented: false, preventDefault() { this.prevented = true; } }, mods);
}

function key(k, mods = {}) {
    return Object.assign({ key: k, code: '', prevented: false, preventDefault() { this.prevented = true; } }, mods);
}

test('Ctrl + and Ctrl - step a tenth, Ctrl 0 resets, and the level is remembered', () => {
    const { zoom, style, storage } = load();
    const plus = key('=', { ctrlKey: true });
    assert.equal(zoom.handle(plus), true);
    assert.equal(plus.prevented, true);
    assert.equal(style.zoom, '1.1');
    assert.equal(storage['slowbooks.desktop.zoom'], '1.1');
    assert.equal(zoom.handle(key('+', { ctrlKey: true, shiftKey: true })), true);
    assert.equal(style.zoom, '1.2');
    assert.equal(zoom.handle(key('-', { ctrlKey: true })), true);
    assert.equal(zoom.handle(key('_', { ctrlKey: true, shiftKey: true })), true);
    // back at 1 nothing is set and nothing is stored
    assert.equal(style.zoom, '');
    assert.equal('slowbooks.desktop.zoom' in storage, false);
    assert.equal(zoom.handle(key('-', { ctrlKey: true })), true);
    assert.equal(style.zoom, '0.9');
    // the numpad's keys, and Cmd on a Mac
    assert.equal(zoom.handle(key('+', { ctrlKey: true, code: 'NumpadAdd' })), true);
    assert.equal(zoom.handle(key('-', { metaKey: true, code: 'NumpadSubtract' })), true);
    assert.equal(style.zoom, '0.9');
    const reset = key('0', { metaKey: true });
    assert.equal(zoom.handle(reset), true);
    assert.equal(reset.prevented, true);
    assert.equal(style.zoom, '');
    assert.equal('slowbooks.desktop.zoom' in storage, false);
});

test('the level stops at 0.5 and 2.0', () => {
    const { zoom, style } = load();
    for (let i = 0; i < 15; i++) zoom.handle(key('=', { ctrlKey: true }));
    assert.equal(style.zoom, '2');
    for (let i = 0; i < 20; i++) zoom.handle(key('-', { ctrlKey: true }));
    assert.equal(style.zoom, '0.5');
});

test('a stored level is applied on load, in the desktop shell only', () => {
    const stored = { 'slowbooks.desktop.zoom': '1.3' };
    assert.equal(load({ stored }).style.zoom, '1.3');
    assert.equal(load({ stored, desktop: false }).style.zoom, undefined);
    // a bad stored value reads as 1
    assert.equal(load({ stored: { 'slowbooks.desktop.zoom': '9' } }).style.zoom, undefined);
});

test('any other key passes untouched, and so does every key in a browser', () => {
    const { zoom, style } = load();
    for (const e of [
        key('='), key('s', { ctrlKey: true }), key('k', { ctrlKey: true }),
        key('0'), key('=', { ctrlKey: true, altKey: true }), key('1', { ctrlKey: true }),
    ]) {
        assert.equal(zoom.handle(e), false);
        assert.equal(e.prevented, false);
    }
    assert.equal(style.zoom, undefined);
    const browser = load({ desktop: false });
    const e = key('=', { ctrlKey: true });
    assert.equal(browser.zoom.handle(e), false);
    assert.equal(e.prevented, false);
});

test('Ctrl + wheel steps the same level, a notch at a time, in the shell only (NEW-186)', () => {
    const { zoom, style, storage } = load();
    const up = wheel(-100, { ctrlKey: true });
    assert.equal(zoom.wheel(up), true);
    assert.equal(up.prevented, true);  // WebView2's own zoom stays out of it
    assert.equal(style.zoom, '1.1');
    assert.equal(storage['slowbooks.desktop.zoom'], '1.1');
    // a trackpad's run of small deltas adds up to one notch
    for (const d of [-20, -20]) assert.equal(zoom.wheel(wheel(d, { metaKey: true })), true);
    assert.equal(style.zoom, '1.1');
    zoom.wheel(wheel(-20, { metaKey: true }));
    assert.equal(style.zoom, '1.2');
    zoom.wheel(wheel(100, { ctrlKey: true }));
    zoom.wheel(wheel(100, { ctrlKey: true }));
    assert.equal(style.zoom, '');
    // a plain scroll, and every wheel in a browser, pass untouched
    const plain = wheel(-100);
    assert.equal(zoom.wheel(plain), false);
    assert.equal(plain.prevented, false);
    const browser = load({ desktop: false });
    const e = wheel(-100, { ctrlKey: true });
    assert.equal(browser.zoom.wheel(e), false);
    assert.equal(e.prevented, false);
});

test('on Windows the stored level comes back when pywebview announces itself (W-46)', () => {
    // WebView2's user agent has no "WebView2", and window.pywebview is not
    // there yet when the shim loads: nothing can be applied at load time
    const stored = { 'slowbooks.desktop.zoom': '1.3' };
    const page = load({ desktop: false, stored, userAgent: 'Mozilla/5.0 Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0' });
    assert.equal(page.style.zoom, undefined);
    page.window.pywebview = { api: {} };
    page.fire('pywebviewready');
    assert.equal(page.style.zoom, '1.3');
    // nothing stored: nothing applied
    const fresh = load({ desktop: false, userAgent: 'Edg/156' });
    fresh.window.pywebview = { api: {} };
    fresh.fire('pywebviewready');
    assert.equal(fresh.style.zoom, undefined);
});
