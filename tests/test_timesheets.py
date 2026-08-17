from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
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


@pytest.mark.asyncio
async def test_timesheet_visibility_policy_for_user_id():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Login as Admin & Employee
        admin_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@omni-hr.com", "password": "Password123!"},
        )
        admin_data = admin_login.json()["data"]
        admin_headers = {"Authorization": f"Bearer {admin_data['access_token']}"}
        admin_me = await client.get("/api/v1/users/me", headers=admin_headers)
        admin_id = admin_me.json()["data"]["id"]

        emp_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "employee@omni-hr.com", "password": "Password123!"},
        )
        emp_data = emp_login.json()["data"]
        emp_headers = {"Authorization": f"Bearer {emp_data['access_token']}"}
        emp_me = await client.get("/api/v1/users/me", headers=emp_headers)
        emp_id = emp_me.json()["data"]["id"]

        today_str = str(date.today())

        # Employee creates an entry (5.0 hrs)
        res_emp_entry = await client.post(
            "/api/v1/timesheets/entries",
            json={
                "work_date": today_str,
                "hours_spent": 5.0,
                "is_billable": True,
                "activity_summary": "Employee Work",
            },
            headers=emp_headers,
        )
        assert res_emp_entry.status_code == 201

        # Admin creates an entry (7.0 hrs)
        res_admin_entry = await client.post(
            "/api/v1/timesheets/entries",
            json={
                "work_date": today_str,
                "hours_spent": 7.0,
                "is_billable": True,
                "activity_summary": "Admin Work",
            },
            headers=admin_headers,
        )
        assert res_admin_entry.status_code == 201

        # 1. Employee attempts to query Admin's entries via user_id -> should be ignored, returning Employee's 5.0 hr entry
        res_emp_peek = await client.get(
            f"/api/v1/timesheets/entries?user_id={admin_id}",
            headers=emp_headers,
        )
        assert res_emp_peek.status_code == 200
        entries_peek = res_emp_peek.json()["data"]
        assert len(entries_peek) == 1
        assert entries_peek[0]["hours_spent"] == 5.0

        # 2. Employee attempts to query Admin's summary via user_id -> should be ignored, returning Employee's 5.0 hr summary
        res_emp_summary_peek = await client.get(
            f"/api/v1/timesheets/summary?start_date={today_str}&end_date={today_str}&user_id={admin_id}",
            headers=emp_headers,
        )
        assert res_emp_summary_peek.status_code == 200
        summary_peek = res_emp_summary_peek.json()["data"]
        assert summary_peek["total_hours"] == 5.0

        # 3. Admin queries Employee's entries via user_id -> authorized, returning Employee's 5.0 hr entry
        res_admin_query = await client.get(
            f"/api/v1/timesheets/entries?user_id={emp_id}",
            headers=admin_headers,
        )
        assert res_admin_query.status_code == 200
        entries_admin_query = res_admin_query.json()["data"]
        assert len(entries_admin_query) == 1
        assert entries_admin_query[0]["hours_spent"] == 5.0


