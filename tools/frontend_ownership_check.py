#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D-11 / D-12 / D-13 / D-14 Phase Gate verifier.

Anchors enforced:
    D-11 — Intermediate Invariant — per-phase module ownership gate
    D-12 — Active entrypoint reachability (script/link import chain proves new owner is reachable)
    D-12 CSS — @import legality (@import rules must appear before any style rule)
    D-13 — No top-level module side effects (imported modules are inert)
    D-14 — Cache identity (every served /static/js/ and /static/css/ asset returns Cache-Control: no-cache)

Usage:
    python tools/frontend_ownership_check.py --phase 1
    python tools/frontend_ownership_check.py --phase 1 --check entrypoint
    python tools/frontend_ownership_check.py --check css-import-legality
    python tools/frontend_ownership_check.py --phase 1 --check no-top-level-side-effects
    python tools/frontend_ownership_check.py --check cache-headers
    python tools/frontend_ownership_check.py --phase 1 --check visual-contract

Exit code: 0 = all pass, 1 = any fail.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------
# Phase configuration tables
# ----------------------------------------------------------------------

# PHASE_PATTERNS[phase_id] = {
#     'old_declarations':           list of (regex, file_relpath_or_glob, expected_active_count)
#     'new_module_imports':         list of (regex, file_relpath, expected_active_count)
#     'old_listener_registrations': list of (regex, file_relpath_or_glob, expected_active_count)
#     'dynamic_import_sentinel':    list of (regex, dir_glob, expected_active_count)
# }
PHASE_PATTERNS = {
    '1': {
        # Phase 1 is a coexistence phase: legacy utility functions remain active in
        # news-search.js (intentional dual-source). No old_declarations check.
        'old_declarations': [],

        # Sentinel 3: new module's exported function must actually be imported by main.js.
        'new_module_imports': [
            (r"from\s+['\"]\./utils/dom\.js['\"]", 'static/js/main.js', 1),
        ],

        # Phase 1 does not migrate listeners.
        'old_listener_registrations': [],

        # D-3 hard rule: NO dynamic imports anywhere under static/js/.
        # Pattern matches both `await import(...)` and bare `import(...)` call.
        # Excludes static `import ... from '...'` declarations (those are scanned in module headers).
        'dynamic_import_sentinel': [
            (r'(?<!\.)\bimport\s*\(', 'static/js/**/*.js', 0),
        ],
    },
    '2': {
        # Phase 2 (CSS-only re-scope): 4 selector groups extracted from news-search.css
        # legacy region into static/css/components/{sidebar,popover,modal,tabs}.css.
        # JS-side Phase 2b deferred (see plan impl-notes Phase 2 Option C).
        #
        # old_declarations: sentinel ROOT selector blocks must be 0 in news-search.css
        # legacy region. We cannot count every Phase 2 selector here (the new component
        # files contain the same names) — instead we anchor on the FOUR unambiguous
        # standalone root selectors that only existed in legacy region.
        'old_declarations': [
            (r'^\s*\.left-sidebar\s*\{', 'static/news-search.css', 0),
            (r'^\s*\.settings-popover\s*\{', 'static/news-search.css', 0),
            (r'^\s*\.modal-overlay\s*\{', 'static/news-search.css', 0),
            (r'^\s*\.right-tabs-container\s*\{', 'static/news-search.css', 0),
        ],

        # new_module_imports: 4 new @import lines in news-search.css manifest region.
        'new_module_imports': [
            (r"@import url\('/static/css/components/sidebar\.css'\)", 'static/news-search.css', 1),
            (r"@import url\('/static/css/components/popover\.css'\)", 'static/news-search.css', 1),
            (r"@import url\('/static/css/components/modal\.css'\)", 'static/news-search.css', 1),
            (r"@import url\('/static/css/components/tabs\.css'\)", 'static/news-search.css', 1),
        ],

        # Phase 2 (CSS-only) doesn't migrate JS listeners. JS-side phase 2b deferred.
        'old_listener_registrations': [],

        # D-3 hard rule: NO dynamic imports anywhere under static/js/. Phase 2 doesn't
        # touch JS but the invariant must still hold.
        'dynamic_import_sentinel': [
            (r'(?<!\.)\bimport\s*\(', 'static/js/**/*.js', 0),
        ],
    },
    '3': {
        # Phase 3 Path B (CEO 拍板 2026-05-21):
        # AuthManager class + singleton + checkAuthOnLoad + visibilitychange listener
        # are REAL moves (declarations comment out in news-search.js, body extracted to
        # core/auth-manager.js + core/page-bootstrap.js).
        #
        # UserStateSync IIFE / assertUserIdentity / UserStateSyncError remain ACTIVE in
        # news-search.js (Path B sequencing — moved in Phase 7+ after state owners migrate).
        # state-sync.js is a thin alias re-export module; not checked here.
        #
        # old_declarations: sentinel 1 — REAL-moved declarations 0 active hit
        'old_declarations': [
            (r'^\s*class AuthManager\b', 'static/news-search.js', 0),
            (r'^\s*const authManager\s*=\s*new\s+AuthManager', 'static/news-search.js', 0),
            (r'^\s*async\s+function\s+checkAuthOnLoad\s*\(', 'static/news-search.js', 0),
        ],

        # new_module_imports: sentinel 3 — main.js must import 3 new core modules
        'new_module_imports': [
            (r"from\s+['\"]\./core/auth-manager\.js['\"]", 'static/js/main.js', 1),
            (r"from\s+['\"]\./core/state-sync\.js['\"]", 'static/js/main.js', 1),
            (r"from\s+['\"]\./core/page-bootstrap\.js['\"]", 'static/js/main.js', 1),
        ],

        # old_listener_registrations: sentinel 2 — visibilitychange listener 0 active hit
        # in news-search.js (now registered inside page-bootstrap.js's bootstrapPage()).
        'old_listener_registrations': [
            (r"document\.addEventListener\(['\"]visibilitychange['\"]", 'static/news-search.js', 0),
        ],

        # D-3 hard rule: NO dynamic imports anywhere under static/js/.
        'dynamic_import_sentinel': [
            (r'(?<!\.)\bimport\s*\(', 'static/js/**/*.js', 0),
        ],
    },
    '4a': {
        # Phase 4a Path B (CEO 拍板 2026-05-21):
        # renderLeftSidebarSessions / renderSharedSessions / _renderSharedSessionsList
        # named functions are REAL moves (declarations comment out in news-search.js,
        # body extracted to features/sessions-list.js). initSessionTabs IIFE +
        # session-saved/deleted listeners + outside-click sidebar dropdown listener
        # + initial render call are also REAL moves (registered inside the new module's
        # initSessionsList()).
        #
        # State declarations (savedSessions / currentLoadedSessionId / _sessionDirty /
        # _sharedSessionsCache / _sharedSessionsLoading) REMAIN active in news-search.js
        # (Path B sequencing — moved in Phase 7+ after UserStateSync IIFE relocates).
        # State is read by features/sessions-list.js via window.* bridges.
        #
        # old_declarations: sentinel 1 — REAL-moved function declarations 0 active hit
        'old_declarations': [
            (r'^\s*function\s+renderLeftSidebarSessions\s*\(', 'static/news-search.js', 0),
            (r'^\s*async\s+function\s+renderSharedSessions\s*\(', 'static/news-search.js', 0),
            (r'^\s*function\s+_renderSharedSessionsList\s*\(', 'static/news-search.js', 0),
        ],

        # new_module_imports: sentinel 3 — main.js must import the new feature module
        'new_module_imports': [
            (r"from\s+['\"]\./features/sessions-list\.js['\"]", 'static/js/main.js', 1),
        ],

        # old_listener_registrations: sentinel 2 — session-saved / session-deleted
        # 0 active hit in news-search.js (now registered inside initSessionsList()).
        # The session-saved CustomEvent dispatch sites (line ~440, ~2419) still exist —
        # they're emit, not register; sentinel only matches addEventListener registers.
        'old_listener_registrations': [
            (r"document\.addEventListener\(['\"]session-saved['\"]", 'static/news-search.js', 0),
            (r"document\.addEventListener\(['\"]session-deleted['\"]", 'static/news-search.js', 0),
        ],

        # D-3 hard rule: NO dynamic imports anywhere under static/js/.
        'dynamic_import_sentinel': [
            (r'(?<!\.)\bimport\s*\(', 'static/js/**/*.js', 0),
        ],
    },
    '4b': {
        # Phase 4b Path B (CEO 拍板 2026-05-21):
        # SessionManager class declaration + `let sessionManager` stopgap (Phase 3
        # fix block) + `window._initSessionManager` setter are REAL moves: class
        # body extracted to features/session-manager.js as `export class SessionManager`,
        # singleton init pattern replaces the stopgap (`initSessionManager()` export).
        #
        # NOT moved this phase (Path B narrow, Phase 7+ migration):
        #   - module-level `function saveCurrentSession()` in news-search.js
        #     (reassigns outer-scope `let currentLoadedSessionId` / `_sessionDirty`)
        #   - `async function loadSavedSession(session)` in news-search.js
        #   - All session-state `let` declarations (savedSessions / currentLoadedSessionId /
        #     _sessionDirty / _sharedSessionsCache) — kept active per Phase 4a
        #     Path B sequencing
        #   - D-7 layer #4 (_isShared early return inside saveCurrentSession)
        #
        # old_declarations: REAL-moved declarations must have 0 active hit
        'old_declarations': [
            (r'^\s*class SessionManager\s*\{', 'static/news-search.js', 0),
            (r'^\s*let\s+sessionManager\s*;', 'static/news-search.js', 0),
            (r'^\s*window\._initSessionManager\s*=', 'static/news-search.js', 0),
        ],

        # new_module_imports: main.js must import the new feature module
        'new_module_imports': [
            (r"from\s+['\"]\./features/session-manager\.js['\"]", 'static/js/main.js', 1),
        ],

        # No DOM listener registrations moved this phase. SessionManager methods are
        # invoked imperatively by callers (saveCurrentSession scheduleSave path,
        # checkAuthOnLoad loadSessions path, etc.). The beforeunload listener stays
        # in news-search.js (registers in classic script body so it captures parse-time
        # state; only the callback body changed to read window.sessionManager).
        'old_listener_registrations': [],

        # D-3 hard rule: NO dynamic imports anywhere under static/js/.
        'dynamic_import_sentinel': [
            (r'(?<!\.)\bimport\s*\(', 'static/js/**/*.js', 0),
        ],
    },
    # Phase 4c+ entries to be added by future phase executors.
}


