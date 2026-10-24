// #476: an optional short chime when a transaction posts. toastPosted() (and
// toastAction with opts.posted) rings it when the per-user preference is on;
// a plain toast — a delete, a void, a saved setting — never does, nor an
// error or warning, and nothing rings when it's off. The sound is guarded so a browser or
// WebView without AudioContext (or one that refuses autoplay) stays silent
// instead of throwing. These stub AudioContext and watch toast() drive it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

// A fake AudioContext that counts the oscillators a chime starts. Two tones
// per chime, so `starts === 2` means the sound played once.
function fakeAudioContext() {
    const counts = { contexts: 0, starts: 0, resumes: 0 };
    function Ctx() {
        counts.contexts += 1;
        this.state = Ctx.startState || 'running';
        this.resume = () => { counts.resumes += 1; this.state = 'running'; return Promise.resolve(); };
        this.currentTime = 0;
        this.destination = {};
        this.createOscillator = () => ({
            type: '', frequency: {},
            connect: (node) => node,
            start: () => { counts.starts += 1; },
            stop: () => {},
        });
        this.createGain = () => ({
            gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} },
            connect: (node) => node,
        });
    }
    return { Ctx, counts };
}

// A minimal page: a toast container, createElement that swallows everything
// toast() does to the node, and setTimeout/clearTimeout as no-ops.
function load(audioCtor) {
    const container = { appendChild() {} };
    const listeners = {};
    const document = {
        querySelector: (sel) => (sel === '#toast-container' ? container : null),
        querySelectorAll: () => [],
        createElement: () => ({
            className: '', textContent: '', style: {},
            appendChild() {}, remove() {}, addEventListener() {},
            matches: () => false, contains: () => false,
        }),
        addEventListener(type, fn) { listeners[type] = fn; },
        removeEventListener(type) { delete listeners[type]; },
        contains: () => false,
    };
    const window = {};
    if (audioCtor) window.AudioContext = audioCtor;
    const context = {
        window, document, App: { settings: {} },
        setTimeout: () => 0, clearTimeout: () => {},
    };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8');
    vm.runInContext(
        js + '\nthis.toast = toast; this.playPostSound = playPostSound;'
           + ' this.setPostSoundEnabled = setPostSoundEnabled;'
           + ' this.toastPosted = toastPosted; this.toastAction = toastAction;'
           + ' this.primePostAudio = primePostAudio;',
        context,
    );
    context.listeners = listeners;
    return context;
}

test('a posting toast rings the chime when the preference is on', () => {
    const { Ctx, counts } = fakeAudioContext();
    const { toastPosted, setPostSoundEnabled } = load(Ctx);
    setPostSoundEnabled(true);
    toastPosted('Payment recorded');
    assert.equal(counts.starts, 2, 'two tones for one chime');
});

test('a plain success toast never rings: deletes, voids, settings stay silent', () => {
    const { Ctx, counts } = fakeAudioContext();
    const { toast, setPostSoundEnabled } = load(Ctx);
    setPostSoundEnabled(true);
    toast('Payment voided');
    toast('Settings saved');
    toast('Attachment deleted');
    assert.equal(counts.starts, 0);
});

test('a toastAction that confirms a posting rings when asked to', () => {
    const { Ctx, counts } = fakeAudioContext();
    const { toastAction, setPostSoundEnabled } = load(Ctx);
    setPostSoundEnabled(true);
    toastAction('Saved to Downloads', 'Show in folder', () => {});
    assert.equal(counts.starts, 0);
    toastAction('Journal entry created', 'View', () => {}, 8000, null, { posted: true });
    assert.equal(counts.starts, 2);
});

test('a posting is silent when the preference is off (the default)', () => {
    const { Ctx, counts } = fakeAudioContext();
    const { toastPosted } = load(Ctx);
    toastPosted('Payment recorded');  // nothing turned it on
    assert.equal(counts.starts, 0);
    assert.equal(counts.contexts, 0, 'no AudioContext made while off');
});

test('an error or warning toast never rings, even with the preference on', () => {
    const { Ctx, counts } = fakeAudioContext();
    const { toast, setPostSoundEnabled } = load(Ctx);
    setPostSoundEnabled(true);
    toast('Something broke', 'error');
    toast('Heads up', 'warning');
    toast('For your information', 'info');
    assert.equal(counts.starts, 0);
});

test('the first click or key press resumes a context WKWebView left suspended', () => {
    const { Ctx, counts } = fakeAudioContext();
    Ctx.startState = 'suspended';
    const page = load(Ctx);
    page.setPostSoundEnabled(true);  // the preference arrives from App.init
    assert.ok(page.listeners.pointerdown && page.listeners.keydown, 'armed for a gesture');
    page.listeners.keydown();  // the user's first key press
    assert.equal(counts.contexts, 1, 'made inside the gesture');
    assert.equal(counts.resumes, 1, 'resumed inside the gesture');
    assert.equal(page.listeners.keydown, undefined, 'disarmed once running');
});

test('turning the setting on primes the context inside its change handler', () => {
    const { Ctx, counts } = fakeAudioContext();
    Ctx.startState = 'suspended';
    const page = load(Ctx);
    page.setPostSoundEnabled(true);
    page.primePostAudio();
    assert.equal(counts.contexts, 1);
    assert.equal(counts.resumes, 1);
});

test('a browser with no AudioContext stays silent and does not throw', () => {
    const { toastPosted, setPostSoundEnabled } = load(undefined);  // no window.AudioContext
    setPostSoundEnabled(true);
    assert.doesNotThrow(() => toastPosted('Entry posted'));
});
