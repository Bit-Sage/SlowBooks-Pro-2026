/**
 * Contact support — the in-app help form.
 *
 * The app sends NOTHING: Send builds a mailto: link to support and opens the
 * user's own mail program with the message already written. The environment
 * block and a redacted tail of the log come from GET /api/support/diagnostics
 * (read-only, no outbound call). mailto can't attach files, so the form says
 * to attach screenshots or the log by hand; long logs are trimmed to keep the
 * mailto body under the length some mail clients truncate, and Copy
 * diagnostics gives the full block.
 */
const SupportPage = {
    SUPPORT_EMAIL: 'support@slowbookspro.com',
    MAILTO: 'mailto:support@slowbookspro.com',
    MAX_BODY_CHARS: 1800,

    _diag: null,  // the last diagnostics payload, for the live mailto rebuild

    // --- Pure builders (unit-tested in tests/js/support_mailto.test.cjs) ---

    shortTitle(whatHappened, limit = 60) {
        const text = (whatHappened || '').split(/\s+/).filter(Boolean).join(' ');
        if (!text) return 'support request';
        return text.slice(0, limit).trimEnd();
    },

    buildSubject(diag, whatHappened) {
        return `[SBP] v${diag.version} · ${diag.platform} ${diag.deployment}`
            + ` · ${SupportPage.shortTitle(whatHappened)}`;
    },

    // The environment the email carries: the server's block, plus the browser
    // when it matters (a browser/Server/Docker client, not the native window).
    environmentText(diag) {
        let env = diag.environment || '';
        if (!diag.is_native && diag.browser) env += `\nBrowser: ${diag.browser}`;
        return env;
    },

    buildBody(diag, fields) {
        const wh = (fields.whatHappened || '').trim() || '(not described)';
        const we = (fields.whatExpected || '').trim() || '(not described)';
        const parts = [
            'What happened:', wh, '',
            'What you expected:', we, '',
        ];
        if (fields.replyTo) parts.push(`Reply to: ${fields.replyTo}`, '');
        parts.push('--- Environment ---', SupportPage.environmentText(diag), '', '--- Recent log ---');
        const header = parts.join('\n') + '\n';
        if (!diag.log_available) return header + '(log file not found)';
        const footer = '\n(log trimmed — use Copy diagnostics for the full block)';
        const room = SupportPage.MAX_BODY_CHARS - header.length;
        const tail = diag.log_tail || '(log is empty)';
        if (room <= 0) return header.replace(/\n+$/, '');
        if (tail.length <= room) return header + tail;
        // Keep the END of the log (newest lines, where the error is).
        return header + footer + '\n' + tail.slice(-(room - footer.length - 1));
    },

    buildMailto(diag, fields) {
        const subject = SupportPage.buildSubject(diag, fields.whatHappened);
        const body = SupportPage.buildBody(diag, fields);
        return `${SupportPage.MAILTO}?subject=${encodeURIComponent(subject)}`
            + `&body=${encodeURIComponent(body)}`;
    },

    // The full environment block (no trimming) for Copy diagnostics.
    fullBlock(diag) {
        let block = SupportPage.environmentText(diag);
        if (diag.log_available) block += `\n\n--- Recent log ---\n${diag.log_tail || '(log is empty)'}`;
        else block += '\n\n(log file not found)';
        return block;
    },

    // --- The dialog --------------------------------------------------------

    async open() {
        let diag;
        try {
            diag = await API.get('/support/diagnostics');
        } catch (err) {
            toast(err.message, 'error');
            return;
        }
        diag.browser = diag.is_native ? '' : (navigator.userAgent || '');
        SupportPage._diag = diag;

        const revealBtn = diag.is_native
            ? '<button type="button" class="btn btn-secondary" onclick="SupportPage.revealLog()">Reveal log file</button>'
            : '';
        const logNote = diag.log_note
            ? `<p style="font-size:11px; color:var(--text-muted); margin:4px 0 0;">${escapeHtml(diag.log_note)}</p>`
            : '';

        openModal('Contact support', `
            <form id="support-form" onsubmit="return false;">
                <p style="font-size:12px; margin:0 0 10px;">Send opens your own mail program with the message written for you — SlowBooks sends nothing itself. mailto can't attach files, so attach screenshots or the log file in your mail program if you can. Long logs are trimmed; use Copy diagnostics for the full block.</p>
                <div class="form-group">
                    <label for="support-what-happened">What happened</label>
                    <textarea id="support-what-happened" name="What happened" rows="4" oninput="SupportPage.refresh()"></textarea>
                </div>
                <div class="form-group">
                    <label for="support-what-expected">What you expected</label>
                    <textarea id="support-what-expected" name="What you expected" rows="3" oninput="SupportPage.refresh()"></textarea>
                </div>
                <div class="form-group">
                    <label for="support-reply-to">Can we reply to you at this address?</label>
                    <input type="email" id="support-reply-to" name="Reply-to address" value="${escapeHtml(diag.reply_to || '')}">
                </div>
                ${logNote}
                <p style="font-size:11px; color:var(--text-muted); margin:10px 0 0;">Support emails, including any logs you attach, are stored in our private project tracker (Basecamp).</p>
                <p style="font-size:11px; color:var(--text-muted); margin:4px 0 0;">Found a security issue? Report it through <a href="https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/security/advisories/new" target="_blank" rel="noopener">GitHub Security Advisories</a>, not this form.</p>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    ${revealBtn}
                    <button type="button" class="btn btn-secondary" onclick="SupportPage.copyDiagnostics()">Copy diagnostics</button>
                    <a id="support-send" class="btn btn-primary" href="${escapeHtml(SupportPage._mailto())}">Send</a>
                </div>
            </form>`);
        SupportPage.refresh();
    },

    _fields() {
        return {
            whatHappened: (document.getElementById('support-what-happened') || {}).value || '',
            whatExpected: (document.getElementById('support-what-expected') || {}).value || '',
            replyTo: (document.getElementById('support-reply-to') || {}).value || '',
        };
    },

    _mailto() {
        if (!SupportPage._diag) return SupportPage.MAILTO;
        return SupportPage.buildMailto(SupportPage._diag, SupportPage._fields());
    },

    // Rebuild the Send link's href from the live field values.
    refresh() {
        const send = document.getElementById('support-send');
        if (send) send.setAttribute('href', SupportPage._mailto());
    },

    copyDiagnostics() {
        if (!SupportPage._diag) return;
        copyToClipboard(SupportPage.fullBlock(SupportPage._diag), 'Diagnostics');
    },

    async revealLog() {
        try {
            await API.post('/support/reveal-log', {});
        } catch (err) {
            toast(err.message, 'error');
        }
    },
};

window.SupportPage = SupportPage;