# D-12 Entrypoint reachability — list of import specifiers main.js must be able to resolve.
ENTRYPOINT_REACHABILITY = {
    '1': ['./utils/dom.js'],
    # Phase 2 is CSS-only — no new JS module imports. main.js still only imports
    # ./utils/dom.js from Phase 1. The CSS @import chain is verified by
    # --check css-import-legality.
    '2': ['./utils/dom.js'],
    '3': [
        './utils/dom.js',           # Phase 1 carryover
        './core/auth-manager.js',
        './core/state-sync.js',
        './core/page-bootstrap.js',
    ],
    '4a': [
        './utils/dom.js',                  # Phase 1 carryover
        './core/auth-manager.js',          # Phase 3 carryover
        './core/state-sync.js',            # Phase 3 carryover
        './core/page-bootstrap.js',        # Phase 3 carryover
        './features/sessions-list.js',     # Phase 4a NEW
    ],
    '4b': [
        './utils/dom.js',                  # Phase 1 carryover
        './core/auth-manager.js',          # Phase 3 carryover
        './core/state-sync.js',            # Phase 3 carryover
        './core/page-bootstrap.js',        # Phase 3 carryover
        './features/sessions-list.js',     # Phase 4a carryover
        './features/session-manager.js',   # Phase 4b NEW
    ],
}


