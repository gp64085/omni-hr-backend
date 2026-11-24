# API Contract & Specification: OmniHR

## 1. General Conventions
*   **Base URL:** `/api/v1`
*   **Authentication Header:** `Authorization: Bearer <JWT_TOKEN>`
*   **Standard Response Format (Success):**
    ```json
    {
      "success": true,
      "data": { ... },
      "error": null,
      "meta": {
        "page": 1,
        "limit": 20,
        "total": 100
      }
    }
    ```
*   **Standard Error Format:**
    ```json
    {
      "success": false,
      "data": null,
      "error": {
        "code": "OVERLAPPING_LEAVE_REQUEST",
        "message": "Leave request overlaps with an existing pending or approved leave.",
        "details": { "conflicting_request_id": "uuid-here" }
      }
    }
    ```

## 2. HTTP Status Codes & Error Taxonomy
| HTTP Code | Error Code | Description |
|---|---|---|
| `400 Bad Request` | `INVALID_INPUT` / `DATE_MISMATCH` | Malformed parameters or invalid date ranges (`end_date < start_date`). |
| `401 Unauthorized` | `UNAUTHORIZED` / `TOKEN_EXPIRED` | Missing, expired, or revoked JWT token. |
| `403 Forbidden` | `FORBIDDEN` / `INSUFFICIENT_PERMISSIONS` | Authenticated user lacks required role/permissions for operation. |
| `404 Not Found` | `RESOURCE_NOT_FOUND` | User, leave request, or pay run ID does not exist. |
| `409 Conflict` | `OVERLAPPING_LEAVE` / `DUPLICATE_PAY_RUN` | Overlapping leave application or duplicate monthly pay run. |
| `422 Unprocessable` | `INSUFFICIENT_LEAVE_BALANCE` | Requested days exceed remaining allocated leave balance. |
| `500 Server Error` | `INTERNAL_SERVER_ERROR` | Unexpected server fault or database execution failure. |

## 3. Detailed Endpoint Specs

### 3.1 Authentication & Profile
*   `POST /api/v1/auth/login` - Authenticate with email/password & return JWT access + refresh tokens.
*   `POST /api/v1/auth/refresh` - Rotate refresh token to obtain a new access token.
*   `POST /api/v1/auth/logout` - Revoke current refresh token and invalidate session.
*   `POST /api/v1/auth/forgot-password` - Request a password reset link. Anti-enumeration design returns generic 200 OK regardless of email existence. Dispatches reset link with 15-minute validity; preserves current active sessions.
*   `POST /api/v1/auth/reset-password` - Complete password reset using token and new password (min 8 chars). Validates token expiry and single-use, updates password hash, marks token used, and revokes all active refresh tokens for the user.
*   `GET /api/v1/users/me` - Fetch current user info, active `role`, and assigned `permissions`.
*   `GET /api/v1/users/me/profile` - Fetch self-service profile details (phone, bank, emergency contacts).
*   `PUT /api/v1/users/me/profile` - Update allowed self-service profile fields.

### 3.2 Roles & Permissions Management (RBAC)
*   `GET /api/v1/roles` - Searchable, paginated roles list (`page`, `limit`, `search`) returning role metadata and `permission_ids` array.
*   `POST /api/v1/roles` - Create a new custom role with assigned permission IDs (Super Admin only).
*   `GET /api/v1/roles/{role_id}` - Get detailed role specifications and permission IDs.
*   `GET /api/v1/roles/{role_id}/permissions` - Dedicated endpoint to fetch full permissions list for a specific role.
*   `PUT /api/v1/roles/{role_id}` - Update role metadata and permissions list (Super Admin only).
*   `DELETE /api/v1/roles/{role_id}` - Delete custom role (Protected against deleting system roles or roles in use).
*   `GET /api/v1/permissions` - Searchable, paginated system permissions list (`page`, `limit`, `search`, `module`).
*   `POST /api/v1/permissions` - Register new system permission code (Super Admin only).

### 3.3 User & Department Directory (Admin/HR/Lead)
*   `GET /api/v1/users` - Searchable, paginated user list with filters by `department_id`, `role_id`, or `role_name`.
*   `POST /api/v1/users` - Create user account with optional `role_id` (defaults to Employee system role if omitted).
*   `GET /api/v1/departments` - List all departments.

