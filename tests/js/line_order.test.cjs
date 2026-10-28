// Line grids let a line move up or down, take a drop, and take a new line
// below any line (#604). The page's rows are stood in for by a small tree.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function module_() {
    const context = { document: {} };
    vm.createContext(context);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/line_order.js'), 'utf8');
    return vm.runInContext(src + '\n;LineOrder', context);
}

// a table body of rows named by a letter, with the tree calls the module uses
function body(names) {
    const tbody = {
        children: [],
        get lastElementChild() { return this.children[this.children.length - 1] || null; },
        insertBefore(node, ref) {
            this.children = this.children.filter(c => c !== node);
            const at = ref ? this.children.indexOf(ref) : this.children.length;
            this.children.splice(at, 0, node);
        },
    };
    const make = name => {
        const row = {
            name, parentNode: tbody, tagName: 'TR',
            get previousElementSibling() { return tbody.children[tbody.children.indexOf(this) - 1] || null; },
            get nextElementSibling() { return tbody.children[tbody.children.indexOf(this) + 1] || null; },
        };
        return row;
    };
    tbody.make = make;
    tbody.children = names.map(make);
    return tbody;
}
const names = tbody => tbody.children.map(r => r.name);

test('a line moves up and down, and stops at the ends', () => {
    const LineOrder = module_();
    const t = body(['a', 'b', 'c']);
    assert.equal(LineOrder.moveRow(t.children[1], -1), true);
    assert.deepEqual(names(t), ['b', 'a', 'c']);
    assert.equal(LineOrder.moveRow(t.children[0], -1), false);
    assert.equal(LineOrder.moveRow(t.children[1], 1), true);
    assert.deepEqual(names(t), ['b', 'c', 'a']);
    assert.equal(LineOrder.moveRow(t.children[2], 1), false);
});

test('a dropped line lands before or after the line it is dropped on', () => {
    const LineOrder = module_();
    const t = body(['a', 'b', 'c', 'd']);
    LineOrder.moveNextTo(t.children[0], t.children[2], true);
    assert.deepEqual(names(t), ['b', 'c', 'a', 'd']);
    LineOrder.moveNextTo(t.children[3], t.children[0], false);
    assert.deepEqual(names(t), ['d', 'b', 'c', 'a']);
    assert.equal(LineOrder.moveNextTo(t.children[0], t.children[0], true), false);
});

test('a new line is slotted in below the line it was asked for', () => {
    const LineOrder = module_();
    const t = body(['a', 'b', 'c']);
    LineOrder._opts.set(t, { add: () => t.children.push(t.make('new')) });
    LineOrder.insertBelow(t, t.children[0]);
    assert.deepEqual(names(t), ['a', 'new', 'b', 'c']);
    LineOrder.insertBelow(t, t.children[3]);
    assert.deepEqual(names(t), ['a', 'new', 'b', 'c', 'new']);
});
