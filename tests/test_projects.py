import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import get_password_hash
from app.db.session import Base, get_db
from app.main import app
from app.models.role import Permission, PermissionEnum, Role
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
async def setup_test_db():
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with TestingSessionLocal() as session:
            # Seed Permissions
            p_read = Permission(
                code=PermissionEnum.PROJECTS_READ.value, module="projects"
            )
            p_write = Permission(
                code=PermissionEnum.PROJECTS_WRITE.value, module="projects"
            )
            session.add_all([p_read, p_write])
            await session.flush()

            # Seed Roles
            admin_role = Role(
                name="Admin",
                description="Admin role",
                permissions=[p_read, p_write],
            )
            restricted_role = Role(
                name="Restricted",
                description="Restricted role without projects read",
                permissions=[],
            )
            session.add_all([admin_role, restricted_role])
            await session.flush()

            # Seed Users
            hashed_password = get_password_hash("Password123!")
            admin_user = User(
                email="admin@omni-hr.com",
                first_name="Admin",
                last_name="User",
                password_hash=hashed_password,
                is_active=True,
                role_id=admin_role.id,
            )
            restricted_user = User(
                email="noperm@omni-hr.com",
                first_name="Restricted",
                last_name="User",
                password_hash=hashed_password,
                is_active=True,
                role_id=restricted_role.id,
            )
            session.add_all([admin_user, restricted_user])
            await session.commit()

        yield
    finally:
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_multi_department_projects_flow():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Login as Admin
        admin_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@omni-hr.com", "password": "Password123!"},
        )
        assert admin_login.status_code == 200
        admin_token = admin_login.json()["data"]["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 2. Create Company-Wide Project (Empty department_ids)
        res_global = await client.post(
            "/api/v1/projects",
            json={"name": "Global All-Hands", "code": "GLOB-01", "department_ids": []},
            headers=admin_headers,
        )
        assert res_global.status_code == 201
        data_global = res_global.json()["data"]
        assert data_global["departments"] == []

        # 3. Create Department Bounded Project with non-existent ID
        dummy_dept_id = str(uuid.uuid4())
        res_invalid = await client.post(
            "/api/v1/projects",
            json={
                "name": "Invalid Dept Project",
                "code": "BAD-01",
                "department_ids": [dummy_dept_id],
            },
            headers=admin_headers,
        )
        # Should return 400 because dummy department ID doesn't exist
        assert res_invalid.status_code == 400

        # 4. List projects
        res_list = await client.get("/api/v1/projects", headers=admin_headers)
        assert res_list.status_code == 200
        projects = res_list.json()["data"]
        assert any(p["code"] == "GLOB-01" for p in projects)


@pytest.mark.asyncio
async def test_projects_read_permission_enforcement():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Login as Admin and create a project first
        admin_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@omni-hr.com", "password": "Password123!"},
        )
        admin_token = admin_login.json()["data"]["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        res_create = await client.post(
            "/api/v1/projects",
            json={"name": "Secret Project", "code": "SEC-01", "department_ids": []},
            headers=admin_headers,
        )
        assert res_create.status_code == 201
        project_id = res_create.json()["data"]["id"]

        # Login as Restricted User (no PROJECTS_READ permission)
        user_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "noperm@omni-hr.com", "password": "Password123!"},
        )
        assert user_login.status_code == 200
        user_token = user_login.json()["data"]["access_token"]
        user_headers = {"Authorization": f"Bearer {user_token}"}

        # Attempt to list projects -> expect 403
        res_list = await client.get("/api/v1/projects", headers=user_headers)
        assert res_list.status_code == 403

        # Attempt to get project by ID -> expect 403
        res_get = await client.get(
            f"/api/v1/projects/{project_id}", headers=user_headers
        )
        assert res_get.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_project_name_validation():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        admin_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@omni-hr.com", "password": "Password123!"},
        )
        admin_token = admin_login.json()["data"]["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Create first project
        res1 = await client.post(
            "/api/v1/projects",
            json={"name": "Alpha Project", "code": "ALPHA-01", "department_ids": []},
            headers=admin_headers,
        )
        assert res1.status_code == 201

        # 2. Attempt to create second project with duplicate name -> expect 409
        res_dup_name = await client.post(
            "/api/v1/projects",
            json={"name": "Alpha Project", "code": "ALPHA-02", "department_ids": []},
            headers=admin_headers,
        )
        assert res_dup_name.status_code == 409

        # 3. Create second project with unique name
        res2 = await client.post(
            "/api/v1/projects",
            json={"name": "Beta Project", "code": "BETA-01", "department_ids": []},
            headers=admin_headers,
        )
        assert res2.status_code == 201
        beta_id = res2.json()["data"]["id"]

        # 4. Attempt to update Beta Project's name to "Alpha Project" -> expect 409
        res_update_dup = await client.put(
            f"/api/v1/projects/{beta_id}",
            json={"name": "Alpha Project"},
            headers=admin_headers,
        )
        assert res_update_dup.status_code == 409
