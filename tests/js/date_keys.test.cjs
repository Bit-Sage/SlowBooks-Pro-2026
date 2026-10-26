// The date keys (#566): + and - step any date box a day, T is today, M / H
// the first and last of its month, Y / R the first and last of its year.
// DateKeys.step is the arithmetic; DateKeys.handle is the one delegated
// keydown handler, tried here against a stand-in date box.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load() {
    const context = {
        window: {}, App: { settings: {} }, document: { addEventListener() {} },
        Event: class { constructor(type) { this.type = type; } },
    };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8');
    vm.runInContext(js + '\nthis.DateKeys = DateKeys; this.TxnWarn = TxnWarn;', context);
    return context;
}

const today = new Date(2026, 9, 26);  // Oct 26, 2026, local

test('+ and - step a day, across a month and a year end', () => {
    const { DateKeys } = load();
    assert.equal(DateKeys.step('2026-10-26', '+', today), '2026-10-27');
    assert.equal(DateKeys.step('2026-10-26', '-', today), '2026-10-25');
    assert.equal(DateKeys.step('2026-10-31', '+', today), '2026-11-01');
    assert.equal(DateKeys.step('2026-03-01', '-', today), '2026-02-28');
    assert.equal(DateKeys.step('2026-12-31', '+', today), '2027-01-01');
    assert.equal(DateKeys.step('2026-01-01', '-', today), '2025-12-31');
    // the numpad's keys and an unshifted = count too
    assert.equal(DateKeys.step('2026-10-26', 'Add', today), '2026-10-27');
    assert.equal(DateKeys.step('2026-10-26', 'Subtract', today), '2026-10-25');
    assert.equal(DateKeys.step('2026-10-26', '=', today), '2026-10-27');
});

test('T is today, M H Y R the edges of the month and the year', () => {
    const { DateKeys } = load();
    assert.equal(DateKeys.step('2024-02-10', 't', today), '2026-10-26');
    assert.equal(DateKeys.step('2024-02-10', 'T', today), '2026-10-26');
    assert.equal(DateKeys.step('2024-02-10', 'm', today), '2024-02-01');
    assert.equal(DateKeys.step('2024-02-10', 'h', today), '2024-02-29');
    assert.equal(DateKeys.step('2024-02-10', 'y', today), '2024-01-01');
    assert.equal(DateKeys.step('2024-02-10', 'R', today), '2024-12-31');
});

test('an empty box steps from today; any other key is left alone', () => {
    const { DateKeys } = load();
    assert.equal(DateKeys.step('', '+', today), '2026-10-27');
    assert.equal(DateKeys.step('', '-', today), '2026-10-25');
    assert.equal(DateKeys.step('2026-10-26', 'a', today), null);
    assert.equal(DateKeys.step('2026-10-26', 'Enter', today), null);
    assert.equal(DateKeys.step('2026-10-26', 'Tab', today), null);
    assert.equal(DateKeys.step('2026-10-26', '', today), null);
});

function box(value, extra = {}) {
    const fired = [];
    return Object.assign({
        tagName: 'INPUT', type: 'date', value, fired,
        dispatchEvent(ev) { fired.push(ev.type); },
    }, extra);
}
function key(target, k, mods = {}) {
    return Object.assign({ target, key: k, prevented: false, preventDefault() { this.prevented = true; } }, mods);
}

test('the handler takes the key in a date box only, and fires change', () => {
    const { DateKeys } = load();
    const el = box('2026-10-26');
    const e = key(el, '+', { shiftKey: true });
    assert.equal(DateKeys.handle(e), true);
    assert.equal(el.value, '2026-10-27');
    assert.equal(e.prevented, true);
    assert.deepEqual(el.fired, ['input', 'change']);

    // a text box, a select, a read-only or disabled date box, a chord
    const text = box('abc', { type: 'text' });
    assert.equal(DateKeys.handle(key(text, '+')), false);
    assert.equal(text.value, 'abc');
    assert.equal(DateKeys.handle(key({ tagName: 'SELECT', value: '' }, '+')), false);
    assert.equal(DateKeys.handle(key(box('2026-10-26', { readOnly: true }), '+')), false);
    assert.equal(DateKeys.handle(key(box('2026-10-26', { disabled: true }), '+')), false);
    assert.equal(DateKeys.handle(key(box('2026-10-26'), 't', { ctrlKey: true })), false);
    assert.equal(DateKeys.handle(key(box('2026-10-26'), 't', { metaKey: true })), false);
    // another key passes through untouched
    const other = key(box('2026-10-26'), 'Tab');
    assert.equal(DateKeys.handle(other), false);
    assert.equal(other.prevented, false);
});

test('the far-date question is skipped for a carried date, asked for a changed one', async () => {
    const ctx = load();
    ctx.App.settings = { warn_date_past: 'true', warn_date_past_days: '90' };
    const asked = [];
    ctx.confirm = (m) => { asked.push(m); return true; };
    ctx.formatDate = (s) => s;
    // typed fresh: asked
    assert.equal(await ctx.TxnWarn.ok(null, { date: '2020-01-01' }), true);
    assert.equal(asked.length, 1);
    // the same date carried from the last post: not asked
    assert.equal(await ctx.TxnWarn.ok(null, { date: '2020-01-01', carried: '2020-01-01' }), true);
    assert.equal(asked.length, 1);
    // changed from the carried date: asked again
    assert.equal(await ctx.TxnWarn.ok(null, { date: '2020-01-02', carried: '2020-01-01' }), true);
    assert.equal(asked.length, 2);
    // a form's date box carries it in data-carried
    const form = { date: { value: '2020-01-01', dataset: { carried: '2020-01-01' } } };
    assert.equal(await ctx.TxnWarn.ok(form), true);
    assert.equal(asked.length, 2);
    form.date.dataset.carried = '';
    assert.equal(await ctx.TxnWarn.ok(form), true);
    assert.equal(asked.length, 3);
});
