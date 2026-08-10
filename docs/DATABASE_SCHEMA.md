# Database Schema Blueprint: OmniHR (PostgreSQL)

## 1. Enums & Custom Types
```sql
CREATE TYPE leave_status AS ENUM ('pending', 'approved', 'rejected', 'cancelled');
CREATE TYPE leave_type_enum AS ENUM ('casual', 'sick', 'earned', 'unpaid');
CREATE TYPE half_day_type_enum AS ENUM ('none', 'first_half', 'second_half');
CREATE TYPE pay_run_status AS ENUM ('draft', 'processing', 'completed', 'failed');
```

## 2. Core Tables DDL

### Users & Organization
```sql
CREATE TABLE departments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE designations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    description VARCHAR(255),
    is_system BOOLEAN DEFAULT FALSE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(100) NOT NULL UNIQUE,
    module VARCHAR(50) NOT NULL,
    description VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE role_permissions (
    role_id UUID REFERENCES roles(id) ON DELETE CASCADE,
    permission_id UUID REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    role_id UUID REFERENCES roles(id) ON DELETE SET NULL,
    department_id UUID REFERENCES departments(id) ON DELETE SET NULL,
    designation_id UUID REFERENCES designations(id) ON DELETE SET NULL,
    manager_id UUID REFERENCES users(id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE employee_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE UNIQUE NOT NULL,
    phone_number VARCHAR(20),
    emergency_contact VARCHAR(100),
    address TEXT,
    bank_account_number VARCHAR(50),
    bank_name VARCHAR(100),
    ifsc_swift_code VARCHAR(20),
    pan_ssn VARCHAR(50),
    joining_date DATE NOT NULL DEFAULT CURRENT_DATE,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    token_hash VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    is_revoked BOOLEAN DEFAULT FALSE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Company Holidays Calendar
```sql
CREATE TABLE company_holidays (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(150) NOT NULL,
    holiday_date DATE NOT NULL UNIQUE,
    is_optional BOOLEAN DEFAULT FALSE NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Leave Management & Approvals
```sql
CREATE TABLE leave_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name leave_type_enum UNIQUE NOT NULL,
    default_quota NUMERIC(5,2) NOT NULL CHECK (default_quota >= 0),
    requires_approval BOOLEAN DEFAULT TRUE,
    auto_approve_threshold INT DEFAULT 0 -- 0 means off, 1 means auto-approve 1 day leaves
);

CREATE TABLE leave_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    leave_type_id UUID REFERENCES leave_types(id) ON DELETE CASCADE NOT NULL,
    year INT NOT NULL CHECK (year >= 2000 AND year <= 2100),
    allocated_days NUMERIC(5,2) NOT NULL CHECK (allocated_days >= 0),
    used_days NUMERIC(5,2) DEFAULT 0.00 NOT NULL CHECK (used_days >= 0),
    comp_off_credits NUMERIC(5,2) DEFAULT 0.00 NOT NULL CHECK (comp_off_credits >= 0),
    UNIQUE(user_id, leave_type_id, year)
);

CREATE TABLE leave_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    leave_type_id UUID REFERENCES leave_types(id) ON DELETE CASCADE NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    half_day_type half_day_type_enum DEFAULT 'none' NOT NULL,
    total_days NUMERIC(5,2) NOT NULL CHECK (total_days > 0),
    status leave_status DEFAULT 'pending' NOT NULL,
    is_auto_approved BOOLEAN DEFAULT FALSE NOT NULL,
    reason TEXT,
    approver_id UUID REFERENCES users(id) ON DELETE SET NULL,
    rejection_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT check_dates CHECK (end_date >= start_date)
);

CREATE TABLE leave_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    leave_request_id UUID REFERENCES leave_requests(id) ON DELETE CASCADE NOT NULL,
    approver_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    tier_level INT NOT NULL DEFAULT 1, -- 1 for Manager, 2 for HR
    status leave_status NOT NULL,
    comments TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for overlapping leave validation query performance
CREATE INDEX idx_leave_requests_user_dates ON leave_requests (user_id, start_date, end_date) WHERE status IN ('pending', 'approved');
```

### Projects, Tasks & Daily Timesheets
```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(150) NOT NULL UNIQUE,
    code VARCHAR(50) NOT NULL UNIQUE,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE project_departments (
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    department_id UUID REFERENCES departments(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, department_id)
);

CREATE TABLE timesheet_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    work_date DATE NOT NULL,
    hours_spent NUMERIC(4,2) NOT NULL CHECK (hours_spent > 0 AND hours_spent <= 24),
    is_billable BOOLEAN DEFAULT TRUE NOT NULL,
    activity_summary TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'draft' NOT NULL, -- draft, submitted, approved, rejected
    approver_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_timesheets_user_date ON timesheet_entries (user_id, work_date);
```

### Payroll & Payslips
```sql
CREATE TABLE salary_structures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE UNIQUE NOT NULL,
    base_salary NUMERIC(12,2) NOT NULL CHECK (base_salary >= 0),
    allowances NUMERIC(12,2) DEFAULT 0.00 CHECK (allowances >= 0),
    deductions NUMERIC(12,2) DEFAULT 0.00 CHECK (deductions >= 0),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE pay_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pay_month VARCHAR(7) NOT NULL UNIQUE, -- Format 'YYYY-MM'
    status pay_run_status DEFAULT 'draft' NOT NULL,
    executed_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE payslips (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pay_run_id UUID REFERENCES pay_runs(id) ON DELETE CASCADE NOT NULL,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    base_pay NUMERIC(12,2) NOT NULL CHECK (base_pay >= 0),
    allowances NUMERIC(12,2) DEFAULT 0.00 CHECK (allowances >= 0),
    lop_deductions NUMERIC(12,2) DEFAULT 0.00 CHECK (lop_deductions >= 0),
    other_deductions NUMERIC(12,2) DEFAULT 0.00 CHECK (other_deductions >= 0),
    gross_pay NUMERIC(12,2) NOT NULL CHECK (gross_pay >= 0),
    net_pay NUMERIC(12,2) NOT NULL CHECK (net_pay >= 0),
    pdf_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(pay_run_id, user_id)
);
```

### Audit Logs & Notifications
```sql
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    entity VARCHAR(50) NOT NULL,
    entity_id UUID,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Range Partitioning for audit_logs performance at scale
-- CREATE TABLE audit_logs_y2026m07 PARTITION OF audit_logs FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    title VARCHAR(200) NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN DEFAULT FALSE NOT NULL,
    link_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Attendance & Shifts
```sql
CREATE TABLE attendance_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    clock_in TIMESTAMPTZ NOT NULL,
    clock_out TIMESTAMPTZ,
    ip_address VARCHAR(45),
    location_gis VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Expenses & Reimbursements
```sql
CREATE TABLE expense_claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    category VARCHAR(100) NOT NULL,
    amount NUMERIC(10,2) NOT NULL CHECK (amount > 0),
    receipt_url TEXT,
    description TEXT,
    status leave_status DEFAULT 'pending' NOT NULL,
    approver_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Applicant Tracking System (ATS)
```sql
CREATE TABLE job_requisitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(150) NOT NULL,
    department_id UUID REFERENCES departments(id) ON DELETE SET NULL,
    headcount_target INT NOT NULL CHECK (headcount_target > 0),
    min_experience_years INT DEFAULT 0,
    status VARCHAR(50) DEFAULT 'open' NOT NULL, -- draft, open, filled, cancelled
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requisition_id UUID REFERENCES job_requisitions(id) ON DELETE SET NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone_number VARCHAR(30),
    resume_url TEXT,
    stage VARCHAR(50) DEFAULT 'applied' NOT NULL, -- applied, screening, interview, offer, hired, rejected
    rating INT CHECK (rating >= 1 AND rating <= 5),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Document Vault & Compliance
```sql
CREATE TABLE employee_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    document_type VARCHAR(100) NOT NULL, -- passport, visa, contract, tax_form, certification
    document_name VARCHAR(200) NOT NULL,
    file_url TEXT NOT NULL,
    expires_at DATE,
    is_verified BOOLEAN DEFAULT FALSE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Hardware Assets & IT Provisioning
```sql
CREATE TABLE hardware_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_tag VARCHAR(100) UNIQUE NOT NULL,
    category VARCHAR(100) NOT NULL, -- laptop, monitor, mobile, peripheral
    model_name VARCHAR(150) NOT NULL,
    serial_number VARCHAR(100) UNIQUE,
    assigned_to UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_date DATE,
    status VARCHAR(50) DEFAULT 'in_stock' NOT NULL, -- in_stock, assigned, in_repair, retired
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```