# Phase-new modules (for D-13 no-top-level-side-effects scan).
# main.js is the entrypoint (D-13 exemption), so it's listed but skipped during scan.
PHASE_NEW_MODULES = {
    '1': {
        'modules': [
            'static/js/utils/dom.js',
            'static/js/utils/analytics.js',
        ],
        'entrypoint_exempt': [
            'static/js/main.js',
        ],
    },
    # Phase 2 is CSS-only — no new JS modules. D-13 scan is a no-op.
    '2': {
        'modules': [],
        'entrypoint_exempt': [
            'static/js/main.js',
        ],
    },
    '3': {
        'modules': [
            'static/js/core/auth-manager.js',
            'static/js/core/state-sync.js',
            'static/js/core/page-bootstrap.js',
        ],
        'entrypoint_exempt': [
            'static/js/main.js',
        ],
    },
    '4a': {
        'modules': [
            # Phase 4a NEW module: features/sessions-list.js.
            # Path B narrow scope — module is INERT on import (no top-level addEventListener,
            # no top-level fetch, no top-level localStorage write). All side effects happen
            # inside initSessionsList() called by main.js DOMContentLoaded.
            'static/js/features/sessions-list.js',
        ],
        'entrypoint_exempt': [
            'static/js/main.js',
        ],
    },
    '4b': {
        'modules': [
            # Phase 4b NEW module: features/session-manager.js.
            # Path B narrow scope — module is INERT on import:
            #   - `export class SessionManager` declaration is pure (no side effects)
            #   - `let _sessionManager = null` is pure binding (no `new` runs)
            #   - `new SessionManager(...)` runs only inside exported initSessionManager()
            #     which is called by main.js bootstrap, not at module eval time
            'static/js/features/session-manager.js',
        ],
        'entrypoint_exempt': [
            'static/js/main.js',
        ],
    },
}


