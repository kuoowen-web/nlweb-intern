"""
Authentication service: register, login, JWT, org management, brute force protection.

All methods are async — uses AuthDB async interface (no event-loop blocking).
"""

import os
import uuid
import time
import hashlib
import secrets
import asyncio
from typing import Optional, Dict, Any

import re

import bcrypt
import jwt
import sentry_sdk

from auth.auth_db import AuthDB
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("auth_service")

# JWT config — read lazily so load_dotenv() timing doesn't matter
def _get_jwt_secret() -> str:
    return os.environ.get('JWT_SECRET', '')

JWT_ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_SECONDS = 15 * 60        # 15 minutes
REFRESH_TOKEN_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 days

# Brute force config
BRUTE_FORCE_WINDOW_SECONDS = 15 * 60  # 15 minutes
BRUTE_FORCE_MAX_ATTEMPTS = 5

# Pre-computed dummy hash for constant-time login comparison (prevents user enumeration via timing)
_DUMMY_BCRYPT_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt()).decode('utf-8')


class AuthService:
    """Core authentication service (async)."""

    def __init__(self):
        self.db = AuthDB.get_instance()

    # ── Bootstrap Token Management ────────────────────────────────

    async def create_bootstrap_token(self, org_name_hint: str = '', expires_hours: int = 72) -> Dict[str, str]:
        """Create a bootstrap token for B2B customer onboarding.

        Returns dict with 'token' and 'url'.
        """
        token_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + expires_hours * 3600

        await self.db.execute(
            "INSERT INTO bootstrap_tokens (id, token, org_name_hint, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token_id, token, org_name_hint, now, expires_at)
        )

        from auth.email_service import BASE_URL
        url = f"{BASE_URL}/setup?token={token}"

        logger.info(f"Bootstrap token created (id={token_id}, org_hint={org_name_hint!r}, expires_hours={expires_hours})")
        return {'token': token, 'url': url}

    async def validate_bootstrap_token(self, token: str) -> Dict[str, Any]:
        """Validate a bootstrap token. Returns token row or raises ValueError."""
        row = await self.db.fetchone(
            "SELECT id, token, org_name_hint, created_at, expires_at, used_at, used_by_email "
            "FROM bootstrap_tokens WHERE token = ?",
            (token,)
        )
        if not row:
            raise ValueError("Invalid bootstrap token")
        if row['used_at'] is not None:
            raise ValueError("Bootstrap token has already been used")
        if row['expires_at'] < time.time():
            raise ValueError("Bootstrap token has expired")
        return row

    # ── Registration (Bootstrap via Token) ─────────────────────

    async def register_user(self, email: str, password: str, name: str,
                            org_name: str = '', bootstrap_token: str = '') -> Dict[str, Any]:
        """Register admin + org using a bootstrap token."""
        # B2B guard: require valid bootstrap token
        if not bootstrap_token:
            raise ValueError("Bootstrap token is required")
        token_row = await self.validate_bootstrap_token(bootstrap_token)

        email = email.strip().lower()
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            raise ValueError("Invalid email format")
        self._validate_password(password)

        existing = await self.db.fetchone("SELECT id FROM users WHERE email = ?", (email,))
        if existing:
            raise ValueError("Email already registered")

        user_id = str(uuid.uuid4())
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        verification_token = secrets.token_urlsafe(32)
        verification_expires = time.time() + 48 * 3600  # BP-3: 48h expiry
        now = time.time()

        await self.db.execute(
            "INSERT INTO users (id, email, password_hash, name, email_verification_token, "
            "email_verification_expires, email_verified, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, email, password_hash, name, verification_token, verification_expires, True, now)
        )

        # Create organization with admin as owner
        org_hint = token_row.get('org_name_hint', '')
        await self.create_organization(org_name or org_hint or f"{name}'s organization", user_id)

        # Mark bootstrap token as used
        await self.db.execute(
            "UPDATE bootstrap_tokens SET used_at = ?, used_by_email = ? WHERE token = ?",
            (time.time(), email, bootstrap_token)
        )

        # Bootstrap admin is auto-verified — no verification email needed
        logger.info(f"Bootstrap admin registered (auto-verified): {email} (id={user_id})")

        # Task 6 Backend Variant: auto-issue session tokens so the inline JS can
        # redirect straight into the authenticated app — eliminates the onboarding
        # leak window where the page sat on "前往登入" while old session cookies
        # remained in the browser. Look up the membership we just created via
        # create_organization (admin/active) to populate org_id + role claims.
        # Inlined here (not extracted to a helper) per plan D-5: we don't want
        # a cross login/register/activate helper.
        membership = await self.db.fetchone(
            "SELECT org_id, role FROM org_memberships "
            "WHERE user_id = ? AND status = 'active' LIMIT 1",
            (user_id,)
        )
        org_id_str = str(membership['org_id']) if membership else None
        role = membership['role'] if membership else None
        access_token = self._create_access_token(user_id, email, name, org_id_str, role)
        refresh_token = await self._create_refresh_token(user_id)

        return {
            'id': user_id,
            'email': email,
            'name': name,
            'email_verified': True,
            'access_token': access_token,
            'refresh_token': refresh_token,
        }

    # ── Admin User Creation (B2B) ─────────────────────────────────

    async def admin_create_user(self, email: str, name: str, role: str,
                                org_id: str, admin_user_id: str) -> Dict[str, Any]:
        """Admin creates a user in their org. Employee gets activation email."""
        email = email.strip().lower()
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            raise ValueError("Invalid email format")

        # Verify admin has permission
        membership = await self.db.fetchone(
            "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (admin_user_id, org_id)
        )
        if not membership or membership['role'] != 'admin':
            raise ValueError("Only admins can create users")

        # Check org member limit
        org = await self.db.fetchone("SELECT max_members, name FROM organizations WHERE id = ?", (org_id,))
        if not org:
            raise ValueError("Organization not found")

        member_count = await self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM org_memberships WHERE org_id = ? AND status = 'active'",
            (org_id,)
        )
        if member_count['cnt'] >= org['max_members']:
            raise ValueError("Organization member limit reached")

        existing = await self.db.fetchone("SELECT id FROM users WHERE email = ?", (email,))
        if existing:
            raise ValueError("Email already registered")

        user_id = str(uuid.uuid4())
        activation_token = secrets.token_urlsafe(32)
        activation_expires = time.time() + 48 * 3600  # 48h to activate
        now = time.time()

        # Create user without password (activation pending)
        await self.db.execute(
            "INSERT INTO users (id, email, password_hash, name, email_verification_token, "
            "email_verification_expires, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, email, None, name, activation_token, activation_expires, now)
        )

        # Create org membership
        membership_id = str(uuid.uuid4())
        if role not in ('admin', 'member'):
            role = 'member'
        await self.db.execute(
            "INSERT INTO org_memberships (id, user_id, org_id, role, invited_by, status, accepted_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (membership_id, user_id, org_id, role, admin_user_id, now)
        )

        # BP-4: Non-blocking activation email
        from auth.email_service import send_activation_email
        await asyncio.to_thread(send_activation_email, email, activation_token, name, org['name'])

        logger.info(f"Admin created user: {email} (id={user_id}) in org {org_id}")
        return {
            'id': user_id,
            'email': email,
            'name': name,
            'role': role,
            'activated': False,
        }

    # ── Account Activation (Employee sets password) ───────────────

    async def activate_account(self, token: str, password: str) -> Dict[str, Any]:
        """Employee activates account by setting password via activation token."""
        self._validate_password(password)

        # First check if token exists at all (including already-activated accounts)
        token_row = await self.db.fetchone(
            "SELECT id, email, name, email_verification_token, email_verification_expires, "
            "password_hash, is_active FROM users WHERE email_verification_token = ?",
            (token,)
        )
        if not token_row:
            raise ValueError("Invalid activation token")

        is_active = token_row['is_active']
        if self.db.db_type == 'sqlite':
            is_active = bool(is_active)
        if not is_active:
            raise ValueError("Account is deactivated. Please contact your administrator.")

        if token_row['password_hash'] is not None:
            logger.warning(f"Activation attempted on already-activated account: {token_row['email']}")
            raise ValueError("Account is already activated. Please log in.")

        user = token_row

        # BP-3: Check token expiry
        if user.get('email_verification_expires') and user['email_verification_expires'] < time.time():
            raise ValueError("Activation token expired. Please contact your administrator.")

        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        await self.db.execute(
            "UPDATE users SET password_hash = ?, email_verified = ?, "
            "email_verification_token = NULL, email_verification_expires = NULL WHERE id = ?",
            (password_hash, True, user['id'])
        )

        logger.info(f"Account activated: {user['email']}")

        # Task 6 Backend Variant: auto-issue session tokens (mirror of register_user
        # change above). Look up the existing membership (created when the admin
        # invited this employee via admin_create_user) to populate JWT claims.
        # Inlined per D-5 — no cross login/register/activate helper.
        user_id_str = str(user['id'])
        membership = await self.db.fetchone(
            "SELECT org_id, role FROM org_memberships "
            "WHERE user_id = ? AND status = 'active' LIMIT 1",
            (user_id_str,)
        )
        org_id_str = str(membership['org_id']) if membership else None
        role = membership['role'] if membership else None
        access_token = self._create_access_token(user_id_str, user['email'], user['name'], org_id_str, role)
        refresh_token = await self._create_refresh_token(user_id_str)

        return {
            'id': user_id_str,
            'email': user['email'],
            'name': user['name'],
            'activated': True,
            'access_token': access_token,
            'refresh_token': refresh_token,
        }

    # ── Email Verification (Bootstrap Admin) ──────────────────────

    async def verify_email(self, token: str) -> Dict[str, Any]:
        """Verify admin email with token. Returns user dict or raises ValueError."""
        user = await self.db.fetchone(
            "SELECT id, email, name, email_verification_expires FROM users "
            "WHERE email_verification_token = ? AND is_active = ? AND password_hash IS NOT NULL",
            (token, True)
        )
        if not user:
            raise ValueError("Invalid or expired verification token")

        # BP-3: Check token expiry (NULL expires = legacy token, still valid)
        if user.get('email_verification_expires') and user['email_verification_expires'] < time.time():
            raise ValueError("Verification token expired. Please request a new one.")

        await self.db.execute(
            "UPDATE users SET email_verified = ?, email_verification_token = NULL, "
            "email_verification_expires = NULL WHERE id = ?",
            (True, user['id'])
        )

        logger.info(f"Email verified: {user['email']}")
        return {'id': str(user['id']), 'email': user['email'], 'name': user['name'], 'email_verified': True}

    # ── Login ─────────────────────────────────────────────────────

    async def login(self, email: str, password: str, ip: str = None) -> Dict[str, Any]:
        """Authenticate user. Returns tokens or raises ValueError."""
        email = email.strip().lower()

        await self._check_brute_force(email, ip)

        user = await self.db.fetchone(
            "SELECT id, email, password_hash, name, email_verified, is_active FROM users WHERE email = ?",
            (email,)
        )

        # Constant-time comparison: always run bcrypt even if user not found (or not yet activated)
        hash_to_check = user['password_hash'] if user and user.get('password_hash') else _DUMMY_BCRYPT_HASH
        valid_password = bcrypt.checkpw(password.encode('utf-8'), hash_to_check.encode('utf-8'))
        if not user or not valid_password:
            await self._record_login_attempt(email, ip, success=False)
            raise ValueError("Invalid email or password")

        is_active = user['is_active']
        if self.db.db_type == 'sqlite':
            is_active = bool(is_active)
        if not is_active:
            await self._record_login_attempt(email, ip, success=False)
            raise ValueError("Account is deactivated. Please contact your administrator.")

        email_verified = user['email_verified']
        if self.db.db_type == 'sqlite':
            email_verified = bool(email_verified)
        if not email_verified:
            raise ValueError("Email not verified. Please check your email for a verification link.")

        await self._record_login_attempt(email, ip, success=True)

        now = time.time()
        await self.db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user['id']))

        # Get org membership
        membership = await self.db.fetchone(
            "SELECT org_id, role FROM org_memberships WHERE user_id = ? AND status = 'active' LIMIT 1",
            (user['id'],)
        )
        org_id = membership['org_id'] if membership else None
        role = membership['role'] if membership else None

        user_id_str = str(user['id'])
        org_id_str = str(org_id) if org_id else None
        access_token = self._create_access_token(user_id_str, user['email'], user['name'], org_id_str, role)
        refresh_token = await self._create_refresh_token(user_id_str)

        logger.info(f"User logged in: {email}")
        return {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'token_type': 'Bearer',
            'expires_in': ACCESS_TOKEN_EXPIRE_SECONDS,
            'user': {
                'id': str(user['id']),
                'email': user['email'],
                'name': user['name'],
                'org_id': str(org_id) if org_id else None,
                'role': role,
            }
        }

    # ── Token Refresh ─────────────────────────────────────────────

    async def refresh_token(self, token: str) -> Dict[str, Any]:
        """Refresh access token using refresh token. BP-2: rotates refresh token."""
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()

        row = await self.db.fetchone(
            "SELECT rt.id, rt.user_id, rt.expires_at, rt.revoked_at, "
            "u.email, u.name, u.is_active "
            "FROM refresh_tokens rt JOIN users u ON rt.user_id = u.id "
            "WHERE rt.token_hash = ?",
            (token_hash,)
        )

        if not row:
            raise ValueError("Invalid refresh token")
        if row['revoked_at'] is not None:
            raise ValueError("Refresh token has been revoked")
        if row['expires_at'] < time.time():
            raise ValueError("Refresh token expired")

        is_active = row['is_active']
        if self.db.db_type == 'sqlite':
            is_active = bool(is_active)
        if not is_active:
            raise ValueError("Account is deactivated")

        # BP-2: Revoke old refresh token
        await self.db.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE id = ?",
            (time.time(), row['id'])
        )

        membership = await self.db.fetchone(
            "SELECT org_id, role FROM org_memberships WHERE user_id = ? AND status = 'active' LIMIT 1",
            (row['user_id'],)
        )
        org_id = membership['org_id'] if membership else None
        role = membership['role'] if membership else None

        user_id_str = str(row['user_id'])
        org_id_str = str(org_id) if org_id else None
        access_token = self._create_access_token(user_id_str, row['email'], row['name'], org_id_str, role)

        # BP-2: Issue new refresh token
        new_refresh_token = await self._create_refresh_token(user_id_str)

        logger.info(f"Token refreshed for user: {row['email']}")
        return {
            'access_token': access_token,
            'refresh_token': new_refresh_token,
            'token_type': 'Bearer',
            'expires_in': ACCESS_TOKEN_EXPIRE_SECONDS,
        }

    # ── Logout ────────────────────────────────────────────────────

    async def logout(self, refresh_token_value: str) -> bool:
        """Revoke a refresh token."""
        token_hash = hashlib.sha256(refresh_token_value.encode('utf-8')).hexdigest()
        await self.db.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE token_hash = ?",
            (time.time(), token_hash)
        )
        logger.info("Refresh token revoked")
        return True

    # ── Password Reset ────────────────────────────────────────────

    async def forgot_password(self, email: str) -> bool:
        """Generate password reset token and send email. Always returns True (no email leak)."""
        email = email.strip().lower()

        user = await self.db.fetchone(
            "SELECT id, name FROM users WHERE email = ? AND is_active = ?", (email, True)
        )
        if not user:
            return True  # Don't reveal if email exists

        reset_token = secrets.token_urlsafe(32)
        expires = time.time() + 3600  # 1 hour

        await self.db.execute(
            "UPDATE users SET password_reset_token = ?, password_reset_expires = ? WHERE id = ?",
            (reset_token, expires, user['id'])
        )

        # BP-4: Non-blocking email
        from auth.email_service import send_password_reset_email
        await asyncio.to_thread(send_password_reset_email, email, reset_token, user['name'])

        logger.info(f"Password reset requested for: {email}")
        return True

    async def reset_password(self, token: str, new_password: str) -> bool:
        """Reset password using token."""
        self._validate_password(new_password)

        user = await self.db.fetchone(
            "SELECT id FROM users WHERE password_reset_token = ? AND password_reset_expires > ? AND is_active = ?",
            (token, time.time(), True)
        )
        if not user:
            raise ValueError("Invalid or expired reset token")

        password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        await self.db.execute(
            "UPDATE users SET password_hash = ?, password_reset_token = NULL, password_reset_expires = NULL WHERE id = ?",
            (password_hash, user['id'])
        )

        # Revoke all refresh tokens
        await self.db.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (time.time(), user['id'])
        )

        logger.info(f"Password reset completed for user: {user['id']}")
        return True

    # ── Change Password (Authenticated User) ──────────────────────

    async def change_password(self, user_id: str, current_password: str, new_password: str) -> bool:
        """Change password for authenticated user. Revokes all refresh tokens."""
        self._validate_password(new_password)

        user = await self.db.fetchone(
            "SELECT id, password_hash FROM users WHERE id = ? AND is_active = ?",
            (user_id, True)
        )
        if not user or not user.get('password_hash'):
            raise ValueError("User not found")

        valid = bcrypt.checkpw(current_password.encode('utf-8'), user['password_hash'].encode('utf-8'))
        if not valid:
            raise ValueError("Current password is incorrect")

        new_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        await self.db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, user_id)
        )

        # Revoke all refresh tokens (force re-login on all devices)
        await self.db.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (time.time(), user_id)
        )

        logger.info(f"Password changed for user: {user_id}")
        return True

    # ── Revoke All User Tokens ─────────────────────────────────────

    async def revoke_all_user_tokens(self, user_id: str) -> bool:
        """Revoke all active refresh tokens for a user (logout all devices)."""
        await self.db.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (time.time(), user_id)
        )
        logger.info(f"All tokens revoked for user: {user_id}")
        return True

    # ── Admin: Set User Active ─────────────────────────────────────

    async def set_user_active(self, target_user_id: str, is_active: bool,
                               admin_user_id: str, org_id: str) -> bool:
        """Admin activates or deactivates a user. Deactivation also revokes all tokens."""
        if target_user_id == admin_user_id:
            raise PermissionError("Cannot deactivate your own account")

        membership = await self.db.fetchone(
            "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (admin_user_id, org_id)
        )
        if not membership or membership['role'] != 'admin':
            raise PermissionError("Only admins can change user status")

        # Verify target is in same org
        target_membership = await self.db.fetchone(
            "SELECT id FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (target_user_id, org_id)
        )
        if not target_membership:
            raise ValueError("User not found in organization")

        active_val = True if is_active else False
        if self.db.db_type == 'sqlite':
            active_val = 1 if is_active else 0

        await self.db.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (active_val, target_user_id)
        )

        if not is_active:
            await self.revoke_all_user_tokens(target_user_id)

        logger.info(f"User {target_user_id} set is_active={is_active} by admin {admin_user_id}")
        return True

    # ── Admin: Delete User (Soft) ──────────────────────────────────

    async def delete_user(self, target_user_id: str, admin_user_id: str, org_id: str) -> bool:
        """Hard-delete a user: revoke tokens, clean up all associated data, delete record."""
        if target_user_id == admin_user_id:
            raise PermissionError("Cannot delete your own account")

        membership = await self.db.fetchone(
            "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (admin_user_id, org_id)
        )
        if not membership or membership['role'] != 'admin':
            raise PermissionError("Only admins can delete users")

        target_membership = await self.db.fetchone(
            "SELECT id FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (target_user_id, org_id)
        )
        if not target_membership:
            raise ValueError("User not found in organization")

        user = await self.db.fetchone("SELECT email FROM users WHERE id = ?", (target_user_id,))

        # 1. Delete login attempts
        await self.db.execute(
            "DELETE FROM login_attempts WHERE email = ?",
            (user['email'],)
        )

        # 2. Delete refresh tokens
        await self.db.execute(
            "DELETE FROM refresh_tokens WHERE user_id = ?",
            (target_user_id,)
        )

        # 3. Remove from org
        await self.db.execute(
            "DELETE FROM org_memberships WHERE user_id = ? AND org_id = ?",
            (target_user_id, org_id)
        )

        # 4. Nullify search sessions (in separate DB — ignore if table doesn't exist)
        try:
            await self.db.execute(
                "UPDATE search_sessions SET user_id = NULL WHERE user_id = ?",
                (target_user_id,)
            )
        except Exception as e:
            logger.warning(f"Failed to nullify search sessions during GDPR delete: {e}")
            sentry_sdk.capture_exception(e)

        # 5. Hard delete the user record
        await self.db.execute(
            "DELETE FROM users WHERE id = ?",
            (target_user_id,)
        )

        logger.info(f"User {target_user_id} hard-deleted by admin {admin_user_id} in org {org_id}")
        return True

    # ── Admin: Change Member Role ──────────────────────────────────

    async def change_member_role(self, org_id: str, target_user_id: str,
                                  new_role: str, admin_user_id: str) -> bool:
        """Change a member's role in an organization."""
        if target_user_id == admin_user_id:
            raise PermissionError("Cannot change your own role")

        if new_role not in ('admin', 'member'):
            raise ValueError("role must be 'admin' or 'member'")

        membership = await self.db.fetchone(
            "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (admin_user_id, org_id)
        )
        if not membership or membership['role'] != 'admin':
            raise PermissionError("Only admins can change member roles")

        target_membership = await self.db.fetchone(
            "SELECT id FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (target_user_id, org_id)
        )
        if not target_membership:
            raise ValueError("User not found in organization")

        await self.db.execute(
            "UPDATE org_memberships SET role = ? WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (new_role, target_user_id, org_id)
        )

        logger.info(f"User {target_user_id} role changed to {new_role} by admin {admin_user_id} in org {org_id}")
        return True

    # ── Organization ──────────────────────────────────────────────

    async def create_organization(self, name: str, admin_user_id: str) -> Dict[str, Any]:
        """Create organization and set creator as admin."""
        org_id = str(uuid.uuid4())
        slug = name.lower().replace(' ', '-').replace('.', '')[:50]
        now = time.time()

        existing = await self.db.fetchone("SELECT id FROM organizations WHERE slug = ?", (slug,))
        if existing:
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"

        await self.db.execute(
            "INSERT INTO organizations (id, name, slug, created_at) VALUES (?, ?, ?, ?)",
            (org_id, name, slug, now)
        )

        membership_id = str(uuid.uuid4())
        await self.db.execute(
            "INSERT INTO org_memberships (id, user_id, org_id, role, status, accepted_at) "
            "VALUES (?, ?, ?, 'admin', 'active', ?)",
            (membership_id, admin_user_id, org_id, now)
        )

        logger.info(f"Organization created: {name} (id={org_id}) by user {admin_user_id}")
        return {'id': org_id, 'name': name, 'slug': slug}

    async def invite_member(self, org_id: str, email: str, role: str, invited_by: str) -> Dict[str, Any]:
        """Create invitation for a member."""
        email = email.strip().lower()

        membership = await self.db.fetchone(
            "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (invited_by, org_id)
        )
        if not membership or membership['role'] != 'admin':
            raise ValueError("Only admins can invite members")

        org = await self.db.fetchone("SELECT max_members FROM organizations WHERE id = ?", (org_id,))
        if not org:
            raise ValueError("Organization not found")

        member_count_row = await self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM org_memberships WHERE org_id = ? AND status = 'active'",
            (org_id,)
        )
        if member_count_row['cnt'] >= org['max_members']:
            raise ValueError("Organization member limit reached")

        invitation_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + 7 * 24 * 3600

        await self.db.execute(
            "INSERT INTO invitations (id, org_id, email, role, invited_by, token, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (invitation_id, org_id, email, role, invited_by, token, expires_at, now)
        )

        org_row = await self.db.fetchone("SELECT name FROM organizations WHERE id = ?", (org_id,))
        inviter_row = await self.db.fetchone("SELECT name FROM users WHERE id = ?", (invited_by,))

        # BP-4: Non-blocking email
        from auth.email_service import send_invitation_email
        await asyncio.to_thread(
            send_invitation_email,
            email,
            org_row['name'] if org_row else 'Organization',
            inviter_row['name'] if inviter_row else 'Someone',
            token
        )

        logger.info(f"Invitation sent to {email} for org {org_id}")
        return {'id': invitation_id, 'token': token, 'email': email}

    async def accept_invitation(self, token: str, user_id: str) -> Dict[str, Any]:
        """Accept an invitation and create membership."""
        invitation = await self.db.fetchone(
            "SELECT id, org_id, email, role FROM invitations "
            "WHERE token = ? AND expires_at > ? AND accepted_at IS NULL",
            (token, time.time())
        )
        if not invitation:
            raise ValueError("Invalid or expired invitation")

        user = await self.db.fetchone("SELECT email FROM users WHERE id = ?", (user_id,))
        if not user or user['email'] != invitation['email']:
            raise ValueError("This invitation is for a different email address")

        now = time.time()

        await self.db.execute(
            "UPDATE invitations SET accepted_at = ? WHERE id = ?",
            (now, invitation['id'])
        )

        membership_id = str(uuid.uuid4())
        await self.db.execute(
            "INSERT INTO org_memberships (id, user_id, org_id, role, status, accepted_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (membership_id, user_id, invitation['org_id'], invitation['role'], now)
        )

        logger.info(f"Invitation accepted by user {user_id} for org {invitation['org_id']}")
        return {'org_id': invitation['org_id'], 'role': invitation['role']}

    async def list_user_orgs(self, user_id: str) -> list:
        """List organizations a user belongs to."""
        return await self.db.fetchall(
            "SELECT o.id, o.name, o.slug, o.plan, m.role "
            "FROM org_memberships m JOIN organizations o ON m.org_id = o.id "
            "WHERE m.user_id = ? AND m.status = 'active' AND o.is_active = ?",
            (user_id, True)
        )

    async def list_org_members(self, org_id: str, requester_user_id: str) -> list:
        """List members of an organization."""
        membership = await self.db.fetchone(
            "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (requester_user_id, org_id)
        )
        if not membership:
            raise ValueError("Not a member of this organization")

        rows = await self.db.fetchall(
            "SELECT u.id, u.email, u.name, u.is_active, m.role, m.accepted_at, "
            "(u.password_hash IS NOT NULL) as is_activated "
            "FROM org_memberships m JOIN users u ON m.user_id = u.id "
            "WHERE m.org_id = ? AND m.status = 'active'",
            (org_id,)
        )
        # Normalize boolean for SQLite/PG compatibility
        result = []
        for row in rows:
            r = dict(row)
            r['is_activated'] = bool(r.get('is_activated', False))
            r['is_active'] = bool(r.get('is_active', True))
            result.append(r)
        return result

    async def admin_resend_activation(
        self, user_id: str, admin_user_id: str, org_id: str
    ) -> Dict[str, Any]:
        """Admin resends activation email to an unactivated member.
        Invalidates old token by overwriting with a new one (same column).
        Raises PermissionError if admin lacks permission.
        Raises LookupError if target user not found in org.
        Raises ValueError if target is already activated or deactivated.
        """
        # 驗證 admin 是該 org 的 admin
        admin_membership = await self.db.fetchone(
            "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (admin_user_id, org_id)
        )
        if not admin_membership or admin_membership['role'] != 'admin':
            raise PermissionError("Only admins can resend activation")

        # 確認 target user 在同一個 org
        target_membership = await self.db.fetchone(
            "SELECT id FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (user_id, org_id)
        )
        if not target_membership:
            raise LookupError("User not found in organization")  # → 404

        # 取得 target user 資料
        user = await self.db.fetchone(
            "SELECT id, email, name, password_hash, is_active FROM users WHERE id = ?",
            (user_id,)
        )
        if not user:
            raise LookupError("User not found")  # → 404

        # Boolean 正規化（SQLite = 0/1, PG = bool）
        is_active = bool(user['is_active'])
        if not is_active:
            raise ValueError("Cannot resend activation to a deactivated account")

        # 已啟用（已設密碼）→ 拒絕
        if user['password_hash'] is not None:
            raise ValueError("User account is already activated")

        # 取得 org 名稱（email template 需要）
        org = await self.db.fetchone(
            "SELECT name FROM organizations WHERE id = ?", (org_id,)
        )
        if not org:
            raise ValueError("Organization not found")

        # 產新 token，覆蓋舊 token（舊 token 自動失效）
        new_token = secrets.token_urlsafe(32)
        new_expires = time.time() + 48 * 3600

        await self.db.execute(
            "UPDATE users SET email_verification_token = ?, email_verification_expires = ? WHERE id = ?",
            (new_token, new_expires, user_id)
        )

        # 非阻塞發送 email（重用既有 send_activation_email）
        from auth.email_service import send_activation_email
        await asyncio.to_thread(
            send_activation_email,
            user['email'], new_token, user['name'], org['name']
        )

        logger.info(
            f"Admin {admin_user_id} resent activation for user {user_id} in org {org_id}"
        )
        return {'success': True, 'email': user['email']}

    async def remove_member(self, org_id: str, target_user_id: str, requester_user_id: str) -> bool:
        """Remove a member from organization. Only admins can remove."""
        membership = await self.db.fetchone(
            "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (requester_user_id, org_id)
        )
        if not membership or membership['role'] != 'admin':
            raise ValueError("Only admins can remove members")

        if target_user_id == requester_user_id:
            raise ValueError("Cannot remove yourself from the organization")

        await self.db.execute(
            "UPDATE org_memberships SET status = 'removed' WHERE user_id = ? AND org_id = ? AND status = 'active'",
            (target_user_id, org_id)
        )

        logger.info(f"Member {target_user_id} removed from org {org_id}")
        return True

    # ── User Queries ──────────────────────────────────────────────

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID."""
        user = await self.db.fetchone(
            "SELECT id, email, name, email_verified, last_login, created_at FROM users WHERE id = ? AND is_active = ?",
            (user_id, True)
        )
        return user

    async def get_org_by_id(self, org_id: str) -> Optional[Dict[str, Any]]:
        """Get organization by ID. Used by /api/user/init composite endpoint."""
        org = await self.db.fetchone(
            "SELECT id, name, slug, plan, max_members, storage_quota_gb, created_at "
            "FROM organizations WHERE id = ?",
            (org_id,)
        )
        return org

    # ── Private Helpers ───────────────────────────────────────────

    def _validate_password(self, password: str):
        """Validate password strength."""
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in password):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in password):
            raise ValueError("Password must contain at least one digit")

    def _create_access_token(self, user_id: str, email: str, name: str,
                              org_id: Optional[str], role: Optional[str]) -> str:
        """Create JWT access token."""
        secret = _get_jwt_secret()
        if not secret:
            raise RuntimeError("JWT_SECRET environment variable is not set")

        now = time.time()
        payload = {
            'user_id': user_id,
            'email': email,
            'name': name,
            'org_id': org_id,
            'role': role,
            'iat': int(now),
            'exp': int(now + ACCESS_TOKEN_EXPIRE_SECONDS),
        }
        return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)

    async def _create_refresh_token(self, user_id: str) -> str:
        """Create refresh token, store hash in DB. Returns raw token."""
        raw_token = secrets.token_urlsafe(64)
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        token_id = str(uuid.uuid4())
        now = time.time()
        expires_at = now + REFRESH_TOKEN_EXPIRE_SECONDS

        await self.db.execute(
            "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token_id, user_id, token_hash, expires_at, now)
        )
        return raw_token

    async def _check_brute_force(self, email: str, ip: str = None):
        """Check for brute force attempts. Raises ValueError if locked."""
        cutoff = time.time() - BRUTE_FORCE_WINDOW_SECONDS
        row = await self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM login_attempts "
            "WHERE email = ? AND success = ? AND attempted_at > ?",
            (email, False, cutoff)
        )
        if row and row['cnt'] >= BRUTE_FORCE_MAX_ATTEMPTS:
            raise ValueError("Too many failed login attempts. Please try again in 15 minutes.")

    async def _record_login_attempt(self, email: str, ip: str = None, success: bool = False):
        """Record a login attempt. Sends lockout notification when threshold is first hit."""
        attempt_id = str(uuid.uuid4())
        success_val = 1 if success else 0
        if self.db.db_type == 'postgres':
            success_val = success

        await self.db.execute(
            "INSERT INTO login_attempts (id, email, ip_address, success, attempted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (attempt_id, email, ip, success_val, time.time())
        )

        # Send lockout notification exactly once — when failed attempts hit the threshold
        if not success:
            cutoff = time.time() - BRUTE_FORCE_WINDOW_SECONDS
            count_row = await self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM login_attempts "
                "WHERE email = ? AND attempted_at > ? AND success = ?",
                (email, cutoff, False)
            )
            if count_row and count_row['cnt'] == BRUTE_FORCE_MAX_ATTEMPTS:
                import asyncio
                from auth.email_service import send_lockout_notification
                asyncio.create_task(
                    asyncio.to_thread(send_lockout_notification, email, ip or 'unknown')
                )
                logger.warning(f"Account locked: {email} ({BRUTE_FORCE_MAX_ATTEMPTS} failed attempts)")
