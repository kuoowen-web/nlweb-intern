"""
Contract test for frontend assertUserIdentity helper.

This is a documentation-style test that verifies the JS contract is
correctly described. It does NOT execute JS — it only ensures the
contract description in the source matches expected invariant.

NOTE (2026-07-13): assertUserIdentity + UserStateSyncError were refactored
out of the inline news-search.js definition into ES modules under
static/js/core/. The real definitions now live in state-sync.js; the
AuthManager.login flow moved to auth-manager.js. Assertions below read the
file where each symbol genuinely lives now (invariant preserved, source
file re-pointed to the live definition rather than dead commented code).
"""

import re
import unittest
from pathlib import Path

_STATIC_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "static"
STATE_SYNC_JS = _STATIC_ROOT / "js" / "core" / "state-sync.js"
AUTH_MANAGER_JS = _STATIC_ROOT / "js" / "core" / "auth-manager.js"


class AssertUserIdentityContractTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(STATE_SYNC_JS.exists(), f"Missing {STATE_SYNC_JS}")
        self.assertTrue(AUTH_MANAGER_JS.exists(), f"Missing {AUTH_MANAGER_JS}")
        self.src = STATE_SYNC_JS.read_text(encoding='utf-8')
        self.auth_src = AUTH_MANAGER_JS.read_text(encoding='utf-8')

    def test_helper_defined(self):
        self.assertIn("function assertUserIdentity", self.src,
                      "assertUserIdentity helper must be defined in state-sync.js")

    def test_returns_boolean_or_throws(self):
        # Contract: helper returns true if match, throws UserStateSyncError if mismatch.
        # Spot-check: name UserStateSyncError must appear.
        self.assertIn("UserStateSyncError", self.src,
                      "UserStateSyncError class must be defined for mismatch throw")

    def test_compares_user_id_field(self):
        # Helper must compare .id (not .email / .uuid / .userId — pin the contract)
        # Look for `cached.id` and `fresh.id` (or equivalent) in helper body.
        helper_match = re.search(
            r"function assertUserIdentity[^}]+\}", self.src, re.DOTALL
        )
        self.assertIsNotNone(helper_match, "assertUserIdentity function body must parse")
        body = helper_match.group(0)
        # Must reference .id (the canonical user identifier).
        self.assertIn(".id", body,
                      "assertUserIdentity must compare on .id field")

    def test_login_uses_run_init_sync(self):
        # Contract: AuthManager.login (success path) must call UserStateSync.runInitSync
        # before returning. AuthManager moved to auth-manager.js (Phase 3 Path B); the
        # live login method is a 4-space-indented class method there.
        login_match = re.search(
            r"async login\s*\([^)]*\)\s*\{(.*?)^\s{4}\}",
            self.auth_src,
            re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(login_match, "AuthManager.login method must be parsable")
        body = login_match.group(1)
        self.assertIn("UserStateSync.runInitSync", body,
                      "login() must call UserStateSync.runInitSync after auth success")


if __name__ == '__main__':
    unittest.main()