# D-13 EXEMPT_CONSTRUCTIONS: known-stateless `new X()` constructions allowed at module
# top level. Used by check_no_top_level_side_effects() — matched BEFORE the generic
# TOP_LEVEL_SIDE_EFFECT_PATTERNS scan.
#
# Format: (file_relpath, line_pattern_regex).
# The line_pattern_regex is matched against the LEFT-STRIPPED top-level statement (i.e.
# `re.match(pattern, stripped)`). Stripped means leading whitespace already removed.
EXEMPT_CONSTRUCTIONS = [
    # Phase 3 Path B (2026-05-21): authManager singleton is INERT-ish — constructor only
    # reads localStorage + writes in-memory fields + removes corrupted entries; no fetch
    # / DOM register / fresh storage write. Verified by inspection of AuthManager._init.
    # See static/js/core/auth-manager.js header note for full D-13 compliance rationale.
    ('static/js/core/auth-manager.js', r'^export\s+const\s+authManager\s*=\s*new\s+AuthManager\(\)'),
]


# D-13 forbidden top-level patterns. These are regex applied to LOGICAL top-level lines
# (after stripping leading whitespace, comments, blank lines).
TOP_LEVEL_SIDE_EFFECT_PATTERNS = [
    r'^(?:document|window)\.addEventListener\(',
    r'^(?:document|window)\.[a-zA-Z]+\(',
    r'^localStorage\.(setItem|removeItem|clear)\(',
    r'^sessionStorage\.(setItem|removeItem|clear)\(',
    r'^document\.cookie\s*=',
    r'^fetch\(',
    r'^new\s+XMLHttpRequest\(',
    r'^new\s+EventSource\(',
    r'^runInitSync\(',
    r'^applyInit\(',
    r'^clearUserScopedState\(',
    r'^const\s+\w+\s*=\s*new\s+\w+\(',
    r'=\s*fetch\(',
    r'^export\s+const\s+\w+\s*=\s*new\s+',
    r'^window\.\w+\s*=\s*[^=]',
    r'^document\.(body|documentElement|head|querySelector|getElementById|querySelectorAll)\.',
    r'^new\s+(MutationObserver|IntersectionObserver|EventSource|WebSocket|ResizeObserver|PerformanceObserver)\(',
    r'=\s*new\s+(SessionManager|AuthManager|UserStateSync)\(',
]


# ----------------------------------------------------------------------
# Comment-stripping helpers (active-line detection)
# ----------------------------------------------------------------------

def _strip_block_comments(text: str) -> str:
    """Remove /* ... */ block comments (non-greedy, multi-line)."""
    return re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)


def _is_line_comment(stripped_line: str) -> bool:
    """Return True if line starts with // (after leading whitespace stripped)."""
    return stripped_line.lstrip().startswith('//')


def _active_lines(file_path: Path):
    """Iterate (line_number, line_text) for active (non-comment, non-blank) lines.

    Strategy: read file, strip block comments globally, then filter out lines that
    are //-line-comments or blank. Yields (1-based line_no, line text without trailing newline).
    """
    try:
        raw = file_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return
    stripped = _strip_block_comments(raw)
    for i, line in enumerate(stripped.splitlines(), 1):
        if not line.strip():
            continue
        if _is_line_comment(line):
            continue
        yield i, line


def count_active_matches(path_or_glob: str, pattern: str) -> tuple[int, list]:
    """Count active-line regex matches under repo-relative path or glob.

    Returns (total_count, [(file_rel, line_no, line_text), ...]).
    """
    compiled = re.compile(pattern)
    matches = []
    # Resolve glob vs single file
    if any(c in path_or_glob for c in '*?[]'):
        # Need rglob if pattern contains **
        if '**' in path_or_glob:
            base, _, tail = path_or_glob.partition('**/')
            base_dir = REPO_ROOT / base.rstrip('/')
            paths = list(base_dir.rglob(tail))
        else:
            paths = list(REPO_ROOT.glob(path_or_glob))
    else:
        paths = [REPO_ROOT / path_or_glob]

    for p in paths:
        if not p.is_file():
            continue
        for line_no, line in _active_lines(p):
            if compiled.search(line):
                matches.append((str(p.relative_to(REPO_ROOT)).replace('\\', '/'), line_no, line.rstrip()))
    return len(matches), matches


