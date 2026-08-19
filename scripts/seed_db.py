import asyncio
import os
import sys

# Ensure backend directory is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, Base, engine
from app.models.leave import LeaveType, LeaveTypeEnum
from app.models.organization import Department, Designation
from app.models.role import Permission, PermissionEnum, Role
from app.models.user import EmployeeProfile, User, UserRole


async def seed_database():
    print("Re-creating Database Tables with Fresh Schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        print("Seeding Departments & Designations...")
        depts_data = [
            "Engineering",
            "Human Resources",
            "Product & Design",
            "Sales & Marketing",
        ]
        dept_map = {}
        for name in depts_data:
            res = await session.execute(
                select(Department).where(Department.name == name)
            )
            dept = res.scalar_one_or_none()
            if not dept:
                dept = Department(name=name)
                session.add(dept)
                await session.flush()
            dept_map[name] = dept.id

        desigs_data = [
            "Senior Software Engineer",
            "HR Specialist",
            "Product Manager",
            "Account Executive",
        ]
        desig_map = {}
        for title in desigs_data:
            res = await session.execute(
                select(Designation).where(Designation.title == title)
            )
            desig = res.scalar_one_or_none()
            if not desig:
                desig = Designation(title=title)
                session.add(desig)
                await session.flush()
            desig_map[title] = desig.id

        # Leave Types
        leave_types = [
            (LeaveTypeEnum.CASUAL, 12.0, True, 1),
            (LeaveTypeEnum.SICK, 10.0, True, 1),
            (LeaveTypeEnum.EARNED, 15.0, True, 0),
            (LeaveTypeEnum.UNPAID, 30.0, True, 0),
        ]
        for lt_enum, default_quota, req_app, auto_app in leave_types:
            res = await session.execute(
                select(LeaveType).where(LeaveType.name == lt_enum)
            )
            lt = res.scalar_one_or_none()
            if not lt:
                lt = LeaveType(
                    name=lt_enum,
                    default_quota=default_quota,
                    requires_approval=req_app,
                    auto_approve_threshold=auto_app,
                )
                session.add(lt)

        await session.flush()

        print("Seeding Permissions Catalog & System Roles...")
        permissions_data = [
            # Users
            (PermissionEnum.USERS_READ, "users", "View user accounts and profiles"),
            (PermissionEnum.USERS_WRITE, "users", "Create and edit user accounts"),
            (PermissionEnum.USERS_DELETE, "users", "Delete user accounts"),
            # Roles & Permissions
            (PermissionEnum.ROLES_READ, "roles", "View roles and permissions catalog"),
            (PermissionEnum.ROLES_WRITE, "roles", "Create and modify custom roles"),
            (PermissionEnum.ROLES_DELETE, "roles", "Delete custom roles"),
            # Leave
            (PermissionEnum.LEAVE_APPLY, "leave", "Apply for leave"),
            (PermissionEnum.LEAVE_READ, "leave", "View leave requests"),
            (PermissionEnum.LEAVE_APPROVE, "leave", "Approve or reject leave requests"),
            (
                PermissionEnum.LEAVE_MANAGE_TYPES,
                "leave",
                "Create and manage leave types",
            ),
            # Payroll
            (
                PermissionEnum.PAYROLL_READ,
                "payroll",
                "View payslips and salary structures",
            ),
            (
                PermissionEnum.PAYROLL_PROCESS,
                "payroll",
                "Process pay runs and salary structures",
            ),
            # Timesheets
            (PermissionEnum.TIMESHEET_SUBMIT, "timesheet", "Submit timesheet entries"),
            (
                PermissionEnum.TIMESHEET_APPROVE,
                "timesheet",
                "Approve timesheet entries",
            ),
            # Projects
            (
                PermissionEnum.PROJECTS_READ,
                "projects",
                "View assigned projects and assignments",
            ),
            (
                PermissionEnum.PROJECTS_WRITE,
                "projects",
                "Create, update, and assign projects",
            ),
            # Audit
            (PermissionEnum.AUDIT_READ, "audit", "View system audit logs"),
        ]

        perm_map = {}
        for code, module, desc in permissions_data:
            code_str = code.value if hasattr(code, "value") else str(code)
            res = await session.execute(
                select(Permission).where(Permission.code == code_str)
            )
            perm = res.scalar_one_or_none()
            if not perm:
                perm = Permission(code=code_str, module=module, description=desc)
                session.add(perm)
                await session.flush()
            perm_map[code_str] = perm

        # System Roles
        roles_data = [
            (
                UserRole.SUPER_ADMIN.value,
                "Super Administrator with unrestricted access",
                True,
                list(perm_map.values()),
            ),
            (
                UserRole.HR_MANAGER.value,
                "HR Manager with employee, leave, and payroll management access",
                True,
                [
                    perm_map[c]
                    for c in [
                        "users:read",
                        "users:write",
                        "roles:read",
                        "leave:read",
                        "leave:approve",
                        "leave:manage_types",
                        "payroll:read",
                        "payroll:process",
                        "timesheet:approve",
                        "audit:read",
                    ]
                ],
            ),
            (
                UserRole.DEPARTMENT_LEAD.value,
                "Department Lead with team approval access",
                True,
                [
                    perm_map[c]
                    for c in [
                        "users:read",
                        "leave:apply",
                        "leave:read",
                        "leave:approve",
                        "timesheet:submit",
                        "timesheet:approve",
                    ]
                ],
            ),
            (
                UserRole.EMPLOYEE.value,
                "Standard Employee access",
                True,
                [
                    perm_map[c]
                    for c in [
                        "leave:apply",
                        "leave:read",
                        "timesheet:submit",
                        "payroll:read",
                    ]
                ],
            ),
        ]

        role_map = {}
        for r_name, r_desc, r_system, r_perms in roles_data:
            res = await session.execute(select(Role).where(Role.name == r_name))
            role = res.scalar_one_or_none()
            if not role:
                role = Role(
                    name=r_name,
                    description=r_desc,
                    is_system=r_system,
                    permissions=r_perms,
                )
                session.add(role)
                await session.flush()
            role_map[r_name] = role.id

        print("Seeding Test User Accounts...")
        users_to_seed = [
            {
                "email": "admin@omnihr.com",
                "password": "AdminPass123!",
                "first_name": "Super",
                "last_name": "Admin",
                "role_name": UserRole.SUPER_ADMIN.value,
                "dept": "Engineering",
                "title": "Senior Software Engineer",
            },
            {
                "email": "hr@omnihr.com",
                "password": "HrPass123!",
                "first_name": "Sarah",
                "last_name": "Jenkins",
                "role_name": UserRole.HR_MANAGER.value,
                "dept": "Human Resources",
                "title": "HR Specialist",
            },
            {
                "email": "lead@omnihr.com",
                "password": "LeadPass123!",
                "first_name": "Michael",
                "last_name": "Scott",
                "role_name": UserRole.DEPARTMENT_LEAD.value,
                "dept": "Engineering",
                "title": "Senior Software Engineer",
            },
            {
                "email": "employee@omnihr.com",
                "password": "EmpPass123!",
                "first_name": "Jim",
                "last_name": "Halpert",
                "role_name": UserRole.EMPLOYEE.value,
                "dept": "Engineering",
                "title": "Senior Software Engineer",
            },
        ]

        created_users = {}
        for udata in users_to_seed:
            res = await session.execute(
                select(User).where(User.email == udata["email"])
            )
            user = res.scalar_one_or_none()
            if not user:
                user = User(
                    email=udata["email"],
                    password_hash=get_password_hash(udata["password"]),
                    first_name=udata["first_name"],
                    last_name=udata["last_name"],
                    role_id=role_map[udata["role_name"]],
                    department_id=dept_map.get(udata["dept"]),
                    designation_id=desig_map.get(udata["title"]),
                    is_active=True,
                )
                session.add(user)
                await session.flush()

                # Add employee profile with complete fields
                profile = EmployeeProfile(
                    user_id=user.id,
                    phone_number="+1-555-0199",
                    address="Scranton, PA",
                    emergency_contact="Pam Beesly (+1-555-0188)",
                    bank_account_number="987654321011",
                    bank_ifsc="OMNI0001234",
                )
                session.add(profile)

            created_users[udata["email"]] = user

        # Set Lead as Manager for Employee
        emp = created_users.get("employee@omnihr.com")
        lead = created_users.get("lead@omnihr.com")
        if emp and lead and not emp.manager_id:
            emp.manager_id = lead.id

        print("Seeding Default Leave Accrual Policies...")
        from app.models.leave import AccrualFrequency, LeaveAccrualPolicy

        # Seed global fallback policies for Casual (1.5 days/mo) and Sick (1.0 day/mo)
        for lt_enum, rate, max_q in [
            (LeaveTypeEnum.CASUAL, 1.5, 18.0),
            (LeaveTypeEnum.SICK, 1.0, 12.0),
            (LeaveTypeEnum.EARNED, 1.25, 30.0),
        ]:
            res = await session.execute(
                select(LeaveType).where(LeaveType.name == lt_enum)
            )
            lt = res.scalar_one_or_none()
            if lt:
                pol_res = await session.execute(
                    select(LeaveAccrualPolicy).where(
                        LeaveAccrualPolicy.leave_type_id == lt.id,
                        LeaveAccrualPolicy.designation_id.is_(None),
                    )
                )
                if not pol_res.scalar_one_or_none():
                    policy = LeaveAccrualPolicy(
                        leave_type_id=lt.id,
                        designation_id=None,
                        frequency=AccrualFrequency.MONTHLY,
                        accrual_rate=rate,
                        max_quota=max_q,
                        is_active=True,
                    )
                    session.add(policy)

        await session.commit()
        print("Database Seeding Completed Successfully!")


if __name__ == "__main__":
    asyncio.run(seed_database())
