# OmniHR Backend Service

High-performance Enterprise HR Management System API built with **FastAPI**, **Async SQLAlchemy 2.0**, **PostgreSQL**, **Redis**, and managed with **uv**.

---

## 🛠️ Technology Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Python 3.13+)
- **Database & ORM**: PostgreSQL with [SQLAlchemy 2.0](https://www.sqlalchemy.org/) (Async Engine & `aiosqlite` in-memory test suite)
- **Caching**: Redis via `redis-py` (route-level cache decorators with prefix invalidation)
- **Authentication**: OAuth2 JWT Tokens (Access & Refresh tokens) + Password Hashing via Passlib / Bcrypt
- **Background Scheduler**: Asyncio-based midnight cron runner for automated leave accruals
- **Package Manager**: [uv](https://github.com/astral-sh/uv)
- **Formatting & Linting**: [Ruff](https://github.com/astral-sh/ruff)
- **Testing**: [Pytest](https://docs.pytest.org/) with `pytest-asyncio` & `httpx`

---

## 🚀 Key Modules & Features

### 1. Authentication & RBAC (Role-Based Access Control)
- **JWT Auth**: Login, Refresh token rotation, and Logout with token revocation.
- **Dynamic System & Custom Roles**: Create custom roles with granular permission codes.
- **Permission Guards**: Declarative role and granular permission enforcement across protected endpoints.

### 2. User & Profile Management
- Self-service profile updates (contacts, bank info, emergency details).
- Complete User CRUD for HR Admins with role assignment validation.

### 3. Leave Management & Approvals Suite
- **Working Day Calculation**: Automatically excludes weekends (Sat/Sun) and official company holidays.
- **Overlapping Leave Validation**: Prevents conflicting pending or approved leave applications.
- **Auto-Approval Engine**: Auto-approves requests meeting thresholds or non-approval-required types.
- **Multi-tier Approval Workflow**: Manager (Tier 1) and HR (Tier 2) approvals with status audit logs.
- **Leave Cancellations**: Restores used quota for future cancelled leaves.

### 4. Dynamic Periodic Leave Accrual Engine
- **Configurable Frequencies**: Supports `monthly`, `quarterly`, `half_yearly`, `yearly`, and `manual` accrual policies per role/type.
- **Automated Midnight Cron**: Background scheduler runs daily at 00:00 to accrue leave credits automatically up to configured `max_quota`.
- **Manual HR Grants**: Direct leave allocation grants for joining bonuses or custom adjustments.
- **Idempotency Guard**: Tracks `last_accrual_date` to eliminate double-crediting.

### 5. Projects & Timesheet Tracking
- **Multi-Department Project Allocation**: Flexible project configuration supporting both organization-wide initiatives and department-specific projects.
- **Department-Based Filtering**: Retrieve active projects relevant to a specific department or accessible company-wide.
- **Time Logging & Approval Workflow**: Track daily employee work hours, billable vs non-billable tasks, and manage approval workflows.

---

## 📁 Repository Structure

```
backend/
├── app/
│   ├── api/
│   │   ├── deps.py              # FastAPI dependencies (auth, permissions, services)
│   │   └── v1/
│   │       └── router.py        # Central API v1 router aggregator
│   ├── core/
│   │   ├── config.py            # Pydantic BaseSettings environment config
│   │   ├── redis.py             # Async Redis connection pool management
│   │   ├── scheduler.py         # Midnight background accrual cron loop
│   │   ├── security.py          # JWT generation, token verification & password hashing
│   │   └── services/
│   │       └── cache_service.py # Redis route caching decorator & prefix invalidation
│   ├── db/
│   │   └── session.py           # SQLAlchemy async engine & session factory
│   ├── models/                  # Declarative SQLAlchemy domain models
│   │   ├── user.py              # User & EmployeeProfile models
│   │   ├── role.py              # Role, Permission, RolePermission models
│   │   ├── leave.py             # LeaveType, LeaveAllocation, LeaveRequest, LeaveAccrualPolicy
│   │   └── holiday.py           # CompanyHoliday model
│   ├── modules/                 # Domain feature modules (Repository & Service patterns)
│   │   ├── auth/                # Auth routes, schemas, service
│   │   ├── users/               # Users routes, repository, service
│   │   ├── roles/               # Roles & permissions routes, repository, service
│   │   └── leaves/              # Leave management routes, repository, service
│   ├── schemas/                 # Pydantic DTOs & standardized response envelopes
│   └── main.py                  # FastAPI application entrypoint & lifespan
├── scripts/
│   └── seed_db.py               # Database seeder for system roles & initial admin account
├── tests/                       # Complete Pytest async test suite
└── pyproject.toml               # Dependencies, Ruff, and Pytest configuration
```

---

## ⚡ Quickstart Guide

### Prerequisites
- Python 3.13+
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Redis server running locally or via Docker

### 1. Environment Configuration

Create a `.env` file in `backend/`:

```env
PROJECT_NAME="OmniHR Enterprise API"
SECRET_KEY="your-super-secret-jwt-key"
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/omnihr"
REDIS_URL="redis://localhost:6379/0"
BACKEND_CORS_ORIGINS=["http://localhost:3000"]
```

### 2. Install Dependencies

```bash
uv sync
```

### 3. Seed Database

Seed system roles (`super_admin`, `hr_manager`, `department_lead`, `employee`), default permissions, leave types, and the initial Super Admin user:

```bash
uv run python scripts/seed_db.py
```

### 4. Start Development Server

```bash
uv run uvicorn app.main:app --reload --port 8000
```

- **Interactive API Docs (Swagger UI)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Documentation**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## 🧪 Testing & Code Quality

### Run Test Suite

```bash
uv run pytest
```

### Linting & Formatting

```bash
# Run Ruff formatter
uv run ruff format .

# Run Ruff linter with auto-fix
uv run ruff check --fix .
```

---

## 🗄️ Database Migrations (Alembic)

Database schema migrations are managed via **Alembic** with async PostgreSQL support (`asyncpg`).

### 🔄 Sequential Migration Workflow (Step-by-Step)

Follow these commands in sequence whenever you add or modify SQLAlchemy models:

#### 1. Export New Models
Ensure any new SQLAlchemy model in `app/models/` is exported in `app/models/__init__.py` so Alembic detects it.

#### 2. Check Current Migration Status
```bash
uv run alembic current
```

#### 3. Ensure Existing Migrations are Applied
```bash
uv run alembic upgrade head
```

> **Note**: If Alembic outputs `Target database is not up to date` because tables were auto-created via `Base.metadata.create_all`, stamp the current head revision directly:
> ```bash
> uv run alembic stamp head
> ```

#### 4. Autogenerate New Migration Script
```bash
uv run alembic revision --autogenerate -m "describe_your_changes"
```

#### 5. Apply New Migration to Database
```bash
uv run alembic upgrade head
```

---

### ℹ️ Utility & Rollback Commands

```bash
# View full migration revision history
uv run alembic history

# Rollback the last applied migration step
uv run alembic downgrade -1
```


