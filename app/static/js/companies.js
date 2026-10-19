/**
 * Multi-Company — list and create company files/databases
 * Feature 16: Company management UI
 *
 * On desktop installs each company is its own database file (like a
 * QuickBooks company file). "Switch company…" signs this company out and
 * asks the launcher for its picker (pywebview.api.show_picker); the page
 * cannot switch a browser session, so the button only appears under the
 * desktop shell. On server (PostgreSQL, Docker) installs each company is a
 * separate database, and a server serves the one its DATABASE_URL names:
 * a second company is opened by a second server pointed at its database
 * (docs/operations.md, "Several companies on a server").
 */
const CompaniesPage = {
    _isDesktop() {
        return typeof window.pywebview !== 'undefined' && window.pywebview.api
            && typeof window.pywebview.api.show_picker === 'function';
    },

    async switchCompany() {
        if (!confirm('Sign out of this company and choose another?')) return;
        try {
            await API.post('/auth/logout', {});
        } catch (e) {
            // Signed out already (a 401) is fine; anything else may have left
            // this sign-in open, so the picker waits (skytech R6-1, 2.18.0).
            if (!(e && e.status === 401)) {
                toast(`Couldn't sign out of this company (${e.message}). Try again.`, 'error');
                return;
            }
        }
        window.pywebview.api.show_picker();
    },

    async render() {
        const companies = await API.get('/companies');
        let html = `
            <div class="page-header">
                <h2>Company Files</h2>
                <div>
                    ${CompaniesPage._isDesktop() ? '<button class="btn btn-secondary" onclick="CompaniesPage.switchCompany()">Switch company…</button> ' : ''}
                    <button class="btn btn-primary" data-admin onclick="CompaniesPage.showCreate()">+ New Company</button>
                </div>
            </div>
            <p style="font-size:11px;color:var(--text-muted);margin-bottom:12px;">
                Each company is stored in its own separate database.
                ${CompaniesPage._isDesktop()
                    ? 'Switch company takes you back to the company picker; this company is signed out.'
                    : 'A server serves one database, the one its DATABASE_URL names. A new company is created as its own database, ready to serve: run a second server pointed at it (on Docker, a second service in docker-compose.yml on another port), or point this one at it and restart. The sign-in page names the company a server serves; it never lists them. See docs/operations.md, "Several companies on a server".'}
            </p>
            <!-- Creating one is the administrator's (POST /api/companies) -->
            <p class="hidden" data-admin-note style="font-size:11px;color:var(--text-muted);margin-bottom:12px;">New company files are created by an administrator.</p>`;

        if (companies.length === 0) {
            html += '<div class="empty-state"><p>No additional companies created</p></div>';
        } else {
            html += '<div class="card-grid">';
            for (const c of companies) {
                const fileLabel = c.file || c.database_name || '';
                html += `<div class="card">
                    <div class="card-header">${escapeHtml(c.name)}${c.is_current ? ' <span style="font-size:9px;color:var(--text-success);">(currently open)</span>' : ''}</div>
                    <div style="font-size:10px;color:var(--text-muted);">${escapeHtml(fileLabel)}</div>
                    ${c.description ? `<div style="font-size:11px;margin-top:4px;">${escapeHtml(c.description)}</div>` : ''}
                    ${c.last_accessed ? `<div style="font-size:9px;color:var(--text-light);margin-top:4px;">Last accessed: ${new Date(c.last_accessed).toLocaleDateString()}</div>` : ''}
                </div>`;
            }
            html += '</div>';
        }
        return html;
    },

    showCreate() {
        openModal('New Company', `
            <form onsubmit="CompaniesPage.create(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Company Name *</label>
                        <input name="name" required></div>
                    <div class="form-group"><label>Database Name</label>
                        <input name="database_name" pattern="[a-z0-9_]+" title="Lowercase letters, numbers, underscores only"
                            placeholder="Server installs only — auto-generated on desktop"></div>
                    <div class="form-group full-width"><label>Description</label>
                        <textarea name="description"></textarea></div>
                </div>
                <div id="company-create-status" role="status" aria-live="polite"
                    style="font-size:11px; color:var(--text-muted); min-height:14px; margin-top:6px;"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Company</button>
                </div>
            </form>`);
    },

    async create(e) {
        e.preventDefault();
        const form = e.target;
        const data = Object.fromEntries(new FormData(form).entries());
        if (!data.database_name) delete data.database_name;
        // Building a company file (its tables and chart of accounts) takes a
        // few seconds, and the dialog used to sit unchanged until it was done
        // (explore 2.17.3, skytech M18). Say so, and take away a second click.
        const btn = form.querySelector('button[type="submit"]');
        const status = form.querySelector('#company-create-status');
        if (btn) { btn.disabled = true; btn.textContent = 'Creating…'; }
        if (status) status.textContent = `Setting up ${data.name || 'the company'} — this takes a few seconds.`;
        try {
            await API.post('/companies', data);
            toast(CompaniesPage._isDesktop()
                ? 'Company created. Switch company… opens it.'
                : `Company created as its own database${data.database_name ? ` (${data.database_name})` : ''}. To open it, run a second server pointed at that database, or point this one at it and restart (docs/operations.md, "Several companies on a server").`);
            closeModal();
            App.navigate('#/companies');
        } catch (err) {
            toast(err.message, 'error');
            if (btn) { btn.disabled = false; btn.textContent = 'Create Company'; }
            if (status) status.textContent = '';
        }
    },
};
