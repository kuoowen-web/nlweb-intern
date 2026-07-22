"""
Tests for auth/auth_service.py — registration, login, JWT, brute force, password reset.

Uses real SQLite (no mocks for DB). Each test gets a fresh DB via tmp_path fixture.
"""

import os
import time
import uuid
import asyncio
import hashlib

import bcrypt
import jwt
import pytest
import pytest_asyncio

os.environ['JWT_SECRET'] = 'test-secret-key-for-jwt-signing-1234'

from auth.auth_db import AuthDB
from auth.auth_service import (
    AuthService,
    ACCESS_TOKEN_EXPIRE_SECONDS,
    BRUTE_FORCE_MAX_ATTEMPTS,
    BRUTE_FORCE_WINDOW_SECONDS,
    JWT_ALGORITHM,
)

# Force SQLite mode: pop AFTER imports (load_dotenv in logger.py re-sets them)
os.environ.pop('DATABASE_URL', None)
os.environ.pop('ANALYTICS_DATABASE_URL', None)
os.environ.pop('POSTGRES_CONNECTION_STRING', None)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """
    Reset AuthDB singleton to a fresh SQLite file for every test.
    Also patches email_service to be a no-op so tests don't send mail.
    """
    db_path = str(tmp_path / "auth_test.db")

    # Reset singleton
    AuthDB._instance = None
    db = AuthDB(db_path=db_path)
    AuthDB._instance = db

    # Synchronous schema init (fine for tests)
    db._init_database_sync()
    db._initialized = True

    yield db

    # Cleanup singleton
    AuthDB._instance = None


@pytest.fixture
def service():
    return AuthService()


@pytest.fixture
def _no_email(monkeypatch):
    """Stub email_service functions so register/forgot_password don't fail."""
    import auth.email_service as es
    monkeypatch.setattr(es, 'send_verification_email', lambda *a, **kw: None)
    monkeypatch.setattr(es, 'send_password_reset_email', lambda *a, **kw: None)
    monkeypatch.setattr(es, 'send_lockout_notification', lambda *a, **kw: None)


async def _create_bootstrap_token(service: AuthService, org_hint: str = '') -> str:
    """Helper: create a bootstrap token and return the raw token string."""
    result = await service.create_bootstrap_token(org_name_hint=org_hint, expires_hours=1)
    return result['token']


async def _register_and_verify(service: AuthService,
                                email: str = "test@example.com",
                                password: str = "Password1",
                                name: str = "Test User") -> dict:
    """Helper: register a user (with bootstrap token) and mark email as verified. Returns user dict."""
    token = await _create_bootstrap_token(service)
    user = await service.register_user(email, password, name, bootstrap_token=token)
    # Directly verify
    db = AuthDB.get_instance()
    await db.execute(
        "UPDATE users SET email_verified = 1, email_verification_token = NULL WHERE id = ?",
        (user['id'],)
    )
    return user


async def _register_verify_and_login(service: AuthService,
                                      email: str = "test@example.com",
                                      password: str = "Password1",
                                      name: str = "Test User") -> dict:
    """Helper: register, verify, then login. Returns login result dict."""
    await _register_and_verify(service, email, password, name)
    return await service.login(email, password, ip="127.0.0.1")


# ── Registration Tests ───────────────────────────────────────────


