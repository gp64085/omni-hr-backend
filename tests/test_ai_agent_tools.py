import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.integrations.ai.agent import AIAgentOrchestrator
from app.modules.integrations.ai.groq_client import GroqClient
from app.modules.integrations.ai.tools import (
    ToolContext,
    execute_tool,
)


@pytest.mark.asyncio
async def test_groq_client_chat_completion(monkeypatch):
    client = GroqClient(api_key="gsk_test_key", model="llama-3.3-70b-versatile")

    async def mock_post(*args, **kwargs):
        class MockResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "You have 10 casual leaves left.",
                                "tool_calls": None,
                            }
                        }
                    ]
                }

        return MockResponse()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    result = await client.chat_completion(
        messages=[{"role": "user", "content": "What is my balance?"}]
    )
    assert (
        result["choices"][0]["message"]["content"] == "You have 10 casual leaves left."
    )


@pytest.mark.asyncio
async def test_execute_get_leave_balances_tool():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.first_name = "Jane"
    user.last_name = "Doe"

    leave_service = MagicMock()
    balance_mock = MagicMock()
    balance_mock.leave_type_name = "Casual Leave"
    balance_mock.leave_type_code = "casual"
    balance_mock.remaining_balance = 8.0
    balance_mock.allocated_days = 12.0
    leave_service.get_user_balances = AsyncMock(return_value=[balance_mock])

    context = ToolContext(
        user=user,
        leave_service=leave_service,
        timesheet_service=MagicMock(),
        project_repository=MagicMock(),
    )

    res = await execute_tool("get_leave_balances", {}, context)
    assert res["success"] is True
    assert len(res["balances"]) == 1
    assert res["balances"][0]["leave_type"] == "Casual Leave"
    assert res["balances"][0]["available_days"] == 8.0


@pytest.mark.asyncio
async def test_execute_draft_leave_application_tool():
    user = MagicMock()
    user.id = uuid.uuid4()

    leave_service = MagicMock()
    leave_service.calculate_working_days = MagicMock(return_value=2.0)
    leave_service.list_holidays = AsyncMock(return_value=[])

    balance_mock = MagicMock()
    balance_mock.leave_type_name = "Casual Leave"
    balance_mock.leave_type_code = "casual"
    balance_mock.remaining_balance = 5.0
    balance_mock.allocated_days = 10.0
    leave_service.get_user_balances = AsyncMock(return_value=[balance_mock])

    context = ToolContext(
        user=user,
        leave_service=leave_service,
        timesheet_service=MagicMock(),
        project_repository=MagicMock(),
    )

    args = {
        "leave_type": "casual",
        "start_date": "2026-12-10",
        "end_date": "2026-12-11",
        "reason": "Family vacation",
    }
    res = await execute_tool("draft_leave_application", args, context)
    assert res["success"] is True
    assert res["action"] == "draft_ready"
    assert res["draft_data"]["total_days"] == 2.0
    assert res["draft_data"]["leave_type"] == "Casual Leave"
    # Ensure apply_leave was NOT called yet (2-phase confirmation guard)
    assert (
        not hasattr(leave_service, "apply_leave")
        or not leave_service.apply_leave.called
    )


@pytest.mark.asyncio
async def test_agent_orchestrator_flow():
    client_mock = MagicMock()
    # Step 1: LLM decides to call draft_leave_application
    step1_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "function": {
                                "name": "draft_leave_application",
                                "arguments": '{"leave_type": "casual", "start_date": "2026-12-10", "end_date": "2026-12-11", "reason": "Rest"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    # Step 2: LLM replies with confirmation text after seeing tool output
    step2_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "I have prepared your casual leave draft. Please click confirm below.",
                    "tool_calls": None,
                }
            }
        ]
    }

    client_mock.chat_completion = AsyncMock(
        side_effect=[step1_response, step2_response]
    )
    agent = AIAgentOrchestrator(client_mock)

    user = MagicMock()
    user.id = uuid.uuid4()
    user.first_name = "Jane"
    user.last_name = "Doe"
    user.email = "jane@example.com"

    leave_service = MagicMock()
    leave_service.calculate_working_days = MagicMock(return_value=2.0)
    leave_service.list_holidays = AsyncMock(return_value=[])
    balance_mock = MagicMock()
    balance_mock.leave_type_name = "Casual Leave"
    balance_mock.leave_type_code = "casual"
    balance_mock.remaining_balance = 5.0
    balance_mock.allocated_days = 10.0
    leave_service.get_user_balances = AsyncMock(return_value=[balance_mock])

    context = ToolContext(
        user=user,
        leave_service=leave_service,
        timesheet_service=MagicMock(),
        project_repository=MagicMock(),
    )

    result = await agent.process_user_message(
        user, "Apply casual leave Dec 10-11", context
    )
    assert "casual leave draft" in result.text
    assert result.draft_data is not None
    assert result.draft_data["total_days"] == 2.0
    assert result.blocks is not None
    assert len(result.blocks) >= 3
