from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import get_password_hash
from app.db.session import Base, get_db
from app.main import app
from app.models.leave import LeaveType, LeaveTypeEnum
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
            p_apply = Permission(code=PermissionEnum.LEAVE_APPLY.value, module="leaves")
            p_read = Permission(code=PermissionEnum.LEAVE_READ.value, module="leaves")
            p_approve = Permission(
                code=PermissionEnum.LEAVE_APPROVE.value, module="leaves"
            )
            p_manage = Permission(
                code=PermissionEnum.LEAVE_MANAGE_TYPES.value, module="leaves"
            )
            p_ts_submit = Permission(
                code=PermissionEnum.TIMESHEET_SUBMIT.value, module="timesheets"
            )
            session.add_all([p_apply, p_read, p_approve, p_manage, p_ts_submit])
            await session.flush()

            # Seed System Roles with permissions
            admin_role = Role(
                name=UserRole.SUPER_ADMIN.value,
                description="Super Admin",
                is_system=True,
                permissions=[p_apply, p_read, p_approve, p_manage, p_ts_submit],
            )
            emp_role = Role(
                name=UserRole.EMPLOYEE.value,
                description="Employee",
                is_system=True,
                permissions=[p_apply, p_read, p_ts_submit],
            )
            session.add_all([admin_role, emp_role])
            await session.flush()

            admin = User(
                email="admin_leave@omnihr.com",
                password_hash=get_password_hash("TestPass123!"),
                first_name="Admin",
                last_name="Tester",
                role_id=admin_role.id,
                is_active=True,
            )
            emp = User(
                email="emp_leave@omnihr.com",
                password_hash=get_password_hash("EmpPass123!"),
                first_name="Employee",
                last_name="Tester",
                role_id=emp_role.id,
                is_active=True,
            )
            session.add_all([admin, emp])
            await session.flush()

            # Seed Leave Types
            lt_casual = LeaveType(
                name=LeaveTypeEnum.CASUAL,
                default_quota=12.0,
                requires_approval=True,
                auto_approve_threshold=0,
            )
            lt_sick = LeaveType(
                name=LeaveTypeEnum.SICK,
                default_quota=10.0,
                requires_approval=True,
                auto_approve_threshold=1,
            )
            lt_unpaid = LeaveType(
                name=LeaveTypeEnum.UNPAID,
                default_quota=0.0,
                requires_approval=True,
                auto_approve_threshold=0,
            )
            session.add_all([lt_casual, lt_sick, lt_unpaid])
            await session.commit()

        yield
    finally:
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_get_leave_types():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_leave@omnihr.com", "password": "EmpPass123!"},
        )
        token = login_res.json()["data"]["access_token"]

        res = await ac.get(
            "/api/v1/leaves/types", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert len(body["data"]) >= 3


@pytest.mark.asyncio
async def test_get_leave_balance_seeding():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_leave@omnihr.com", "password": "EmpPass123!"},
        )
        token = login_res.json()["data"]["access_token"]

        res = await ac.get(
            "/api/v1/leaves/balance", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        casual_alloc = next(
            b for b in body["data"] if b["leave_type"]["name"] == "casual"
        )
        assert casual_alloc["allocated_days"] == 12.0
        assert casual_alloc["remaining_days"] == 12.0


@pytest.mark.asyncio
async def test_apply_leave_and_overlap_rejection():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_leave@omnihr.com", "password": "EmpPass123!"},
        )
        token = login_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        types_res = await ac.get("/api/v1/leaves/types", headers=headers)
        casual_type_id = next(
            t["id"] for t in types_res.json()["data"] if t["name"] == "casual"
        )

        today = date.today()
        # Find next Monday to test clean working days
        next_monday = today + timedelta(days=(7 - today.weekday()))
        next_tuesday = next_monday + timedelta(days=1)

        # 1. Apply for Monday-Tuesday casual leave
        apply_res = await ac.post(
            "/api/v1/leaves/requests",
            headers=headers,
            json={
                "leave_type_id": casual_type_id,
                "start_date": next_monday.isoformat(),
                "end_date": next_tuesday.isoformat(),
                "reason": "Personal work",
            },
        )
        assert apply_res.status_code == 201
        body = apply_res.json()
        assert body["success"] is True
        assert body["data"]["total_days"] == 2.0
        assert body["data"]["status"] == "pending"

        # 2. Attempt overlapping leave on Tuesday (should fail 409)
        overlap_res = await ac.post(
            "/api/v1/leaves/requests",
            headers=headers,
            json={
                "leave_type_id": casual_type_id,
                "start_date": next_tuesday.isoformat(),
                "end_date": (next_tuesday + timedelta(days=1)).isoformat(),
                "reason": "Overlapping request",
            },
        )
        assert overlap_res.status_code == 409
        assert overlap_res.json()["error"]["code"] == "OVERLAPPING_LEAVE_REQUEST"