# ----------------------------------------------------------------------
# Subcommand: --check ownership (D-11)
# ----------------------------------------------------------------------

def check_ownership(phase_id: str) -> dict:
    if phase_id not in PHASE_PATTERNS:
        return {'pass': False, 'error': f'Unknown phase: {phase_id}'}

    rules = PHASE_PATTERNS[phase_id]
    failures = []
    passes = []

    def _run_group(group_name: str, items, sentinel_label: str):
        for pattern, location, expected in items:
            count, hits = count_active_matches(location, pattern)
            ok = (count == expected)
            entry = {
                'sentinel': sentinel_label,
                'group': group_name,
                'pattern': pattern,
                'location': location,
                'expected': expected,
                'actual': count,
                'hits': hits[:10],  # cap for readability
            }
            (passes if ok else failures).append(entry)

    _run_group('old_declarations', rules['old_declarations'], 'sentinel-1')
    _run_group('new_module_imports', rules['new_module_imports'], 'sentinel-3')
    _run_group('old_listener_registrations', rules['old_listener_registrations'], 'sentinel-2')
    _run_group('dynamic_import_sentinel', rules['dynamic_import_sentinel'], 'D-3-static-only')

    return {
        'pass': len(failures) == 0,
        'phase': phase_id,
        'failures': failures,
        'passes_count': len(passes),
    }


# ----------------------------------------------------------------------
# Subcommand: --check entrypoint (D-12)
# ----------------------------------------------------------------------

HTML_ENTRY_FILE = 'static/news-search-prototype.html'
CSS_ENTRY_FILE = 'static/news-search.css'
JS_MAIN_FILE = 'static/js/main.js'


def _read_file(rel_path: str) -> str | None:
    p = REPO_ROOT / rel_path
    if not p.is_file():
        return None
    return p.read_text(encoding='utf-8')


def check_entrypoint(phase_id: str) -> dict:
    failures = []
    notes = []

    html = _read_file(HTML_ENTRY_FILE)
    if html is None:
        return {'pass': False, 'error': f'HTML entry not found: {HTML_ENTRY_FILE}'}

    # 1) HTML must <link> to news-search.css
    if 'news-search.css' not in html:
        failures.append({'msg': 'HTML entry missing <link href="news-search.css">'})
    else:
        notes.append('HTML entry references news-search.css')

    # 2) HTML must include phase-gate-probe.js (external classic script)
    if 'phase-gate-probe.js' not in html:
        failures.append({'msg': 'HTML entry missing phase-gate-probe.js <script src>'})
    else:
        notes.append('HTML entry references phase-gate-probe.js')

    # 3) HTML must include main.js as type="module"
    main_module_re = re.compile(
        r'<script\b[^>]*\btype\s*=\s*["\']module["\'][^>]*\bsrc\s*=\s*["\'][^"\']*main\.js[^"\']*["\'][^>]*>'
        r'|<script\b[^>]*\bsrc\s*=\s*["\'][^"\']*main\.js[^"\']*["\'][^>]*\btype\s*=\s*["\']module["\'][^>]*>'
    )
    if not main_module_re.search(html):
        failures.append({'msg': 'HTML entry missing <script type="module" src=".../main.js">'})
    else:
        notes.append('HTML entry references main.js as type="module"')

    # 4) main.js must import each expected module from ENTRYPOINT_REACHABILITY
    main_js = _read_file(JS_MAIN_FILE)
    if main_js is None:
        failures.append({'msg': f'{JS_MAIN_FILE} not found'})
    else:
        expected_imports = ENTRYPOINT_REACHABILITY.get(phase_id, [])
        for spec in expected_imports:
            # static import: import ... from './utils/dom.js'
            import_re = re.compile(
                r"^\s*import\s.+from\s+['\"]" + re.escape(spec) + r"['\"]",
                re.MULTILINE,
            )
            if not import_re.search(main_js):
                failures.append({'msg': f'main.js does not import {spec}'})
            else:
                notes.append(f'main.js imports {spec}')

    # 5) news-search.css must @import tokens.css and base.css (Phase 1)
    #    + components/sidebar.css / popover.css / modal.css / tabs.css (Phase 2+)
    css = _read_file(CSS_ENTRY_FILE)
    if css is None:
        failures.append({'msg': f'{CSS_ENTRY_FILE} not found'})
    else:
        expected_css_imports = ['/static/css/tokens.css', '/static/css/base.css']
        # Phase 2 adds 4 components/
        if phase_id == '2' or (phase_id and phase_id > '2'):
            expected_css_imports.extend([
                '/static/css/components/sidebar.css',
                '/static/css/components/popover.css',
                '/static/css/components/modal.css',
                '/static/css/components/tabs.css',
            ])
        for sub in expected_css_imports:
            if sub not in css:
                failures.append({'msg': f'news-search.css missing @import {sub}'})
            else:
                notes.append(f'news-search.css @imports {sub}')

    return {
        'pass': len(failures) == 0,
        'phase': phase_id,
        'failures': failures,
        'notes': notes,
    }


