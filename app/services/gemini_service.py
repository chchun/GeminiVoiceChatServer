"""Gemini API async streaming wrapper.

Uses the modern `google-genai` SDK (legacy `google-generativeai` is deprecated).
Decoupled from FastAPI/WebSocket so Stage 3+ LangGraph agents can replace it
without touching the transport layer.
"""
from typing import AsyncIterator

from google import genai


class GeminiSession:
    """Per-WebSocket-session conversation. Holds multi-turn history in memory.

    History is automatically accumulated by AsyncChat across successive
    `stream()` calls. Discard the instance to forget the history.
    """

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash-lite") -> None:
        self._client = genai.Client(api_key=api_key)
        self._chat = self._client.aio.chats.create(model=model_name, history=[])

    async def stream(self, user_text: str) -> AsyncIterator[str]:
        """Stream Gemini response tokens for a single user turn."""
        stream = await self._chat.send_message_stream(user_text)
        async for chunk in stream:
            text = getattr(chunk, "text", None)
            if text:
                yield text
