import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import get_password_hash
from app.db.session import Base, get_db
from app.main import app
from app.models.role import Role
from app.models.user import EmployeeProfile, User, UserRole

# Use SQLite in-memory for fast asynchronous unit testing
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

        # Seed test roles & users
        async with TestingSessionLocal() as session:
            admin_role = Role(
                name=UserRole.SUPER_ADMIN.value,
                description="Super Admin",
                is_system=True,
            )
            emp_role = Role(
                name=UserRole.EMPLOYEE.value,
                description="Employee",
                is_system=True,
            )
            session.add_all([admin_role, emp_role])
            await session.flush()

            admin = User(
                email="admin_test@omnihr.com",
                password_hash=get_password_hash("TestPass123!"),
                first_name="Admin",
                last_name="Tester",
                role_id=admin_role.id,
                is_active=True,
            )
            emp = User(
                email="emp_test@omnihr.com",
                password_hash=get_password_hash("EmpPass123!"),
                first_name="Employee",
                last_name="Tester",
                role_id=emp_role.id,
                is_active=True,
            )
            session.add_all([admin, emp])
            await session.flush()

            session.add(EmployeeProfile(user_id=admin.id, phone_number="1234567890"))
            session.add(EmployeeProfile(user_id=emp.id, phone_number="9876543210"))
            await session.commit()

        yield
    finally:
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_login_success():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin_test@omnihr.com", "password": "TestPass123!"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert "access_token" in body["data"]
        assert "refresh_token" in body["data"]


@pytest.mark.asyncio
async def test_login_invalid_password():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin_test@omnihr.com", "password": "WrongPassword"},
        )
        assert res.status_code == 401
        body = res.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_get_current_user():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin_test@omnihr.com", "password": "TestPass123!"},
        )
        token = login_res.json()["data"]["access_token"]

        me_res = await ac.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me_res.status_code == 200
        body = me_res.json()
        assert body["success"] is True
        assert body["data"]["role"]["name"] == "super_admin"


@pytest.mark.asyncio
async def test_rbac_permission_denied_for_regular_employee():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_test@omnihr.com", "password": "EmpPass123!"},
        )
        emp_token = login_res.json()["data"]["access_token"]

        # Regular employee trying to list all users
        users_res = await ac.get(
            "/api/v1/users", headers={"Authorization": f"Bearer {emp_token}"}
        )
        assert users_res.status_code == 403
        body = users_res.json()
        assert body["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_self_service_profile_update():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_test@omnihr.com", "password": "EmpPass123!"},
        )
        emp_token = login_res.json()["data"]["access_token"]

        update_res = await ac.put(
            "/api/v1/users/me/profile",
            headers={"Authorization": f"Bearer {emp_token}"},
            json={
                "phone_number": "+1-999-888-7777",
                "address": "Scranton Business Park",
            },
        )
        assert update_res.status_code == 200
        body = update_res.json()
        assert body["success"] is True
        assert body["data"]["phone_number"] == "+1-999-888-7777"
        assert body["data"]["address"] == "Scranton Business Park"


@pytest.mark.asyncio
async def test_get_users_me_endpoint():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_test@omnihr.com", "password": "EmpPass123!"},
        )
        token = login_res.json()["data"]["access_token"]

        res = await ac.get(
            "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["data"]["email"] == "emp_test@omnihr.com"


@pytest.mark.asyncio
async def test_admin_get_update_delete_user():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        admin_login = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin_test@omnihr.com", "password": "TestPass123!"},
        )
        admin_token = admin_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Fetch user list to get target employee ID
        list_res = await ac.get("/api/v1/users?limit=100", headers=headers)
        assert list_res.status_code == 200
        emp_user = next(
            (u for u in list_res.json()["data"] if u["email"] == "emp_test@omnihr.com"),
            None,
        )
        assert emp_user is not None, (
            "Target employee user 'emp_test@omnihr.com' was not found in user list."
        )
        target_id = emp_user["id"]

        # 2. GET /users/{id}
        get_res = await ac.get(f"/api/v1/users/{target_id}", headers=headers)
        assert get_res.status_code == 200
        assert get_res.json()["data"]["email"] == "emp_test@omnihr.com"

        # 3. PUT /users/{id}
        update_res = await ac.put(
            f"/api/v1/users/{target_id}",
            headers=headers,
            json={"first_name": "UpdatedEmp", "last_name": "NewLast"},
        )
        assert update_res.status_code == 200
        assert update_res.json()["data"]["first_name"] == "UpdatedEmp"

        # 4. Attempt self deletion (should fail 400)
        admin_user = next(
            (
                u
                for u in list_res.json()["data"]
                if u["email"] == "admin_test@omnihr.com"
            ),
            None,
        )
        assert admin_user is not None, (
            "Admin user 'admin_test@omnihr.com' was not found in user list."
        )
        self_del_res = await ac.delete(
            f"/api/v1/users/{admin_user['id']}", headers=headers
        )

        assert self_del_res.status_code == 400
        assert self_del_res.json()["error"]["code"] == "CANNOT_DELETE_SELF"

        # 5. DELETE /users/{id}
        del_res = await ac.delete(f"/api/v1/users/{target_id}", headers=headers)
        assert del_res.status_code == 200
        assert del_res.json()["success"] is True

        # 6. Verify user no longer exists
        get_deleted = await ac.get(f"/api/v1/users/{target_id}", headers=headers)
        assert get_deleted.status_code == 404