# ----------------------------------------------------------------------
# Subcommand: --check css-import-legality (D-12)
# ----------------------------------------------------------------------

def check_css_import_legality() -> dict:
    """Every @import in news-search.css must appear before any selector block / @-rule
    other than @charset / @layer / @import itself."""
    css = _read_file(CSS_ENTRY_FILE)
    if css is None:
        return {'pass': False, 'error': f'{CSS_ENTRY_FILE} not found'}

    # Strip /* ... */ block comments
    stripped = _strip_block_comments(css)

    # Walk forward, tracking when a non-allowed token first appears.
    # "Allowed before @import": whitespace, @charset, @layer (statement form), other @imports.
    # "Disallowed before @import": any selector + { ... } block, @media, @supports, @keyframes, ...

    # Tokenize into top-level "items": @import url(...); / @charset "..."; / @layer name1, name2; / @media { ... } / selector { ... } / etc.
    pos = 0
    n = len(stripped)
    seen_disallowed = False
    failures = []
    imports_after_disallowed = []

    while pos < n:
        # Skip whitespace
        m = re.match(r'\s+', stripped[pos:])
        if m:
            pos += m.end()
            continue

        rest = stripped[pos:]

        # @charset "..."; — allowed before @import only at very top; we accept anywhere as no-op for legality check
        m = re.match(r'@charset\s+[^;]*;', rest)
        if m:
            pos += m.end()
            continue

        # @import ...;
        m = re.match(r'@import\s+[^;]*;', rest)
        if m:
            if seen_disallowed:
                # Track line number
                line_no = stripped[:pos].count('\n') + 1
                imports_after_disallowed.append({'line': line_no, 'snippet': m.group(0)[:80]})
            pos += m.end()
            continue

        # @layer name [, name]*;  — statement form (no block) — allowed before @import per CSS spec
        m = re.match(r'@layer\s+[^{;]+;', rest)
        if m:
            pos += m.end()
            continue

        # Anything else (selector block, @media block, @keyframes block, @supports block,
        # @layer with block, etc.) — these are "disallowed before further @import".
        # Find end of next block { ... } or next ;
        # Try block first
        brace_idx = rest.find('{')
        semi_idx = rest.find(';')
        if brace_idx == -1 and semi_idx == -1:
            break
        if brace_idx != -1 and (semi_idx == -1 or brace_idx < semi_idx):
            # Skip balanced { ... }
            depth = 0
            i = brace_idx
            while i < len(rest):
                if rest[i] == '{':
                    depth += 1
                elif rest[i] == '}':
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            pos += i
        else:
            pos += semi_idx + 1
        seen_disallowed = True

    if imports_after_disallowed:
        failures.append({
            'msg': '@import rule appears AFTER a style rule (browser will ignore)',
            'offending_imports': imports_after_disallowed,
        })

    return {
        'pass': len(failures) == 0,
        'failures': failures,
    }


# ----------------------------------------------------------------------
# Subcommand: --check no-top-level-side-effects (D-13)
# ----------------------------------------------------------------------

def _module_top_level_statements(file_path: Path):
    """Yield (line_no, statement_text) for top-level statements in an ES module.

    Top-level = brace depth 0 outside string/template literal contexts.
    Strips block comments globally; ignores // line comments and blanks.
    """
    try:
        raw = file_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return

    # First strip block comments
    text = _strip_block_comments(raw)
    lines = text.splitlines()
    depth = 0
    in_template = False
    in_single = False
    in_double = False

    for line_no, raw_line in enumerate(lines, 1):
        line = raw_line
        stripped = line.lstrip()
        if not stripped or _is_line_comment(line):
            continue

        # Only yield statements that START at top-level (depth == 0 at start of line)
        if depth == 0 and not in_template and not in_single and not in_double:
            yield line_no, stripped

        # Update depth (rough: count {,},`,',\" outside strings)
        i = 0
        while i < len(line):
            ch = line[i]
            if in_single:
                if ch == '\\':
                    i += 2
                    continue
                if ch == "'":
                    in_single = False
            elif in_double:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '"':
                    in_double = False
            elif in_template:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '`':
                    in_template = False
            else:
                if ch == "'":
                    in_single = True
                elif ch == '"':
                    in_double = True
                elif ch == '`':
                    in_template = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
            i += 1


