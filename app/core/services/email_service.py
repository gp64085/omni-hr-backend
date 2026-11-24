import email.mime.multipart
import email.mime.text
import logging
import smtplib

import anyio

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self):
        self._environment = settings.ENVIRONMENT.lower().strip()
        self._email_transport = settings.EMAIL_TRANSPORT.lower().strip()
        self._smtp_host = settings.SMTP_HOST
        self._smtp_port = settings.SMTP_PORT
        self._smtp_user = settings.SMTP_USER
        self._smtp_password = settings.SMTP_PASSWORD
        self._smtp_tls = settings.SMTP_TLS
        self._from_email = settings.EMAILS_FROM_EMAIL
        self._from_name = settings.EMAILS_FROM_NAME

    async def send_password_reset_email(
        self, to_email: str, recipient_name: str, reset_url: str
    ) -> None:
        if self._email_transport == "smtp":
            if not (self._smtp_host and self._smtp_user):
                raise RuntimeError(
                    "SMTP email transport is selected but SMTP_HOST or SMTP_USER is not configured. "
                    "Incomplete SMTP configuration will not silently fall back to console transport."
                )
            await anyio.to_thread.run_sync(
                self._send_via_smtp, to_email, recipient_name, reset_url
            )
        elif self._email_transport == "console":
            self._deliver_console(to_email, recipient_name)
        elif self._email_transport == "none":
            logger.info(
                "Email transport is 'none'; skipping password reset email to %s",
                to_email,
            )
        else:
            raise ValueError(
                f"Unsupported EMAIL_TRANSPORT: '{self._email_transport}'. "
                "Must be one of: 'smtp', 'console', 'none'."
            )

    def _send_via_smtp(
        self, to_email: str, recipient_name: str, reset_url: str
    ) -> None:
        subject = "Reset Your OmniHR Password"
        msg = email.mime.multipart.MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self._from_name} <{self._from_email}>"
        msg["To"] = f"{recipient_name} <{to_email}>"

        text_content = f"""Hello {recipient_name},

A password reset request was submitted for your OmniHR account.

Reset your password by visiting this link:
{reset_url}

This link is valid for {settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutes and can only be used once.

If you did not request a password reset, you can safely ignore this email.
"""

        html_content = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 24px;">
  <div style="max-width: 560px; margin: 0 auto; background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 32px;">
    <h2 style="color: #818cf8; margin-top: 0;">OmniHR Password Reset</h2>
    <p style="color: #cbd5e1; font-size: 15px; line-height: 1.6;">Hello {recipient_name},</p>
    <p style="color: #cbd5e1; font-size: 15px; line-height: 1.6;">
      We received a request to reset your password for OmniHR Workspace. Click the button below to create a new password:
    </p>
    <div style="text-align: center; margin: 32px 0;">
      <a href="{reset_url}" style="background-color: #6366f1; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; display: inline-block;">
        Reset My Password
      </a>
    </div>
    <p style="color: #94a3b8; font-size: 13px; line-height: 1.5;">
      Or copy and paste this link into your browser:<br>
      <a href="{reset_url}" style="color: #818cf8; word-break: break-all;">{reset_url}</a>
    </p>
    <hr style="border: 0; border-top: 1px solid #334155; margin: 24px 0;">
    <p style="color: #64748b; font-size: 12px; margin-bottom: 0;">
      This link is valid for {settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutes. If you did not make this request, please ignore this email.
    </p>
  </div>
</body>
</html>"""

        msg.attach(email.mime.text.MIMEText(text_content, "plain"))
        msg.attach(email.mime.text.MIMEText(html_content, "html"))

        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as server:
                if self._smtp_tls:
                    server.starttls()
                if self._smtp_password:
                    server.login(self._smtp_user, self._smtp_password)
                server.send_message(msg)
                logger.info("Password reset email sent to %s via SMTP", to_email)
        except Exception as e:
            logger.error("Failed to send email via SMTP to %s: %s", to_email, e)
            raise

    def _deliver_console(self, to_email: str, recipient_name: str) -> None:
        if self._environment in ("production", "prod"):
            raise RuntimeError(
                "Console email transport is strictly prohibited in production environments. "
                "Configure a verified SMTP transport."
            )

        box = f"""
================================================================================
                           [DEV EMAIL TRANSPORT]
To: {to_email} ({recipient_name})
From: {self._from_name} <{self._from_email}>
Subject: Reset Your OmniHR Password
Status: Password reset email dispatched (out-of-band recovery token omitted for security)
Valid for: {settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutes
================================================================================
"""
        print(box)
        logger.info(
            "Password reset notification sent to %s via development console transport",
            to_email,
        )
