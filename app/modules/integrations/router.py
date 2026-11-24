import json
import logging
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import (
    ProtectedAPIRouter,
    get_integration_service,
    get_leave_service,
    get_project_repository,
    get_timesheet_service,
    get_user_repository,
    require_roles,
)
from app.models.leave import HalfDayType, LeaveStatus
from app.models.user import UserRole
from app.modules.integrations.adapters.slack.adapter import SlackMessagingAdapter
from app.modules.integrations.adapters.slack.block_kit import build_status_card
from app.modules.integrations.ai.agent import AIAgentOrchestrator
from app.modules.integrations.ai.groq_client import GroqClient
from app.modules.integrations.ai.tools import ToolContext
from app.modules.integrations.schemas import (
    IntegrationResponse,
    SaveIntegrationRequest,
    TestConnectionRequest,
    TestConnectionResponse,
)
from app.modules.integrations.service import IntegrationService
from app.modules.leaves.schemas import LeaveRequestCreate
from app.modules.leaves.service import LeaveService
from app.modules.projects.repository import ProjectRepository
from app.modules.timesheets.service import TimesheetService
from app.modules.users.repository import UserRepository
from app.schemas.common import StandardResponse

logger = logging.getLogger(__name__)

integrations_admin_router = ProtectedAPIRouter(
    dependencies=[Depends(require_roles([UserRole.SUPER_ADMIN]))]
)
integrations_public_router = APIRouter()


# -----------------------------------------------------------------------------
# Admin Configuration Endpoints
# -----------------------------------------------------------------------------


@integrations_admin_router.get(
    "",
    response_model=StandardResponse[list[IntegrationResponse]],
    summary="List all third-party integrations with masked credentials",
)
async def list_integrations(
    service: IntegrationService = Depends(get_integration_service),
):
    configs = await service.list_configs()
    return StandardResponse.ok(data=configs)


@integrations_admin_router.get(
    "/{platform}",
    response_model=StandardResponse[IntegrationResponse],
    summary="Get integration configuration by platform with masked credentials",
)
async def get_integration(
    platform: str,
    service: IntegrationService = Depends(get_integration_service),
):
    config = await service.get_masked_config(platform)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Integration configuration for '{platform}' not found.",
        )
    return StandardResponse.ok(data=config)


