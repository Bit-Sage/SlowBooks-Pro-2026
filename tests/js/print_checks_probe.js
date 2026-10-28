// The Print Checks screen (bills.js, #603): the list's markup, the numbers
// the checked rows take from the starting number, and the batch request the
// Print button sends. Prints JSON.
const fs = require('fs'), vm = require('vm');

const calls = [];
const rows = [['11', true], ['12', false], ['13', true]].map(([id, on]) => {
  const cb = { checked: on }, numInput = { value: '' };
  return {
    dataset: { id },
    cb,
    numInput,
    querySelector: (sel) => (sel === '.batch-check' ? cb : numInput),
  };
});
const els = {
  'batch-start': { value: '1051' },
  'batch-account': { value: '7' },
  'batch-print': { disabled: false },
};
const ctx = {
  console,
  window: {},
  document: {
    getElementById: (id) => els[id],
    querySelectorAll: (sel) => (sel === '.batch-check-row' ? rows : []),
  },
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
  setTimeout: () => 0,
  escapeHtml: (s) => String(s ?? ''),
  formatDate: (s) => s,
  formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
  toast: (m, k) => calls.push(['toast', m, k || '']),
  fetch: async (url, opts) => {
    calls.push(['fetch', url, JSON.parse(opts.body)]);
    return { ok: true, blob: async () => ({}) };
  },
  API: {
    get: async (p) => {
      calls.push(['get', p]);
      if (p.startsWith('/accounts')) return [{ id: 7, name: 'Checking', bank_kind: 'bank' }];
      return { next_check_number: '1051', checks: [] };
    },
    responseError: async () => 'refused',
  },
  openModal: (t, h) => calls.push(['modal', t]),
};
ctx.window.open = (u) => calls.push(['open', u]);
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/bills.js', 'utf8') + '\nthis.BillsPage = BillsPage;', ctx);
const B = ctx.BillsPage;

(async () => {
  const out = {
    numbers: B._batchNumbers('1051', [true, false, true]),
    bad_start: B._batchNumbers('abc', [true, true]),
  };
  const list = {
    next_check_number: '1051',
    checks: [{ bill_payment_id: 11, payee: 'Harbor Supply', date: '2026-09-10', amount: '100.00', memo: 'Acct 9' }],
  };
  const accounts = [{ id: 7, name: 'Checking' }, { id: 8, name: 'Savings' }];
  out.html = B._printChecksHtml(accounts, 7, list);
  out.empty = B._printChecksHtml(accounts, 7, { next_check_number: '1001', checks: [] });
  B.renumberChecks();
  out.filled = rows.map((r) => r.numInput.value);
  await B.printSelectedChecks();
  out.calls = calls;
  console.log(JSON.stringify(out));
})();