class TestRegisterUser:

    @pytest.mark.asyncio
    async def test_register_success(self, service, _no_email):
        token = await _create_bootstrap_token(service)
        result = await service.register_user("alice@example.com", "Passw0rd", "Alice", bootstrap_token=token)
        assert result['email'] == "alice@example.com"
        assert result['name'] == "Alice"
        # B2B bootstrap: first admin is auto-verified
        assert result['email_verified'] is True
        assert 'id' in result

    @pytest.mark.asyncio
    async def test_register_normalizes_email(self, service, _no_email):
        token = await _create_bootstrap_token(service)
        result = await service.register_user("  Alice@Example.COM  ", "Passw0rd", "Alice", bootstrap_token=token)
        assert result['email'] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_register_without_token_fails(self, service, _no_email):
        """Register without bootstrap token should fail."""
        with pytest.raises(ValueError, match="Bootstrap token is required"):
            await service.register_user("a@b.com", "Passw0rd", "X")

    @pytest.mark.asyncio
    async def test_register_invalid_token_fails(self, service, _no_email):
        """Register with invalid bootstrap token should fail."""
        with pytest.raises(ValueError, match="Invalid bootstrap token"):
            await service.register_user("a@b.com", "Passw0rd", "X", bootstrap_token="bogus-token")

    @pytest.mark.asyncio
    async def test_register_used_token_fails(self, service, _no_email):
        """A bootstrap token can only be used once."""
        token = await _create_bootstrap_token(service)
        await service.register_user("first@example.com", "Passw0rd", "First", bootstrap_token=token)
        with pytest.raises(ValueError, match="already been used"):
            await service.register_user("second@example.com", "Passw0rd", "Second", bootstrap_token=token)

    @pytest.mark.asyncio
    async def test_register_expired_token_fails(self, service, _no_email):
        """Expired bootstrap token should fail."""
        result = await service.create_bootstrap_token(org_name_hint='', expires_hours=1)
        # Force expire it
        db = AuthDB.get_instance()
        await db.execute(
            "UPDATE bootstrap_tokens SET expires_at = ? WHERE token = ?",
            (time.time() - 1, result['token'])
        )
        with pytest.raises(ValueError, match="expired"):
            await service.register_user("a@b.com", "Passw0rd", "X", bootstrap_token=result['token'])

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, service, _no_email):
        # First register uses one token, second register with a new token but same email.
        token1 = await _create_bootstrap_token(service)
        await service.register_user("dup@example.com", "Passw0rd", "First", bootstrap_token=token1)
        token2 = await _create_bootstrap_token(service)
        with pytest.raises(ValueError, match="已被註冊"):
            await service.register_user("dup@example.com", "Passw0rd", "Second", bootstrap_token=token2)

    @pytest.mark.asyncio
    async def test_register_weak_password_too_short(self, service, _no_email):
        token = await _create_bootstrap_token(service)
        with pytest.raises(ValueError, match="至少須 8 個字元"):
            await service.register_user("a@b.com", "Ab1", "X", bootstrap_token=token)

    @pytest.mark.asyncio
    async def test_register_weak_password_no_uppercase(self, service, _no_email):
        token = await _create_bootstrap_token(service)
        with pytest.raises(ValueError, match="大寫字母"):
            await service.register_user("a@b.com", "password1", "X", bootstrap_token=token)

    @pytest.mark.asyncio
    async def test_register_weak_password_no_digit(self, service, _no_email):
        token = await _create_bootstrap_token(service)
        with pytest.raises(ValueError, match="數字"):
            await service.register_user("a@b.com", "Password", "X", bootstrap_token=token)


# ── Email Verification Tests ────────────────────────────────────


class TestVerifyEmail:

    @pytest.mark.asyncio
    async def test_verify_email_success(self, service, _no_email):
        bt = await _create_bootstrap_token(service)
        user = await service.register_user("v@e.com", "Passw0rd", "V", bootstrap_token=bt)
        # Grab the verification token from DB
        db = AuthDB.get_instance()
        row = await db.fetchone("SELECT email_verification_token FROM users WHERE id = ?", (user['id'],))
        token = row['email_verification_token']

        result = await service.verify_email(token)
        assert result['email_verified'] is True
        assert result['id'] == user['id']

    @pytest.mark.asyncio
    async def test_verify_email_invalid_token(self, service, _no_email):
        with pytest.raises(ValueError, match="驗證連結無效"):
            await service.verify_email("nonexistent-token")

    @pytest.mark.asyncio
    async def test_verify_email_token_consumed(self, service, _no_email):
        """After verification, the token is nulled — second call should fail."""
        bt = await _create_bootstrap_token(service)
        user = await service.register_user("v2@e.com", "Passw0rd", "V2", bootstrap_token=bt)
        db = AuthDB.get_instance()
        row = await db.fetchone("SELECT email_verification_token FROM users WHERE id = ?", (user['id'],))
        token = row['email_verification_token']

        await service.verify_email(token)
        with pytest.raises(ValueError, match="驗證連結無效"):
            await service.verify_email(token)


# ── Login Tests ──────────────────────────────────────────────────


class TestLogin:

    @pytest.mark.asyncio
    async def test_login_success(self, service, _no_email):
        result = await _register_verify_and_login(service)
        assert 'access_token' in result
        assert 'refresh_token' in result
        assert result['token_type'] == 'Bearer'
        assert result['expires_in'] == ACCESS_TOKEN_EXPIRE_SECONDS
        assert result['user']['email'] == "test@example.com"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, service, _no_email):
        await _register_and_verify(service)
        with pytest.raises(ValueError, match="電子郵件或密碼錯誤"):
            await service.login("test@example.com", "WrongPass1", ip="127.0.0.1")

    @pytest.mark.asyncio
    async def test_login_nonexistent_email(self, service, _no_email):
        with pytest.raises(ValueError, match="電子郵件或密碼錯誤"):
            await service.login("nobody@example.com", "Passw0rd", ip="127.0.0.1")

    @pytest.mark.asyncio
    async def test_login_unverified_email(self, service, _no_email):
        """User with password set but email_verified=False -> should fail with Email not verified."""
        # Bootstrap admin (auto-verified), then insert an unverified user directly.
        # This represents a user whose account was manually created without going through activation.
        bt = await _create_bootstrap_token(service)
        await service.register_user("admin@e.com", "Passw0rd", "Admin", bootstrap_token=bt)
        db = AuthDB.get_instance()
        password_hash = bcrypt.hashpw(b"Passw0rd", bcrypt.gensalt()).decode('utf-8')
        await db.execute(
            "INSERT INTO users (id, email, password_hash, name, email_verified, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), "unv@e.com", password_hash, "Unv", False, time.time())
        )
        with pytest.raises(ValueError, match="電子郵件尚未驗證"):
            await service.login("unv@e.com", "Passw0rd", ip="127.0.0.1")

    @pytest.mark.asyncio
    async def test_login_deactivated_account(self, service, _no_email):
        user = await _register_and_verify(service)
        db = AuthDB.get_instance()
        await db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user['id'],))
        # B2B: deactivated account returns a clear message so user doesn't keep retrying
        with pytest.raises(ValueError, match="帳號已停用"):
            await service.login("test@example.com", "Password1", ip="127.0.0.1")

    @pytest.mark.asyncio
    async def test_login_jwt_payload(self, service, _no_email):
        """Verify the JWT access token contains the expected claims."""
        result = await _register_verify_and_login(service)
        payload = jwt.decode(result['access_token'], os.environ['JWT_SECRET'], algorithms=[JWT_ALGORITHM])
        assert payload['user_id'] == result['user']['id']
        assert payload['email'] == "test@example.com"
        assert 'exp' in payload
        assert 'iat' in payload


