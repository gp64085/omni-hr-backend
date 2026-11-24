import pytest
from app.core.config import settings
from app.models.user import PasswordResetToken
from app.models.audit import AuditAction

def test_settings_and_model_attributes():
    assert hasattr(settings, "FRONTEND_URL")
    assert hasattr(settings, "PASSWORD_RESET_TOKEN_EXPIRE_MINUTES")
    assert hasattr(settings, "SMTP_HOST")
    assert hasattr(settings, "EMAILS_FROM_EMAIL")
    assert hasattr(PasswordResetToken, "token_hash")
    assert hasattr(PasswordResetToken, "is_used")
    assert hasattr(PasswordResetToken, "expires_at")
    assert "PASSWORD_RESET_REQUESTED" in AuditAction.__members__
    assert "PASSWORD_RESET_COMPLETED" in AuditAction.__members__


@pytest.mark.asyncio
async def test_email_service_console_fallback(capsys):
    from app.core.services.email_service import EmailService

    service = EmailService()
    await service.send_password_reset_email(
        to_email="test@company.com",
        recipient_name="John Doe",
        reset_url="http://localhost:3000/reset-password?token=mocktoken123",
    )
    captured = capsys.readouterr()
    assert "test@company.com" in captured.out
    assert "http://localhost:3000/reset-password?token=mocktoken123" in captured.out

