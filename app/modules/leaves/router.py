import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import Depends, Query, Request, status

from app.api.deps import (
    ProtectedAPIRouter,
    get_cache_service,
    get_current_user,
    get_leave_service,
    get_user_repository,
    require_permission,
)
from app.core.services.cache_service import CacheService, cache_response
from app.models.leave import LeaveStatus
from app.models.role import PermissionEnum
from app.models.user import User
from app.modules.leaves.schemas import (
    HolidayCreatePayload,
    HolidayRead,
    LeaveAccrualPolicyCreatePayload,
    LeaveAccrualPolicyRead,
    LeaveAllocationRead,
    LeaveRequestCreate,
    LeaveRequestRead,
    LeaveStatusUpdatePayload,
    LeaveTypeRead,
    ManualAllocationGrantPayload,
)
from app.modules.leaves.service import LeaveService
from app.modules.users.repository import UserRepository
from app.schemas.common import MetaPayload, StandardResponse

leaves_router = ProtectedAPIRouter()
holidays_router = ProtectedAPIRouter()


@leaves_router.get(
    "/types",
    response_model=StandardResponse[list[LeaveTypeRead]],
    response_model_exclude_none=True,
)
@cache_response(ttl_seconds=300, key_prefix="leave_types")
async def get_leave_types(
    request: Request,
    leave_service: LeaveService = Depends(get_leave_service),
):
    types = await leave_service.get_leave_types()
    return StandardResponse.ok(data=types)


@leaves_router.get(
    "/balance",
    response_model=StandardResponse[list[LeaveAllocationRead]],
    response_model_exclude_none=True,
)
@cache_response(ttl_seconds=120, key_prefix="leave_balance")
async def get_leave_balance(
    request: Request,
    year: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    leave_service: LeaveService = Depends(get_leave_service),
):
    target_year = year or datetime.now().year
    balances = await leave_service.get_user_balances(current_user.id, target_year)
    return StandardResponse.ok(data=balances)


