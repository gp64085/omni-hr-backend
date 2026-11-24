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