@pytest.mark.asyncio
async def test_department_lead_and_users_read_visibility_isolation():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        import uuid

        from app.models.organization import Department

        dept1_id = uuid.uuid4()
        dept2_id = uuid.uuid4()

        async with TestingSessionLocal() as session:
            # Seed Departments
            dept1 = Department(id=dept1_id, name="Engineering")
            dept2 = Department(id=dept2_id, name="Marketing")
            session.add_all([dept1, dept2])

            # Fetch existing submit permission and seed USERS_READ
            res_submit = await session.execute(
                select(Permission).where(
                    Permission.code == PermissionEnum.TIMESHEET_SUBMIT.value
                )
            )
            p_submit = res_submit.scalar_one()

            p_users_read = Permission(
                code=PermissionEnum.USERS_READ.value, module="users"
            )
            session.add(p_users_read)
            await session.flush()

            # Seed Roles
            users_reader_role = Role(
                name="UsersReader",
                description="Role with users:read permission",
                permissions=[p_users_read, p_submit],
            )
            dept_lead_role = Role(
                name=UserRole.DEPARTMENT_LEAD.value,
                description="Department Lead Role",
                permissions=[p_submit],
            )
            emp_role = Role(
                name="StandardEmp",
                description="Standard Employee Role",
                permissions=[p_submit],
            )
            session.add_all([users_reader_role, dept_lead_role, emp_role])
            await session.flush()

            pwd = get_password_hash("Password123!")

            # User 1: Has USERS_READ permission (in Dept 1)
            u_reader = User(
                email="reader@omni-hr.com",
                password_hash=pwd,
                first_name="User",
                last_name="Reader",
                role_id=users_reader_role.id,
                department_id=dept1_id,
                is_active=True,
            )
            # User 2: Department Lead of Dept 1
            u_lead = User(
                email="lead@omni-hr.com",
                password_hash=pwd,
                first_name="Lead",
                last_name="User",
                role_id=dept_lead_role.id,
                department_id=dept1_id,
                is_active=True,
            )
            # User 3: Employee in Dept 1 (same as Lead)
            u_emp_dept1 = User(
                email="emp1@omni-hr.com",
                password_hash=pwd,
                first_name="Emp",
                last_name="One",
                role_id=emp_role.id,
                department_id=dept1_id,
                is_active=True,
            )
            # User 4: Employee in Dept 2 (different dept)
            u_emp_dept2 = User(
                email="emp2@omni-hr.com",
                password_hash=pwd,
                first_name="Emp",
                last_name="Two",
                role_id=emp_role.id,
                department_id=dept2_id,
                is_active=True,
            )
            session.add_all([u_reader, u_lead, u_emp_dept1, u_emp_dept2])
            await session.commit()

            emp1_id = u_emp_dept1.id
            emp2_id = u_emp_dept2.id

        today_str = str(date.today())

        # Log entry for Emp 1 (dept 1)
        login_e1 = await client.post(
            "/api/v1/auth/login",
            json={"email": "emp1@omni-hr.com", "password": "Password123!"},
        )
        h_e1 = {"Authorization": f"Bearer {login_e1.json()['data']['access_token']}"}
        await client.post(
            "/api/v1/timesheets/entries",
            json={
                "work_date": today_str,
                "hours_spent": 4.0,
                "is_billable": True,
                "activity_summary": "Task E1",
            },
            headers=h_e1,
        )

        # Log entry for Emp 2 (dept 2)
        login_e2 = await client.post(
            "/api/v1/auth/login",
            json={"email": "emp2@omni-hr.com", "password": "Password123!"},
        )
        h_e2 = {"Authorization": f"Bearer {login_e2.json()['data']['access_token']}"}
        await client.post(
            "/api/v1/timesheets/entries",
            json={
                "work_date": today_str,
                "hours_spent": 6.0,
                "is_billable": True,
                "activity_summary": "Task E2",
            },
            headers=h_e2,
        )

        # 1. User with USERS_READ attempts to access Emp 1's timesheets -> DENIED (returns own 0 entries)
        login_reader = await client.post(
            "/api/v1/auth/login",
            json={"email": "reader@omni-hr.com", "password": "Password123!"},
        )
        h_reader = {
            "Authorization": f"Bearer {login_reader.json()['data']['access_token']}"
        }
        res_reader_peek = await client.get(
            f"/api/v1/timesheets/entries?user_id={emp1_id}", headers=h_reader
        )
        assert res_reader_peek.status_code == 200
        assert len(res_reader_peek.json()["data"]) == 0

        # 2. Dept Lead attempts to access Emp 1 (same dept) -> ALLOWED (returns Emp 1's entry)
        login_lead = await client.post(
            "/api/v1/auth/login",
            json={"email": "lead@omni-hr.com", "password": "Password123!"},
        )
        h_lead = {
            "Authorization": f"Bearer {login_lead.json()['data']['access_token']}"
        }
        res_lead_same = await client.get(
            f"/api/v1/timesheets/entries?user_id={emp1_id}", headers=h_lead
        )
        assert res_lead_same.status_code == 200
        assert len(res_lead_same.json()["data"]) == 1
        assert res_lead_same.json()["data"][0]["hours_spent"] == 4.0

        # 3. Dept Lead attempts to access Emp 2 (different dept) -> DENIED (returns Lead's own 0 entries)
        res_lead_diff = await client.get(
            f"/api/v1/timesheets/entries?user_id={emp2_id}", headers=h_lead
        )
        assert res_lead_diff.status_code == 200
        assert len(res_lead_diff.json()["data"]) == 0


