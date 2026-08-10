import uuid
from datetime import date
from typing import Optional

from fastapi import Depends, Query, Request, status

from app.api.deps import (
    ProtectedAPIRouter,
    get_authorized_target_user_id,
    get_cache_service,
    get_current_user,
    get_timesheet_service,
    get_user_repository,
    require_permission,
)
from app.core.services.cache_service import CacheService, cache_response
from app.models.role import PermissionEnum
from app.models.user import User
from app.modules.timesheets.schemas import (
    TimesheetEntryCreatePayload,
    TimesheetEntryRead,
    TimesheetEntryUpdatePayload,
    TimesheetStatusUpdatePayload,
    TimesheetSubmitPayload,
    WeeklyTimesheetSummaryRead,
)
from app.modules.timesheets.service import TimesheetService
from app.modules.users.repository import UserRepository
from app.schemas.common import MetaPayload, StandardResponse

timesheets_router = ProtectedAPIRouter()


@timesheets_router.post(
    "/entries",
    response_model=StandardResponse[TimesheetEntryRead],
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def create_timesheet_entry(
    payload: TimesheetEntryCreatePayload,
    current_user: User = Depends(require_permission(PermissionEnum.TIMESHEET_SUBMIT)),
    timesheet_service: TimesheetService = Depends(get_timesheet_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    created_entry = await timesheet_service.create_entry(current_user.id, payload)
    await cache_service.invalidate_prefixes("timesheet_entries", "timesheet_summary")
    return StandardResponse.ok(data=created_entry)


@timesheets_router.get(
    "/entries",
    response_model=StandardResponse[list[TimesheetEntryRead]],
    response_model_exclude_none=True,
)
@cache_response(ttl_seconds=60, key_prefix="timesheet_entries")
async def list_timesheet_entries(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user_id: Optional[uuid.UUID] = Query(None),
    project_id: Optional[uuid.UUID] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    entry_status: Optional[str] = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    user_repository: UserRepository = Depends(get_user_repository),
    timesheet_service: TimesheetService = Depends(get_timesheet_service),
):
    target_user_id = await get_authorized_target_user_id(
        user_id, current_user, user_repository
    )
    offset = (page - 1) * limit
    entries, total = await timesheet_service.list_entries(
        user_id=target_user_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        entry_status=entry_status,
        offset=offset,
        limit=limit,
    )
    meta = MetaPayload(page=page, limit=limit, total=total)
    return StandardResponse.ok(data=entries, meta=meta)


@timesheets_router.put(
    "/entries/{entry_id}",
    response_model=StandardResponse[TimesheetEntryRead],
    response_model_exclude_none=True,
)
async def update_timesheet_entry(
    entry_id: uuid.UUID,
    payload: TimesheetEntryUpdatePayload,
    current_user: User = Depends(require_permission(PermissionEnum.TIMESHEET_SUBMIT)),
    timesheet_service: TimesheetService = Depends(get_timesheet_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    updated_entry = await timesheet_service.update_entry(
        current_user.id, entry_id, payload
    )
    await cache_service.invalidate_prefixes("timesheet_entries", "timesheet_summary")
    return StandardResponse.ok(data=updated_entry)


@timesheets_router.delete(
    "/entries/{entry_id}",
    response_model=StandardResponse[dict],
    response_model_exclude_none=True,
)
async def delete_timesheet_entry(
    entry_id: uuid.UUID,
    current_user: User = Depends(require_permission(PermissionEnum.TIMESHEET_SUBMIT)),
    timesheet_service: TimesheetService = Depends(get_timesheet_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    await timesheet_service.delete_entry(current_user.id, entry_id)
    await cache_service.invalidate_prefixes("timesheet_entries", "timesheet_summary")
    return StandardResponse.ok(
        data={"message": "Timesheet entry deleted successfully."}
    )


@timesheets_router.post(
    "/submit",
    response_model=StandardResponse[dict],
    response_model_exclude_none=True,
)
async def submit_timesheets(
    payload: TimesheetSubmitPayload,
    current_user: User = Depends(require_permission(PermissionEnum.TIMESHEET_SUBMIT)),
    timesheet_service: TimesheetService = Depends(get_timesheet_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    submitted_count = await timesheet_service.submit_timesheets(
        current_user.id, payload
    )
    await cache_service.invalidate_prefixes("timesheet_entries", "timesheet_summary")
    return StandardResponse.ok(
        data={"message": f"Successfully submitted {submitted_count} timesheet entries."}
    )


@timesheets_router.patch(
    "/entries/{entry_id}/status",
    response_model=StandardResponse[TimesheetEntryRead],
    response_model_exclude_none=True,
)
async def update_timesheet_entry_status(
    entry_id: uuid.UUID,
    payload: TimesheetStatusUpdatePayload,
    current_user: User = Depends(require_permission(PermissionEnum.TIMESHEET_APPROVE)),
    timesheet_service: TimesheetService = Depends(get_timesheet_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    updated_entry = await timesheet_service.update_entry_status(
        current_user.id, entry_id, payload
    )
    await cache_service.invalidate_prefixes("timesheet_entries", "timesheet_summary")
    return StandardResponse.ok(data=updated_entry)


@timesheets_router.get(
    "/summary",
    response_model=StandardResponse[WeeklyTimesheetSummaryRead],
    response_model_exclude_none=True,
)
@cache_response(ttl_seconds=60, key_prefix="timesheet_summary")
async def get_weekly_timesheet_summary(
    request: Request,
    start_date: date = Query(...),
    end_date: date = Query(...),
    user_id: Optional[uuid.UUID] = Query(None),
    current_user: User = Depends(get_current_user),
    user_repository: UserRepository = Depends(get_user_repository),
    timesheet_service: TimesheetService = Depends(get_timesheet_service),
):
    target_user_id = await get_authorized_target_user_id(
        user_id, current_user, user_repository
    )
    summary = await timesheet_service.get_weekly_summary(
        user_id=target_user_id, start_date=start_date, end_date=end_date
    )
    return StandardResponse.ok(data=summary)
