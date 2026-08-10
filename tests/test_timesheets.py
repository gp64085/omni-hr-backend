from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import get_password_hash
from app.db.session import Base, get_db
from app.main import app
from app.models.role import Permission, PermissionEnum, Role
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
async def setup_test_db():
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with TestingSessionLocal() as session:
            # Seed Permissions
            p_roles_write = Permission(
                code=PermissionEnum.ROLES_WRITE.value, module="roles"
            )
            p_submit = Permission(
                code=PermissionEnum.TIMESHEET_SUBMIT.value, module="timesheet"
            )
            p_approve = Permission(
                code=PermissionEnum.TIMESHEET_APPROVE.value, module="timesheet"
            )
            session.add_all([p_roles_write, p_submit, p_approve])
            await session.flush()

            admin_role = Role(
                name=UserRole.SUPER_ADMIN.value,
                is_system=True,
                permissions=[p_roles_write, p_submit, p_approve],
            )
            employee_role = Role(
                name=UserRole.EMPLOYEE.value,
                is_system=True,
                permissions=[p_submit],
            )
            session.add_all([admin_role, employee_role])
            await session.flush()

            admin_user = User(
                email="admin@omni-hr.com",
                password_hash=get_password_hash("Password123!"),
                first_name="Admin",
                last_name="User",
                role_id=admin_role.id,
                is_active=True,
            )
            emp_user = User(
                email="employee@omni-hr.com",
                password_hash=get_password_hash("Password123!"),
                first_name="John",
                last_name="Doe",
                role_id=employee_role.id,
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
async def test_timesheets_and_projects_flow():
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

        # 2. Login as Employee
        emp_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "employee@omni-hr.com", "password": "Password123!"},
        )
        assert emp_login.status_code == 200
        emp_token = emp_login.json()["data"]["access_token"]
        emp_headers = {"Authorization": f"Bearer {emp_token}"}

        # 3. Create a Project as Admin
        project_res = await client.post(
            "/api/v1/projects",
            json={"name": "OmniHR Core Engine", "code": "OMNI-01"},
            headers=admin_headers,
        )
        assert project_res.status_code == 201
        project_data = project_res.json()["data"]
        project_id = project_data["id"]
        assert project_data["name"] == "OmniHR Core Engine"

        # 4. Create Timesheet Entry as Employee
        today_str = str(date.today())
        entry_res = await client.post(
            "/api/v1/timesheets/entries",
            json={
                "project_id": project_id,
                "work_date": today_str,
                "hours_spent": 8.0,
                "is_billable": True,
                "activity_summary": "Developed Timesheet module API endpoints",
            },
            headers=emp_headers,
        )
        assert entry_res.status_code == 201
        entry_data = entry_res.json()["data"]
        entry_id = entry_data["id"]
        assert entry_data["hours_spent"] == 8.0
        assert entry_data["status"] == "draft"

        # 5. List Timesheet Entries
        list_res = await client.get("/api/v1/timesheets/entries", headers=emp_headers)
        assert list_res.status_code == 200
        assert len(list_res.json()["data"]) >= 1

        # 6. Submit Timesheet Entries for range
        submit_res = await client.post(
            "/api/v1/timesheets/submit",
            json={"start_date": today_str, "end_date": today_str},
            headers=emp_headers,
        )
        assert submit_res.status_code == 200

        # 7. Approve Entry as Admin/Manager
        approve_res = await client.patch(
            f"/api/v1/timesheets/entries/{entry_id}/status",
            json={"status": "approved"},
            headers=admin_headers,
        )
        assert approve_res.status_code == 200
        assert approve_res.json()["data"]["status"] == "approved"

        # 8. Fetch Weekly Summary
        summary_res = await client.get(
            f"/api/v1/timesheets/summary?start_date={today_str}&end_date={today_str}",
            headers=emp_headers,
        )
        assert summary_res.status_code == 200
        summary_data = summary_res.json()["data"]
        assert summary_data["total_hours"] == 8.0
        assert summary_data["billable_hours"] == 8.0
        assert summary_data["entries_count"] == 1


@pytest.mark.asyncio
async def test_timesheet_status_transition_and_rejection_reason():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Login as Admin & Employee
        admin_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@omni-hr.com", "password": "Password123!"},
        )
        admin_headers = {
            "Authorization": f"Bearer {admin_login.json()['data']['access_token']}"
        }

        emp_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "employee@omni-hr.com", "password": "Password123!"},
        )
        emp_headers = {
            "Authorization": f"Bearer {emp_login.json()['data']['access_token']}"
        }

        today_str = str(date.today())

        # Create draft entry
        res_create = await client.post(
            "/api/v1/timesheets/entries",
            json={
                "work_date": today_str,
                "hours_spent": 4.0,
                "is_billable": True,
                "activity_summary": "Draft Entry Test",
            },
            headers=emp_headers,
        )
        assert res_create.status_code == 201
        entry_id = res_create.json()["data"]["id"]

        # Attempt to approve draft entry directly -> Expect 400
        res_invalid = await client.patch(
            f"/api/v1/timesheets/entries/{entry_id}/status",
            json={"status": "approved"},
            headers=admin_headers,
        )
        assert res_invalid.status_code == 400
        assert (
            "Only submitted timesheet entries can be approved or rejected"
            in res_invalid.json()["error"]["message"]
        )

        # Submit entry
        await client.post(
            "/api/v1/timesheets/submit",
            json={"start_date": today_str, "end_date": today_str},
            headers=emp_headers,
        )

        # Reject entry with rejection_reason -> Expect 200 and rejection_reason persisted
        res_reject = await client.patch(
            f"/api/v1/timesheets/entries/{entry_id}/status",
            json={"status": "rejected", "rejection_reason": "Incomplete activity logs"},
            headers=admin_headers,
        )
        assert res_reject.status_code == 200
        data_reject = res_reject.json()["data"]
        assert data_reject["status"] == "rejected"
        assert data_reject["rejection_reason"] == "Incomplete activity logs"