@pytest.mark.asyncio
async def test_auto_approve_sick_leave():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_leave@omnihr.com", "password": "EmpPass123!"},
        )
        token = login_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        types_res = await ac.get("/api/v1/leaves/types", headers=headers)
        sick_type_id = next(
            t["id"] for t in types_res.json()["data"] if t["name"] == "sick"
        )

        today = date.today()
        next_wednesday = today + timedelta(days=(9 - today.weekday()))

        # Sick leave <= 1 day threshold is auto-approved
        res = await ac.post(
            "/api/v1/leaves/requests",
            headers=headers,
            json={
                "leave_type_id": sick_type_id,
                "start_date": next_wednesday.isoformat(),
                "end_date": next_wednesday.isoformat(),
                "reason": "Fever",
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["data"]["status"] == "approved"
        assert body["data"]["is_auto_approved"] is True


@pytest.mark.asyncio
async def test_company_holiday_crud():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        admin_login = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin_leave@omnihr.com", "password": "TestPass123!"},
        )
        admin_token = admin_login.json()["data"]["access_token"]

        res = await ac.post(
            "/api/v1/holidays",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "New Year Day",
                "holiday_date": "2027-01-01",
                "is_optional": False,
                "description": "Public holiday",
            },
        )
        assert res.status_code == 201
        assert res.json()["data"]["name"] == "New Year Day"

        list_res = await ac.get(
            "/api/v1/holidays?year=2027",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert list_res.status_code == 200
        assert len(list_res.json()["data"]) == 1


@pytest.mark.asyncio
async def test_accrual_policy_creation_and_manual_grant():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        admin_login = await ac.post(
            "/api/v1/auth/login",
            json={"email": "admin_leave@omnihr.com", "password": "TestPass123!"},
        )
        admin_token = admin_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        types_res = await ac.get("/api/v1/leaves/types", headers=headers)
        casual_type_id = next(
            t["id"] for t in types_res.json()["data"] if t["name"] == "casual"
        )

        # 1. Create monthly accrual policy (1.5 days/month, max 18 days)
        policy_res = await ac.post(
            "/api/v1/leaves/policies",
            headers=headers,
            json={
                "leave_type_id": casual_type_id,
                "designation_id": None,
                "frequency": "monthly",
                "accrual_rate": 1.5,
                "max_quota": 18.0,
            },
        )
        assert policy_res.status_code == 201
        assert policy_res.json()["data"]["accrual_rate"] == 1.5

        # 2. Grant manual leave days to employee
        users_res = await ac.get("/api/v1/users?limit=100", headers=headers)
        emp_user = next(
            (
                u
                for u in users_res.json()["data"]
                if u["email"] == "emp_leave@omnihr.com"
            ),
            None,
        )
        assert emp_user is not None, (
            "Employee user 'emp_leave@omnihr.com' was not found in user list."
        )

        grant_res = await ac.post(
            "/api/v1/leaves/allocations/grant",
            headers=headers,
            json={
                "user_id": emp_user["id"],
                "leave_type_id": casual_type_id,
                "year": date.today().year,
                "granted_days": 3.0,
                "reason": "Joining bonus",
            },
        )
        assert grant_res.status_code == 200
        assert grant_res.json()["data"]["allocated_days"] >= 3.0

        # 3. Trigger periodic accrual engine on-demand
        run_res = await ac.post(
            "/api/v1/leaves/accruals/run?target_date=2026-09-01",
            headers=headers,
        )
        assert run_res.status_code == 200
        assert run_res.json()["success"] is True

        # 4. Verify Audit Logs recorded in centralized Audit Logs module
        audit_res = await ac.get(
            "/api/v1/audit-logs?module=leaves",
            headers=headers,
        )

        assert audit_res.status_code == 200
        audit_logs = audit_res.json()["data"]
        assert len(audit_logs) >= 2
        actions = [log["action"] for log in audit_logs]
        assert "MANUAL_LEAVE_GRANT" in actions
        assert "PERIODIC_LEAVE_ACCRUAL" in actions


@pytest.mark.asyncio
async def test_leave_rejected_when_timesheet_exists_on_date():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        login_res = await ac.post(
            "/api/v1/auth/login",
            json={"email": "emp_leave@omnihr.com", "password": "EmpPass123!"},
        )
        token = login_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        types_res = await ac.get("/api/v1/leaves/types", headers=headers)
        casual_type_id = next(
            t["id"] for t in types_res.json()["data"] if t["name"] == "casual"
        )

        today = date.today()

        # 1. Log a timesheet for today
        ts_res = await ac.post(
            "/api/v1/timesheets/entries",
            headers=headers,
            json={
                "work_date": today.isoformat(),
                "total_minutes_spent": 240,
                "activity_summary": [
                    {
                        "project_name": "Internal Project",
                        "tasks": [{"summary": "Daily tasks", "hours": 4, "minutes": 0}],
                    }
                ],
            },
        )
        assert ts_res.status_code == 201

        # 2. Attempt to apply leave for today (should fail 409)
        leave_res = await ac.post(
            "/api/v1/leaves/requests",
            headers=headers,
            json={
                "leave_type_id": casual_type_id,
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
                "reason": "Doctor appointment",
            },
        )
        assert leave_res.status_code == 409
        body = leave_res.json()
        assert body["error"]["code"] == "TIMESHEET_ALREADY_EXISTS_FOR_DATE"