# ── Token Refresh Tests ──────────────────────────────────────────


class TestRefreshToken:

    @pytest.mark.asyncio
    async def test_refresh_success(self, service, _no_email):
        login_result = await _register_verify_and_login(service)
        refresh_result = await service.refresh_token(login_result['refresh_token'])
        assert 'access_token' in refresh_result
        assert refresh_result['token_type'] == 'Bearer'
        assert refresh_result['expires_in'] == ACCESS_TOKEN_EXPIRE_SECONDS
        # Verify the refreshed token decodes correctly
        payload = jwt.decode(
            refresh_result['access_token'],
            os.environ['JWT_SECRET'],
            algorithms=[JWT_ALGORITHM],
        )
        assert payload['user_id'] == login_result['user']['id']

    @pytest.mark.asyncio
    async def test_refresh_invalid_token(self, service, _no_email):
        with pytest.raises(ValueError, match="Invalid refresh token"):
            await service.refresh_token("not-a-real-token")

    @pytest.mark.asyncio
    async def test_refresh_revoked_token(self, service, _no_email):
        login_result = await _register_verify_and_login(service)
        await service.logout(login_result['refresh_token'])
        with pytest.raises(ValueError, match="revoked"):
            await service.refresh_token(login_result['refresh_token'])

    @pytest.mark.asyncio
    async def test_refresh_expired_token(self, service, _no_email):
        login_result = await _register_verify_and_login(service)
        # Force the refresh token to be expired
        token_hash = hashlib.sha256(login_result['refresh_token'].encode('utf-8')).hexdigest()
        db = AuthDB.get_instance()
        await db.execute(
            "UPDATE refresh_tokens SET expires_at = ? WHERE token_hash = ?",
            (time.time() - 1, token_hash)
        )
        with pytest.raises(ValueError, match="expired"):
            await service.refresh_token(login_result['refresh_token'])


# ── Logout Tests ─────────────────────────────────────────────────


class TestLogout:

    @pytest.mark.asyncio
    async def test_logout_revokes_token(self, service, _no_email):
        login_result = await _register_verify_and_login(service)
        result = await service.logout(login_result['refresh_token'])
        assert result is True

        # Verify the token is actually revoked in DB
        token_hash = hashlib.sha256(login_result['refresh_token'].encode('utf-8')).hexdigest()
        db = AuthDB.get_instance()
        row = await db.fetchone("SELECT revoked_at FROM refresh_tokens WHERE token_hash = ?", (token_hash,))
        assert row is not None
        assert row['revoked_at'] is not None

    @pytest.mark.asyncio
    async def test_logout_nonexistent_token_no_error(self, service, _no_email):
        """Logout with a token that doesn't exist should not raise."""
        result = await service.logout("fake-token-value")
        assert result is True


# ── Forgot Password Tests ───────────────────────────────────────


class TestForgotPassword:

    @pytest.mark.asyncio
    async def test_forgot_password_existing_email(self, service, _no_email):
        user = await _register_and_verify(service)
        result = await service.forgot_password("test@example.com")
        assert result is True

        # Verify a reset token was stored
        db = AuthDB.get_instance()
        row = await db.fetchone("SELECT password_reset_token FROM users WHERE id = ?", (user['id'],))
        assert row['password_reset_token'] is not None

    @pytest.mark.asyncio
    async def test_forgot_password_nonexistent_email(self, service, _no_email):
        """Should return True even for non-existent email (no leak)."""
        result = await service.forgot_password("nobody@example.com")
        assert result is True

    @pytest.mark.asyncio
    async def test_reset_password_success(self, service, _no_email):
        user = await _register_and_verify(service)
        await service.forgot_password("test@example.com")

        db = AuthDB.get_instance()
        row = await db.fetchone("SELECT password_reset_token FROM users WHERE id = ?", (user['id'],))
        reset_token = row['password_reset_token']

        result = await service.reset_password(reset_token, "NewPassword1")
        assert result is True

        # Should be able to login with new password
        login_result = await service.login("test@example.com", "NewPassword1", ip="127.0.0.1")
        assert 'access_token' in login_result

    @pytest.mark.asyncio
    async def test_reset_password_invalid_token(self, service, _no_email):
        with pytest.raises(ValueError, match="重設密碼連結"):
            await service.reset_password("bogus-token", "NewPassword1")


