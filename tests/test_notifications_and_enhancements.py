from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.security import get_password_hash
from app.db.session import Base, get_db
from app.main import app
from app.models.leave import LeaveStatus, LeaveType, LeaveTypeEnum
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
            # Permissions
            p_submit_ts = Permission(
                code=PermissionEnum.TIMESHEET_SUBMIT.value, module="timesheet"
            )
            p_approve_ts = Permission(
                code=PermissionEnum.TIMESHEET_APPROVE.value, module="timesheet"
            )
            p_apply_leave = Permission(
                code=PermissionEnum.LEAVE_APPLY.value, module="leave"
            )
            p_approve_leave = Permission(
                code=PermissionEnum.LEAVE_APPROVE.value, module="leave"
            )
            p_roles = Permission(code=PermissionEnum.ROLES_WRITE.value, module="roles")
            session.add_all(
                [p_submit_ts, p_approve_ts, p_apply_leave, p_approve_leave, p_roles]
            )
            await session.flush()

            admin_role = Role(
                name=UserRole.SUPER_ADMIN.value,
                is_system=True,
                permissions=[
                    p_submit_ts,
                    p_approve_ts,
                    p_apply_leave,
                    p_approve_leave,
                    p_roles,
                ],
            )
            employee_role = Role(
                name=UserRole.EMPLOYEE.value,
                is_system=True,
                permissions=[p_submit_ts, p_apply_leave],
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
            session.add(admin_user)
            await session.flush()

            emp_user = User(
                email="employee@omni-hr.com",
                password_hash=get_password_hash("Password123!"),
                first_name="John",
                last_name="Doe",
                role_id=employee_role.id,
                manager_id=admin_user.id,
                is_active=True,
            )
            session.add(emp_user)

            # Leave Types
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
                auto_approve_threshold=2,
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
async def test_timesheet_7_day_window_and_single_endpoint():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Login employee
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": "employee@omni-hr.com", "password": "Password123!"},
        )
        token = login_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        today = date.today()
        older_than_7_days = today - timedelta(days=8)

        # 1. Reject logging timesheet older than 7 days
        payload_old = {
            "work_date": older_than_7_days.isoformat(),
            "hours_spent": 8.0,
            "is_billable": True,
            "activity_summary": [{"summary": "Past work", "hours": 8.0}],
        }
        res_old = await client.post(
            "/api/v1/timesheets/entries",
            json=payload_old,
            headers=headers,
        )
        assert res_old.status_code == 400
        assert "older than 7 days" in res_old.json()["error"]["message"]

        # 2. Reject future dates
        future_date = today + timedelta(days=1)
        payload_future = {
            "work_date": future_date.isoformat(),
            "hours_spent": 8.0,
            "is_billable": True,
            "activity_summary": [{"summary": "Future work", "hours": 8.0}],
        }
        res_future = await client.post(
            "/api/v1/timesheets/entries",
            json=payload_future,
            headers=headers,
        )
        assert res_future.status_code == 400
        assert "future dates" in res_future.json()["error"]["message"]

        # 3. Allow logging timesheet within 7 days
        payload_valid = {
            "work_date": today.isoformat(),
            "hours_spent": 7.5,
            "is_billable": True,
            "activity_summary": [
                {
                    "tasks": [
                        {"summary": "Feature work", "hours": 5.0, "minutes": 0},
                        {"summary": "Code review", "hours": 2.5, "minutes": 0},
                    ],
                    "total_hours": 7.5,
                }
            ],
        }
        res_valid = await client.post(
            "/api/v1/timesheets/entries",
            json=payload_valid,
            headers=headers,
        )
        assert res_valid.status_code == 201
        assert res_valid.json()["data"]["hours_spent"] == 7.5


