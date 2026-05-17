"""WebSocket packet schemas.

Authoritative spec: D:/dev/GeminiVoiceChat/app/docs/03_server_api.md
"""
from typing import Literal, Union

from pydantic import BaseModel, Field, RootModel


# --- Inbound (Client -> Server) ---


class TextInputPacket(BaseModel):
    type: Literal["text_input"]
    text: str


class AudioInputPacket(BaseModel):
    type: Literal["audio_input"]
    payload: str
    is_final: bool


class ModeChangePacket(BaseModel):
    type: Literal["mode_change"]
    mode: Literal["TEXT", "VOICE"]


class PingPacket(BaseModel):
    type: Literal["ping"]


class InboundPacket(RootModel):
    root: Union[TextInputPacket, AudioInputPacket, ModeChangePacket, PingPacket] = Field(
        discriminator="type"
    )


# --- Outbound (Server -> Client) ---

StatusCode = Literal["READY", "THINKING", "ERROR"]
ErrorCode = Literal[
    "AUTH_FAILED",
    "AUDIO_FORMAT_INVALID",
    "GEMINI_API_ERROR",
    "INTERNAL_ERROR",
]


class StatusPacket(BaseModel):
    type: Literal["status"] = "status"
    code: StatusCode


class TextChunkPacket(BaseModel):
    type: Literal["text_chunk"] = "text_chunk"
    text: str


class TextDonePacket(BaseModel):
    type: Literal["text_done"] = "text_done"


class ErrorPacket(BaseModel):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str


class PongPacket(BaseModel):
    type: Literal["pong"] = "pong"