# ── Brute Force Tests ───────────────────────────────────────────


class TestBruteForce:

    @pytest.mark.asyncio
    async def test_lockout_after_max_attempts(self, service, _no_email):
        """5 failed logins in 15 min should trigger lockout."""
        await _register_and_verify(service)

        for i in range(BRUTE_FORCE_MAX_ATTEMPTS):
            with pytest.raises(ValueError, match="電子郵件或密碼錯誤"):
                await service.login("test@example.com", "WrongPass1", ip="127.0.0.1")

        # Next attempt should get the lockout message
        with pytest.raises(ValueError, match="登入失敗次數過多"):
            await service.login("test@example.com", "Password1", ip="127.0.0.1")

    @pytest.mark.asyncio
    async def test_lockout_blocks_correct_password(self, service, _no_email):
        """Even the correct password should be blocked during lockout."""
        await _register_and_verify(service)

        for _ in range(BRUTE_FORCE_MAX_ATTEMPTS):
            with pytest.raises(ValueError, match="電子郵件或密碼錯誤"):
                await service.login("test@example.com", "Wrong1234", ip="127.0.0.1")

        with pytest.raises(ValueError, match="登入失敗次數過多"):
            await service.login("test@example.com", "Password1", ip="127.0.0.1")

    @pytest.mark.asyncio
    async def test_successful_login_after_fewer_than_max_failures(self, service, _no_email):
        """Under the threshold, login should still work with correct password."""
        await _register_and_verify(service)

        for _ in range(BRUTE_FORCE_MAX_ATTEMPTS - 1):
            with pytest.raises(ValueError, match="電子郵件或密碼錯誤"):
                await service.login("test@example.com", "Wrong1234", ip="127.0.0.1")

        # Should still be able to login
        result = await service.login("test@example.com", "Password1", ip="127.0.0.1")
        assert 'access_token' in result


# ── Change Password Tests ────────────────────────────────────────


class TestChangePassword:

    @pytest.mark.asyncio
    async def test_change_password_success(self, service, _no_email):
        login_result = await _register_verify_and_login(service)
        user_id = login_result['user']['id']
        result = await service.change_password(user_id, "Password1", "NewPassw0rd")
        assert result is True
        # Old password no longer works
        with pytest.raises(ValueError, match="電子郵件或密碼錯誤"):
            await service.login("test@example.com", "Password1", ip="127.0.0.1")
        # New password works
        new_login = await service.login("test@example.com", "NewPassw0rd", ip="127.0.0.1")
        assert 'access_token' in new_login

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, service, _no_email):
        login_result = await _register_verify_and_login(service)
        user_id = login_result['user']['id']
        with pytest.raises(ValueError, match="目前密碼不正確"):
            await service.change_password(user_id, "WrongPass1", "NewPassw0rd")

    @pytest.mark.asyncio
    async def test_change_password_weak_new_password(self, service, _no_email):
        login_result = await _register_verify_and_login(service)
        user_id = login_result['user']['id']
        with pytest.raises(ValueError, match="至少須 8 個字元"):
            await service.change_password(user_id, "Password1", "short")

    @pytest.mark.asyncio
    async def test_change_password_revokes_all_tokens(self, service, _no_email):
        """After change_password, old refresh tokens are revoked."""
        login_result = await _register_verify_and_login(service)
        user_id = login_result['user']['id']
        old_refresh = login_result['refresh_token']
        await service.change_password(user_id, "Password1", "NewPassw0rd")
        with pytest.raises(ValueError, match="revoked"):
            await service.refresh_token(old_refresh)


# ── Revoke All User Tokens Tests ────────────────────────────────


class TestRevokeAllUserTokens:

    @pytest.mark.asyncio
    async def test_revoke_all_tokens_success(self, service, _no_email):
        login_result = await _register_verify_and_login(service)
        user_id = login_result['user']['id']
        old_refresh = login_result['refresh_token']
        result = await service.revoke_all_user_tokens(user_id)
        assert result is True
        with pytest.raises(ValueError, match="revoked"):
            await service.refresh_token(old_refresh)

    @pytest.mark.asyncio
    async def test_revoke_all_tokens_multiple_sessions(self, service, _no_email):
        """Revokes tokens from multiple login sessions."""
        await _register_and_verify(service)
        r1 = await service.login("test@example.com", "Password1", ip="1.1.1.1")
        r2 = await service.login("test@example.com", "Password1", ip="2.2.2.2")
        user_id = r1['user']['id']
        await service.revoke_all_user_tokens(user_id)
        with pytest.raises(ValueError, match="revoked"):
            await service.refresh_token(r1['refresh_token'])
        with pytest.raises(ValueError, match="revoked"):
            await service.refresh_token(r2['refresh_token'])


# ── Set User Active Tests ────────────────────────────────────────