@pytest.mark.asyncio
async def test_emergency_leave_lwp_fallback_and_deferred_deduction():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Login employee
        emp_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "employee@omni-hr.com", "password": "Password123!"},
        )
        emp_token = emp_login.json()["data"]["access_token"]
        emp_headers = {"Authorization": f"Bearer {emp_token}"}

        # Login admin
        adm_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@omni-hr.com", "password": "Password123!"},
        )
        adm_token = adm_login.json()["data"]["access_token"]
        adm_headers = {"Authorization": f"Bearer {adm_token}"}

        # Get leave types
        types_res = await client.get("/api/v1/leaves/types", headers=emp_headers)
        assert types_res.status_code == 200
        casual_type = next(
            t
            for t in types_res.json()["data"]
            if t["name"] == LeaveTypeEnum.CASUAL.value
        )

        # 1. Apply for future leave spanning 15 days
        # Dates: next year June (future date)
        next_year = date.today().year + 1
        start_d = date(next_year, 6, 1)
        end_d = date(next_year, 6, 21)  # 15 working days

        apply_payload = {
            "leave_type_id": casual_type["id"],
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "reason": "Emergency leave request",
        }
        apply_res = await client.post(
            "/api/v1/leaves/requests",
            json=apply_payload,
            headers=emp_headers,
        )
        assert apply_res.status_code == 201
        created_leave = apply_res.json()["data"]
        assert created_leave["status"] == "pending"

        # 2. Check Admin received Notification about leave submission
        admin_notifs = await client.get("/api/v1/notifications", headers=adm_headers)
        assert admin_notifs.status_code == 200
        notif_list = admin_notifs.json()["data"]
        assert len(notif_list) >= 1
        assert "New Leave Request" in notif_list[0]["title"]

        # 3. Admin approves future leave
        approve_res = await client.patch(
            f"/api/v1/leaves/requests/{created_leave['id']}/status",
            json={"status": LeaveStatus.APPROVED.value},
            headers=adm_headers,
        )
        assert approve_res.status_code == 200

        # 4. Check user balance before date arrives - deferred deduction!
        # Because start_date is in the future, used_days is NOT yet deducted (0.0)!
        bal_res = await client.get(
            f"/api/v1/leaves/balance?year={next_year}", headers=emp_headers
        )
        assert bal_res.status_code == 200
        casual_bal = next(
            b for b in bal_res.json()["data"] if b["leave_type_id"] == casual_type["id"]
        )
        assert casual_bal["used_days"] == 0.0  # Deferred deduction
        assert casual_bal["scheduled_future_days"] > 0

        # 5. Simulate date arriving: Midnight reconciliation job triggers on end_d (settling all days)
        reconcile_res = await client.post(
            f"/api/v1/leaves/reconcile?target_date={end_d.isoformat()}",
            headers=adm_headers,
        )
        assert reconcile_res.status_code == 200
        assert "Settled" in reconcile_res.json()["data"]["message"]

        # 6. Check updated leave request has been settled with dynamic paid and LWP calculation
        req_res = await client.get(
            f"/api/v1/leaves/requests/{created_leave['id']}", headers=emp_headers
        )
        assert req_res.status_code == 200
        settled_leave = req_res.json()["data"]
        assert settled_leave["extra_metadata"]["settled"] is True
        assert settled_leave["extra_metadata"]["paid_days"] == 12.0  # Max allocated
        assert settled_leave["extra_metadata"]["lwp_days"] == 3.0  # 15 - 12 = 3d LWP

        # 7. Check Employee received notification about leave approval
        emp_notifs = await client.get("/api/v1/notifications", headers=emp_headers)
        assert emp_notifs.status_code == 200
        emp_notif_list = emp_notifs.json()["data"]
        assert len(emp_notif_list) >= 1
        assert "Leave Request Approved" in emp_notif_list[0]["title"]

        # 8. Mark notification as read
        notif_id = emp_notif_list[0]["id"]
        read_res = await client.patch(
            f"/api/v1/notifications/{notif_id}/read", headers=emp_headers
        )
        assert read_res.status_code == 200
        assert read_res.json()["data"]["is_read"] is True

        # 9. Unread count becomes 0
        count_res = await client.get(
            "/api/v1/notifications/unread-count", headers=emp_headers
        )
        assert count_res.json()["data"]["unread_count"] == 0


