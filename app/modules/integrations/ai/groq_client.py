import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

GROQ_API_BASE = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


class GroqClient:
    """Client for Groq Chat Completions API with Tool-Calling / Function Calling support."""

    def __init__(self, api_key: str, model: str = DEFAULT_GROQ_MODEL):
        self.api_key = api_key.strip() if api_key else ""
        self.model = (model or "").strip() or DEFAULT_GROQ_MODEL

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Call Groq chat completions endpoint."""
        url = f"{GROQ_API_BASE}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code != 200:
                logger.error(
                    "Groq API error: %s (status %s)", res.text, res.status_code
                )
                raise RuntimeError(
                    f"Groq API returned HTTP {res.status_code}: {res.text}"
                )
            return res.json()
