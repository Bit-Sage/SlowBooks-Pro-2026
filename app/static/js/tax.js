/**
 * Tax Reports — Schedule C generation (and the entity returns: Form 1065,
 * 1120, 1120-S, #488) + Sales Tax Payment
 * Feature 19: Generate and export Schedule C from P&L data
 * Feature 5: Pay Sales Tax
 */
const TaxPage = {
    // Legal entity type (#488) — the server computes the one entity table
    // (app/services/settings_service.py entity_info) and returns it as
    // App.settings.entity on the settings GET/PUT, so this page reads it
    // rather than keeping its own copy. `return_form` is the federal return
    // ("Schedule C", "Form 1065", "Form 990", …); `schedule_c` is whether
    // Schedule C is that return; `label` carries its own article. An empty
    // `return_form` means unset (or an LLC with no election), which leaves
    // Schedule C exactly as it was.
    _entityInfo() {
        const s = (typeof App !== 'undefined' && App.settings) || {};
        // If the settings fetch failed there is no entity record: fall back to
        // the unset record so the full Schedule C page shows; the server's 409
        // still guards the report for an entity that does not file it.
        return s.entity || { entity_type: '', label: '', return_form: '', schedule_c: false, nonprofit: false };
    },

    // The entity returns this page draws besides Schedule C (#488 step 2):
    // page 1 of each, from the same P&L lines, at /api/tax/return.
    _ENTITY_RETURNS: ['Form 1065', 'Form 1120', 'Form 1120-S'],

    // One sentence for an S corporation (#557): owner pay is W-2 wages first,
    // and a distribution is equity, never an expense.
    S_CORP_NOTE: 'S corporation: an active shareholder-employee takes reasonable compensation as W-2 wages through Payroll before any distribution. Distributions post to Shareholder Distributions (equity), never to the profit and loss.',

    // The report the page draws: '/tax/schedule-c' (Schedule C, and an unset
    // entity type exactly as before), '/tax/return' for an entity return, or
    // '' when the company's return is not one this report draws (Form 990).
    _reportPath(info) {
        if (!info.return_form || info.schedule_c) return '/tax/schedule-c';
        if (TaxPage._ENTITY_RETURNS.includes(info.return_form)) return '/tax/return';
        return '';
    },

    async render() {
        const year = new Date().getFullYear();
        const info = TaxPage._entityInfo();
        const path = TaxPage._reportPath(info);
        // A company that files something this page does not draw (a nonprofit
        // on Form 990) sees one sentence in place of the report controls — but
        // the header and the Pay Sales Tax button stay, since that button is
        // the only way to record a sales-tax payment and every entity can owe
        // sales tax.
        if (!path) {
            return `
            <div class="page-header">
                <h2>Tax Reports</h2>
            </div>
            <div class="toolbar">
                <button class="btn btn-secondary" onclick="TaxPage.showPaySalesTax()">Pay Sales Tax</button>
            </div>
            <p style="margin-top:12px;">This company is ${escapeHtml(info.label)}; it files ${escapeHtml(info.return_form)}, not Schedule C. Change the ${info.nonprofit ? 'company' : 'entity'} type under Settings → Company if that is wrong.</p>`;
        }
        const form = path === '/tax/return' ? info.return_form : 'Schedule C';
        const sub = info.return_form
            ? `${escapeHtml(info.label)} &middot; files ${escapeHtml(info.return_form)}`
            : 'Profit or Loss from Business (Sole Proprietorship)';
        const sCorpNote = info.return_form === 'Form 1120-S'
            ? `<div style="font-size:10px;color:var(--text-muted);margin-bottom:12px;">${escapeHtml(TaxPage.S_CORP_NOTE)}</div>`
            : '';
        return `
            <div class="page-header">
                <h2>Tax Reports — ${escapeHtml(form)}</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    ${sub}
                </div>
            </div>
            <div class="toolbar">
                <label style="font-size:10px;font-weight:700;">From:</label>
                <input type="date" id="tax-start" value="${year}-01-01">
                <label style="font-size:10px;font-weight:700;">To:</label>
                <input type="date" id="tax-end" value="${year}-12-31">
                <button class="btn btn-primary" onclick="TaxPage.generate()">Generate</button>
                <button class="btn btn-secondary" onclick="TaxPage.exportCSV()">Export CSV</button>
                <button class="btn btn-secondary" onclick="TaxPage.showPaySalesTax()">Pay Sales Tax</button>
            </div>
            <div style="background:#fef3c7;border:1px solid #fbbf24;padding:6px 10px;margin-bottom:12px;font-size:10px;color:#92400e;">
                <strong>Disclaimer:</strong> This report is for reference only. Please verify all figures with a qualified tax professional.
            </div>
            ${sCorpNote}
            <div id="tax-results"></div>`;
    },

    // The rows under the table, from the report's own `summary` (the form's
    // words and line numbers). Returns and cost of goods sold print as
    // subtractions, the optional rows only when there is an amount.
    _summaryRows(data) {
        const row = (label, value, cls) => `<div class="total-row${cls || ''}"><span class="label">${escapeHtml(label)}</span><span class="value">${formatCurrency(value)}</span></div>`;
        const optional = new Set(['returns_and_allowances', 'cost_of_goods_sold', 'other_income']);
        const negative = new Set(['returns_and_allowances', 'cost_of_goods_sold']);
        const summary = data.summary || [];
        return summary.map((s, i) => {
            const value = data[s.key] || 0;
            if (optional.has(s.key) && !value) return '';
            const last = i === summary.length - 1;
            return row(`${s.label} (line ${s.line})`, negative.has(s.key) ? -value : value, last ? ' grand-total' : '');
        }).join('');
    },

    async generate() {
        const start = $('#tax-start').value;
        const end = $('#tax-end').value;
        const path = TaxPage._reportPath(TaxPage._entityInfo()) || '/tax/schedule-c';
        const data = await API.get(`${path}?start_date=${start}&end_date=${end}`);
        const container = $('#tax-results');

        let html = `<div class="table-container"><table>
            <thead><tr><th scope="col">Tax Line</th><th scope="col">Account</th><th scope="col" class="amount">Amount</th></tr></thead><tbody>`;

        for (const line of data.lines) {
            html += `<tr style="background:var(--toolbar-bg);"><td colspan="2" style="font-weight:700;font-size:11px;">${escapeHtml(line.line)}</td><td class="amount" style="font-weight:700;">${formatCurrency(line.total)}</td></tr>`;
            for (const acct of line.accounts) {
                html += `<tr><td style="padding-left:24px;">${escapeHtml(acct.account_number || '')}</td><td>${escapeHtml(acct.account_name)}</td><td class="amount">${formatCurrency(acct.amount)}</td></tr>`;
            }
        }

        html += `</tbody></table></div>
            <div class="invoice-totals" style="margin-top:12px;">
                ${TaxPage._summaryRows(data)}
            </div>`;
        container.innerHTML = html;
    },

    exportCSV() {
        const start = $('#tax-start').value;
        const end = $('#tax-end').value;
        // literal paths, so the wiring audit (tests/test_wiring.py) sees both
        const url = TaxPage._reportPath(TaxPage._entityInfo()) === '/tax/return'
            ? `/api/tax/return/csv?start_date=${start}&end_date=${end}`
            : `/api/tax/schedule-c/csv?start_date=${start}&end_date=${end}`;
        window.open(url, '_blank');
    },

    async showPaySalesTax() {
        // Tax is paid out of a bank or card account — never Accounts
        // Receivable, Inventory or Undeposited Funds, which the old
        // every-asset list offered (the server refuses those too).
        const [accounts, balSheet] = await Promise.all([
            API.get('/accounts?bank=1&active_only=true'),
            API.get('/reports/balance-sheet'),
        ]);

        // Find Sales Tax Payable balance
        const taxLiability = balSheet.liabilities.find(l => l.account_number === '2200');
        const taxBalance = taxLiability ? Math.abs(taxLiability.amount) : 0;

        const bankOpts = accounts.map(a => `<option value="${a.id}">${escapeHtml(a.name)} (${formatCurrency(a.balance)})</option>`).join('');
        const noBank = accounts.length ? '' : '<small style="color:var(--danger);">No bank or credit card account yet. Add one under Banking first.</small>';

        openModal('Pay Sales Tax', `
            <form onsubmit="TaxPage.submitPaySalesTax(event)">
                <div style="margin-bottom:12px; padding:8px; background:var(--primary-light); border-radius:4px;">
                    <strong>Sales Tax Payable Balance:</strong> ${formatCurrency(taxBalance)}
                </div>
                <div class="form-grid">
                    <div class="form-group"><label>Amount *</label>
                        <input name="amount" type="number" step="0.01" required value="${taxBalance.toFixed(2)}"></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Pay From Account *</label>
                        <select name="pay_from_account_id" required><option value="">Select...</option>${bankOpts}</select>${noBank}</div>
                    <div class="form-group"><label>Check #</label>
                        <input name="check_number"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Pay Sales Tax</button>
                </div>
            </form>`);
    },

    async submitPaySalesTax(e) {
        e.preventDefault();
        const form = e.target;
        try {
            await API.post('/reports/sales-tax/pay', {
                amount: parseFloat(form.amount.value),
                date: form.date.value,
                pay_from_account_id: parseInt(form.pay_from_account_id.value),
                check_number: form.check_number.value || null,
            });
            toastPosted('Sales tax payment recorded');
            App.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },
};
