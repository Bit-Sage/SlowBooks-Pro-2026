// A split line follows the entry's direction, and "Taken out" flips it the
// other way (#387 review). The old code just negated whatever was typed, so
// a −500 check with Insurance −620 and a 120 refund ticked made a −120
// second expense and "$240.00 still to place". Now the ticked line posts as
// a +120 refund and the note reads "every cent placed". Only the magnitude
// typed counts, so 620 and −620 read the same.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function loadBankingPage() {
    const context = { window: {}, document: {}, console };
    vm.createContext(context);
    const js = fs.readFileSync(
        path.join(__dirname, '../../app/static/js/banking.js'),
        'utf8'
    );
    vm.runInContext(js + '\nthis.BankingPage = BankingPage;', context);
    return context.BankingPage;
}

function row(typed, ticked) {
    return {
        querySelector(sel) {
            if (sel === '.split-amt') return { value: String(typed) };
            if (sel === '.split-fee') return { checked: !!ticked };
            return null;
        },
    };
}

function form(amount) {
    return { amount: { value: String(amount) } };
}

test('a deposit: gross income runs with the entry, the fee is taken out', () => {
    const BP = loadBankingPage();
    const sign = BP._entrySign(form('97.00'));
    assert.equal(sign, 1);
    assert.equal(BP._splitAmount(row('100.00', false), sign), 100);
    assert.equal(BP._splitAmount(row('3.00', true), sign), -3);
});

test('a check: the refund ticked goes the opposite way from the entry', () => {
    const BP = loadBankingPage();
    const sign = BP._entrySign(form('-500.00'));
    assert.equal(sign, -1);
    // Insurance 620 (either sign typed) is an expense against the check
    assert.equal(BP._splitAmount(row('-620.00', false), sign), -620);
    assert.equal(BP._splitAmount(row('620.00', false), sign), -620);
    // the 120 refund ticked posts the other way: a credit back to the check
    assert.equal(BP._splitAmount(row('120.00', true), sign), 120);
    // the lines place every cent: -620 + 120 = -500, the whole check
    const placed =
        BP._splitAmount(row('-620.00', false), sign) +
        BP._splitAmount(row('120.00', true), sign);
    assert.equal(placed, -500);
});

test('nothing typed on a line is null, and an empty entry reads as money in', () => {
    const BP = loadBankingPage();
    assert.equal(BP._entrySign(form('')), 1);
    assert.equal(BP._splitAmount(row('', false), 1), null);
});
