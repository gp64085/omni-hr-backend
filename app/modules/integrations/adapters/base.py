from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class NormalizedEvent:
    platform: str
    event_type: str  # "message", "app_mention", "interactive_action"
    user_id: str
    channel_id: str
    text: str
    clean_text: str = ""
    channel_type: Optional[str] = None  # "im", "channel", "group"
    user_email: Optional[str] = None
    thread_ts: Optional[str] = None
    message_ts: Optional[str] = None
    action_id: Optional[str] = None
    action_value: Optional[str] = None
    raw_event: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.clean_text:
            self.clean_text = self.text


class BaseMessagingAdapter(ABC):
    """Abstract Base Class for messaging platform adapters (Slack, Teams, Discord, etc.)."""

    @abstractmethod
    def verify_signature(
        self, request_headers: dict[str, str], raw_body: bytes
    ) -> bool:
        """Verify authenticity of the incoming webhook request."""
        pass

    @abstractmethod
    def parse_inbound_event(self, payload: dict[str, Any]) -> Optional[NormalizedEvent]:
        """Convert a platform-specific event payload into a NormalizedEvent."""
        pass

    @abstractmethod
    async def resolve_user_email(self, user_id: str) -> Optional[str]:
        """Resolve a platform user ID to their corporate email address."""
        pass

    @abstractmethod
    async def send_message(
        self,
        channel: str,
        text: str,
        blocks: Optional[list[dict[str, Any]]] = None,
        thread_ts: Optional[str] = None,
    ) -> bool:
        """Send a message to a channel or direct message channel."""
        pass

    @abstractmethod
    async def update_message(
        self,
        channel: str,
        message_ts: str,
        text: str,
        blocks: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        """Update an existing message (e.g. updating an interactive card)."""
        pass
