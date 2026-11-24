import hashlib
import hmac
import logging
import re
import time
from typing import Any, Optional

import httpx

from app.modules.integrations.adapters.base import BaseMessagingAdapter, NormalizedEvent

logger = logging.getLogger(__name__)


class SlackMessagingAdapter(BaseMessagingAdapter):
    def __init__(self, bot_token: str, signing_secret: str):
        self.bot_token = bot_token
        self.signing_secret = signing_secret
        self._user_email_cache: dict[str, str] = {}

    def verify_signature(
        self, request_headers: dict[str, str], raw_body: bytes
    ) -> bool:
        """Verify Slack HMAC-SHA256 signature and prevent replay attacks."""
        headers_lower = {k.lower(): v for k, v in request_headers.items()}
        timestamp_str = headers_lower.get("x-slack-request-timestamp")
        slack_signature = headers_lower.get("x-slack-signature")

        if not timestamp_str or not slack_signature:
            logger.warning("Missing Slack signature or timestamp headers")
            return False

        try:
            timestamp = int(timestamp_str)
        except ValueError:
            logger.warning("Invalid timestamp header: %s", timestamp_str)
            return False

        # Replay attack protection: reject requests older or newer than 300 seconds
        current_time = int(time.time())
        if abs(current_time - timestamp) > 300:
            logger.warning(
                "Slack request timestamp out of bounds (drift=%s seconds)",
                abs(current_time - timestamp),
            )
            return False

        if self.signing_secret.strip().startswith("xoxb-"):
            logger.error(
                "Slack signing secret is set to a Bot Token (starts with 'xoxb-'). "
                "The Signing Secret is a 32-character hexadecimal string found in Slack App Basic Information -> App Credentials."
            )
            return False

        sig_basestring = f"v0:{timestamp}:".encode("utf-8") + raw_body
        computed_hash = hmac.new(
            self.signing_secret.strip().encode("utf-8"),
            sig_basestring,
            hashlib.sha256,
        ).hexdigest()
        expected_signature = f"v0={computed_hash}"

        match = hmac.compare_digest(expected_signature, slack_signature)
        if not match:
            logger.warning("Slack signature mismatch")
        return match

    def parse_inbound_event(self, payload: dict[str, Any]) -> Optional[NormalizedEvent]:
        """Normalize Slack event_callback and block_actions payloads."""
        payload_type = payload.get("type")

        # 1. Interactive block actions (buttons clicked)
        if payload_type == "block_actions":
            actions = payload.get("actions", [])
            action = actions[0] if actions else {}
            user = payload.get("user", {})
            channel = payload.get("channel", {})
            container = payload.get("container", {})
            message = payload.get("message", {})

            return NormalizedEvent(
                platform="slack",
                event_type="interactive_action",
                user_id=user.get("id", ""),
                channel_id=channel.get("id", "") or container.get("channel_id", ""),
                text="",
                action_id=action.get("action_id"),
                action_value=action.get("value"),
                message_ts=container.get("message_ts") or message.get("ts"),
                raw_event=payload,
            )

        # 2. Standard Events (event_callback)
        if payload_type == "event_callback":
            event = payload.get("event", {})
            event_type = event.get("type")

            # Ignore bot's own messages to avoid infinite response loops
            if event.get("bot_id") or event.get("subtype") == "bot_message":
                return None

            user_id = event.get("user", "")
            channel_id = event.get("channel", "")
            channel_type = event.get("channel_type")
            text = event.get("text", "")
            ts = event.get("ts")
            thread_ts = event.get("thread_ts") or ts

            # Clean mention syntax e.g. "<@U123456> apply leave" -> "apply leave"
            clean_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

            if event_type in ("message", "app_mention"):
                return NormalizedEvent(
                    platform="slack",
                    event_type=event_type,
                    user_id=user_id,
                    channel_id=channel_id,
                    channel_type=channel_type,
                    text=text,
                    clean_text=clean_text,
                    thread_ts=thread_ts,
                    message_ts=ts,
                    raw_event=event,
                )

        return None

    async def resolve_user_email(self, user_id: str) -> Optional[str]:
        """Fetch email address of a Slack user via users.info API."""
        if not user_id:
            return None
        if user_id in self._user_email_cache:
            return self._user_email_cache[user_id]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    "https://slack.com/api/users.info",
                    params={"user": user_id},
                    headers={"Authorization": f"Bearer {self.bot_token}"},
                )
                data = res.json()
                if data.get("ok"):
                    email = data.get("user", {}).get("profile", {}).get("email")
                    if email:
                        clean_email = email.strip().lower()
                        self._user_email_cache[user_id] = clean_email
                        return clean_email
                else:
                    logger.warning(
                        "Failed to resolve Slack user %s: %s",
                        user_id,
                        data.get("error"),
                    )
        except Exception as exc:
            logger.error("Exception resolving Slack user %s email: %s", user_id, exc)

        return None

    async def send_message(
        self,
        channel: str,
        text: str,
        blocks: Optional[list[dict[str, Any]]] = None,
        thread_ts: Optional[str] = None,
    ) -> bool:
        """Post a message or Block Kit card to a Slack channel or thread."""
        payload: dict[str, Any] = {
            "channel": channel,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {self.bot_token}"},
                    json=payload,
                )
                data = res.json()
                if not data.get("ok"):
                    logger.error("Failed to post Slack message: %s", data.get("error"))
                    return False
                return True
        except Exception as exc:
            logger.error("Exception posting Slack message: %s", exc)
            return False

    async def update_message(
        self,
        channel: str,
        message_ts: str,
        text: str,
        blocks: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        """Update an existing Slack message."""
        payload: dict[str, Any] = {
            "channel": channel,
            "ts": message_ts,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    "https://slack.com/api/chat.update",
                    headers={"Authorization": f"Bearer {self.bot_token}"},
                    json=payload,
                )
                data = res.json()
                if not data.get("ok"):
                    logger.error(
                        "Failed to update Slack message: %s", data.get("error")
                    )
                    return False
                return True
        except Exception as exc:
            logger.error("Exception updating Slack message: %s", exc)
            return False