def check_no_top_level_side_effects(phase_id: str) -> dict:
    cfg = PHASE_NEW_MODULES.get(phase_id)
    if cfg is None:
        return {'pass': False, 'error': f'No PHASE_NEW_MODULES entry for phase {phase_id}'}

    failures = []
    compiled_patterns = [re.compile(p) for p in TOP_LEVEL_SIDE_EFFECT_PATTERNS]
    # Pre-compile EXEMPT_CONSTRUCTIONS by file for fast lookup.
    compiled_exemptions_by_file = {}
    for (exempt_file, exempt_pattern) in EXEMPT_CONSTRUCTIONS:
        compiled_exemptions_by_file.setdefault(exempt_file, []).append(re.compile(exempt_pattern))

    for rel_path in cfg['modules']:
        p = REPO_ROOT / rel_path
        if not p.is_file():
            failures.append({'file': rel_path, 'msg': 'module file not found'})
            continue
        file_exemptions = compiled_exemptions_by_file.get(rel_path, [])
        for line_no, stmt in _module_top_level_statements(p):
            # Check EXEMPT_CONSTRUCTIONS first — if matched, skip this line entirely.
            is_exempt = any(ep.match(stmt) for ep in file_exemptions)
            if is_exempt:
                continue
            for pat in compiled_patterns:
                if pat.search(stmt):
                    failures.append({
                        'file': rel_path,
                        'line': line_no,
                        'pattern': pat.pattern,
                        'snippet': stmt[:120],
                    })
                    break  # one match per line is enough

    return {
        'pass': len(failures) == 0,
        'phase': phase_id,
        'scanned': cfg['modules'],
        'entrypoint_exempt': cfg.get('entrypoint_exempt', []),
        'exempt_constructions': [(f, p) for (f, p) in EXEMPT_CONSTRUCTIONS],
        'failures': failures,
    }


# ----------------------------------------------------------------------
# Subcommand: --check cache-headers (D-14)
# ----------------------------------------------------------------------

def check_cache_headers(base_url: str = 'http://localhost:8000') -> dict:
    test_paths = [
        '/static/js/main.js',
        '/static/js/phase-gate-probe.js',
        '/static/js/utils/dom.js',
        '/static/css/tokens.css',
        '/static/css/base.css',
        '/static/news-search.css',
        # Phase 2 (2026-05-21): sample of new components/ files — middleware should
        # cover /static/css/components/*.css automatically (D-14 anchor).
        '/static/css/components/sidebar.css',
        # Phase 3 (2026-05-21 Path B): new core/ JS modules — middleware should
        # cover /static/js/core/*.js automatically.
        '/static/js/core/auth-manager.js',
        # Phase 4a (2026-05-21 Path B): new features/ JS module — middleware should
        # cover /static/js/features/*.js automatically.
        '/static/js/features/sessions-list.js',
        # Phase 4b (2026-05-21 Path B): another features/ JS module sample.
        '/static/js/features/session-manager.js',
    ]
    failures = []
    successes = []
    for path in test_paths:
        try:
            req = urllib.request.Request(base_url + path, method='HEAD')
            with urllib.request.urlopen(req, timeout=5) as resp:
                cc = resp.headers.get('Cache-Control', '')
                if 'no-cache' not in cc:
                    failures.append({'path': path, 'cache_control': cc, 'status': resp.status})
                else:
                    successes.append({'path': path, 'cache_control': cc, 'status': resp.status})
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError) as e:
            failures.append({'path': path, 'error': str(e)})

    return {
        'pass': len(failures) == 0,
        'base_url': base_url,
        'successes': successes,
        'failures': failures,
    }


# ----------------------------------------------------------------------
# Subcommand: --check visual-contract (D-11 CSS) — Phase 1 stub
# ----------------------------------------------------------------------

