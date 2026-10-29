// The page never stays white (a support email on 2.29.2: "Full white screen
// on startup, never changing and no menus or sidebars to click on").
// boot_check.js runs first; an error while the app's scripts load puts a
// plain message up at once, and a watchdog does the same when no first screen
// is drawn in time. Run here on a fake window, document and clock.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../app/static/js/boot_check.js'), 'utf8');
const ORIGIN = 'http://127.0.0.1:3001';

class FakeNode {
    constructor(tagName, text) {
        this.tagName = tagName;
        this.text = text || '';
        this.attributes = {};
        this.childNodes = [];
        this.parentNode = null;
        this.focused = false;
    }
    appendChild(child) {
        child.parentNode = this;
        this.childNodes.push(child);
        return child;
    }
    removeChild(child) {
        this.childNodes = this.childNodes.filter((c) => c !== child);
        child.parentNode = null;
        return child;
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
    focus() { this.focused = true; }
    get textContent() { return this.text + this.childNodes.map((c) => c.textContent).join(''); }
    find(pred) {
        for (const c of this.childNodes) {
            if (pred(c)) return c;
            const deeper = c.find(pred);
            if (deeper) return deeper;
        }
        return null;
    }
}

function fakeClock() {
    let now = 0;
    let seq = 0;
    const timers = new Map();
    return {
        setTimeout(fn, ms) { seq += 1; timers.set(seq, { at: now + (ms || 0), fn }); return seq; },
        clearTimeout(id) { timers.delete(id); },
        advance(ms) {
            const until = now + ms;
            for (;;) {
                let next = null;
                for (const [id, t] of timers) {
                    if (t.at <= until && (!next || t.at < next.t.at)) next = { id, t };
                }
                if (!next) break;
                timers.delete(next.id);
                now = next.t.at;
                next.t.fn();
            }
            now = until;
        },
    };
}

// A page with boot_check.js loaded from <head>, and the body already there
// (the app's scripts sit at the end of it).
function load({ webview2 = true } = {}) {
    const clock = fakeClock();
    const windowListeners = [];
    const documentListeners = [];
    const body = new FakeNode('BODY');
    const document = {
        body,
        createElement: (tag) => new FakeNode(tag.toUpperCase()),
        createTextNode: (text) => new FakeNode(undefined, text),
        addEventListener: (type, fn) => documentListeners.push({ type, fn }),
    };
    const context = {
        document,
        location: { origin: ORIGIN, protocol: 'http:', host: '127.0.0.1:3001' },
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        addEventListener: (type, fn, capture) => windowListeners.push({ type, fn, capture }),
    };
    context.window = context;
    if (webview2) context.chrome = { webview: {} };
    vm.createContext(context);
    vm.runInContext(SRC, context, { filename: 'boot_check.js' });

    const page = {
        context,
        clock,
        boot: context.SlowBooksBoot,
        // what the window's error listeners see, as a browser hands it over
        throwIn(file, message, lineno) {
            const event = { type: 'error', target: context, message, filename: ORIGIN + file, lineno };
            windowListeners.filter((l) => l.type === 'error').forEach((l) => l.fn(event));
        },
        errorEvent(event) {
            windowListeners.filter((l) => l.type === 'error').forEach((l) => l.fn({ type: 'error', target: context, ...event }));
        },
        failToLoad(tagName, src) {
            const target = { tagName, src };
            windowListeners.filter((l) => l.type === 'error').forEach((l) => l.fn({ type: 'error', target }));
        },
        reject(reason) {
            windowListeners.filter((l) => l.type === 'unhandledrejection').forEach((l) => l.fn({ reason }));
        },
        // the app's own scripts register their start after boot_check.js has
        // registered its own, as they do in index.html
        onReady(fn) { document.addEventListener('DOMContentLoaded', fn); },
        // DOMContentLoaded, through every handler in order, as the DOM does
        ready() {
            const event = { stopped: false, stopImmediatePropagation() { this.stopped = true; } };
            for (const l of documentListeners.filter((x) => x.type === 'DOMContentLoaded')) {
                if (event.stopped) break;
                l.fn(event);
            }
            return event;
        },
        message() { return body.find((n) => n.getAttribute && n.getAttribute('role') === 'alert'); },
    };
    return page;
}

const HEADING = 'SlowBooks Pro could not start in this window.';
const WEBVIEW2 = 'The Microsoft Edge WebView2 runtime on this computer may be out of date. '
    + 'Update it from https://developer.microsoft.com/microsoft-edge/webview2/ and open SlowBooks again.';

test('a syntax error while the scripts load shows the message at once', () => {
    const page = load();
    assert.equal(page.message(), null);
    page.throwIn('/static/js/benefits.js', "Uncaught SyntaxError: Unexpected token '='", 486);
    const box = page.message();
    assert.ok(box, 'no message after a load error');
    const text = box.textContent;
    assert.ok(text.startsWith(HEADING), text);
    assert.ok(text.includes(WEBVIEW2), text);
    assert.ok(text.includes('If that does not help, write to support@slowbookspro.com and include the error below.'), text);
    assert.ok(text.includes("Error: Uncaught SyntaxError: Unexpected token '=' (/static/js/benefits.js, line 486)"), text);
    // it covers the page, above the sign-in overlay (z-index 99999), and takes focus
    assert.match(box.getAttribute('style'), /position:fixed;top:0;right:0;bottom:0;left:0;z-index:2147483647;/);
    assert.equal(box.focused, true);
    // the download page opens in the system browser, not in this window
    const link = box.find((n) => n.tagName === 'A' && n.getAttribute('href') === 'https://developer.microsoft.com/microsoft-edge/webview2/');
    assert.ok(link && link.getAttribute('target') === '_blank', 'the WebView2 link');
    assert.ok(box.find((n) => n.tagName === 'A' && n.getAttribute('href') === 'mailto:support@slowbookspro.com'));
});

test('after a load error the app does not start over the message', () => {
    const page = load();
    let started = 0;
    page.onReady(() => { started += 1; });  // auth.js promptAuth, app.js App.init
    page.throwIn('/static/js/reports.js', 'Uncaught TypeError: Object.hasOwn is not a function', 84);
    const event = page.ready();
    assert.equal(event.stopped, true);
    assert.equal(started, 0);
    // and a screen drawn after all (a sign-in that was already on its way) leaves it up
    page.boot.drawn();
    assert.ok(page.message());
    // a second error does not stack a second message
    page.throwIn('/static/js/app.js', 'Uncaught ReferenceError: ReportsPage is not defined', 12);
    assert.equal(page.context.document.body.childNodes.length, 1);
    assert.ok(page.message().textContent.includes('Object.hasOwn is not a function'));
});

test('a script that does not load counts as a load error', () => {
    const page = load();
    page.failToLoad('SCRIPT', ORIGIN + '/static/js/app.js');
    assert.ok(page.message().textContent.includes('Error: Could not load /static/js/app.js'));
});

test("errors that are not the app's own scripts' do not count", () => {
    const page = load();
    page.errorEvent({ message: 'Script error.', filename: '', lineno: 0 });  // another origin's
    page.errorEvent({ message: 'Uncaught Error: x', filename: 'chrome-extension://abc/inject.js', lineno: 1 });
    page.errorEvent({ message: 'Uncaught Error: y', filename: ORIGIN + '/', lineno: 3 });  // not a script file
    page.failToLoad('IMG', ORIGIN + '/static/logo.png');
    page.failToLoad('LINK', ORIGIN + '/static/css/dark.css');
    page.failToLoad('SCRIPT', 'https://js.stripe.com/v3/');
    assert.equal(page.message(), null);
});

test('a cleared watchdog never shows the message', () => {
    const page = load();
    page.ready();
    page.boot.drawn();
    page.clock.advance(10 * page.boot.WATCHDOG_MS);
    assert.equal(page.message(), null);
    // once a screen is up, an error belongs to the app's own handling
    page.throwIn('/static/js/invoices.js', 'Uncaught TypeError: x is undefined', 9);
    page.clock.advance(10 * page.boot.WATCHDOG_MS);
    assert.equal(page.message(), null);
});

test('an uncleared watchdog shows the message after its time, not before', () => {
    const page = load();
    page.ready();
    page.clock.advance(page.boot.WATCHDOG_MS - 1);
    assert.equal(page.message(), null);
    page.clock.advance(1);
    const text = page.message().textContent;
    assert.ok(text.startsWith(HEADING) && text.includes(WEBVIEW2), text);
    // nothing was recorded, so there is no error to quote
    assert.ok(text.endsWith('If that does not help, write to support@slowbookspro.com.'), text);
});

test('the watchdog leaves a slow machine room to open a big company', () => {
    const page = load();
    assert.ok(page.boot.WATCHDOG_MS >= 20000, page.boot.WATCHDOG_MS);
});

test('a start the watchdog only thought had failed takes the message away', () => {
    const page = load();
    page.ready();
    page.clock.advance(page.boot.WATCHDOG_MS);
    assert.ok(page.message());
    page.boot.drawn();  // the first page drew after all
    assert.equal(page.message(), null);
});

test('after the scripts have loaded, an error waits for the watchdog and is quoted', () => {
    const page = load();
    page.ready();
    page.clock.advance(0);  // the DOMContentLoaded handlers are done
    page.throwIn('/static/js/app.js', 'Uncaught TypeError: App.applyTerminology failed', 2676);
    assert.equal(page.message(), null);
    page.clock.advance(page.boot.WATCHDOG_MS);
    assert.ok(page.message().textContent.includes('Error: Uncaught TypeError: App.applyTerminology failed (/static/js/app.js, line 2676)'));
});

test("a promise nothing handled is quoted when the watchdog fires", () => {
    const page = load();
    page.ready();
    page.reject(new Error('Failed to fetch'));
    assert.equal(page.message(), null);
    page.clock.advance(page.boot.WATCHDOG_MS);
    assert.ok(page.message().textContent.includes('Error: Failed to fetch'));
});

test('an error in a DOMContentLoaded handler is still a load error', () => {
    const page = load();
    page.onReady(() => {
        page.throwIn('/static/js/app.js', "Uncaught TypeError: Cannot read properties of undefined (reading 'init')", 2797);
    });
    page.ready();
    assert.ok(page.message(), 'App.init throwing as the page loads');
});

test('outside WebView2 the message does not talk about WebView2', () => {
    const page = load({ webview2: false });
    page.throwIn('/static/js/benefits.js', "SyntaxError: Unexpected token '='", 486);
    const text = page.message().textContent;
    assert.ok(text.startsWith(HEADING), text);
    assert.ok(!text.includes('WebView2'), text);
    assert.ok(text.includes('Write to support@slowbookspro.com and include the error below.'), text);
});

test('the message is built from text, never from markup', () => {
    assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write/.test(SRC));
    const page = load();
    page.throwIn('/static/js/app.js', '<img src=x onerror=alert(1)>', 1);
    assert.ok(page.message().textContent.includes('<img src=x onerror=alert(1)>'));
    assert.equal(page.message().find((n) => n.tagName === 'IMG'), null);
});
