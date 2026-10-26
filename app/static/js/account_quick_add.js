/**
 * A new chart account made from the picker that needs it, without leaving
 * the form (#485). Factored out of the register's category quick add
 * (#386): the register's quick row and + Entry form, the Account cell on
 * bill, vendor credit and purchase order lines and the expense's account
 * all use it.
 *
 * An Add button beside the select opens a small panel — name, type, an
 * optional parent ("Sub-account of", #416) and an optional number — and
 * Save posts it to /accounts, puts it on the page's pickers and picks it
 * on the one the button sits beside. The panel is never opened by the
 * select itself: arrowing through a native select with "+ Add…" on it
 * trapped the keyboard one ArrowDown from the top (#386 review).
 *
 * A page names a profile once (register(name, cfg)) and draws its pickers
 * with picker(selectHtml, name). The profile says:
 *   types        the account types offered, the first the default
 *   chart(ctx)   the accounts as the page has them: the parents offered
 *                come from here, and the new one is added to it
 *   added(made, select, ctx)   put the new account on the page's other
 *                pickers (the helper picks it on `select` itself)
 *   host(select, ctx)    where the panel goes; the select's cell or group
 *   next(select, ctx)    where the keyboard goes after Save; the picker
 *   change       the select's change event fires once the new account is
 *                picked, for a page that saves a pick as it is made
 *   compact      a stacked panel for a table cell, not a form grid
 * ctx is the extra value the page passed to picker()/button() (the
 * register's own account id).
 *
 * The parents offered are the accounts of the chosen type, never a bank
 * or card account, the excluded one (the register's own), or a control
 * account the software posts to by number (1100, 1200, 2000, 2200, 3200 …).
 * A read-only or accountant sign-in is not offered the button: the server
 * would refuse the save. Cancel and Escape close the panel and give the
 * keyboard back to the picker; Enter in the panel saves the account, not
 * the document around it.
 */
