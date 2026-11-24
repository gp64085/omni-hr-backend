from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import Base, get_db
from app.main import app
from app.models.audit import AuditAction
from app.models.role import Role
from app.models.user import PasswordResetToken, RefreshToken, User, UserRole
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
    assert hasattr(settings, "ENVIRONMENT")
    assert hasattr(settings, "EMAIL_TRANSPORT")
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
async def test_email_service_console_transport_omits_tokens(capsys):
    from app.core.services.email_service import EmailService

    service = EmailService()
    service._email_transport = "console"
    service._environment = "development"
    raw_token = "mocktoken123_usable_secret"
    reset_url = f"http://localhost:3000/reset-password?token={raw_token}"

    await service.send_password_reset_email(
        to_email="test@company.com",
        recipient_name="John Doe",
        reset_url=reset_url,
    )
    captured = capsys.readouterr()
    assert "test@company.com" in captured.out
    # Security requirement: out-of-band recovery tokens must NOT appear in application output or logs
    assert raw_token not in captured.out
    assert reset_url not in captured.out
    assert "out-of-band recovery token omitted for security" in captured.out


@pytest.mark.asyncio
async def test_email_service_smtp_missing_config_raises_error():
    from app.core.services.email_service import EmailService

    service = EmailService()
    service._email_transport = "smtp"
    service._smtp_host = None

    with pytest.raises(RuntimeError, match="SMTP email transport is selected but"):
        await service.send_password_reset_email(
            to_email="test@company.com",
            recipient_name="John Doe",
            reset_url="http://localhost:3000/reset-password?token=secret",
        )


@pytest.mark.asyncio
async def test_email_service_console_transport_rejected_in_production():
    from app.core.services.email_service import EmailService

    service = EmailService()
    service._email_transport = "console"
    service._environment = "production"

    with pytest.raises(
        RuntimeError, match="Console email transport is strictly prohibited"
    ):
        await service.send_password_reset_email(
            to_email="test@company.com",
            recipient_name="John Doe",
            reset_url="http://localhost:3000/reset-password?token=secret",
        )


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


@pytest.mark.asyncio
async def test_forgot_password_generic_success_for_existing_user():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/auth/forgot-password",
            json={"email": "emp_recovery@omnihr.com"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert (
            body["data"]["message"]
            == "If an account with this email exists, a password reset link has been sent."
        )

        async with TestingSessionLocal() as session:
            repo = UserRepository(session)
            user = await repo.get_by_email("emp_recovery@omnihr.com")
            assert user is not None
            # Check token was created
            tokens_res = await session.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.is_used.is_(False),
                )
            )
            token_record = tokens_res.scalar_one_or_none()
            assert token_record is not None


@pytest.mark.asyncio
async def test_forgot_password_generic_success_for_non_existing_user():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/auth/forgot-password",
            json={"email": "nobody_exists@omnihr.com"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert (
            body["data"]["message"]
            == "If an account with this email exists, a password reset link has been sent."
        )


@pytest.mark.asyncio
async def test_forgot_password_delivery_failure_preserves_generic_response_and_rolls_back(
    monkeypatch, caplog
):
    import smtplib

    from app.core.services.email_service import EmailService

    async def mock_failing_send(
        self, to_email: str, recipient_name: str, reset_url: str
    ):
        raise smtplib.SMTPException("Simulated SMTP delivery failure")

    monkeypatch.setattr(EmailService, "send_password_reset_email", mock_failing_send)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/auth/forgot-password",
            json={"email": "emp_recovery@omnihr.com"},
        )
        # Anti-enumeration requirement: delivery failure must produce exact same 200 generic success response
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert (
            body["data"]["message"]
            == "If an account with this email exists, a password reset link has been sent."
        )

        # Database state: uncommitted reset work must be cleanly rolled back
        async with TestingSessionLocal() as session:
            repo = UserRepository(session)
            user = await repo.get_by_email("emp_recovery@omnihr.com")
            assert user is not None

            tokens_res = await session.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.is_used.is_(False),
                )
            )
            # Token was not committed due to savepoint rollback
            assert tokens_res.scalar_one_or_none() is None

        # Logging check: failure logged without the secret token or reset URL
        assert (
            "Password reset email delivery failed for user emp_recovery@omnihr.com"
            in caplog.text
        )
        assert "reset-password?token=" not in caplog.text


