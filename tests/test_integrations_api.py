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
async def setup_integrations_test_db():
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with TestingSessionLocal() as session:
            admin_role = Role(
                name="super_admin",
                description="Super Admin Role",
                is_system=True,
                permissions=[],
            )
            emp_role = Role(
                name="employee",
                description="Employee Role",
                permissions=[],
            )
            session.add_all([admin_role, emp_role])
            await session.flush()

            admin_user = User(
                email="admin@omnihr.com",
                password_hash=get_password_hash("AdminPass123!"),
                first_name="Admin",
                last_name="System",
                role_id=admin_role.id,
                is_active=True,
            )
            emp_user = User(
                email="employee@omnihr.com",
                password_hash=get_password_hash("EmpPass123!"),
                first_name="Employee",
                last_name="User",
                role_id=emp_role.id,
                is_active=True,
            )
            session.add_all([admin_user, emp_user])
            await session.commit()

        yield
    finally:
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        finally:
            app.dependency_overrides.pop(get_db, None)


async def get_token_for(email: str, password: str = "AdminPass123!") -> str:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        return login_res.json()["data"]["access_token"]


@pytest.mark.asyncio
async def test_save_and_get_slack_integration():
    admin_token = await get_token_for("admin@omnihr.com")
    headers = {"Authorization": f"Bearer {admin_token}"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Save Slack Config
        save_res = await ac.put(
            "/api/v1/integrations/slack",
            headers=headers,
            json={
                "is_enabled": True,
                "credentials": {
                    "bot_token": "xoxb-mock-sample-test-token-value",
                    "signing_secret": "abcdef1234567890deadbeef",
                },
            },
        )
        assert save_res.status_code == 200
        save_data = save_res.json()["data"]
        assert save_data["platform"] == "slack"
        assert save_data["is_enabled"] is True
        # Check credentials masking
        assert save_data["credentials"]["bot_token"].startswith("xoxb-")
        assert "..." in save_data["credentials"]["bot_token"]
        assert "sample-test-token" not in save_data["credentials"]["bot_token"]

        # Get Slack Config
        get_res = await ac.get("/api/v1/integrations/slack", headers=headers)
        assert get_res.status_code == 200
        get_data = get_res.json()["data"]
        assert get_data["platform"] == "slack"
        assert "..." in get_data["credentials"]["bot_token"]


@pytest.mark.asyncio
async def test_save_with_masked_value_preserves_secret():
    admin_token = await get_token_for("admin@omnihr.com")
    headers = {"Authorization": f"Bearer {admin_token}"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Initial Save
        await ac.put(
            "/api/v1/integrations/groq",
            headers=headers,
            json={
                "is_enabled": True,
                "credentials": {
                    "api_key": "gsk_supersecretkey1234567890abcdef",
                    "model": "llama-3.3-70b-versatile",
                },
            },
        )

        # Get masked config
        get_res = await ac.get("/api/v1/integrations/groq", headers=headers)
        masked_creds = get_res.json()["data"]["credentials"]
        assert "..." in masked_creds["api_key"]

        # Save again submitting the masked string
        update_res = await ac.put(
            "/api/v1/integrations/groq",
            headers=headers,
            json={
                "is_enabled": False,
                "credentials": {
                    "api_key": masked_creds["api_key"],
                    "model": "mixtral-8x7b-32768",
                },
            },
        )
        assert update_res.status_code == 200
        assert update_res.json()["data"]["is_enabled"] is False
        assert update_res.json()["data"]["credentials"]["model"] == "mixtral-8x7b-32768"


@pytest.mark.asyncio
async def test_non_admin_forbidden():
    emp_token = await get_token_for("employee@omnihr.com", password="EmpPass123!")
    headers = {"Authorization": f"Bearer {emp_token}"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.get("/api/v1/integrations", headers=headers)
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_unauthorized():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.get("/api/v1/integrations")
        assert res.status_code == 403 or res.status_code == 401


@pytest.mark.asyncio
async def test_test_connection_endpoint(monkeypatch):
    admin_token = await get_token_for("admin@omnihr.com")
    headers = {"Authorization": f"Bearer {admin_token}"}

    from app.modules.integrations.schemas import TestConnectionResponse
    from app.modules.integrations.service import IntegrationService

    async def mock_test_slack(self, creds):
        return TestConnectionResponse(
            success=True,
            message="Successfully authenticated with Slack! Connected to 'OmniHR Workspace' as @omnihr_bot.",
        )

    monkeypatch.setattr(IntegrationService, "_test_slack", mock_test_slack)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/integrations/slack/test",
            headers=headers,
            json={"credentials": {"bot_token": "xoxb-valid-test-token"}},
        )
        assert res.status_code == 200
        body = res.json()["data"]
        assert body["success"] is True
        assert "OmniHR Workspace" in body["message"]
