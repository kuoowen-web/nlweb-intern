"""
Email service for auth system.

Dev mode: logs verification/reset/invitation URLs to console.
Production: sends via Resend API when RESEND_API_KEY is set.
"""

import os
from html import escape as _esc
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("email_service")

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
RESEND_FROM_EMAIL = os.environ.get('RESEND_FROM_EMAIL', 'noreply@localhost')
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:8000')


def _email_wrapper(title: str, body_html: str) -> str:
    """Wrap email body in branded template."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#FBF5E6;font-family:'Noto Sans TC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#FBF5E6;">
<tr><td align="center" style="padding:40px 20px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:480px;background:#FFFFFF;border-radius:10px;overflow:hidden;">
<tr><td style="padding:32px 32px 20px;text-align:center;">
<img src="{BASE_URL}/static/images/Leopard.png" alt="讀豹" width="64" height="64" style="display:block;margin:0 auto 12px;">
<h1 style="margin:0;font-size:20px;color:#2D3436;font-weight:700;">{title}</h1>
</td></tr>
<tr><td style="padding:0 32px 32px;color:#2D3436;font-size:15px;line-height:1.6;">
{body_html}
</td></tr>
<tr><td style="padding:0 32px 24px;text-align:center;font-size:12px;color:#B2BEC3;">
臺灣讀豹 — 可信新聞搜尋與分析
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def _email_button(url: str, label: str) -> str:
    """Generate a branded CTA button."""
    return (f'<table role="presentation" cellspacing="0" cellpadding="0" style="margin:20px auto;">'
            f'<tr><td style="background:#FDCB6E;border-radius:8px;padding:12px 32px;">'
            f'<a href="{url}" style="color:#2D3436;text-decoration:none;font-weight:600;font-size:15px;">{label}</a>'
            f'</td></tr></table>')


def _send_via_resend(to: str, subject: str, html: str):
    """Send email via Resend API."""
    try:
        import resend
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({
            "from": RESEND_FROM_EMAIL,
            "to": to,
            "subject": subject,
            "html": html,
        })
        logger.info(f"Email sent via Resend to: {to}")
    except Exception as e:
        logger.error(f"Failed to send email via Resend to {to}: {e}", exc_info=True)
        raise


def send_verification_email(email: str, token: str, name: str):
    """Send email verification link."""
    url = f"{BASE_URL}/api/auth/verify-email?token={token}"

    if RESEND_API_KEY:
        body = (
            f"<p>您好 {_esc(name)}，</p>"
            f"<p>請點擊下方按鈕驗證您的 Email 地址，完成帳號設定。</p>"
            f"{_email_button(url, '驗證 Email')}"
            f"<p style='font-size:13px;color:#B2BEC3;'>若您沒有申請此帳號，請忽略此封信件。</p>"
        )
        _send_via_resend(
            to=email,
            subject="驗證您的 Email",
            html=_email_wrapper("驗證您的 Email", body)
        )
    else:
        print(f"[DEV EMAIL] Verification email for {email}")
        print(f"[DEV EMAIL] Verification URL: {url}", flush=True)


def send_password_reset_email(email: str, token: str, name: str):
    """Send password reset link."""
    url = f"{BASE_URL}/api/auth/reset-password?token={token}"

    if RESEND_API_KEY:
        body = (
            f"<p>您好 {_esc(name)}，</p>"
            f"<p>我們收到您的密碼重設請求，請點擊下方按鈕重設密碼。</p>"
            f"<p>此連結有效期限為 <strong>1 小時</strong>，逾期需重新申請。</p>"
            f"{_email_button(url, '重設密碼')}"
            f"<p style='font-size:13px;color:#B2BEC3;'>若您沒有申請重設密碼，請忽略此封信件，您的帳號不會有任何變更。</p>"
        )
        _send_via_resend(
            to=email,
            subject="重設密碼",
            html=_email_wrapper("重設密碼", body)
        )
    else:
        print(f"[DEV EMAIL] Password reset email for {email}")
        print(f"[DEV EMAIL] Reset URL: {url}", flush=True)


def send_invitation_email(email: str, org_name: str, inviter_name: str, token: str):
    """Send organization invitation link."""
    url = f"{BASE_URL}/?invite={token}"

    if RESEND_API_KEY:
        body = (
            f"<p>您好，</p>"
            f"<p><strong>{_esc(inviter_name)}</strong> 邀請您加入 <strong>{_esc(org_name)}</strong>。</p>"
            f"<p>點擊下方按鈕接受邀請，開始使用臺灣讀豹新聞搜尋平台。</p>"
            f"{_email_button(url, '接受邀請')}"
            f"<p style='font-size:13px;color:#B2BEC3;'>若您不認識邀請者，請忽略此封信件。</p>"
        )
        _send_via_resend(
            to=email,
            subject=f"您被邀請加入 {org_name}",
            html=_email_wrapper(f"您被邀請加入 {_esc(org_name)}", body)
        )
    else:
        print(f"[DEV EMAIL] Invitation email for {email} to join {org_name}")
        print(f"[DEV EMAIL] Invitation URL: {url}", flush=True)


def send_activation_email(email: str, token: str, name: str, org_name: str):
    """Send account activation link (employee sets password)."""
    url = f"{BASE_URL}/api/auth/activate?token={token}"

    if RESEND_API_KEY:
        body = (
            f"<p>您好 {_esc(name)}，</p>"
            f"<p>管理員已在 <strong>{_esc(org_name)}</strong> 為您建立帳號。</p>"
            f"<p>請點擊下方按鈕設定密碼並啟用帳號。此連結有效期限為 <strong>48 小時</strong>。</p>"
            f"{_email_button(url, '設定密碼')}"
            f"<p style='font-size:13px;color:#B2BEC3;'>若您對此帳號有任何疑問，請聯繫您的管理員。</p>"
        )
        _send_via_resend(
            to=email,
            subject=f"啟用您的 {org_name} 帳號",
            html=_email_wrapper("啟用您的帳號", body)
        )
    else:
        print(f"[DEV EMAIL] Activation email for {email} (org: {org_name})")
        print(f"[DEV EMAIL] Activation URL: {url}", flush=True)


def send_lockout_notification(email: str, ip: str):
    """Notify user that their account has been temporarily locked due to failed login attempts."""
    masked_ip = ip[:ip.rfind('.')] + '.***' if '.' in ip else ip[:len(ip)//2] + '***'

    if RESEND_API_KEY:
        body = (
            f"<p>我們偵測到您的帳號發生多次登入失敗，為保護您的帳號安全，已暫時鎖定 <strong>15 分鐘</strong>。</p>"
            f"<table role='presentation' cellspacing='0' cellpadding='0' style='margin:16px 0;background:#FBF5E6;border-radius:6px;padding:12px 16px;width:100%;'>"
            f"<tr><td style='font-size:14px;color:#2D3436;'>"
            f"登入嘗試來源 IP：<code style='font-family:monospace;background:#FFEAA7;padding:2px 6px;border-radius:4px;'>{_esc(masked_ip)}</code>"
            f"</td></tr></table>"
            f"<p>若此操作並非您本人，建議您在鎖定期結束後立即重設密碼，並確認帳號安全。</p>"
            f"<p style='font-size:13px;color:#B2BEC3;'>若您確認是自己操作失誤，請等待 15 分鐘後重試即可。</p>"
        )
        _send_via_resend(
            to=email,
            subject="安全通知：帳號已暫時鎖定",
            html=_email_wrapper("安全通知：帳號已暫時鎖定", body)
        )
    else:
        print(f"[DEV EMAIL] Lockout notification for {email} (IP: {masked_ip})", flush=True)
