// Evaluate memorized.js with stubs and draw MemorizedPage.whileAwayHtml for
// runs that entered, only skipped, did both and did neither. Prints one
// JSON object for the pytest side (tests/test_memorized_transactions.py).
const fs = require('fs'), vm = require('vm');

const toasts = [];
const ctx = {
  console,
  T: (s) => s,
  escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
  formatDate: (d) => d,
  toastPosted: (msg) => toasts.push(msg),
};
vm.createContext(ctx);
vm.runInContext(
  fs.readFileSync('app/static/js/memorized.js', 'utf8') + '\nthis.MemorizedPage = MemorizedPage;',
  ctx,
);
const P = ctx.MemorizedPage;
const text = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').replace(/ ([.,])/g, '$1').trim();
const closed = { message: 'Rent (due 2026-01-05) was not entered: the closing date is 2026-01-31.' };
const zero = { message: 'Water (due 2026-01-07) was not entered: it adds up to $0.00.' };
const entered = { name: 'Electric bill', kind: 'bill', document_id: 7, date: '2026-01-31' };

const out = {};
const draw = (key, run) => {
  const before = toasts.length;
  const html = P.whileAwayHtml(run);
  out[key] = { text: text(html), html, toasts: toasts.slice(before) };
};
draw('skippedOne', { ran_at: 'a', origin: 'startup', entered_count: 0, invoices_created: 0, entered: [], skipped: [closed] });
draw('skippedTwo', { ran_at: 'b', origin: 'daily', entered_count: 0, invoices_created: 0, entered: [], skipped: [closed, zero] });
draw('both', { ran_at: 'c', origin: 'startup', entered_count: 1, invoices_created: 0, entered: [entered], skipped: [closed] });
draw('neither', { ran_at: 'd', origin: 'startup', entered_count: 0, invoices_created: 0, entered: [], skipped: [] });
draw('again', { ran_at: 'c', origin: 'startup', entered_count: 1, invoices_created: 0, entered: [entered], skipped: [closed] });
console.log(JSON.stringify(out));