const AccountQuickAdd = {
    TYPES: ['expense', 'income', 'cogs', 'asset', 'liability', 'equity'],
    // a purchase line's Account cell: operating expenses and cost of goods
    PURCHASE_TYPES: ['expense', 'cogs'],
    // read when the panel is drawn, so a nonprofit's words (T) apply
    typeLabel(t) {
        const labels = {
            expense: 'Expense', income: T('Income'), cogs: 'Cost of Goods Sold',
            asset: 'Asset', liability: 'Liability', equity: T('Equity'),
        };
        return labels[t] || t;
    },
    _profiles: {},
    _seq: 0,

    register(name, cfg) { AccountQuickAdd._profiles[name] = cfg; },

    canAdd() {
        return !(typeof App !== 'undefined' && App && typeof App.isLocked === 'function' && App.isLocked());
    },

    // The Add button for a profile, or nothing for a sign-in that can't add.
    button(profile, { ctx = null, id = '', label = 'Add', style = '', disabled = false } = {}) {
        if (!AccountQuickAdd.canAdd()) return '';
        const arg = ctx === null || ctx === undefined ? '' : `, ${JSON.stringify(ctx)}`;
        return `<button type="button"${id ? ` id="${id}"` : ''} class="btn btn-sm btn-secondary aqa-add" data-write`
            + ` aria-label="Add a new account" title="Add a new account"${style ? ` style="${style}"` : ''}${disabled ? ' disabled' : ''}`
            + ` onclick="AccountQuickAdd.open(this, '${profile}'${escapeHtml(arg)})">${escapeHtml(label)}</button>`;
    },

    // A select with its Add button beside it.
    picker(selectHtml, profile, opts = {}) {
        return `<span class="aqa-wrap">${selectHtml}${AccountQuickAdd.button(profile, opts)}</span>`;
    },

    // The select the button belongs to.
    _select(btn) {
        const wrap = btn.closest('.aqa-wrap') || btn.parentElement;
        return wrap ? wrap.querySelector('select') : null;
    },

    _host(select, cfg, ctx) {
        return (cfg.host && cfg.host(select, ctx)) || select.closest('td') || select.closest('.form-group')
            || (select.closest('.aqa-wrap') || select).parentElement;
    },

    _panel(select) {
        return select && select._aqaPanel && select._aqaPanel.isConnected ? select._aqaPanel : null;
    },

    // What the keyboard is on for a picker: its type-ahead box when it has one.
    _focusPicker(select) {
        const target = select && select._cbx ? select._cbx.box : select;
        if (target && target.isConnected) focusFor(target, true);
    },

    open(btn, profile, ctx = null) {
        const cfg = AccountQuickAdd._profiles[profile];
        const select = AccountQuickAdd._select(btn);
        if (!cfg || !select || select.disabled) return null;
        const open = AccountQuickAdd._panel(select);
        if (open) { focusFor(open.querySelector('.aqa-name'), true); return open; }
        const key = select.id ? `${select.id}-new` : `aqa-${++AccountQuickAdd._seq}`;
        const types = (cfg.types || AccountQuickAdd.TYPES).map(t =>
            `<option value="${t}">${AccountQuickAdd.typeLabel(t)}</option>`).join('');
        const field = (part, label, control) => `<div class="form-group aqa-field"><label for="${key}-${part}">${label}</label>${control}</div>`;
        const fields = field('name', 'Account name *',
            `<input id="${key}-name" class="aqa-name" aria-required="true" maxlength="200">`)
            + field('type', 'Type', `<select id="${key}-type" class="aqa-type" data-no-search>${types}</select>`)
            + field('parent', 'Sub-account of', `<select id="${key}-parent" class="aqa-parent" data-no-search><option value="">None</option></select>`)
            + field('number', 'Number <span class="aqa-optional">(optional)</span>',
                `<input id="${key}-number" class="aqa-number" maxlength="20" placeholder="e.g. 6150, or leave blank">`);
        const panel = document.createElement('div');
        panel.id = key;
        panel.className = `aqa-panel${cfg.compact ? ' aqa-panel--compact' : ''}`;
        panel.setAttribute('role', 'group');
        panel.setAttribute('aria-labelledby', `${key}-title`);
        // Nothing in it is `required`: the form around it still submits
        // while it is open, and the name is checked by Save.
        panel.innerHTML = `<div id="${key}-title" class="aqa-title">New account</div>
            <div class="${cfg.compact ? 'aqa-stack' : 'form-grid'}">${fields}</div>
            <div class="aqa-actions">
                <button type="button" class="btn btn-sm btn-primary aqa-save">Add account</button>
                <button type="button" class="btn btn-sm btn-secondary aqa-cancel">Cancel</button>
            </div>`;
        AccountQuickAdd._host(select, cfg, ctx).appendChild(panel);
        select._aqaPanel = panel;
        panel._aqa = { select, cfg, ctx };
        panel.querySelector('.aqa-save').addEventListener('click', () => AccountQuickAdd.save(panel));
        panel.querySelector('.aqa-cancel').addEventListener('click', () => AccountQuickAdd.cancel(panel));
        panel.querySelector('.aqa-type').addEventListener('change', () => AccountQuickAdd._parents(panel));
        panel.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') {
                // Enter in a box saves; on the Type or Sub-account list it
                // does nothing, but still must not reach the form around
                // the panel, which would submit the bill (WebKit does).
                e.preventDefault();
                if (e.target.tagName !== 'SELECT') AccountQuickAdd.save(panel);
            } else if (e.key === 'Escape') {
                e.preventDefault(); e.stopPropagation(); AccountQuickAdd.cancel(panel);
            }
        });
        AccountQuickAdd._parents(panel);
        focusFor(panel.querySelector('.aqa-name'), true);
        return panel;
    },

    _excluded(panel) {
        const { cfg, ctx } = panel._aqa;
        return cfg.excludeId ? cfg.excludeId(ctx) : null;
    },

    // The parents offered: active accounts of the chosen type (a sub-account
    // takes its parent's type), no bank or card, nothing excluded, no
    // control account.
    _parents(panel) {
        const { cfg, ctx } = panel._aqa;
        const type = panel.querySelector('.aqa-type').value;
        const skip = AccountQuickAdd._excluded(panel);
        const chart = (cfg.chart && cfg.chart(ctx)) || [];
        panel.querySelector('.aqa-parent').innerHTML = '<option value="">None</option>' + chart
            .filter(a => a.account_type === type && a.id !== skip && !a.bank_kind && !a.is_control && a.is_active !== false)
            .map(a => `<option value="${a.id}">${accountLabel(a)}</option>`).join('');
    },

    cancel(panel) {
        const { select } = panel._aqa;
        panel.remove();
        if (select) select._aqaPanel = null;
        AccountQuickAdd._focusPicker(select);
    },

    async save(panel) {
        if (!panel || panel.dataset.saving) return null;
        const { select, cfg, ctx } = panel._aqa;
        const f = (cls) => panel.querySelector(`.aqa-${cls}`);
        const name = f('name').value.trim();
        if (!name) { toast('Name the account', 'error'); focusFor(f('name'), true); return null; }
        panel.dataset.saving = '1';
        let made;
        try {
            made = await API.post('/accounts', {
                name,
                account_type: f('type').value,
                parent_id: f('parent').value ? parseInt(f('parent').value, 10) : null,
                account_number: f('number').value.trim() || null,
            });
        } catch (err) {
            delete panel.dataset.saving;
            toast(err.message, 'error');
            return null;
        }
        const chart = cfg.chart && cfg.chart(ctx);
        if (Array.isArray(chart) && !chart.some(a => a.id === made.id)) chart.push(made);
        if (cfg.added) cfg.added(made, select, ctx);
        if (!select.querySelector(`option[value="${made.id}"]`)) {
            select.insertAdjacentHTML('beforeend', `<option value="${made.id}">${accountLabel(made)}</option>`);
        }
        select.value = String(made.id);
        if (cfg.change) select.dispatchEvent(new Event('change', { bubbles: true }));
        panel.remove();
        select._aqaPanel = null;
        toast(`${made.account_number ? made.account_number + ' - ' : ''}${made.name} added to the chart`);
        const next = cfg.next && cfg.next(select, ctx);
        if (next) focusFor(next, true);
        else AccountQuickAdd._focusPicker(select);
        return made;
    },

    // The purchase pickers (bill, vendor credit and purchase order lines,
    // the expense's account): each redrawn from the page's list, grouped
    // cost of goods then expenses as PurchaseAccounts draws them, its own
    // choice and its first ("Choose…") option kept.
    refillPurchase(selects, accounts) {
        selects.forEach(sel => {
            const value = sel.value;
            const first = sel.options[0] && sel.options[0].value === '' ? sel.options[0].outerHTML : '';
            sel.innerHTML = first + PurchaseAccounts.options(accounts, null);
            sel.value = value;
        });
    },
};