### 3.3 Leaves & Approvals Module
*   `GET /api/v1/leaves/balance` - Get leave allocations, remaining balances, and comp-off credits.
*   `POST /api/v1/leaves/requests` - Submit leave request (Evaluates auto-approval policies and triggers Tier 1/2 workflow).
*   `GET /api/v1/leaves/requests` - List leave requests (filterable by status, tier, department, user, date range).
*   `PATCH /api/v1/leaves/requests/{id}/status` - Approve or reject leave request at Tier 1 (Manager) or Tier 2 (HR).
*   `DELETE /api/v1/leaves/requests/{id}` - Cancel pending or upcoming leave request.

### 3.4 Daily Timesheets & Time Tracking
*   `POST /api/v1/timesheets` - Log daily work status, activity summary, and time spent on projects.
*   `GET /api/v1/timesheets` - Fetch daily/weekly timesheet entries.
*   `PATCH /api/v1/timesheets/submit` - Submit weekly timesheet for manager review.
*   `PATCH /api/v1/timesheets/{id}/status` - Approve or reject timesheet entry (Manager role).

### 3.4 Company Holidays Calendar
*   `GET /api/v1/holidays` - Fetch company holiday calendar for specified year.
*   `POST /api/v1/holidays` - Add company holiday (HR Manager / Admin only).

### 3.5 Payroll Module
*   `GET /api/v1/payroll/structures/{user_id}` - Fetch salary structure (Base, allowances, deductions).
*   `POST /api/v1/payroll/runs` - Initiate a monthly pay run (calculates LOP based on unpaid leaves).
*   `GET /api/v1/payroll/runs` - List monthly pay runs with status (`draft`, `processing`, `completed`).
*   `GET /api/v1/payroll/payslips` - Fetch payslip list or download signed PDF link.

### 3.6 Attendance & Shifts
*   `POST /api/v1/attendance/clock-in` - Record employee clock-in timestamp with IP/location.
*   `POST /api/v1/attendance/clock-out` - Record clock-out timestamp.
*   `GET /api/v1/attendance/logs` - Fetch daily/monthly attendance logs.

### 3.7 Expenses & Claims
*   `POST /api/v1/expenses` - Submit reimbursement claim with receipt attachment.
*   `GET /api/v1/expenses` - List expense claims for approval or payout.
*   `PATCH /api/v1/expenses/{id}/status` - Approve/reject claim.

### 3.8 Real-Time Notifications & Analytics
*   `GET /api/v1/notifications` - Fetch user's unread notifications.
*   `GET /api/v1/analytics/dashboard` - HR analytics metrics (headcount, leave utilization, turnover rates).

### 3.9 Applicant Tracking System (ATS)
*   `GET /api/v1/recruitment/jobs` - List open job requisitions.
*   `POST /api/v1/recruitment/candidates` - Add or parse candidate application.
*   `PATCH /api/v1/recruitment/candidates/{id}/stage` - Move candidate stage across pipeline (e.g. `interview` -> `offer` -> `hired`).

### 3.10 Document Vault & IT Assets
*   `POST /api/v1/documents` - Upload secure document (visa, contract, certification).
*   `GET /api/v1/documents/expiring` - Fetch documents expiring within 30 days.
*   `GET /api/v1/assets` - List IT assets and current assignments.

### 3.11 AI Chatbot Endpoint (OmniBot)
*   `POST /api/v1/ai/chat` - Send natural language message to OmniBot agent.
    *   *Request Body:*
        ```json
        {
          "session_id": "uuid-or-string",
          "message": "Apply for casual leave next Monday and Tuesday"
        }
        ```
    *   *Response Body:*
        ```json
        {
          "success": true,
          "data": {
            "reply": "I have created a pending casual leave request for Aug 3 to Aug 4, 2026 (2 working days). Your request is pending manager approval.",
            "action_executed": "apply_leave_request",
            "metadata": {
              "request_id": "uuid-here",
              "total_days": 2.0,
              "status": "pending"
            }
          }
        }
        ```