import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.models.leave import HalfDayType
from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    user: User
    leave_service: Any
    timesheet_service: Any
    project_repository: Any


AVAILABLE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_leave_balances",
            "description": "Get the current employee's remaining leave balances and allocated quotas across all leave types.",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "Target year (e.g. 2026). Defaults to current year.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_leave_application",
            "description": "Prepare a leave application draft for the user to review before final submission. Validates dates, calculates working days (excluding weekends and public holidays), and checks quota availability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "leave_type": {
                        "type": "string",
                        "description": "Leave type, e.g. 'casual', 'sick', 'earned', or 'unpaid'.",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format.",
                    },
                    "half_day_type": {
                        "type": "string",
                        "enum": ["none", "first_half", "second_half"],
                        "description": "Half day selection if applying for a single half day. Defaults to 'none'.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for taking leave.",
                    },
                },
                "required": ["leave_type", "start_date", "end_date", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_company_holidays",
            "description": "Retrieve official company holidays for the current or specified year.",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "Year to fetch holidays for (e.g. 2026).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_timesheet",
            "description": "Log daily work hours and task description against a project for the authenticated user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "Name or code of the project (e.g. 'Mobile App', 'Internal HRMS').",
                    },
                    "work_date": {
                        "type": "string",
                        "description": "Date of work in YYYY-MM-DD format. Defaults to today if omitted.",
                    },
                    "hours": {
                        "type": "number",
                        "description": "Hours spent working (e.g. 4.5, 8.0).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of work or tasks completed.",
                    },
                },
                "required": ["project_name", "hours", "description"],
            },
        },
    },
]


