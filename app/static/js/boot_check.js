/**
 * The page never stays white.
 *
 * A support email (2.29.2, Windows 10): "Full white screen on startup, never
 * changing and no menus or sidebars to click on". A web engine too old for
 * the app's scripts fails on them as they load, nothing draws, and nothing
 * says why. This file is loaded first, from <head>, before every other script
 * (tests/test_boot_check.py), and does two things:
 *
 *   - It records the first error while the other scripts load (an error one
 *     of the app's own scripts throws, or one of them failing to load) and
 *     replaces the page with a plain message at once. The app's own start
 *     (the DOMContentLoaded handlers in auth.js and app.js) is then not run,
 *     so nothing draws over it.
 *   - It starts a watchdog that shows the same message when no first screen
 *     has been drawn in WATCHDOG_MS. The app calls SlowBooksBoot.drawn() the
 *     moment it draws one: the sign-in or setup screen (auth.js renderView),
 *     or the first page (app.js App.init). If the watchdog fired on a start
 *     that was only slow, the screen drawn after it takes the message away.
 *
 * It must run on any engine, so it is ES5 throughout: no arrow functions, no
 * let or const, no template strings, no optional chaining. And it is a file of
 * its own on this origin, not an inline script, so a stricter
 * Content-Security-Policy (script-src 'self') can never block it.
 */