@leaves_router.post(
    "/requests",
    response_model=StandardResponse[LeaveRequestRead],
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def apply_leave(
    payload: LeaveRequestCreate,
    current_user: User = Depends(require_permission(PermissionEnum.LEAVE_APPLY)),
    leave_service: LeaveService = Depends(get_leave_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    created_request = await leave_service.apply_leave(current_user.id, payload)
    await cache_service.invalidate_prefixes("leave_balance", "leave_requests")
    return StandardResponse.ok(data=created_request)


@leaves_router.get(
    "/requests",
    response_model=StandardResponse[list[LeaveRequestRead]],
    response_model_exclude_none=True,
)
@cache_response(ttl_seconds=120, key_prefix="leave_requests")
async def list_leave_requests(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    user_id: Optional[uuid.UUID] = Query(None),
    leave_status: Optional[LeaveStatus] = Query(None, alias="status"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(require_permission(PermissionEnum.LEAVE_READ)),
    user_repository: UserRepository = Depends(get_user_repository),
    leave_service: LeaveService = Depends(get_leave_service),
):
    authorized_user_ids = await user_repository.get_authorized_viewable_user_ids(
        current_user, user_id
    )

    requests, total = await leave_service.list_leave_requests(
        page=page,
        limit=limit,
        user_id=None,
        user_ids=authorized_user_ids,
        leave_status=leave_status,
        start_date=start_date,
        end_date=end_date,
    )
    meta = MetaPayload(page=page, limit=limit, total=total)
    return StandardResponse.ok(data=requests, meta=meta)


@leaves_router.patch(
    "/requests/{id}/status",
    response_model=StandardResponse[LeaveRequestRead],
    response_model_exclude_none=True,
)
async def update_leave_status(
    id: uuid.UUID,
    payload: LeaveStatusUpdatePayload,
    current_user: User = Depends(require_permission(PermissionEnum.LEAVE_APPROVE)),
    leave_service: LeaveService = Depends(get_leave_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    updated_request = await leave_service.update_leave_status(
        request_id=id, approver_id=current_user.id, payload=payload
    )
    await cache_service.invalidate_prefixes("leave_balance", "leave_requests")
    return StandardResponse.ok(data=updated_request)


@leaves_router.post(
    "/policies",
    response_model=StandardResponse[LeaveAccrualPolicyRead],
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def create_or_update_policy(
    payload: LeaveAccrualPolicyCreatePayload,
    current_user: User = Depends(require_permission(PermissionEnum.LEAVE_MANAGE_TYPES)),
    leave_service: LeaveService = Depends(get_leave_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    policy = await leave_service.create_or_update_accrual_policy(
        payload, user_id=current_user.id
    )
    await cache_service.invalidate_prefixes("leave_policies")
    return StandardResponse.ok(data=policy)


@leaves_router.get(
    "/policies",
    response_model=StandardResponse[list[LeaveAccrualPolicyRead]],
    response_model_exclude_none=True,
)
@cache_response(ttl_seconds=300, key_prefix="leave_policies")
async def list_policies(
    request: Request,
    current_user: User = Depends(require_permission(PermissionEnum.LEAVE_READ)),
    leave_service: LeaveService = Depends(get_leave_service),
):
    policies = await leave_service.list_accrual_policies()
    return StandardResponse.ok(data=policies)


@leaves_router.post(
    "/allocations/grant",
    response_model=StandardResponse[LeaveAllocationRead],
    response_model_exclude_none=True,
)
async def grant_manual_allocation(
    payload: ManualAllocationGrantPayload,
    current_user: User = Depends(require_permission(PermissionEnum.LEAVE_MANAGE_TYPES)),
    leave_service: LeaveService = Depends(get_leave_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    granted = await leave_service.grant_manual_allocation(
        payload, granter_id=current_user.id
    )
    await cache_service.invalidate_prefixes("leave_balance")
    return StandardResponse.ok(data=granted)


@leaves_router.post(
    "/accruals/run",
    response_model=StandardResponse[dict],
    response_model_exclude_none=True,
)
async def trigger_accruals_manually(
    target_date: Optional[date] = Query(None),
    current_user: User = Depends(require_permission(PermissionEnum.LEAVE_MANAGE_TYPES)),
    leave_service: LeaveService = Depends(get_leave_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    accrued_count = await leave_service.trigger_periodic_accruals(target_date)
    await cache_service.invalidate_prefixes("leave_balance")
    return StandardResponse.ok(
        data={
            "message": f"Periodic accruals processed successfully. Updated {accrued_count} allocations."
        }
    )


@leaves_router.delete(
    "/requests/{id}",
    response_model=StandardResponse[dict],
    response_model_exclude_none=True,
)
async def cancel_leave(
    id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    leave_service: LeaveService = Depends(get_leave_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    await leave_service.cancel_leave(request_id=id, user_id=current_user.id)
    await cache_service.invalidate_prefixes("leave_balance", "leave_requests")
    return StandardResponse.ok(
        data={"message": "Leave request successfully cancelled."}
    )


@holidays_router.get(
    "",
    response_model=StandardResponse[list[HolidayRead]],
    response_model_exclude_none=True,
)
@cache_response(ttl_seconds=300, key_prefix="company_holidays")
async def list_holidays(
    request: Request,
    year: Optional[int] = Query(None),
    leave_service: LeaveService = Depends(get_leave_service),
):
    holidays = await leave_service.list_holidays(year)
    return StandardResponse.ok(data=holidays)


@holidays_router.post(
    "",
    response_model=StandardResponse[HolidayRead],
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_none=True,
)
async def create_holiday(
    payload: HolidayCreatePayload,
    current_user: User = Depends(require_permission(PermissionEnum.LEAVE_MANAGE_TYPES)),
    leave_service: LeaveService = Depends(get_leave_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    created_holiday = await leave_service.create_holiday(
        payload, user_id=current_user.id
    )
    await cache_service.invalidate_prefix("company_holidays")
    return StandardResponse.ok(data=created_holiday)
