import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import get_password_hash
from app.db.session import Base, get_db
from app.main import app
from app.models.role import Permission, Role
from app.models.user import User, UserRole

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
async def setup_roles_test_db():
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with TestingSessionLocal() as session:
            perm_read = Permission(
                code="payroll:read", module="payroll", description="Read payroll"
            )
            perm_process = Permission(
                code="payroll:process", module="payroll", description="Process payroll"
            )
            perm_roles = Permission(
                code="roles:write", module="roles", description="Manage roles"
            )
            session.add_all([perm_read, perm_process, perm_roles])
            await session.flush()

            admin_role = Role(
                name="super_admin",
                description="Super Admin Role",
                is_system=True,
                permissions=[perm_read, perm_process, perm_roles],
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


@pytest.mark.asyncio
async def test_list_permissions():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin@omnihr.com", "password": "AdminPass123!"},
        )
        token = login_res.json()["data"]["access_token"]

        res = await ac.get(
            "/api/v1/permissions", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        permissions = body["data"]
        assert len(permissions) >= 2
        assert "meta" in body
        assert body["meta"]["total"] >= 2


@pytest.mark.asyncio
async def test_create_and_delete_custom_role():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin@omnihr.com", "password": "AdminPass123!"},
        )
        token = login_res.json()["data"]["access_token"]

        # Create custom role
        create_res = await ac.post(
            "/api/v1/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Payroll Specialist",
                "description": "Custom role for payroll staff",
                "permission_ids": [],
            },
        )
        assert create_res.status_code == 201
        created_role = create_res.json()
        role_id = created_role["id"]
        assert created_role["name"] == "Payroll Specialist"
        assert created_role["is_system"] is False

        # Delete custom role
        del_res = await ac.delete(
            f"/api/v1/roles/{role_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert del_res.status_code == 204


@pytest.mark.asyncio
async def test_system_role_protection():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin@omnihr.com", "password": "AdminPass123!"},
        )
        token = login_res.json()["data"]["access_token"]

        roles_res = await ac.get(
            "/api/v1/roles", headers={"Authorization": f"Bearer {token}"}
        )
        body = roles_res.json()
        roles = body["data"]
        system_role = next(r for r in roles if r["name"] == "super_admin")

        # Dedicated endpoint to fetch permissions for specific role
        role_perms_res = await ac.get(
            f"/api/v1/roles/{system_role['id']}/permissions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert role_perms_res.status_code == 200
        perm_body = role_perms_res.json()
        assert perm_body["success"] is True
        assert len(perm_body["data"]) >= 1

        # Attempt to delete system role
        del_res = await ac.delete(
            f"/api/v1/roles/{system_role['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert del_res.status_code == 400
        err_body = del_res.json()
        assert err_body["error"]["code"] == "SYSTEM_ROLE_PROTECTED"


@pytest.mark.asyncio
async def test_custom_role_permission_enforcement():
    async with TestingSessionLocal() as session:
        perm_read_roles = Permission(
            code="roles:read", module="roles", description="Read roles"
        )
        session.add(perm_read_roles)
        await session.flush()

        custom_role = Role(
            name="Custom Read Only Auditor",
            description="Can only read roles",
            is_system=False,
            permissions=[perm_read_roles],
        )
        session.add(custom_role)
        await session.flush()

        auditor = User(
            email="auditor@omnihr.com",
            password_hash=get_password_hash("AuditorPass123!"),
            first_name="Auditor",
            last_name="Custom",
            role_id=custom_role.id,
            is_active=True,
        )
        session.add(auditor)
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "auditor@omnihr.com", "password": "AuditorPass123!"},
        )
        token = login_res.json()["data"]["access_token"]

        # Should ALLOW roles:read since custom role has roles:read permission
        res_read = await ac.get(
            "/api/v1/roles", headers={"Authorization": f"Bearer {token}"}
        )
        assert res_read.status_code == 200

        # Should BLOCK roles:write since custom role lacks roles:write permission
        res_write = await ac.post(
            "/api/v1/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Forbidden Role", "permission_ids": []},
        )
        assert res_write.status_code == 403
        assert res_write.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_invalid_permission_ids_rejected():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin@omnihr.com", "password": "AdminPass123!"},
        )
        token = login_res.json()["data"]["access_token"]

        fake_uuid = "00000000-0000-0000-0000-000000000000"
        create_res = await ac.post(
            "/api/v1/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Role With Invalid Perms",
                "permission_ids": [fake_uuid],
            },
        )
        assert create_res.status_code == 400
        body = create_res.json()
        assert body["error"]["code"] == "INVALID_PERMISSION_IDS"


@pytest.mark.asyncio
async def test_actor_aware_role_assignment_policy():
    async with TestingSessionLocal() as session:
        perm_users_write = Permission(
            code="users:write", module="users", description="Write users"
        )
        session.add(perm_users_write)
        await session.flush()

        hr_role = Role(
            name=UserRole.HR_MANAGER.value,
            description="HR Manager",
            is_system=True,
            permissions=[perm_users_write],
        )
        session.add(hr_role)
        await session.flush()

        hr_user = User(
            email="hr_policy_test@omnihr.com",
            password_hash=get_password_hash("HrPass123!"),
            first_name="HR",
            last_name="Manager",
            role_id=hr_role.id,
            is_active=True,
        )
        session.add(hr_user)
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Get super_admin role ID
        admin_login = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin@omnihr.com", "password": "AdminPass123!"},
        )
        admin_token = admin_login.json()["data"]["access_token"]
        roles_res = await ac.get(
            "/api/v1/roles", headers={"Authorization": f"Bearer {admin_token}"}
        )
        super_admin_role_id = next(
            r["id"] for r in roles_res.json()["data"] if r["name"] == "super_admin"
        )

        # Login as HR Manager
        hr_login = await ac.post(
            "/api/v1/auth/login",
            json={"email": "hr_policy_test@omnihr.com", "password": "HrPass123!"},
        )
        hr_token = hr_login.json()["data"]["access_token"]

        # HR Manager attempting to assign Super Admin role to a new user
        attempt_res = await ac.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {hr_token}"},
            json={
                "email": "hacked_admin@omnihr.com",
                "password": "HackedPass123!",
                "first_name": "Hacked",
                "last_name": "Admin",
                "role_id": super_admin_role_id,
            },
        )
        assert attempt_res.status_code == 403
        assert attempt_res.json()["error"]["code"] == "ROLE_ASSIGNMENT_FORBIDDEN"
