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
  'batch-select-all': { checked: false },
};
// the duplicate-number 409 the server sends (#481), answered "keep" once
let fetchStatus = 200, keepAnswer = true;
const dupBody = {
  detail: {
    code: 'duplicate_check_number', number: '1051',
    message: 'Check number 1051 is already used on 1000 Checking (Harbor Supply, 9/10/2026, $100.00).',
    uses: [{ date: '2026-09-10', payee: 'Harbor Supply', amount: '100.00' }],
  },
};
const utils = fs.readFileSync('app/static/js/utils.js', 'utf8');
const warnStart = utils.indexOf('const CheckNumberWarn = {');
const warnSrc = utils.slice(warnStart, utils.indexOf('\n};', warnStart) + 3);
const ctx = {
  console,
  window: {},
  document: {
    getElementById: (id) => els[id],
    querySelectorAll: (sel) => (sel === '.batch-check-row' ? rows : sel === '.batch-check' ? rows.map(r => r.cb) : []),
    contains: () => false,
  },
  askChoice: async ({ title }) => { calls.push(['ask', title]); return keepAnswer; },
  focusFor() {},
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
  setTimeout: () => 0,
  escapeHtml: (s) => String(s ?? ''),
  formatDate: (s) => s,
  formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
  toast: (m, k) => calls.push(['toast', m, k || '']),
  fetch: async (url, opts) => {
    calls.push(['fetch', url, JSON.parse(opts.body)]);
    if (fetchStatus === 409 && !url.includes('allow_duplicate_check_number')) {
      return { ok: false, status: 409, json: async () => dupBody };
    }
    return { ok: true, blob: async () => ({}) };
  },
  API: {
    get: async (p) => {
      calls.push(['get', p]);
      if (p.startsWith('/accounts')) return [{ id: 7, name: 'Checking', bank_kind: 'bank' }];
      return { next_check_number: '1051', checks: [] };
    },
    responseError: async () => 'refused',
    errorMessage: (detail) => (typeof detail === 'string' ? detail : detail.message),
  },
  openModal: (t, h) => calls.push(['modal', t]),
};
ctx.window.open = (u) => calls.push(['open', u]);
vm.createContext(ctx);
vm.runInContext(warnSrc + '\nthis.CheckNumberWarn = CheckNumberWarn;', ctx);
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
  out.html = B._printChecksHtml(accounts, 7, list, new Set([11]));
  out.empty = B._printChecksHtml(accounts, 7, { next_check_number: '1001', checks: [] });
  // opened on its own: every row listed, none ticked; after Pay Bills:
  // the checks it just wrote ticked, the older one not
  const two = {
    next_check_number: '1051',
    checks: [
      { bill_payment_id: 11, payee: 'Harbor Supply', date: '2026-09-10', amount: '100.00', memo: '' },
      { bill_payment_id: 12, payee: 'Old Vendor', date: '2024-01-05', amount: '20.00', memo: '' },
    ],
  };
  const tick = (html) => [...html.matchAll(/class="batch-check"( checked)?/g)].map((m) => !!m[1]);
  out.ticked_alone = tick(B._printChecksHtml(accounts, 7, two));
  out.ticked_after_pay = tick(B._printChecksHtml(accounts, 7, two, new Set([11])));
  out.select_all_after_pay = /id="batch-select-all"( checked)?/.exec(B._printChecksHtml(accounts, 7, two, new Set([11])))[1] || '';
  out.select_all_every = /id="batch-select-all"( checked)?/.exec(B._printChecksHtml(accounts, 7, two, new Set([11, 12])))[1] || '';
  B.renumberChecks();
  out.filled = rows.map((r) => r.numInput.value);
  out.select_all_box = els['batch-select-all'].checked;
  await B.printSelectedChecks();
  // Select all ticks every row and numbers them
  els['batch-select-all'].checked = true;
  B.toggleAllChecks();
  out.all_ticked = rows.map((r) => [r.cb.checked, r.numInput.value]);
  // a number the account already has: the keep-it question, then the
  // same batch sent again with allow_duplicate_check_number
  const before = calls.length;
  fetchStatus = 409;
  await B.printSelectedChecks();
  out.dup_calls = calls.slice(before).map((c) => c.slice(0, 2));
  // "go back and edit": nothing printed, the button works again
  keepAnswer = false;
  els['batch-print'].disabled = false;
  const before2 = calls.length;
  await B.printSelectedChecks();
  out.edit_calls = calls.slice(before2).map((c) => c.slice(0, 2));
  out.edit_button_enabled = !els['batch-print'].disabled;
  out.calls = calls;
  console.log(JSON.stringify(out));
})();
