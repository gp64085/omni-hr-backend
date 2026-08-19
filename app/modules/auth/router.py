from fastapi import APIRouter, Depends

from app.api.deps import (
    ProtectedAPIRouter,
    get_auth_service,
    get_cache_service,
    get_current_user,
)
from app.core.services.cache_service import CacheService
from app.models.user import User
from app.modules.auth.schemas import LoginRequest, RefreshTokenRequest, TokenResponse
from app.modules.auth.service import AuthService
from app.modules.users.schemas import UserResponse
from app.schemas.common import StandardResponse

public_auth_router = APIRouter()
protected_auth_router = ProtectedAPIRouter()


@public_auth_router.post(
    "/login",
    response_model=StandardResponse[TokenResponse],
    response_model_exclude_none=True,
)
async def login(
    payload: LoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    token_response = await auth_service.authenticate_user(payload)
    return StandardResponse.ok(data=token_response)


@public_auth_router.post(
    "/refresh",
    response_model=StandardResponse[TokenResponse],
    response_model_exclude_none=True,
)
async def refresh_access_token(
    payload: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    token_response = await auth_service.refresh_tokens(payload.refresh_token)
    return StandardResponse.ok(data=token_response)


@protected_auth_router.post(
    "/logout", response_model=StandardResponse[dict], response_model_exclude_none=True
)
async def logout(
    payload: RefreshTokenRequest,
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
    cache_service: CacheService = Depends(get_cache_service),
):
    await auth_service.logout(str(current_user.id), payload.refresh_token)
    await cache_service.invalidate_prefixes(
        "auth_me", "users_me", f"user:{current_user.id}"
    )
    return StandardResponse.ok(data={"message": "Logged out successfully."})


@protected_auth_router.get(
    "/me",
    response_model=StandardResponse[UserResponse],
    response_model_exclude_none=True,
)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
):
    return StandardResponse.ok(data=UserResponse.model_validate(current_user))
