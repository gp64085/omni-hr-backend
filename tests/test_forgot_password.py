import uuid
from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import Base, get_db
from app.main import app
from app.models.role import Role
from app.models.user import PasswordResetToken, RefreshToken, User, UserRole
from app.models.audit import AuditAction
from app.modules.users.repository import UserRepository

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


async def override_get_db():
    async with TestingSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with TestingSessionLocal() as session:
            emp_role = Role(
                name=UserRole.EMPLOYEE.value,
                description="Employee",
                is_system=True,
            )
            session.add(emp_role)
            await session.flush()

            emp = User(
                email="emp_recovery@omnihr.com",
                password_hash=get_password_hash("EmpPass123!"),
                first_name="Recovery",
                last_name="Tester",
                role_id=emp_role.id,
                is_active=True,
            )
            session.add(emp)
            await session.commit()

        yield
    finally:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        app.dependency_overrides.clear()


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


@pytest.mark.asyncio
async def test_user_repository_reset_and_refresh_token_ops():
    async with TestingSessionLocal() as session:
        repo = UserRepository(session)
        user = await repo.get_by_email("emp_recovery@omnihr.com")
        assert user is not None

        # 1. Test create_password_reset_token
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash="testhash12345",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            is_used=False,
        )
        saved_token = await repo.create_password_reset_token(reset_token)
        assert saved_token.id is not None

        # 2. Test get_password_reset_token
        found_token = await repo.get_password_reset_token("testhash12345")
        assert found_token is not None
        assert found_token.user_id == user.id
        assert not found_token.is_used

        # 3. Test invalidate_user_reset_tokens
        await repo.invalidate_user_reset_tokens(user.id)
        refreshed_token = await repo.get_password_reset_token("testhash12345")
        assert refreshed_token is not None
        assert refreshed_token.is_used is True

        # 4. Test revoke_all_refresh_tokens
        rt = RefreshToken(
            user_id=user.id,
            token_hash="refreshtokenhash123",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            is_revoked=False,
        )
        await repo.save_refresh_token(rt)
        unrevoked = await repo.get_refresh_token("refreshtokenhash123")
        assert unrevoked is not None

        await repo.revoke_all_refresh_tokens(user.id)
        revoked = await repo.get_refresh_token("refreshtokenhash123")
        assert revoked is None  # get_refresh_token only returns non-revoked