async def execute_tool(
    name: str, arguments: dict[str, Any], context: ToolContext
) -> dict[str, Any]:
    """Execute a registered domain tool within user's security context."""
    user = context.user
    leave_service = context.leave_service
    timesheet_service = context.timesheet_service

    if name == "get_leave_balances":
        year = arguments.get("year", date.today().year)
        balances_raw = await leave_service.get_user_balances(user_id=user.id, year=year)
        balances = []
        for b in balances_raw:
            leave_name = getattr(b, "leave_type_name", None) or getattr(
                b, "leave_type_code", "Leave"
            )
            remaining = getattr(b, "remaining_balance", 0.0)
            allocated = getattr(b, "allocated_days", 0.0)
            balances.append(
                {
                    "leave_type": leave_name,
                    "available_days": remaining,
                    "total_allocated": allocated,
                }
            )
        return {
            "success": True,
            "user_name": f"{user.first_name} {user.last_name}",
            "year": year,
            "balances": balances,
        }

    elif name == "draft_leave_application":
        leave_type_str = arguments.get("leave_type", "").lower()
        start_str = arguments.get("start_date")
        end_str = arguments.get("end_date")
        reason = arguments.get("reason", "").strip()
        half_day_str = arguments.get("half_day_type", "none").lower()

        try:
            start_d = date.fromisoformat(start_str)
            end_d = date.fromisoformat(end_str)
        except Exception as exc:
            return {
                "success": False,
                "error": f"Invalid date format: {exc}. Use YYYY-MM-DD.",
            }

        if start_d > end_d:
            return {"success": False, "error": "Start date must not be after end date."}

        half_day = HalfDayType.NONE
        if half_day_str == "first_half":
            half_day = HalfDayType.FIRST_HALF
        elif half_day_str == "second_half":
            half_day = HalfDayType.SECOND_HALF

        # Calculate working days using company holiday calendar
        holidays_raw = await leave_service.list_holidays(year=start_d.year)
        holiday_dates = {h.date for h in holidays_raw if hasattr(h, "date")}
        total_days = leave_service.calculate_working_days(
            start_date=start_d,
            end_date=end_d,
            half_day_type=half_day,
            holiday_dates=holiday_dates,
        )

        if total_days <= 0:
            return {
                "success": False,
                "error": "Selected date range contains 0 working days (weekends or public holidays).",
            }

        # Validate quota
        balances_raw = await leave_service.get_user_balances(
            user_id=user.id, year=start_d.year
        )
        matched_balance = None
        matched_type_name = leave_type_str.capitalize()
        matched_type_id = None

        try:
            leave_types = await leave_service.get_leave_types()
            for lt in leave_types:
                lt_name = lt.name.value if hasattr(lt.name, "value") else str(lt.name)
                if leave_type_str in lt_name.lower():
                    matched_type_id = str(lt.id)
                    matched_type_name = lt_name
                    break
        except Exception:
            pass

        for b in balances_raw:
            code = getattr(b, "leave_type_code", "").lower()
            name_val = getattr(b, "leave_type_name", "").lower()
            if leave_type_str in code or leave_type_str in name_val:
                matched_balance = b
                matched_type_name = getattr(b, "leave_type_name", matched_type_name)
                break

        remaining_days = (
            getattr(matched_balance, "remaining_balance", None)
            if matched_balance
            else None
        )
        if (
            remaining_days is not None
            and remaining_days < total_days
            and "unpaid" not in leave_type_str
        ):
            return {
                "success": False,
                "error": (
                    f"Insufficient leave balance for {matched_type_name}. "
                    f"Required: {total_days} days, Available: {remaining_days} days."
                ),
            }

        draft_payload = {
            "leave_type": matched_type_name,
            "leave_type_code": leave_type_str,
            "leave_type_id": matched_type_id,
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "half_day_type": half_day.value,
            "total_days": total_days,
            "reason": reason,
            "user_id": str(user.id),
        }

        return {
            "success": True,
            "action": "draft_ready",
            "message": (
                f"Draft prepared for {total_days} day(s) of {matched_type_name} "
                f"from {start_d} to {end_d}."
            ),
            "draft_data": draft_payload,
        }

    elif name == "get_company_holidays":
        target_year = arguments.get("year", date.today().year)
        holidays_raw = await leave_service.list_holidays(year=target_year)
        holidays_list = []
        for h in holidays_raw:
            holidays_list.append(
                {
                    "name": getattr(h, "name", "Holiday"),
                    "date": h.date.isoformat() if hasattr(h, "date") else str(h),
                    "description": getattr(h, "description", None),
                }
            )
        return {"success": True, "year": target_year, "holidays": holidays_list}

    elif name == "log_timesheet":
        project_name = arguments.get("project_name", "").strip()
        work_date_str = arguments.get("work_date") or date.today().isoformat()
        hours = float(arguments.get("hours", 0.0))
        description = arguments.get("description", "").strip()

        try:
            work_d = date.fromisoformat(work_date_str)
        except Exception:
            work_d = date.today()

        # Find matching project from database
        project_repo = context.project_repository
        projects, _ = await project_repo.list_paginated(limit=50)
        target_project = None
        for p in projects:
            if project_name.lower() in p.name.lower() or (
                p.code and project_name.lower() in p.code.lower()
            ):
                target_project = p
                break

        if not target_project:
            available_names = [p.name for p in projects[:5]]
            return {
                "success": False,
                "error": f"Project '{project_name}' not found. Available projects: {', '.join(available_names)}.",
            }

        # Create Timesheet entry
        from app.modules.timesheets.schemas import TimesheetCreatePayload

        payload = TimesheetCreatePayload(
            project_id=target_project.id,
            date=work_d,
            duration_minutes=int(hours * 60),
            task_description=description,
        )
        entry = await timesheet_service.create_entry(user_id=user.id, payload=payload)

        return {
            "success": True,
            "action": "timesheet_logged",
            "entry": {
                "id": str(entry.id),
                "project_name": target_project.name,
                "date": work_d.isoformat(),
                "hours": hours,
                "description": description,
            },
        }

    return {"success": False, "error": f"Unknown tool: '{name}'"}
