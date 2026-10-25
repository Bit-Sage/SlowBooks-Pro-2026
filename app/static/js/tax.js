/**
 * Tax Reports — Schedule C generation + Sales Tax Payment
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

    async render() {
        const year = new Date().getFullYear();
        const info = TaxPage._entityInfo();
        // A company that files something other than Schedule C (a partnership,
        // a corporation, or a nonprofit on Form 990) sees one sentence in
        // place of the Schedule C controls — but the header and the Pay Sales
        // Tax button stay, since that button is the only way to record a
        // sales-tax payment and every entity can owe sales tax.
        if (info.return_form && !info.schedule_c) {
            return `
            <div class="page-header">
                <h2>Tax Reports</h2>
            </div>
            <div class="toolbar">
                <button class="btn btn-secondary" onclick="TaxPage.showPaySalesTax()">Pay Sales Tax</button>
            </div>
            <p style="margin-top:12px;">This company is ${escapeHtml(info.label)}; it files ${escapeHtml(info.return_form)}, not Schedule C. Change the ${info.nonprofit ? 'company' : 'entity'} type under Settings → Company if that is wrong.</p>`;
        }
        const sub = info.return_form
            ? `${escapeHtml(info.label)} &middot; files ${escapeHtml(info.return_form)}`
            : 'Profit or Loss from Business (Sole Proprietorship)';
        return `
            <div class="page-header">
                <h2>Tax Reports — Schedule C</h2>
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
            <div id="tax-results"></div>`;
    },

    async generate() {
        const start = $('#tax-start').value;
        const end = $('#tax-end').value;
        const data = await API.get(`/tax/schedule-c?start_date=${start}&end_date=${end}`);
        const container = $('#tax-results');

        let html = `<div class="table-container"><table>
            <thead><tr><th scope="col">Tax Line</th><th scope="col">Account</th><th scope="col" class="amount">Amount</th></tr></thead><tbody>`;

        for (const line of data.lines) {
            html += `<tr style="background:var(--toolbar-bg);"><td colspan="2" style="font-weight:700;font-size:11px;">${escapeHtml(line.line)}</td><td class="amount" style="font-weight:700;">${formatCurrency(line.total)}</td></tr>`;
            for (const acct of line.accounts) {
                html += `<tr><td style="padding-left:24px;">${escapeHtml(acct.account_number || '')}</td><td>${escapeHtml(acct.account_name)}</td><td class="amount">${formatCurrency(acct.amount)}</td></tr>`;
            }
        }

        // Part I in the form's own order: returns, cost of goods sold and
        // other income only when there are any.
        const row = (label, value) => `<div class="total-row"><span class="label">${label}</span><span class="value">${formatCurrency(value)}</span></div>`;
        const partOne = [
            row('Gross receipts (line 1)', data.gross_receipts),
            data.returns_and_allowances ? row('Returns and allowances (line 2)', -data.returns_and_allowances) : '',
            data.cost_of_goods_sold ? row('Cost of goods sold (line 4)', -data.cost_of_goods_sold) : '',
            data.other_income ? row('Other income (line 6)', data.other_income) : '',
        ].join('');
        html += `</tbody></table></div>
            <div class="invoice-totals" style="margin-top:12px;">
                ${partOne}
                <div class="total-row"><span class="label">Gross Income (line 7)</span><span class="value">${formatCurrency(data.gross_income)}</span></div>
                <div class="total-row"><span class="label">Total Expenses (line 28)</span><span class="value">${formatCurrency(data.total_expenses)}</span></div>
                <div class="total-row grand-total"><span class="label">Net Profit (Loss) (line 31)</span><span class="value">${formatCurrency(data.net_profit)}</span></div>
            </div>`;
        container.innerHTML = html;
    },

    exportCSV() {
        const start = $('#tax-start').value;
        const end = $('#tax-end').value;
        window.open(`/api/tax/schedule-c/csv?start_date=${start}&end_date=${end}`, '_blank');
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
