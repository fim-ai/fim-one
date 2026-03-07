"""Reusable email sending utility for system emails (verification, etc.)."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _smtp_configured() -> bool:
    """Check whether SMTP env vars are set."""
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"))


def _send_email(to: str, subject: str, body_html: str) -> None:
    """Send an HTML email via SMTP. Raises on failure."""
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "465"))
    ssl_mode = os.getenv("SMTP_SSL", "ssl").lower()
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    from_addr = os.getenv("SMTP_FROM") or user
    from_name = os.getenv("SMTP_FROM_NAME", "")

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    if ssl_mode == "ssl":
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx) as server:
            server.login(user, password)
            server.sendmail(from_addr, [to], msg.as_string())
    elif ssl_mode == "tls":
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port) as server:
            server.starttls(context=ctx)
            server.login(user, password)
            server.sendmail(from_addr, [to], msg.as_string())
    else:
        with smtplib.SMTP(host, port) as server:
            server.login(user, password)
            server.sendmail(from_addr, [to], msg.as_string())


async def send_verification_email(
    to: str, code: str, purpose: str = "register", locale: str = "en"
) -> None:
    """Send a verification code email. Runs SMTP in a thread."""
    app_name = os.getenv("APP_NAME", "FIM Agent")

    # --- i18n strings ---
    if locale == "zh":
        subject = f"[{app_name}] 您的验证码"
        heading = "验证码"
        if purpose == "login":
            body_text = "请使用以下验证码登录您的账户。验证码将在 5 分钟后过期。"
        elif purpose == "reset_password":
            body_text = "请使用以下验证码重置您的密码。验证码将在 5 分钟后过期。"
        else:
            body_text = "请使用以下验证码验证您的邮箱地址。验证码将在 5 分钟后过期。"
        footer_warning = "如果您未请求此验证码，请忽略此邮件。"
    else:
        subject = f"[{app_name}] Your verification code"
        heading = "Verification Code"
        if purpose == "login":
            body_text = (
                "Use the following code to sign in to your account. "
                "This code will expire in 5 minutes."
            )
        elif purpose == "reset_password":
            body_text = (
                "Use the following code to reset your password. "
                "This code will expire in 5 minutes."
            )
        else:
            body_text = (
                "Use the following code to verify your email address. "
                "This code will expire in 5 minutes."
            )
        footer_warning = (
            "If you didn't request this code, you can safely ignore this email."
        )

    copyright_text = f"&copy; {datetime.now().year} {app_name}"

    body_html = f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#e8e4dd;-webkit-font-smoothing:antialiased;">
  <!--[if mso]><table width="480" align="center" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:480px;margin:40px auto;">
    <tr>
      <td>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-radius:16px;overflow:hidden;box-shadow:0 8px 32px rgba(30,27,24,0.12);">
          <!-- Header -->
          <tr>
            <td style="background:#1a1714;padding:32px 32px 24px;text-align:center;">
              <h1 style="margin:0 0 4px;color:#ffffff;font-size:20px;font-weight:700;letter-spacing:-0.2px;">{app_name}</h1>
              <p style="margin:0;color:#c49520;font-size:11px;text-transform:uppercase;letter-spacing:2.5px;font-weight:500;">{heading}</p>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="background:#ffffff;padding:40px 32px 36px;">
              <p style="margin:0 0 28px;color:#4b4540;font-size:15px;line-height:1.65;text-align:center;">{body_text}</p>
              <!-- Code pill -->
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:0 auto 28px;">
                <tr>
                  <td style="background:#faf6ee;border:1px solid #efe8d8;border-radius:12px;padding:16px 32px;">
                    <p style="margin:0;font-family:'Courier New',Courier,monospace;font-size:34px;font-weight:700;color:#8b6914;letter-spacing:10px;text-align:center;white-space:nowrap;">{code}</p>
                  </td>
                </tr>
              </table>
              <p style="margin:0;color:#9c9488;font-size:13px;line-height:1.5;text-align:center;">{footer_warning}</p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background:#f5f2eb;padding:16px 32px;text-align:center;">
              <p style="margin:0;color:#9c9488;font-size:11px;">{copyright_text}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
  <!--[if mso]></td></tr></table><![endif]-->
</body>
</html>"""

    await asyncio.to_thread(_send_email, to, subject, body_html)
    logger.info("Verification email sent to %s (purpose=%s, locale=%s)", to, purpose, locale)