@pytest.mark.asyncio
async def test_partial_day_approval_rejection_and_timesheet_interop():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Login employee & admin
        emp_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "employee@omni-hr.com", "password": "Password123!"},
        )
        emp_token = emp_login.json()["data"]["access_token"]
        emp_headers = {"Authorization": f"Bearer {emp_token}"}

        adm_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@omni-hr.com", "password": "Password123!"},
        )
        adm_token = adm_login.json()["data"]["access_token"]
        adm_headers = {"Authorization": f"Bearer {adm_token}"}

        # Get leave types
        types_res = await client.get("/api/v1/leaves/types", headers=emp_headers)
        sick_type = next(
            t for t in types_res.json()["data"] if t["name"] == LeaveTypeEnum.SICK.value
        )

        # 1. Apply for 3 working days in the future (next year Oct 5, 6, 7)
        next_year = date.today().year + 1
        d1 = date(next_year, 10, 5)  # Mon
        d2 = date(next_year, 10, 6)  # Tue
        d3 = date(next_year, 10, 7)  # Wed

        apply_res = await client.post(
            "/api/v1/leaves/requests",
            json={
                "leave_type_id": sick_type["id"],
                "start_date": d1.isoformat(),
                "end_date": d3.isoformat(),
                "reason": "Medical procedure and recovery",
            },
            headers=emp_headers,
        )
        assert apply_res.status_code == 201
        leave_data = apply_res.json()["data"]
        req_id = leave_data["id"]
        assert len(leave_data["extra_metadata"]["days"]) == 3

        # 2. Manager partially approves d1 & d2, but rejects d3 (e.g. Critical deployment on d3)
        partial_res = await client.patch(
            f"/api/v1/leaves/requests/{req_id}/status",
            json={
                "status": LeaveStatus.APPROVED.value,
                "approved_dates": [d1.isoformat(), d2.isoformat()],
                "rejected_dates": [d3.isoformat()],
                "rejection_reason": "Release deployment scheduled on day 3",
            },
            headers=adm_headers,
        )
        assert partial_res.status_code == 200
        updated_leave = partial_res.json()["data"]
        assert updated_leave["status"] == "approved"
        assert updated_leave["total_days"] == 2.0  # Only approved days count

        # Verify day statuses inside extra_metadata
        days = updated_leave["extra_metadata"]["days"]
        d1_item = next(d for d in days if d["date"] == d1.isoformat())
        d2_item = next(d for d in days if d["date"] == d2.isoformat())
        d3_item = next(d for d in days if d["date"] == d3.isoformat())

        assert d1_item["day_status"] == "approved"
        assert d2_item["day_status"] == "approved"
        assert d3_item["day_status"] == "rejected"
        assert "Release deployment" in d3_item["rejection_reason"]


@pytest.mark.asyncio
async def test_dynamic_leave_application_without_leave_type_selection():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Login employee
        emp_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "employee@omni-hr.com", "password": "Password123!"},
        )
        emp_token = emp_login.json()["data"]["access_token"]
        emp_headers = {"Authorization": f"Bearer {emp_token}"}

        # Apply leave without providing leave_type_id
        next_year = date.today().year + 1
        d1 = date(next_year, 11, 2)
        d2 = date(next_year, 11, 3)

        apply_res = await client.post(
            "/api/v1/leaves/requests",
            json={
                "start_date": d1.isoformat(),
                "end_date": d2.isoformat(),
                "reason": "Family gathering - auto resolved leave type",
            },
            headers=emp_headers,
        )
        assert apply_res.status_code == 201
        created_leave = apply_res.json()["data"]
        assert created_leave["leave_type_id"] is not None
        assert created_leave["total_days"] == 2.0
        assert len(created_leave["extra_metadata"]["days"]) == 2