@integrations_admin_router.put(
    "/{platform}",
    response_model=StandardResponse[IntegrationResponse],
    summary="Create or update integration configuration",
)
async def save_integration(
    platform: str,
    payload: SaveIntegrationRequest,
    service: IntegrationService = Depends(get_integration_service),
):
    try:
        saved = await service.save_config(
            platform=platform,
            credentials=payload.credentials,
            is_enabled=payload.is_enabled,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return StandardResponse.ok(data=saved)


@integrations_admin_router.post(
    "/{platform}/test",
    response_model=StandardResponse[TestConnectionResponse],
    summary="Test connectivity with third-party service",
)
async def test_integration(
    platform: str,
    payload: Optional[TestConnectionRequest] = None,
    service: IntegrationService = Depends(get_integration_service),
):
    credentials = payload.credentials if payload else None
    result = await service.test_connection(platform=platform, credentials=credentials)
    return StandardResponse.ok(data=result)


@integrations_admin_router.get(
    "/groq/models",
    response_model=StandardResponse[list[str]],
    summary="List available models for the Groq account",
)
async def list_groq_models(
    api_key: Optional[str] = None,
    service: IntegrationService = Depends(get_integration_service),
):
    models = await service.list_groq_models(api_key=api_key)
    return StandardResponse.ok(data=models)


# -----------------------------------------------------------------------------
# Slack Webhooks (Events API & Interactivity)
# -----------------------------------------------------------------------------


@integrations_public_router.post("/slack/events")
async def slack_events_webhook(
    request: Request,
    integration_service: IntegrationService = Depends(get_integration_service),
    user_repository: UserRepository = Depends(get_user_repository),
    leave_service: LeaveService = Depends(get_leave_service),
    timesheet_service: TimesheetService = Depends(get_timesheet_service),
    project_repository: ProjectRepository = Depends(get_project_repository),
):
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        payload = {}

    # 1. Slack URL Verification Challenge Handshake
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    # 2. Check if Slack integration is configured
    slack_creds = await integration_service.get_decrypted_credentials("slack")
    if not slack_creds:
        logger.warning(
            "Received Slack webhook but Slack integration is not configured."
        )
        return {"status": "unconfigured"}

    bot_token = slack_creds.get("bot_token", "")
    signing_secret = slack_creds.get("signing_secret", "")
    adapter = SlackMessagingAdapter(bot_token=bot_token, signing_secret=signing_secret)

    # 3. Signature & replay attack verification
    if not adapter.verify_signature(dict(request.headers), raw_body):
        logger.warning("Slack signature verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Slack signature",
        )

    # 4. Normalize inbound event
    event = adapter.parse_inbound_event(payload)
    if not event:
        return {"status": "ignored"}

    # 5. Resolve corporate employee email from Slack user ID
    email = await adapter.resolve_user_email(event.user_id)
    if not email:
        await adapter.send_message(
            channel=event.channel_id,
            text="⚠️ I could not retrieve your corporate email address from Slack. Please ensure your Slack account uses the same email as your OmniHR profile.",
            thread_ts=event.thread_ts if event.event_type == "app_mention" else None,
        )
        return {"status": "email_not_resolved"}

    user = await user_repository.get_by_email(email)
    if not user or not user.is_active:
        await adapter.send_message(
            channel=event.channel_id,
            text=f"⚠️ No active OmniHR employee account found for `{email}`. Please contact your HR administrator.",
            thread_ts=event.thread_ts if event.event_type == "app_mention" else None,
        )
        return {"status": "user_not_found"}

    # 6. Retrieve Groq credentials
    groq_creds = await integration_service.get_decrypted_credentials("groq")
    if not groq_creds or not groq_creds.get("api_key"):
        await adapter.send_message(
            channel=event.channel_id,
            text="⚠️ OmniBot AI reasoning engine is not yet configured. Please ask an HR administrator to provide Groq API credentials.",
            thread_ts=event.thread_ts if event.event_type == "app_mention" else None,
        )
        return {"status": "ai_unconfigured"}

    groq_client = GroqClient(
        api_key=groq_creds["api_key"],
        model=groq_creds.get("model") or "openai/gpt-oss-120b",
    )
    orchestrator = AIAgentOrchestrator(groq_client)

    tool_context = ToolContext(
        user=user,
        leave_service=leave_service,
        timesheet_service=timesheet_service,
        project_repository=project_repository,
    )

    # 7. Process message via AI Agent
    reply = await orchestrator.process_user_message(
        user=user,
        message_text=event.clean_text,
        context=tool_context,
    )

    # 8. Dispatch reply to Slack
    await adapter.send_message(
        channel=event.channel_id,
        text=reply.text,
        blocks=reply.blocks,
        thread_ts=event.thread_ts if event.event_type == "app_mention" else None,
    )
    return {"status": "ok"}


@integrations_public_router.post("/slack/interactions")
async def slack_interactions_webhook(
    request: Request,
    integration_service: IntegrationService = Depends(get_integration_service),
    user_repository: UserRepository = Depends(get_user_repository),
    leave_service: LeaveService = Depends(get_leave_service),
):
    raw_body = await request.body()
    # Slack sends application/x-www-form-urlencoded with 'payload' field containing JSON
    try:
        form_data = await request.form()
        payload_raw = form_data.get("payload")
        if payload_raw:
            payload = json.loads(str(payload_raw))
        else:
            payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        payload = {}

    slack_creds = await integration_service.get_decrypted_credentials("slack")
    if not slack_creds:
        return {"status": "unconfigured"}

    adapter = SlackMessagingAdapter(
        bot_token=slack_creds.get("bot_token", ""),
        signing_secret=slack_creds.get("signing_secret", ""),
    )

    # Verify signature
    if not adapter.verify_signature(dict(request.headers), raw_body):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Slack signature",
        )

    event = adapter.parse_inbound_event(payload)
    if not event or event.event_type != "interactive_action":
        return {"status": "ignored"}

    action_id = event.action_id
    action_val = event.action_value or "{}"
    try:
        action_data = json.loads(action_val)
    except Exception:
        action_data = {}

    channel_id = event.channel_id
    message_ts = event.message_ts

    if action_id == "confirm_leave_submission":
        user_id_str = action_data.get("user_id")
        user_id = uuid.UUID(user_id_str) if user_id_str else None
        if not user_id:
            email = await adapter.resolve_user_email(event.user_id)
            user = await user_repository.get_by_email(email) if email else None
            user_id = user.id if user else None

        if not user_id:
            card = build_status_card(
                title="Submission Error",
                message="Employee account could not be resolved for this confirmation.",
                status="cancelled",
            )
            await adapter.update_message(
                channel=channel_id,
                message_ts=message_ts,
                text="Account not found",
                blocks=card,
            )
            return {"status": "user_not_found"}

        try:
            leave_payload = LeaveRequestCreate(
                leave_type_id=uuid.UUID(action_data["leave_type_id"])
                if action_data.get("leave_type_id")
                else None,
                start_date=date.fromisoformat(action_data["start_date"]),
                end_date=date.fromisoformat(action_data["end_date"]),
                half_day_type=HalfDayType(action_data.get("half_day_type", "none")),
                reason=action_data.get("reason", "Applied via Slack"),
            )
            created_req = await leave_service.apply_leave(
                user_id=user_id, payload=leave_payload
            )
            is_approved = created_req.status == LeaveStatus.APPROVED
            status_badge = "Approved" if is_approved else "Pending Approval"

            card = build_status_card(
                title=f"Leave Application Submitted ({status_badge})",
                message=(
                    f"Your leave request for *{action_data.get('total_days', 1.0)}* day(s) "
                    f"(*{action_data.get('start_date')}* to *{action_data.get('end_date')}*) "
                    f"has been submitted. Current Status: `{status_badge}`."
                ),
                status="success",
            )
            await adapter.update_message(
                channel=channel_id,
                message_ts=message_ts,
                text="Leave request submitted",
                blocks=card,
            )
        except Exception as exc:
            logger.error(
                "Error executing leave submission from Slack interaction: %s", exc
            )
            err_detail = getattr(exc, "detail", str(exc))
            if isinstance(err_detail, dict):
                err_detail = err_detail.get("message", str(err_detail))
            card = build_status_card(
                title="Leave Application Failed",
                message=f"Could not submit leave request: {err_detail}",
                status="cancelled",
            )
            await adapter.update_message(
                channel=channel_id,
                message_ts=message_ts,
                text="Leave application failed",
                blocks=card,
            )

    elif action_id == "cancel_leave_submission":
        card = build_status_card(
            title="Leave Application Cancelled",
            message="The draft leave application was cancelled.",
            status="cancelled",
        )
        await adapter.update_message(
            channel=channel_id,
            message_ts=message_ts,
            text="Draft cancelled",
            blocks=card,
        )

    return {"status": "ok"}