class TestSetUserActive:

    async def _setup_admin_and_member(self, service):
        """Helper: bootstrap admin, create a member. Returns (admin_id, org_id, member_id)."""
        bt = await _create_bootstrap_token(service)
        admin = await service.register_user("admin@e.com", "Passw0rd", "Admin", bootstrap_token=bt)
        db = AuthDB.get_instance()
        org = await db.fetchone("SELECT id FROM organizations LIMIT 1")
        org_id = org['id']
        member = await service.admin_create_user("member@e.com", "Member", "member", org_id, admin['id'])
        return admin['id'], org_id, member['id']

    @pytest.mark.asyncio
    async def test_deactivate_user(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        # Activate member first so they have a password
        db = AuthDB.get_instance()
        row = await db.fetchone("SELECT email_verification_token FROM users WHERE id = ?", (member_id,))
        await service.activate_account(row['email_verification_token'], "MemberPass1")

        result = await service.set_user_active(member_id, False, admin_id, org_id)
        assert result is True
        user = await db.fetchone("SELECT is_active FROM users WHERE id = ?", (member_id,))
        assert user['is_active'] in (0, False)

    @pytest.mark.asyncio
    async def test_deactivate_user_revokes_tokens(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        db = AuthDB.get_instance()
        row = await db.fetchone("SELECT email_verification_token FROM users WHERE id = ?", (member_id,))
        await service.activate_account(row['email_verification_token'], "MemberPass1")
        # Member logs in to get a token
        member_login = await service.login("member@e.com", "MemberPass1", ip="127.0.0.1")
        await service.set_user_active(member_id, False, admin_id, org_id)
        # Tokens are revoked first, so we get "revoked" (not "deactivated")
        with pytest.raises(ValueError, match="revoked"):
            await service.refresh_token(member_login['refresh_token'])

    @pytest.mark.asyncio
    async def test_reactivate_user(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        await service.set_user_active(member_id, False, admin_id, org_id)
        result = await service.set_user_active(member_id, True, admin_id, org_id)
        assert result is True

    @pytest.mark.asyncio
    async def test_cannot_deactivate_self(self, service, _no_email):
        admin_id, org_id, _ = await self._setup_admin_and_member(service)
        with pytest.raises(PermissionError, match="無法停用您自己的帳號"):
            await service.set_user_active(admin_id, False, admin_id, org_id)

    @pytest.mark.asyncio
    async def test_non_admin_cannot_deactivate(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        with pytest.raises(PermissionError, match="只有管理員"):
            await service.set_user_active(admin_id, False, member_id, org_id)


# ── Delete User Tests ────────────────────────────────────────────


class TestDeleteUser:

    async def _setup_admin_and_member(self, service):
        bt = await _create_bootstrap_token(service)
        admin = await service.register_user("admin@e.com", "Passw0rd", "Admin", bootstrap_token=bt)
        db = AuthDB.get_instance()
        org = await db.fetchone("SELECT id FROM organizations LIMIT 1")
        org_id = org['id']
        member = await service.admin_create_user("member@e.com", "Member", "member", org_id, admin['id'])
        return admin['id'], org_id, member['id']

    @pytest.mark.asyncio
    async def test_delete_user_success(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        result = await service.delete_user(member_id, admin_id, org_id)
        assert result is True
        db = AuthDB.get_instance()
        # Hard delete: user record should no longer exist
        user = await db.fetchone("SELECT id FROM users WHERE id = ?", (member_id,))
        assert user is None

    @pytest.mark.asyncio
    async def test_delete_user_removes_membership(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        await service.delete_user(member_id, admin_id, org_id)
        db = AuthDB.get_instance()
        # Hard delete: membership record should no longer exist
        membership = await db.fetchone(
            "SELECT id FROM org_memberships WHERE user_id = ? AND org_id = ?",
            (member_id, org_id)
        )
        assert membership is None

    @pytest.mark.asyncio
    async def test_delete_user_revokes_tokens(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        db = AuthDB.get_instance()
        row = await db.fetchone("SELECT email_verification_token FROM users WHERE id = ?", (member_id,))
        await service.activate_account(row['email_verification_token'], "MemberPass1")
        member_login = await service.login("member@e.com", "MemberPass1", ip="127.0.0.1")
        await service.delete_user(member_id, admin_id, org_id)
        # Hard delete removes refresh_tokens rows → "Invalid refresh token"
        with pytest.raises(ValueError):
            await service.refresh_token(member_login['refresh_token'])

    @pytest.mark.asyncio
    async def test_cannot_delete_self(self, service, _no_email):
        admin_id, org_id, _ = await self._setup_admin_and_member(service)
        with pytest.raises(PermissionError, match="無法刪除您自己的帳號"):
            await service.delete_user(admin_id, admin_id, org_id)

    @pytest.mark.asyncio
    async def test_non_admin_cannot_delete(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        with pytest.raises(PermissionError, match="只有管理員"):
            await service.delete_user(admin_id, member_id, org_id)


# ── Change Member Role Tests ─────────────────────────────────────


class TestChangeMemberRole:

    async def _setup_admin_and_member(self, service):
        bt = await _create_bootstrap_token(service)
        admin = await service.register_user("admin@e.com", "Passw0rd", "Admin", bootstrap_token=bt)
        db = AuthDB.get_instance()
        org = await db.fetchone("SELECT id FROM organizations LIMIT 1")
        org_id = org['id']
        member = await service.admin_create_user("member@e.com", "Member", "member", org_id, admin['id'])
        return admin['id'], org_id, member['id']

    @pytest.mark.asyncio
    async def test_promote_to_admin(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        result = await service.change_member_role(org_id, member_id, "admin", admin_id)
        assert result is True
        db = AuthDB.get_instance()
        row = await db.fetchone(
            "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ?",
            (member_id, org_id)
        )
        assert row['role'] == 'admin'

    @pytest.mark.asyncio
    async def test_demote_to_member(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        # Promote first
        await service.change_member_role(org_id, member_id, "admin", admin_id)
        # Then demote
        result = await service.change_member_role(org_id, member_id, "member", admin_id)
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_role(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        with pytest.raises(ValueError, match="role must be"):
            await service.change_member_role(org_id, member_id, "superuser", admin_id)

    @pytest.mark.asyncio
    async def test_cannot_change_own_role(self, service, _no_email):
        admin_id, org_id, _ = await self._setup_admin_and_member(service)
        with pytest.raises(PermissionError, match="無法變更您自己的角色"):
            await service.change_member_role(org_id, admin_id, "member", admin_id)

    @pytest.mark.asyncio
    async def test_non_admin_cannot_change_role(self, service, _no_email):
        admin_id, org_id, member_id = await self._setup_admin_and_member(service)
        with pytest.raises(PermissionError, match="只有管理員"):
            await service.change_member_role(org_id, admin_id, "member", member_id)


# ── Organization Tests ───────────────────────────────────────────


class TestOrganization:

    @pytest.mark.asyncio
    async def test_create_org(self, service, _no_email):
        """全新 user（無任何 membership）建 org 成功。

        D-2026-07-20 規則 3（一 email 一公司）後 org_memberships.user_id 有
        UNIQUE——register_user 已自動建 org，故這裡直接 INSERT 一個無
        membership 的裸 user 來驗 create_organization 本體。
        """
        db = AuthDB.get_instance()
        user_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO users (id, email, password_hash, name, email_verified, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (user_id, "fresh@example.com", "x", "Fresh User", time.time())
        )
        org = await service.create_organization("Test Org", user_id)
        assert org['name'] == "Test Org"
        assert 'id' in org
        assert org['slug'] == "test-org"

    @pytest.mark.asyncio
    async def test_create_org_existing_member_blocked_by_unique(self, service, _no_email):
        """既有 member 建第二 org 被 UNIQUE 擋（D-2026-07-20 一 email 一公司）。

        bootstrap admin 已有 org membership，再 create_organization 會在
        INSERT org_memberships 時撞 UNIQUE(user_id)——DB 層強制單組織。
        """
        import sqlite3
        user = await _register_and_verify(service)  # 已自動建 org + membership
        with pytest.raises(sqlite3.IntegrityError):
            await service.create_organization("Second Org", user['id'])

    @pytest.mark.asyncio
    async def test_list_user_orgs(self, service, _no_email):
        # B2B bootstrap: register_user automatically creates an org for the admin.
        user = await _register_and_verify(service)
        orgs = await service.list_user_orgs(user['id'])
        assert len(orgs) == 1
        # Auto-created org name follows the pattern "{name}'s organization"
        assert "Test User" in orgs[0]['name']
        assert orgs[0]['role'] == "admin"


class TestAcceptInvitationSingleOrg:
    """W-3 review 補修：既有 member 接受他 org 邀請必須明確拒絕且不燒邀請。

    背景：accept_invitation 原本先 UPDATE invitations SET accepted_at 再
    INSERT membership——org A 既有使用者接受 org B 邀請時 INSERT 撞
    UNIQUE(user_id) → 裸 IntegrityError（route 500），且邀請已被燒
    （accepted_at 非 NULL → 永久不可重試）。修法＝INSERT 前 pre-check 既有
    active membership → raise ValueError（routes 層既有 except ValueError
    → 400 模式自動接手）。
    """

    @pytest.mark.asyncio
    async def test_existing_member_gets_clear_error_and_invite_not_burned(
            self, service, _no_email, monkeypatch):
        import auth.email_service as es
        monkeypatch.setattr(es, 'send_invitation_email', lambda *a, **kw: None)

        # org A admin（既有 member，email=test@example.com）
        user_a = await _register_and_verify(service)
        # org B admin（獨立 bootstrap）
        user_b = await _register_and_verify(
            service, email="admin-b@example.com", name="Admin B")
        orgs_b = await service.list_user_orgs(user_b['id'])
        org_b_id = orgs_b[0]['id']

        # B 邀請 A 的 email 加入 org B
        inv = await service.invite_member(
            org_b_id, "test@example.com", "member", user_b['id'])

        # A 接受 → 必須明確 ValueError（單組織規則），不可裸 IntegrityError
        with pytest.raises(ValueError):
            await service.accept_invitation(inv['token'], user_a['id'])

        # 邀請不可被燒掉：accepted_at 仍 NULL
        db = AuthDB.get_instance()
        row = await db.fetchone(
            "SELECT accepted_at FROM invitations WHERE id = ?", (inv['id'],))
        assert row is not None
        assert row['accepted_at'] is None, "邀請被燒掉了（accepted_at 非 NULL）"

    @pytest.mark.asyncio
    async def test_removed_member_also_blocked_and_invite_not_burned(
            self, service, _no_email, monkeypatch):
        """R2 破口 (a)：removed membership row 仍佔 UNIQUE(user_id) 席位。

        remove_member 是 UPDATE status='removed' 留 row——pre-check 若只查
        status='active' 會放行，燒邀請後 INSERT 撞 UNIQUE → 500。守護面必須
        對齊 UNIQUE 涵蓋面（全 row，不分 status）。
        """
        import auth.email_service as es
        monkeypatch.setattr(es, 'send_invitation_email', lambda *a, **kw: None)
        monkeypatch.setattr(es, 'send_activation_email', lambda *a, **kw: None)

        # org A：admin A + member X（admin_create_user 建 active membership）
        admin_a = await _register_and_verify(service)
        orgs_a = await service.list_user_orgs(admin_a['id'])
        org_a_id = orgs_a[0]['id']
        user_x = await service.admin_create_user(
            "x@example.com", "User X", "member", org_a_id, admin_a['id'])

        # A 把 X 移出（status='removed'，row 留存仍佔 UNIQUE 席位）
        await service.remove_member(org_a_id, user_x['id'], admin_a['id'])

        # org B admin 邀請 X 的 email
        admin_b = await _register_and_verify(
            service, email="admin-b2@example.com", name="Admin B2")
        orgs_b = await service.list_user_orgs(admin_b['id'])
        inv = await service.invite_member(
            orgs_b[0]['id'], "x@example.com", "member", admin_b['id'])

        # X 接受 → 必須明確 ValueError（不可裸 IntegrityError 500）
        with pytest.raises(ValueError):
            await service.accept_invitation(inv['token'], user_x['id'])

        # 且邀請未被燒
        db = AuthDB.get_instance()
        row = await db.fetchone(
            "SELECT accepted_at FROM invitations WHERE id = ?", (inv['id'],))
        assert row is not None
        assert row['accepted_at'] is None, "邀請被燒掉了（accepted_at 非 NULL）"

    @pytest.mark.asyncio
    async def test_race_after_precheck_translated_and_invite_not_burned(
            self, service, _no_email, monkeypatch):
        """R2 破口 (b)：TOCTOU 後盾——pre-check 通過後席位被搶。

        模擬手法：攔截 pre-check 那條 fetchone（對 org_memberships 的
        user_id-only 存在性查詢回 None＝check 當下對手尚未寫入），其餘查詢走
        真 DB。user 實際已有 membership → INSERT 撞 UNIQUE。驗：例外被轉譯
        為 ValueError（非裸 IntegrityError）且邀請未被燒（INSERT 先於
        UPDATE invitations 的順序保證）。
        """
        import auth.email_service as es
        monkeypatch.setattr(es, 'send_invitation_email', lambda *a, **kw: None)

        # user A（org A 既有 member）+ org B admin 邀請 A 的 email
        user_a = await _register_and_verify(service)
        admin_b = await _register_and_verify(
            service, email="admin-b3@example.com", name="Admin B3")
        orgs_b = await service.list_user_orgs(admin_b['id'])
        inv = await service.invite_member(
            orgs_b[0]['id'], "test@example.com", "member", admin_b['id'])

        # 攔截 pre-check：org_memberships 的 user_id-only 查詢回 None
        db = AuthDB.get_instance()
        real_fetchone = db.fetchone

        async def racing_fetchone(query, params=None):
            if ('FROM org_memberships WHERE user_id' in query
                    and 'AND org_id' not in query):
                return None  # 模擬 pre-check 當下對手尚未寫入
            return await real_fetchone(query, params)

        monkeypatch.setattr(db, 'fetchone', racing_fetchone)

        # 直達 INSERT 撞 UNIQUE → 必須轉譯 ValueError
        with pytest.raises(ValueError):
            await service.accept_invitation(inv['token'], user_a['id'])

        # 還原後驗邀請未被燒
        monkeypatch.setattr(db, 'fetchone', real_fetchone)
        row = await db.fetchone(
            "SELECT accepted_at FROM invitations WHERE id = ?", (inv['id'],))
        assert row is not None
        assert row['accepted_at'] is None, "邀請被燒掉了（accepted_at 非 NULL）"


# ── List Org Members is_activated Tests ─────────────────────────


class TestListOrgMembersIsActivated:

    async def _setup_org_with_members(self, service):
        """Helper: bootstrap admin, create a member (unactivated). Returns (admin_id, org_id, member_id)."""
        bt = await _create_bootstrap_token(service)
        admin = await service.register_user("admin@e.com", "Passw0rd", "Admin", bootstrap_token=bt)
        db = AuthDB.get_instance()
        org = await db.fetchone("SELECT id FROM organizations LIMIT 1")
        org_id = org['id']
        member = await service.admin_create_user("member@e.com", "Member", "member", org_id, admin['id'])
        return admin['id'], org_id, member['id']

    @pytest.mark.asyncio
    async def test_list_org_members_includes_is_activated(self, service, _no_email):
        """list_org_members 必須回傳 is_activated 欄位"""
        admin_id, org_id, member_id = await self._setup_org_with_members(service)

        members = await service.list_org_members(org_id, admin_id)
        for m in members:
            assert 'is_activated' in m, f"is_activated missing from member: {m}"

        # 找出 member（未啟用 — password_hash IS NULL）
        member = next(m for m in members if m['id'] == member_id)
        assert member['is_activated'] is False

    @pytest.mark.asyncio
    async def test_list_org_members_activated_member(self, service, _no_email):
        """已啟用成員的 is_activated 應為 True"""
        admin_id, org_id, member_id = await self._setup_org_with_members(service)
        db = AuthDB.get_instance()

        # Activate member by setting password via activation token
        row = await db.fetchone(
            "SELECT email_verification_token FROM users WHERE id = ?", (member_id,)
        )
        await service.activate_account(row['email_verification_token'], "MemberPass1")

        members = await service.list_org_members(org_id, admin_id)
        member = next(m for m in members if m['id'] == member_id)
        assert member['is_activated'] is True


# ── Admin Resend Activation Tests ────────────────────────────────


class TestAdminResendActivation:

    async def _setup_org_with_members(self, service):
        """Helper: bootstrap admin + unactivated member. Returns (admin_id, org_id, member_id)."""
        bt = await _create_bootstrap_token(service)
        admin = await service.register_user("admin@e.com", "Passw0rd", "Admin", bootstrap_token=bt)
        db = AuthDB.get_instance()
        org = await db.fetchone("SELECT id FROM organizations LIMIT 1")
        org_id = org['id']
        member = await service.admin_create_user("member@e.com", "Member", "member", org_id, admin['id'])
        return admin['id'], org_id, member['id']

    @pytest.mark.asyncio
    async def test_admin_resend_activation_success(self, service, _no_email):
        """正常重寄：產新 token，舊 token 被覆蓋"""
        admin_id, org_id, member_id = await self._setup_org_with_members(service)
        db = AuthDB.get_instance()

        # 取得原始 token
        row = await db.fetchone(
            "SELECT email_verification_token FROM users WHERE id = ?", (member_id,)
        )
        old_token = row['email_verification_token']

        result = await service.admin_resend_activation(member_id, admin_id, org_id)
        assert result['success'] is True
        assert result['email'] == 'member@e.com'

        # 確認 token 已更新（不同）
        new_row = await db.fetchone(
            "SELECT email_verification_token FROM users WHERE id = ?", (member_id,)
        )
        new_token = new_row['email_verification_token']
        assert new_token != old_token
        assert new_token is not None

    @pytest.mark.asyncio
    async def test_admin_resend_activation_already_activated(self, service, _no_email):
        """已啟用用戶（password_hash IS NOT NULL）→ ValueError"""
        admin_id, org_id, member_id = await self._setup_org_with_members(service)
        db = AuthDB.get_instance()

        # Activate member
        row = await db.fetchone(
            "SELECT email_verification_token FROM users WHERE id = ?", (member_id,)
        )
        await service.activate_account(row['email_verification_token'], "MemberPass1")

        with pytest.raises(ValueError, match="已啟用"):
            await service.admin_resend_activation(member_id, admin_id, org_id)

    @pytest.mark.asyncio
    async def test_admin_resend_activation_wrong_org(self, service, _no_email):
        """不是該 org 的 admin → PermissionError"""
        admin_id, org_id, member_id = await self._setup_org_with_members(service)

        # Create second org + second admin
        bt2 = await _create_bootstrap_token(service)
        admin2 = await service.register_user("admin2@e.com", "Passw0rd2", "Admin2", bootstrap_token=bt2)
        db = AuthDB.get_instance()
        org2 = await db.fetchone(
            "SELECT id FROM organizations WHERE id != ? LIMIT 1", (org_id,)
        )

        with pytest.raises(PermissionError, match="只有管理員"):
            await service.admin_resend_activation(member_id, admin2['id'], org_id)

    @pytest.mark.asyncio
    async def test_admin_resend_activation_inactive_user(self, service, _no_email):
        """is_active=False 用戶 → ValueError"""
        admin_id, org_id, member_id = await self._setup_org_with_members(service)
        db = AuthDB.get_instance()

        # Deactivate the user directly in DB
        await db.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?", (member_id,)
        )

        with pytest.raises(ValueError, match="已停用"):
            await service.admin_resend_activation(member_id, admin_id, org_id)
