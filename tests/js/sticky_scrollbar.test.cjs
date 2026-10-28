// stickyScrollbars() (utils.js): a table wider than its box gets a second
// scrollbar under it, stuck to the bottom of the dialog, that drives the
// same scroll (the WIP Schedule; the owner at the Mac, 2.30.0 gate). A
// table that fits gets nothing, and a redraw starts over. Opt-in by class:
// the WIP Schedule asks for it; the P&L grid, which scrolls both ways, does not.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function element(tag, props = {}) {
    const el = {
        tag, style: {}, children: [], attrs: {}, listeners: {}, scrollLeft: 0,
        nextElementSibling: null, previousElementSibling: null, parent: null,
        classList: {
            _set: new Set(),
            add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); }, contains(c) { return this._set.has(c); },
        },
        set className(v) { this.classList._set = new Set(v.split(' ')); },
        get className() { return [...this.classList._set].join(' '); },
        setAttribute(k, v) { this.attrs[k] = v; },
        appendChild(child) { this.children.push(child); child.parent = this; return child; },
        addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
        fire(name) { (this.listeners[name] || []).forEach(fn => fn({})); },
        insertAdjacentElement(where, sib) {
            assert.equal(where, 'afterend');
            const was = this.nextElementSibling;
            this.nextElementSibling = sib; sib.previousElementSibling = this; sib.nextElementSibling = was; sib.parent = this.parent;
            if (was) was.previousElementSibling = sib;
            return sib;
        },
        remove() {
            const prev = this.previousElementSibling, next = this.nextElementSibling;
            if (prev) prev.nextElementSibling = next;
            if (next) next.previousElementSibling = prev;
            this.removed = true;
        },
        querySelectorAll(sel) { assert.equal(sel, '.table-container--sticky'); return this.tables || []; },
    };
    return Object.assign(el, props);
}

function load() {
    const context = {
        window: {}, navigator: { userAgent: 'TestUA' },
        document: { createElement: (tag) => element(tag), addEventListener() {} },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8'), context);
    return context;
}

test('a wide table gets a bar as wide as its content that scrolls it, and a redraw replaces it', () => {
    const { stickyScrollbars } = load();
    const wide = element('div', { scrollWidth: 1800, clientWidth: 900 });
    wide.className = 'table-container';
    const fits = element('div', { scrollWidth: 600, clientWidth: 900 });
    fits.className = 'table-container';
    const root = element('div', { tables: [wide, fits] });

    assert.equal(stickyScrollbars(root), 1);
    const bar = wide.nextElementSibling;
    assert.ok(bar && bar.classList.contains('table-scroll-sticky'));
    assert.equal(bar.attrs['aria-hidden'], 'true');
    assert.equal(bar.tabIndex, -1);  // not a Tab stop (Chromium focuses scrollable boxes)
    assert.equal(bar.children[0].style.width, '1800px');
    assert.equal(wide.classList.contains('has-sticky-scroll'), true);
    assert.equal(fits.nextElementSibling, null);
    assert.equal(fits.classList.contains('has-sticky-scroll'), false);

    // the bar drives the table, and the table (shift + wheel) drives the bar
    bar.scrollLeft = 300; bar.fire('scroll');
    assert.equal(wide.scrollLeft, 300);
    wide.scrollLeft = 120; wide.fire('scroll');
    assert.equal(bar.scrollLeft, 120);

    // drawn again (a resize): one bar, sized afresh
    wide.scrollWidth = 2000;
    assert.equal(stickyScrollbars(root), 1);
    assert.equal(bar.removed, true);
    assert.equal(wide.nextElementSibling.children[0].style.width, '2000px');
    assert.equal(wide.nextElementSibling.nextElementSibling, null);

    // and when the table fits after a resize, the bar goes
    wide.clientWidth = 2100;
    assert.equal(stickyScrollbars(root), 0);
    assert.equal(wide.nextElementSibling, null);
    assert.equal(wide.classList.contains('has-sticky-scroll'), false);
});

test('nothing to do without a root', () => {
    const { stickyScrollbars } = load();
    assert.equal(stickyScrollbars(null), 0);
});
