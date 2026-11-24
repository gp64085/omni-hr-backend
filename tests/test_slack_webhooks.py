import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import get_password_hash
from app.db.session import Base, get_db
from app.main import app
from app.models.role import Role
from app.models.user import User
from app.modules.integrations.service import IntegrationService

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
async def setup_webhook_test_db():
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with TestingSessionLocal() as session:
            emp_role = Role(name="employee", description="Employee", permissions=[])
            session.add(emp_role)
            await session.flush()

            emp_user = User(
                email="john.doe@omnihr.com",
                password_hash=get_password_hash("EmpPass123!"),
                first_name="John",
                last_name="Doe",
                role_id=emp_role.id,
                is_active=True,
            )
            session.add(emp_user)
            await session.commit()

        yield
    finally:
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        finally:
            app.dependency_overrides.pop(get_db, None)


def sign_body(secret: str, timestamp: int, body: bytes) -> str:
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    computed = hmac.new(
        secret.encode("utf-8"), sig_basestring, hashlib.sha256
    ).hexdigest()
    return f"v0={computed}"


@pytest.mark.asyncio
async def test_slack_url_verification():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/integrations/slack/events",
            json={"type": "url_verification", "challenge": "challenge_token_12345"},
        )
        assert res.status_code == 200
        assert res.json() == {"challenge": "challenge_token_12345"}


@pytest.mark.asyncio
async def test_slack_events_invalid_signature(monkeypatch):
    async def mock_get_decrypted(self, platform):
        if platform == "slack":
            return {"bot_token": "xoxb-test", "signing_secret": "my_secret"}
        return None

    monkeypatch.setattr(
        IntegrationService, "get_decrypted_credentials", mock_get_decrypted
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        body = b'{"type":"event_callback"}'
        headers = {
            "x-slack-request-timestamp": str(int(time.time())),
            "x-slack-signature": "v0=invalid_sig",
        }
        res = await ac.post(
            "/api/v1/integrations/slack/events",
            content=body,
            headers=headers,
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_slack_events_user_not_found(monkeypatch):
    secret = "test_signing_secret"

    async def mock_get_decrypted(self, platform):
        if platform == "slack":
            return {"bot_token": "xoxb-test", "signing_secret": secret}
        return None

    monkeypatch.setattr(
        IntegrationService, "get_decrypted_credentials", mock_get_decrypted
    )

    from app.modules.integrations.adapters.slack.adapter import SlackMessagingAdapter

    monkeypatch.setattr(
        SlackMessagingAdapter,
        "resolve_user_email",
        AsyncMock(return_value="unknown@external.com"),
    )
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(SlackMessagingAdapter, "send_message", send_mock)

    now = int(time.time())
    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "channel_type": "im",
            "user": "U999",
            "text": "Hello",
            "channel": "D123",
            "ts": "123.456",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = sign_body(secret, now, body)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/integrations/slack/events",
            content=body,
            headers={"x-slack-request-timestamp": str(now), "x-slack-signature": sig},
        )
        assert res.status_code == 200
        assert res.json() == {"status": "user_not_found"}
        assert send_mock.called
        assert "No active OmniHR employee account" in send_mock.call_args[1]["text"]


@pytest.mark.asyncio
async def test_slack_interactions_cancel_submission(monkeypatch):
    secret = "test_signing_secret"

    async def mock_get_decrypted(self, platform):
        if platform == "slack":
            return {"bot_token": "xoxb-test", "signing_secret": secret}
        return None

    monkeypatch.setattr(
        IntegrationService, "get_decrypted_credentials", mock_get_decrypted
    )

    from app.modules.integrations.adapters.slack.adapter import SlackMessagingAdapter

    update_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(SlackMessagingAdapter, "update_message", update_mock)

    payload = {
        "type": "block_actions",
        "user": {"id": "U123"},
        "channel": {"id": "D123"},
        "container": {"message_ts": "123.456", "channel_id": "D123"},
        "actions": [
            {
                "action_id": "cancel_leave_submission",
                "value": json.dumps({"action": "cancel"}),
            }
        ],
    }
    raw_body = json.dumps(payload).encode("utf-8")
    now = int(time.time())
    sig = sign_body(secret, now, raw_body)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/integrations/slack/interactions",
            content=raw_body,
            headers={"x-slack-request-timestamp": str(now), "x-slack-signature": sig},
        )
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}
        assert update_mock.called
        assert (
            "Cancelled" in update_mock.call_args[1]["text"]
            or "cancelled" in update_mock.call_args[1]["text"].lower()
        )