@pytest.mark.asyncio
async def test_reset_password_success():
    from app.core.services.token_service import TokenService

    raw_token = "test_raw_reset_token_secret_32chars"
    token_hash = TokenService.hash_token(raw_token)

    async with TestingSessionLocal() as session:
        repo = UserRepository(session)
        user = await repo.get_by_email("emp_recovery@omnihr.com")
        assert user is not None
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            is_used=False,
        )
        await repo.create_password_reset_token(reset_token)
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": "BrandNewPassword123!"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True

        # Verify old password fails
        login_fail = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_recovery@omnihr.com", "password": "EmpPass123!"},
        )
        assert login_fail.status_code == 401

        # Verify new password succeeds
        login_ok = await ac.post(
            "/api/v1/auth/login",
            json={
                "email": "emp_recovery@omnihr.com",
                "password": "BrandNewPassword123!",
            },
        )
        assert login_ok.status_code == 200


@pytest.mark.asyncio
async def test_reset_password_revokes_active_sessions():
    from app.core.services.token_service import TokenService

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # 1. Log in to get active refresh token
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_recovery@omnihr.com", "password": "EmpPass123!"},
        )
        assert login_res.status_code == 200
        old_refresh_token = login_res.json()["data"]["refresh_token"]

        # 2. Reset password
        raw_token = "session_revocation_test_token"
        token_hash = TokenService.hash_token(raw_token)
        async with TestingSessionLocal() as session:
            repo = UserRepository(session)
            user = await repo.get_by_email("emp_recovery@omnihr.com")
            reset_token = PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                is_used=False,
            )
            await repo.create_password_reset_token(reset_token)
            await session.commit()

        reset_res = await ac.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": "AfterSessionResetPass1!"},
        )
        assert reset_res.status_code == 200

        # 3. Old refresh token must be rejected
        refresh_res = await ac.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": old_refresh_token},
        )
        assert refresh_res.status_code == 401


@pytest.mark.asyncio
async def test_reset_password_expired_token_rejected():
    from app.core.services.token_service import TokenService

    raw_token = "expired_raw_token_xyz"
    token_hash = TokenService.hash_token(raw_token)

    async with TestingSessionLocal() as session:
        repo = UserRepository(session)
        user = await repo.get_by_email("emp_recovery@omnihr.com")
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),  # expired
            is_used=False,
        )
        await repo.create_password_reset_token(reset_token)
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": "ValidPassword123!"},
        )
        assert res.status_code == 400
        body = res.json()
        assert body["success"] is False
        assert "Invalid or expired reset token" in body["error"]["message"]


@pytest.mark.asyncio
async def test_reset_password_reused_token_rejected():
    from app.core.services.token_service import TokenService

    raw_token = "reused_raw_token_xyz"
    token_hash = TokenService.hash_token(raw_token)

    async with TestingSessionLocal() as session:
        repo = UserRepository(session)
        user = await repo.get_by_email("emp_recovery@omnihr.com")
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            is_used=False,
        )
        await repo.create_password_reset_token(reset_token)
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # First reset -> success
        res1 = await ac.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": "ValidPassword123!"},
        )
        assert res1.status_code == 200

        # Second reset with same token -> rejected
        res2 = await ac.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": "SecondPassword123!"},
        )
        assert res2.status_code == 400
        body = res2.json()
        assert body["success"] is False
        assert "Invalid or expired reset token" in body["error"]["message"]


@pytest.mark.asyncio
async def test_reset_password_short_password_rejected():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/auth/reset-password",
            json={"token": "sometoken", "new_password": "short"},
        )
        assert res.status_code == 422


