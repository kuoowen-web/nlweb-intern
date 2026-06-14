"""
Contract test for frontend assertUserIdentity helper.

This is a documentation-style test that verifies the JS contract is
correctly described. It does NOT execute JS — it only ensures the
contract description in news-search.js matches expected invariant.
"""

import re
import unittest
from pathlib import Path

NEWS_SEARCH_JS = Path(__file__).resolve().parent.parent.parent.parent / "static" / "news-search.js"


class AssertUserIdentityContractTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(NEWS_SEARCH_JS.exists(), f"Missing {NEWS_SEARCH_JS}")
        self.src = NEWS_SEARCH_JS.read_text(encoding='utf-8')

    def test_helper_defined(self):
        self.assertIn("function assertUserIdentity", self.src,
                      "assertUserIdentity helper must be defined")

    def test_returns_boolean_or_throws(self):
        # Contract: helper returns true if match, throws UserStateSyncError if mismatch.
        # Spot-check: name UserStateSyncError must appear.
        self.assertIn("UserStateSyncError", self.src,
                      "UserStateSyncError class must be defined for mismatch throw")

    def test_compares_user_id_field(self):
        # Helper must compare .id (not .email / .uuid / .userId — pin the contract)
        # Look for `cached.id` and `jwt.id` (or equivalent) in helper body.
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
        # before returning. We grep for the call within the login method body.
        login_match = re.search(
            r"async login\s*\([^)]*\)\s*\{(.*?)^\s{12}\}",
            self.src,
            re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(login_match, "AuthManager.login method must be parsable")
        body = login_match.group(1)
        self.assertIn("UserStateSync.runInitSync", body,
                      "login() must call UserStateSync.runInitSync after auth success")


if __name__ == '__main__':
    unittest.main()
