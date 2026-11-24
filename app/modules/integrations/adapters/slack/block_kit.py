import json
from typing import Any


def build_leave_draft_card(
    draft_data: dict[str, Any],
    action_id: str = "confirm_leave_submission",
    cancel_action_id: str = "cancel_leave_submission",
) -> list[dict[str, Any]]:
    """Build an interactive Slack Block Kit message for reviewing and confirming a leave application."""
    leave_type = draft_data.get("leave_type", "Leave")
    start_date = draft_data.get("start_date", "")
    end_date = draft_data.get("end_date", "")
    total_days = draft_data.get("total_days", 1.0)
    reason = draft_data.get("reason", "Not specified")

    serialized_payload = json.dumps(draft_data)

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🏖️ Review Leave Application Draft",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Please review your leave details before submitting for approval:\n\n"
                    f"• *Leave Type:* `{leave_type}`\n"
                    f"• *Dates:* *{start_date}* to *{end_date}*\n"
                    f"• *Duration:* *{total_days}* working day(s)\n"
                    f"• *Reason:* _{reason}_"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "✅ Confirm & Submit",
                        "emoji": True,
                    },
                    "style": "primary",
                    "action_id": action_id,
                    "value": serialized_payload,
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "❌ Cancel",
                        "emoji": True,
                    },
                    "style": "danger",
                    "action_id": cancel_action_id,
                    "value": json.dumps({"action": "cancel"}),
                },
            ],
        },
    ]


def build_leave_balance_card(
    user_name: str, balances: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build a Block Kit card summarizing an employee's leave balances."""
    fields = []
    for b in balances:
        name = b.get("leave_type", "Leave")
        avail = b.get("available_days", 0.0)
        alloc = b.get("total_allocated", 0.0)
        fields.append(
            {
                "type": "mrkdwn",
                "text": f"*{name}*\n{avail} / {alloc} days available",
            }
        )

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 Leave Balances for {user_name}",
                "emoji": True,
            },
        },
        {"type": "divider"},
    ]

    if fields:
        # Block Kit sections accept max 10 fields per section block
        for i in range(0, len(fields), 10):
            blocks.append(
                {
                    "type": "section",
                    "fields": fields[i : i + 10],
                }
            )
    else:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_No active leave allocations found for your profile._",
                },
            }
        )

    return blocks


def build_timesheet_card(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a Block Kit card confirming a logged timesheet entry."""
    project_name = summary.get("project_name", "Project")
    work_date = summary.get("date", "")
    hours = summary.get("hours", 0.0)
    description = summary.get("description", "")

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "⏱️ Timesheet Logged Successfully",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Project:*\n`{project_name}`"},
                {"type": "mrkdwn", "text": f"*Date:*\n{work_date}"},
                {"type": "mrkdwn", "text": f"*Hours Logged:*\n*{hours} hrs*"},
                {"type": "mrkdwn", "text": f"*Task:*\n_{description}_"},
            ],
        },
    ]


def build_status_card(
    title: str, message: str, status: str = "success"
) -> list[dict[str, Any]]:
    """Build a status replacement card (e.g. after clicking confirm/cancel button)."""
    icon = "✅" if status == "success" else "❌" if status == "cancelled" else "ℹ️"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{icon} *{title}*\n{message}",
            },
        }
    ]
