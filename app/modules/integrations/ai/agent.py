import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from app.models.user import User
from app.modules.integrations.adapters.slack.block_kit import (
    build_leave_balance_card,
    build_leave_draft_card,
    build_timesheet_card,
)
from app.modules.integrations.ai.groq_client import GroqClient
from app.modules.integrations.ai.tools import AVAILABLE_TOOLS, ToolContext, execute_tool

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    text: str
    blocks: Optional[list[dict[str, Any]]] = None
    draft_data: Optional[dict[str, Any]] = None
    action_type: Optional[str] = None


class AIAgentOrchestrator:
    """Orchestrates multi-turn conversation and function calling loop using Groq LLM."""

    def __init__(self, groq_client: GroqClient):
        self.client = groq_client

    def _build_system_prompt(self, user: User) -> str:
        today_str = date.today().isoformat()
        return (
            f"You are OmniBot, the intelligent, helpful HR assistant for OmniHR. "
            f"You are interacting with employee {user.first_name} {user.last_name} (Email: {user.email}). "
            f"Current date is {today_str}. "
            f"Guidelines:\n"
            f"1. When the user asks to apply for leave, ALWAYS call `draft_leave_application` to validate dates and balance.\n"
            f"2. Never make up dates or balances without calling the appropriate tool.\n"
            f"3. When the user asks about leave balances or holiday calendars, call `get_leave_balances` or `get_company_holidays`.\n"
            f"4. When logging timesheets, use `log_timesheet`.\n"
            f"5. Keep responses courteous, concise, and professional."
        )

    async def process_user_message(
        self, user: User, message_text: str, context: ToolContext
    ) -> AgentResponse:
        """Process an inbound natural language message, executing tools if requested by the LLM."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt(user)},
            {"role": "user", "content": message_text},
        ]

        active_draft: Optional[dict[str, Any]] = None
        timesheet_summary: Optional[dict[str, Any]] = None
        leave_balances_data: Optional[list[dict[str, Any]]] = None

        # Multi-step tool execution loop (max 3 rounds to avoid infinite execution)
        for _ in range(3):
            try:
                response = await self.client.chat_completion(
                    messages=messages,
                    tools=AVAILABLE_TOOLS,
                    tool_choice="auto",
                )
            except Exception as exc:
                logger.error("Error communicating with Groq API: %s", exc)
                return AgentResponse(
                    text="I encountered a temporary issue connecting to the AI reasoning engine. Please try again shortly."
                )

            choice = response.get("choices", [{}])[0]
            choice_message = choice.get("message", {})
            content = choice_message.get("content") or ""
            tool_calls = choice_message.get("tool_calls")

            if not tool_calls:
                # LLM finished execution, return final reply
                blocks = None
                if active_draft:
                    blocks = build_leave_draft_card(active_draft)
                elif timesheet_summary:
                    blocks = build_timesheet_card(timesheet_summary)
                elif leave_balances_data:
                    blocks = build_leave_balance_card(
                        user_name=f"{user.first_name} {user.last_name}",
                        balances=leave_balances_data,
                    )

                return AgentResponse(
                    text=content or "Here is what I found:",
                    blocks=blocks,
                    draft_data=active_draft,
                    action_type="draft_leave" if active_draft else None,
                )

            # Append the assistant's message with tool calls
            messages.append(choice_message)

            # Execute each requested tool call
            for tc in tool_calls:
                call_id = tc.get("id", "call_default")
                function = tc.get("function", {})
                fn_name = function.get("name")
                fn_args_raw = function.get("arguments", "{}")

                try:
                    fn_args = (
                        json.loads(fn_args_raw)
                        if isinstance(fn_args_raw, str)
                        else fn_args_raw
                    )
                except Exception:
                    fn_args = {}

                tool_result = await execute_tool(fn_name, fn_args, context)

                if tool_result.get("action") == "draft_ready":
                    active_draft = tool_result.get("draft_data")
                elif tool_result.get("action") == "timesheet_logged":
                    timesheet_summary = tool_result.get("entry")
                elif fn_name == "get_leave_balances" and tool_result.get("success"):
                    leave_balances_data = tool_result.get("balances")

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": fn_name,
                        "content": json.dumps(tool_result),
                    }
                )

        # Fallback if loop exceeded rounds
        return AgentResponse(
            text="Completed processing your request.",
            blocks=build_leave_draft_card(active_draft) if active_draft else None,
            draft_data=active_draft,
        )
