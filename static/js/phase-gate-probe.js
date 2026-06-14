// static/js/phase-gate-probe.js
//
// D-11 Programmatic Probe — Self-gated, CSP-compliant, parser-blocking
//
// Loads as external classic <script> (NOT type="module"). Parser-blocking + sync
// execution ensures the probe object & monkey-patches are installed before main.js
// module begins to evaluate, so any addEventListener call from any module gets
// counted by the patched EventTarget.prototype.addEventListener.
//
// Gating:
//   Probe activates ONLY when URL has ?phaseProbe query param OR localStorage has
//   __nlweb_dev_mode === '1'. Production users see zero overhead (this script
//   returns immediately and never modifies prototypes / never creates window.__nlwebProbe).
//
// Hard rule: Probe MUST be production-disabled by default. Violating gating =
// violating D-11 spirit ("temporary bridge is production behavior").

(function () {
    var qp = new URLSearchParams(location.search);
    var enableProbe = qp.has('phaseProbe') || localStorage.getItem('__nlweb_dev_mode') === '1';
    if (!enableProbe) return;  // Production: zero overhead

    window.__nlwebProbe = {
        listenerCounts: new Map(),
        legacyWarnings: [],
        initCounts: new Map(),
        recordInit: function (name) {
            this.initCounts.set(name, (this.initCounts.get(name) || 0) + 1);
        },
    };

    // Monkey-patch EventTarget.prototype.addEventListener to count registrations.
    var origAdd = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function (type, listener, options) {
        var targetName = (this.constructor && this.constructor.name) || 'Unknown';
        var key = targetName + ':' + type;
        window.__nlwebProbe.listenerCounts.set(
            key,
            (window.__nlwebProbe.listenerCounts.get(key) || 0) + 1
        );
        return origAdd.call(this, type, listener, options);
    };

    // Capture all console.warn calls tagged "[legacy]" so sentinel 5 can assert
    // legacy forwarder wrappers were never triggered.
    var origWarn = console.warn;
    console.warn = function () {
        var args = Array.prototype.slice.call(arguments);
        var firstStr = typeof args[0] === 'string' ? args[0] : '';
        if (firstStr.indexOf('[legacy]') !== -1) {
            window.__nlwebProbe.legacyWarnings.push(args.join(' '));
        }
        return origWarn.apply(console, args);
    };

    console.log('[phase-gate-probe] D-11 probe installed (gated by ?phaseProbe).');
})();