@pytest.mark.asyncio
async def test_forgot_password_initiation_does_not_revoke_refresh_tokens():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # 1. Log in to get active refresh token
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_recovery@omnihr.com", "password": "EmpPass123!"},
        )
        assert login_res.status_code == 200
        active_refresh_token = login_res.json()["data"]["refresh_token"]

        # 2. Merely initiate a forgot password request
        forgot_res = await ac.post(
            "/api/v1/auth/forgot-password",
            json={"email": "emp_recovery@omnihr.com"},
        )
        assert forgot_res.status_code == 200

        # 3. Existing active refresh token MUST NOT be revoked by initiation
        refresh_res = await ac.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": active_refresh_token},
        )
        assert refresh_res.status_code == 200
        assert "access_token" in refresh_res.json()["data"]


@pytest.mark.asyncio
async def test_forgot_password_uniform_timing_independent_of_account_status():
    import time

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        min_duration = settings.PASSWORD_RESET_MIN_DURATION_SECONDS

        t0 = time.monotonic()
        res_existing = await ac.post(
            "/api/v1/auth/forgot-password",
            json={"email": "emp_recovery@omnihr.com"},
        )
        elapsed_existing = time.monotonic() - t0
        assert res_existing.status_code == 200
        assert elapsed_existing >= min_duration

        t0 = time.monotonic()
        res_unknown = await ac.post(
            "/api/v1/auth/forgot-password",
            json={"email": "non_existent_random_user@omnihr.com"},
        )
        elapsed_unknown = time.monotonic() - t0
        assert res_unknown.status_code == 200
        assert elapsed_unknown >= min_duration


@pytest.mark.asyncio
async def test_refresh_token_serialized_with_password_reset_revocation():
    import asyncio

    from app.core.services.token_service import TokenService

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # 1. Login to obtain a valid refresh token
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_recovery@omnihr.com", "password": "EmpPass123!"},
        )
        assert login_res.status_code == 200
        initial_refresh_token = login_res.json()["data"]["refresh_token"]

        # 2. Seed a valid password reset token
        raw_reset_token = "interleaved_reset_token_secret"
        reset_token_hash = TokenService.hash_token(raw_reset_token)

        async with TestingSessionLocal() as session:
            repo = UserRepository(session)
            user = await repo.get_by_email("emp_recovery@omnihr.com")
            assert user is not None
            pr_token = PasswordResetToken(
                user_id=user.id,
                token_hash=reset_token_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                is_used=False,
            )
            await repo.create_password_reset_token(pr_token)
            await session.commit()

        # 3. Interleave refresh and password reset concurrently for the same user
        refresh_task = ac.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": initial_refresh_token},
        )
        reset_task = ac.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_reset_token, "new_password": "InterleavedResetPass123!"},
        )

        results = await asyncio.gather(refresh_task, reset_task, return_exceptions=True)
        refresh_res, reset_res = results[0], results[1]

        # Reset password must succeed
        assert reset_res.status_code == 200

        # If refresh happened to return 200 (executed right before reset acquired lock),
        # the resulting new refresh token MUST have been revoked by the reset!
        if refresh_res.status_code == 200:
            new_refresh_token = refresh_res.json()["data"]["refresh_token"]
            # Attempting to use the new refresh token must be rejected with 401
            subsequent_refresh = await ac.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": new_refresh_token},
            )
            assert subsequent_refresh.status_code == 401
        else:
            # If refresh was serialized after reset, it was rejected directly with 401
            assert refresh_res.status_code == 401

        # Verify that in database NO unrevoked refresh tokens exist for the user
        async with TestingSessionLocal() as session:
            repo = UserRepository(session)
            user = await repo.get_by_email("emp_recovery@omnihr.com")
            active_tokens_query = await session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id,
                    RefreshToken.is_revoked.is_(False),
                )
            )
            unrevoked_tokens = active_tokens_query.scalars().all()
            assert len(unrevoked_tokens) == 0
