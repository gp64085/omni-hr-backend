import json
import logging
from typing import Optional

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.core.redis import get_redis_client

logger = logging.getLogger("uvicorn.error")

IDEMPOTENCY_TTL_SECONDS = 86400  # 24 hours

# In-flight lock TTL: set comfortably high (5 minutes) to cover long-running
# operational workflows (e.g. payroll processing, bulk data imports).
# If a handler exceeds this TTL, a retried request could acquire a lock.
IN_FLIGHT_LOCK_TTL_SECONDS = 300


class IdempotencyService:
    """Service to handle X-Idempotency-Key caching and request deduplication."""

    def __init__(self, client: Optional[Redis] = None):
        self._client = client

    @property
    def client(self) -> Optional[Redis]:
        return self._client or get_redis_client()

    async def get_cached_response(
        self, idempotency_key: str, path: str
    ) -> Optional[JSONResponse]:
        client = self.client
        if not client or not idempotency_key:
            return None

        redis_key = f"idempotency:{path}:{idempotency_key}"
        try:
            cached_raw = await client.get(redis_key)
            if not cached_raw:
                return None

            data = json.loads(cached_raw)
            if data.get("status") == "PROCESSING":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "CONCURRENT_REQUEST",
                        "message": "A request with this X-Idempotency-Key is currently being processed.",
                    },
                )

            return JSONResponse(
                status_code=data.get("status_code", 200),
                content=data.get("content"),
                headers={"X-Cache-Idempotent": "HIT"},
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(
                "Error fetching idempotency key '%s': %s", idempotency_key, e
            )
            return None

    async def lock_key(self, idempotency_key: str, path: str) -> bool:
        """Mark idempotency key as in-flight / PROCESSING."""
        client = self.client
        if not client or not idempotency_key:
            return True  # Fail-open if Redis client is unavailable

        redis_key = f"idempotency:{path}:{idempotency_key}"
        try:
            # Set key only if it does not exist (nx=True), expire in IN_FLIGHT_LOCK_TTL_SECONDS
            payload = json.dumps({"status": "PROCESSING"})
            is_new = await client.set(
                redis_key, payload, ex=IN_FLIGHT_LOCK_TTL_SECONDS, nx=True
            )
            return bool(is_new)
        except Exception as e:
            logger.warning(
                "Error setting idempotency lock '%s': %s", idempotency_key, e
            )
            return True

    async def save_response(
        self,
        idempotency_key: str,
        path: str,
        status_code: int,
        content: dict,
        ttl_seconds: int = IDEMPOTENCY_TTL_SECONDS,
    ) -> bool:
        """Store finished response content in Redis for the given idempotency key."""
        client = self.client
        if not client or not idempotency_key:
            return False

        redis_key = f"idempotency:{path}:{idempotency_key}"
        try:
            payload = json.dumps(
                {"status": "COMPLETED", "status_code": status_code, "content": content},
                default=str,
            )
            await client.setex(redis_key, ttl_seconds, payload)
            return True
        except Exception as e:
            logger.warning(
                "Error saving idempotency response for '%s': %s", idempotency_key, e
            )
            return False


idempotency_service = IdempotencyService()


async def check_idempotency(request: Request) -> Optional[Response]:
    """Dependency to validate X-Idempotency-Key header on mutating operations."""
    idempotency_key = request.headers.get("X-Idempotency-Key")
    if not idempotency_key:
        return None

    cached = await idempotency_service.get_cached_response(
        idempotency_key, request.url.path
    )
    if cached:
        return cached

    # Attempt to lock the key for in-flight processing
    locked = await idempotency_service.lock_key(idempotency_key, request.url.path)
    if not locked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONCURRENT_REQUEST",
                "message": "A request with this X-Idempotency-Key is currently being processed.",
            },
        )

    return None