@pytest.mark.asyncio
async def test_assigned_manager_timesheet_and_leaves_isolation():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        import uuid

        pwd = get_password_hash("Password123!")

        async with TestingSessionLocal() as session:
            # Seed permissions
            res_submit = await session.execute(
                select(Permission).where(
                    Permission.code == PermissionEnum.TIMESHEET_SUBMIT.value
                )
            )
            p_submit = res_submit.scalar_one()

            # Seed Manager Role
            mgr_role = Role(
                name="ManagerRole",
                description="Manager with submit permission",
                permissions=[p_submit],
            )
            emp_role = Role(
                name="ReportEmpRole",
                description="Report Employee Role",
                permissions=[p_submit],
            )
            session.add_all([mgr_role, emp_role])
            await session.flush()

            # Create Manager 1
            mgr1 = User(
                email="mgr1@omni-hr.com",
                password_hash=pwd,
                first_name="Manager",
                last_name="One",
                role_id=mgr_role.id,
                is_active=True,
            )
            # Create Manager 2
            mgr2 = User(
                email="mgr2@omni-hr.com",
                password_hash=pwd,
                first_name="Manager",
                last_name="Two",
                role_id=mgr_role.id,
                is_active=True,
            )
            session.add_all([mgr1, mgr2])
            await session.flush()

            # Create Direct Report 1 assigned to Manager 1
            report1 = User(
                email="report1@omni-hr.com",
                password_hash=pwd,
                first_name="Report",
                last_name="One",
                role_id=emp_role.id,
                manager_id=mgr1.id,
                is_active=True,
            )
            # Create Direct Report 2 assigned to Manager 2
            report2 = User(
                email="report2@omni-hr.com",
                password_hash=pwd,
                first_name="Report",
                last_name="Two",
                role_id=emp_role.id,
                manager_id=mgr2.id,
                is_active=True,
            )
            session.add_all([report1, report2])
            await session.commit()

            report1_id = report1.id
            report2_id = report2.id

        today_str = str(date.today())

        # Report 1 logs work
        login_r1 = await client.post(
            "/api/v1/auth/login",
            json={"email": "report1@omni-hr.com", "password": "Password123!"},
        )
        h_r1 = {"Authorization": f"Bearer {login_r1.json()['data']['access_token']}"}
        await client.post(
            "/api/v1/timesheets/entries",
            json={
                "work_date": today_str,
                "hours_spent": 5.0,
                "is_billable": True,
                "activity_summary": "Report 1 Task",
            },
            headers=h_r1,
        )

        # Report 2 logs work
        login_r2 = await client.post(
            "/api/v1/auth/login",
            json={"email": "report2@omni-hr.com", "password": "Password123!"},
        )
        h_r2 = {"Authorization": f"Bearer {login_r2.json()['data']['access_token']}"}
        await client.post(
            "/api/v1/timesheets/entries",
            json={
                "work_date": today_str,
                "hours_spent": 8.0,
                "is_billable": True,
                "activity_summary": "Report 2 Task",
            },
            headers=h_r2,
        )

        # Manager 1 logs in
        login_m1 = await client.post(
            "/api/v1/auth/login",
            json={"email": "mgr1@omni-hr.com", "password": "Password123!"},
        )
        h_m1 = {"Authorization": f"Bearer {login_m1.json()['data']['access_token']}"}

        # Manager 1 accesses Report 1 (their assigned direct report) -> ALLOWED
        res_m1_r1 = await client.get(
            f"/api/v1/timesheets/entries?user_id={report1_id}", headers=h_m1
        )
        assert res_m1_r1.status_code == 200
        assert len(res_m1_r1.json()["data"]) == 1
        assert res_m1_r1.json()["data"][0]["hours_spent"] == 5.0

        # Manager 1 attempts to access Report 2 (Manager 2's report) -> DENIED (returns own 0 entries)
        res_m1_r2 = await client.get(
            f"/api/v1/timesheets/entries?user_id={report2_id}", headers=h_m1
        )
        assert res_m1_r2.status_code == 200
        assert len(res_m1_r2.json()["data"]) == 0

