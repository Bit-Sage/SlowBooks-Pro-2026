// The bill payment view's check memo (bills.js _checkMemoHtml,
// printPaymentCheck): a check shows the memo field, ACH and a void payment do
// not, and Print Check saves the typed memo before it opens the check.
// Prints JSON.
const fs = require('fs'), vm = require('vm');

const calls = [];
const memoInput = { value: 'Acct 9' };
const ctx = {
  console,
  window: {
    open: (url) => { calls.push(['open', url]); return null; },
  },
  document: { getElementById: (id) => (id === 'bill-payment-memo' ? memoInput : null) },
  escapeHtml: (s) => String(s ?? ''),
  toast: (m, k) => calls.push(['toast', m, k]),
  API: { put: async (path, body) => { calls.push(['put', path, body]); } },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/bills.js', 'utf8') + '\nthis.BillsPage = BillsPage;', ctx);
const B = ctx.BillsPage;

(async () => {
  const out = {
    check: B._checkMemoHtml({ id: 5, method: 'check', notes: 'Acct "4"' }),
    ach: B._checkMemoHtml({ id: 6, method: 'ach' }),
    void_check: B._checkMemoHtml({ id: 7, method: 'check', is_voided: true }),
  };
  await B.printPaymentCheck(5, '/api/checks/print?bill_payment_id=5');
  out.calls = calls;
  console.log(JSON.stringify(out));
})();
