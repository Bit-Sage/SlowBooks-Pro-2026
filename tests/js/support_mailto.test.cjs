// The Contact support form's mailto builder (support.js). The form opens the
// user's own mail client with a mailto: link — the app sends nothing — so the
// link's shape is what matters: addressed to support, an encodeURIComponent'd
// subject carrying [SBP], and a body kept under the length some mail clients
// truncate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load() {
    const context = { window: {}, navigator: { userAgent: 'TestUA/1.0' }, document: { addEventListener() {} } };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/support.js'), 'utf8');
    vm.runInContext(js, context);
    return context.window.SupportPage;  // support.js sets window.SupportPage
}

const DIAG = {
    version: '2.29.1', commit: 'abc1234', platform: 'macOS', deployment: 'native',
    is_native: true, reply_to: 'me@co.com',
    environment: 'Version: v2.29.1 (abc1234)\nPlatform: macOS\nPython: 3.13.0\nDeployment: native',
    log_tail: '2026-10-25 09:15:01 INFO ready', log_available: true, browser: '',
};

test('the mailto is addressed to support with an [SBP] subject', () => {
    const SupportPage = load();
    const href = SupportPage.buildMailto(DIAG, { whatHappened: 'PDF is blank', whatExpected: 'a PDF' });
    assert.ok(href.startsWith('mailto:support@slowbookspro.com?'), href);
    const subject = decodeURIComponent(href.match(/subject=([^&]*)/)[1]);
    assert.equal(subject, '[SBP] v2.29.1 · macOS native · PDF is blank');
});

test('the body carries the user text, the environment and the log', () => {
    const SupportPage = load();
    const href = SupportPage.buildMailto(DIAG, { whatHappened: 'it broke', whatExpected: 'it to work', replyTo: 'me@co.com' });
    const body = decodeURIComponent(href.match(/body=(.*)$/)[1]);
    assert.ok(body.includes('What happened:\nit broke'));
    assert.ok(body.includes('What you expected:\nit to work'));
    assert.ok(body.includes('Reply to: me@co.com'));
    assert.ok(body.includes('--- Environment ---'));
    assert.ok(body.includes('Deployment: native'));
    assert.ok(body.includes('2026-10-25 09:15:01 INFO ready'));
});

test('a browser is added to the environment off the native window', () => {
    const SupportPage = load();
    const diag = Object.assign({}, DIAG, { is_native: false, deployment: 'Docker', browser: 'TestUA/1.0' });
    const href = SupportPage.buildMailto(diag, { whatHappened: 'x' });
    const body = decodeURIComponent(href.match(/body=(.*)$/)[1]);
    assert.ok(body.includes('Browser: TestUA/1.0'));
});

test('a long log is trimmed to keep the body under the mailto budget', () => {
    const SupportPage = load();
    const diag = Object.assign({}, DIAG, { log_tail: ('a line of log\n').repeat(500) });
    const body = SupportPage.buildBody(diag, { whatHappened: 'x', whatExpected: 'y' });
    assert.ok(body.length <= SupportPage.MAX_BODY_CHARS, `body is ${body.length} chars`);
    assert.ok(body.includes('use Copy diagnostics'));
});

test('a missing log says so and does not pretend to a tail', () => {
    const SupportPage = load();
    const diag = Object.assign({}, DIAG, { log_available: false, log_tail: '' });
    const body = SupportPage.buildBody(diag, { whatHappened: 'x', whatExpected: 'y' });
    assert.ok(body.trimEnd().endsWith('(log file not found)'));
});

test('an empty what-happened still yields a usable subject', () => {
    const SupportPage = load();
    const href = SupportPage.buildMailto(DIAG, { whatHappened: '' });
    const subject = decodeURIComponent(href.match(/subject=([^&]*)/)[1]);
    assert.ok(subject.endsWith('support request'), subject);
});