(function () {
    'use strict';

    // 45 seconds. The first screen waits on the local server as well as on
    // this page: the sign-in screen on /api/auth/status, the app's first page
    // on its settings and /api/system, and in the desktop app on the update
    // check behind that (App.initSystemInfo waits for
    // /api/system/update-check, a server-side fetch whose 6 s timeouts apply
    // to each step: connect, TLS, send, read, so about 24 s at the very
    // worst on a network that swallows the request). A slow machine opening
    // a big or encrypted company adds its first queries, and a cold web view
    // cache the parsing of all the scripts. That all fits well inside 45 s,
    // which still answers a stuck page within a minute. The first page's own
    // data never counts against it: App.init calls drawn() as that page
    // starts, before its data comes back. And the usual way an old engine
    // fails, a script that cannot run, shows the message at once, without
    // waiting for this.
    var WATCHDOG_MS = 45000;
    var WEBVIEW2_URL = 'https://developer.microsoft.com/microsoft-edge/webview2/';
    var SUPPORT_EMAIL = 'support@slowbookspro.com';
    var BOX_ID = 'boot-check';

    var origin = window.location.origin || (window.location.protocol + '//' + window.location.host);
    // the app's own scripts; a browser extension's, pywebview's injected
    // bridge and the page itself (it has no inline scripts) are not
    var SCRIPTS = origin + '/static/js/';

    var loading = true;   // until the DOMContentLoaded handlers have all run
    var drawn = false;    // a first screen is up: nothing more to watch for
    var fatal = false;    // a script failed while loading: the app cannot start
    var firstError = '';
    var firstRejection = '';
    var box = null;       // the message, while it shows
    var timer = null;

    function own(url) {
        return typeof url === 'string' && url.indexOf(SCRIPTS) === 0;
    }

    function inWebView2() {
        return !!(window.chrome && window.chrome.webview);
    }

    function add(parent, tag, style) {
        var el = document.createElement(tag);
        if (style) el.setAttribute('style', style);
        parent.appendChild(el);
        return el;
    }

    function addText(parent, text) {
        parent.appendChild(document.createTextNode(text));
    }

    function addLink(parent, href, text) {
        var a = add(parent, 'a', 'color:#1f5fbf;');
        a.setAttribute('href', href);
        if (href.indexOf('mailto:') !== 0) {
            // the desktop window hands a new window's address to the system browser
            a.setAttribute('target', '_blank');
            a.setAttribute('rel', 'noopener');
        }
        addText(a, text);
    }

    function show() {
        if (box) return;
        if (!document.body) {
            // nothing to draw on yet; DOMContentLoaded below tries again
            return;
        }
        box = document.createElement('div');
        box.id = BOX_ID;
        box.setAttribute('role', 'alert');
        box.setAttribute('tabindex', '-1');
        // position:fixed on all four sides, not inset, which older engines lack
        box.setAttribute('style',
            'position:fixed;top:0;right:0;bottom:0;left:0;z-index:2147483647;' +
            'overflow:auto;background:#ffffff;color:#1a2b3c;padding:48px 24px;' +
            'font-family:"Segoe UI",system-ui,-apple-system,Helvetica,Arial,sans-serif;' +
            'font-size:15px;line-height:1.5;text-align:left;');
        var wrap = add(box, 'div', 'max-width:560px;margin:0 auto;');
        addText(add(wrap, 'h1', 'font-size:20px;margin:0 0 16px;'),
            'SlowBooks Pro could not start in this window.');
        var help;
        if (inWebView2()) {
            var runtime = add(wrap, 'p', 'margin:0 0 12px;');
            addText(runtime, 'The Microsoft Edge WebView2 runtime on this computer may be out of date. Update it from ');
            addLink(runtime, WEBVIEW2_URL, WEBVIEW2_URL);
            addText(runtime, ' and open SlowBooks again.');
            help = add(wrap, 'p', 'margin:0 0 12px;');
            addText(help, 'If that does not help, write to ');
        } else {
            help = add(wrap, 'p', 'margin:0 0 12px;');
            addText(help, 'Write to ');
        }
        addLink(help, 'mailto:' + SUPPORT_EMAIL, SUPPORT_EMAIL);
        var error = firstError || firstRejection;
        addText(help, error ? ' and include the error below.' : '.');
        if (error) {
            addText(add(wrap, 'pre',
                'white-space:pre-wrap;word-wrap:break-word;background:#f4f6f8;' +
                'border:1px solid #d8dee4;border-radius:6px;padding:12px;font-size:13px;'),
                'Error: ' + error);
        }
        document.body.appendChild(box);
        if (box.focus) box.focus();
    }

    function fail() {
        fatal = true;
        if (timer !== null) {
            clearTimeout(timer);
            timer = null;
        }
        show();
    }

    // Capture phase: a <script> that fails to load fires its error on the
    // element and does not bubble, but it passes the window on the way down.
    window.addEventListener('error', function (event) {
        if (drawn || fatal) return;
        var target = event && event.target;
        var text;
        if (target && target !== window && target.tagName) {
            // a resource failed: only one of the app's scripts is why a page stays white
            if (target.tagName !== 'SCRIPT' || !own(target.src)) return;
            text = 'Could not load ' + target.src.slice(origin.length);
        } else {
            // a script threw: only the app's own count ("Script error." from
            // another origin carries nothing, and is not the app's)
            if (!own(event.filename)) return;
            text = (event.message || 'Script error') + ' (' + event.filename.slice(origin.length) +
                (event.lineno ? ', line ' + event.lineno : '') + ')';
        }
        if (!firstError) firstError = text;
        if (loading) fail();
    }, true);

    // A promise nothing handled is not proof of a failed start, but if the
    // watchdog fires it is the best clue there is.
    window.addEventListener('unhandledrejection', function (event) {
        if (drawn || firstRejection) return;
        var reason = event && event.reason;
        firstRejection = String((reason && reason.message) || reason || 'unknown');
    });

    // Registered before any other script runs, so it is the first
    // DOMContentLoaded handler: after a failed load it stops the app's own.
    document.addEventListener('DOMContentLoaded', function (event) {
        if (fatal) {
            if (event.stopImmediatePropagation) event.stopImmediatePropagation();
            show();
            return;
        }
        // the handlers after this one run before this timer: an error they
        // throw is still an error while the page loads
        setTimeout(function () { loading = false; }, 0);
    });

    timer = setTimeout(function () {
        timer = null;
        if (!drawn) show();
    }, WATCHDOG_MS);

    window.SlowBooksBoot = {
        WATCHDOG_MS: WATCHDOG_MS,
        // The app drew a first screen: the sign-in or setup screen, or its
        // first page. Called again later, it does nothing.
        drawn: function () {
            if (drawn) return;
            drawn = true;
            if (timer !== null) {
                clearTimeout(timer);
                timer = null;
            }
            // the watchdog's message over a start that was only slow goes;
            // a failed load's stays, whatever drew after it
            if (box && !fatal) {
                if (box.parentNode) box.parentNode.removeChild(box);
                box = null;
            }
        }
    };
})();