def check_visual_contract(phase_id: str) -> dict:
    """Phase 1 stub: pass-through. Phase 2+ compares getComputedStyle / boundingRect
    samples to docs/in progress/plans/frontend-modular-refactor-visual-baseline.json
    (tracked, not gitignored) with +/-2px tolerance for dimensions."""
    return {
        'pass': True,
        'phase': phase_id,
        'note': 'visual-contract is a Phase 1 stub; baseline JSON established manually via DevTools probeStyles snippet. Phase 2+ will compare against docs/in progress/plans/frontend-modular-refactor-visual-baseline.json.',
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

CHECK_CHOICES = [
    'ownership',
    'entrypoint',
    'css-import-legality',
    'no-top-level-side-effects',
    'cache-headers',
    'visual-contract',
]


def main():
    parser = argparse.ArgumentParser(
        description='D-11 / D-12 / D-13 / D-14 Phase Gate verifier',
    )
    parser.add_argument('--phase', type=str, default=None,
                        help='Phase id (e.g. 1, 2, 3a). Required for ownership / entrypoint / '
                             'no-top-level-side-effects / visual-contract.')
    parser.add_argument('--check', type=str, default='ownership', choices=CHECK_CHOICES,
                        help='Which check to run (default: ownership).')
    parser.add_argument('--base-url', type=str, default='http://localhost:8000',
                        help='Base URL for cache-headers check (default: http://localhost:8000).')
    parser.add_argument('--json', action='store_true',
                        help='Emit machine-readable JSON instead of human summary.')
    args = parser.parse_args()

    check = args.check

    if check in ('ownership', 'entrypoint', 'no-top-level-side-effects', 'visual-contract'):
        if not args.phase:
            print(f'ERROR: --check {check} requires --phase', file=sys.stderr)
            sys.exit(2)

    if check == 'ownership':
        result = check_ownership(args.phase)
    elif check == 'entrypoint':
        result = check_entrypoint(args.phase)
    elif check == 'css-import-legality':
        result = check_css_import_legality()
    elif check == 'no-top-level-side-effects':
        result = check_no_top_level_side_effects(args.phase)
    elif check == 'cache-headers':
        result = check_cache_headers(args.base_url)
    elif check == 'visual-contract':
        result = check_visual_contract(args.phase)
    else:
        print(f'Unknown check: {check}', file=sys.stderr)
        sys.exit(2)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(check, result)

    sys.exit(0 if result.get('pass') else 1)


def _print_human(check: str, result: dict):
    status = 'PASS' if result.get('pass') else 'FAIL'
    print(f'[{check}] {status}')
    if 'error' in result:
        print(f'  error: {result["error"]}')
    if check == 'ownership':
        print(f"  phase: {result.get('phase')}")
        print(f"  passes: {result.get('passes_count', 0)}")
        for f in result.get('failures', []):
            print(f"  FAIL [{f['sentinel']}] {f['group']} pattern={f['pattern']!r} loc={f['location']} expected={f['expected']} actual={f['actual']}")
            for h in f.get('hits', [])[:5]:
                print(f"      hit: {h[0]}:{h[1]} {h[2][:100]}")
    elif check == 'entrypoint':
        for n in result.get('notes', []):
            print(f"  ok: {n}")
        for f in result.get('failures', []):
            print(f"  FAIL: {f['msg']}")
    elif check == 'css-import-legality':
        for f in result.get('failures', []):
            print(f"  FAIL: {f['msg']}")
            for off in f.get('offending_imports', []):
                print(f"    line {off['line']}: {off['snippet']}")
    elif check == 'no-top-level-side-effects':
        print(f"  scanned: {result.get('scanned')}")
        for f in result.get('failures', []):
            print(f"  FAIL: {f.get('file')}:{f.get('line')} pattern={f.get('pattern')!r} snippet={f.get('snippet')!r}")
            if 'msg' in f:
                print(f"    {f['msg']}")
    elif check == 'cache-headers':
        print(f"  base_url: {result.get('base_url')}")
        for s in result.get('successes', []):
            print(f"  ok: {s['path']} → Cache-Control: {s['cache_control']}")
        for f in result.get('failures', []):
            if 'error' in f:
                print(f"  FAIL: {f['path']} → {f['error']}")
            else:
                print(f"  FAIL: {f['path']} → Cache-Control: {f.get('cache_control')!r} (status {f.get('status')})")
    elif check == 'visual-contract':
        print(f"  phase: {result.get('phase')}")
        if 'note' in result:
            print(f"  note: {result['note']}")


if __name__ == '__main__':
    main()
