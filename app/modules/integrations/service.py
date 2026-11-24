import logging
from typing import Any, Optional

import httpx

from app.core.security_crypto import decrypt_json, encrypt_json
from app.models.integration import IntegrationConfig
from app.modules.integrations.repository import IntegrationRepository
from app.modules.integrations.schemas import (
    IntegrationResponse,
    TestConnectionResponse,
    mask_credentials_dict,
)

logger = logging.getLogger(__name__)


class IntegrationService:
    def __init__(self, repository: IntegrationRepository):
        self._repository = repository

    async def get_decrypted_credentials(
        self, platform: str
    ) -> Optional[dict[str, Any]]:
        """Fetch and decrypt credentials for a platform."""
        record = await self._repository.get_by_platform(platform)
        if not record or not record.is_enabled:
            return None
        try:
            return decrypt_json(record.encrypted_credentials)
        except Exception as exc:
            logger.error(
                "Failed to decrypt credentials for platform %s: %s", platform, exc
            )
            return None

    async def get_raw_config(self, platform: str) -> Optional[IntegrationConfig]:
        """Fetch raw integration record."""
        return await self._repository.get_by_platform(platform)

    async def get_masked_config(self, platform: str) -> Optional[IntegrationResponse]:
        """Fetch config with credentials masked for safe API output."""
        record = await self._repository.get_by_platform(platform)
        if not record:
            return None
        try:
            creds = decrypt_json(record.encrypted_credentials)
        except Exception:
            creds = {}
        masked = mask_credentials_dict(creds)
        return IntegrationResponse(
            id=record.id,
            platform=record.platform,
            is_enabled=record.is_enabled,
            credentials=masked,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def list_configs(self) -> list[IntegrationResponse]:
        """List all integration configs with masked credentials."""
        records = await self._repository.list_all()
        results: list[IntegrationResponse] = []
        for r in records:
            try:
                creds = decrypt_json(r.encrypted_credentials)
            except Exception:
                creds = {}
            results.append(
                IntegrationResponse(
                    id=r.id,
                    platform=r.platform,
                    is_enabled=r.is_enabled,
                    credentials=mask_credentials_dict(creds),
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
            )
        return results

    async def save_config(
        self, platform: str, credentials: dict[str, Any], is_enabled: bool = True
    ) -> IntegrationResponse:
        """Upsert integration config with merged credentials and encryption."""
        platform_key = platform.strip().lower()
        existing = await self._repository.get_by_platform(platform_key)

        merged_credentials = dict(credentials)
        if existing:
            try:
                old_creds = decrypt_json(existing.encrypted_credentials)
                for k, v in credentials.items():
                    # If incoming value is masked (e.g. contains '...'), retain existing secret
                    if isinstance(v, str) and "..." in v and k in old_creds:
                        merged_credentials[k] = old_creds[k]
            except Exception:
                pass

        if platform_key == "slack":
            signing_sec = merged_credentials.get("signing_secret", "")
            if isinstance(signing_sec, str) and signing_sec.strip().startswith("xoxb-"):
                raise ValueError(
                    "Invalid Signing Secret: starts with 'xoxb-'. You pasted a Bot Token into the Signing Secret field. "
                    "Find the 32-character Signing Secret in your Slack App under Basic Information -> App Credentials."
                )

        encrypted_payload = encrypt_json(merged_credentials)

        if existing:
            await self._repository.update(
                existing,
                {
                    "encrypted_credentials": encrypted_payload,
                    "is_enabled": is_enabled,
                },
            )
            saved_record = existing
        else:
            new_record = IntegrationConfig(
                platform=platform_key,
                encrypted_credentials=encrypted_payload,
                is_enabled=is_enabled,
            )
            saved_record = await self._repository.create(new_record)

        return IntegrationResponse(
            id=saved_record.id,
            platform=saved_record.platform,
            is_enabled=saved_record.is_enabled,
            credentials=mask_credentials_dict(merged_credentials),
            created_at=saved_record.created_at,
            updated_at=saved_record.updated_at,
        )

    async def test_connection(
        self, platform: str, credentials: Optional[dict[str, Any]] = None
    ) -> TestConnectionResponse:
        """Test live connectivity for a platform using provided or saved credentials."""
        platform_key = platform.strip().lower()
        creds_to_test = credentials

        if not creds_to_test:
            record = await self._repository.get_by_platform(platform_key)
            if not record:
                return TestConnectionResponse(
                    success=False,
                    message=f"No configuration found for platform '{platform_key}'.",
                )
            try:
                creds_to_test = decrypt_json(record.encrypted_credentials)
            except Exception as exc:
                return TestConnectionResponse(
                    success=False,
                    message=f"Failed to decrypt saved credentials: {exc}",
                )

        if platform_key == "slack":
            return await self._test_slack(creds_to_test)
        elif platform_key == "groq":
            return await self._test_groq(creds_to_test)
        else:
            return TestConnectionResponse(
                success=False,
                message=f"Unsupported platform '{platform_key}' for connectivity test.",
            )

    async def _test_slack(self, creds: dict[str, Any]) -> TestConnectionResponse:
        bot_token = creds.get("bot_token")
        if not bot_token:
            return TestConnectionResponse(
                success=False,
                message="Missing 'bot_token' in Slack credentials.",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {bot_token}"},
                )
                data = res.json()
                if data.get("ok"):
                    team = data.get("team", "Unknown Workspace")
                    user = data.get("user", "Bot")
                    return TestConnectionResponse(
                        success=True,
                        message=f"Successfully authenticated with Slack! Connected to '{team}' as @{user}.",
                        details={
                            "team": team,
                            "user": user,
                            "bot_id": data.get("bot_id"),
                        },
                    )
                else:
                    return TestConnectionResponse(
                        success=False,
                        message=f"Slack authentication failed: {data.get('error', 'Unknown error')}",
                    )
        except Exception as exc:
            return TestConnectionResponse(
                success=False,
                message=f"Slack connection request failed: {str(exc)}",
            )

    async def _test_groq(self, creds: dict[str, Any]) -> TestConnectionResponse:
        api_key = creds.get("api_key")
        if not api_key:
            return TestConnectionResponse(
                success=False,
                message="Missing 'api_key' in Groq credentials.",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if res.status_code == 200:
                    data = res.json()
                    models_count = len(data.get("data", []))
                    return TestConnectionResponse(
                        success=True,
                        message=f"Successfully authenticated with Groq API! Access to {models_count} models verified.",
                        details={"models_available": models_count},
                    )
                else:
                    return TestConnectionResponse(
                        success=False,
                        message=f"Groq API returned HTTP {res.status_code}: {res.text}",
                    )
        except Exception as exc:
            return TestConnectionResponse(
                success=False,
                message=f"Groq connection request failed: {str(exc)}",
            )

    async def list_groq_models(self, api_key: Optional[str] = None) -> list[str]:
        """Fetch list of available models from Groq API."""
        key = api_key
        if not key:
            record = await self._repository.get_by_platform("groq")
            if record:
                try:
                    creds = decrypt_json(record.encrypted_credentials)
                    key = creds.get("api_key")
                except Exception:
                    pass

        if not key:
            return []

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                if res.status_code == 200:
                    data = res.json()
                    models = [m["id"] for m in data.get("data", []) if "id" in m]
                    return sorted(models)
        except Exception as exc:
            logger.error("Failed to list Groq models: %s", exc)
        return []
