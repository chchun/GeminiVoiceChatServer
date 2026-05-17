"""FastAPI app entry point.

WebSocket endpoint: /ws?api_key=<WS_API_KEY>
Authoritative spec: D:/dev/GeminiVoiceChat/app/docs/03_server_api.md
"""
import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.harness import TurnHarness, now_ms
from app.schemas.protocol import (
    ErrorCode,
    ErrorPacket,
    InboundPacket,
    ModeChangePacket,
    PongPacket,
    StatusPacket,
    TextChunkPacket,
    TextDonePacket,
    TextInputPacket,
)
from app.services.gemini_service import GeminiSession

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("server")

app = FastAPI(title="Gemini Voice Chat Server", version="1.0.0")


async def _send(ws: WebSocket, packet: BaseModel) -> None:
    await ws.send_text(packet.model_dump_json())


async def _send_error(ws: WebSocket, code: ErrorCode, message: str) -> None:
    """Per Android spec: error packet then status(ERROR). Session is kept open."""
    await _send(ws, ErrorPacket(code=code, message=message))
    await _send(ws, StatusPacket(code="ERROR"))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, api_key: str = Query(default="")) -> None:
    if api_key != settings.WS_API_KEY:
        logger.warning("WebSocket auth failed: bad api_key")
        await websocket.close(code=1008, reason="Invalid api_key")
        return

    trace_id = str(uuid.uuid4())
    await websocket.accept()
    logger.info(f"WS connected trace_id={trace_id}")

    await _send(websocket, StatusPacket(code="READY"))

    try:
        gemini = GeminiSession(
            api_key=settings.GEMINI_API_KEY,
            model_name=settings.GEMINI_MODEL,
        )
    except Exception as e:
        logger.exception("Failed to initialize GeminiSession")
        await _send_error(websocket, "INTERNAL_ERROR", f"Service init failed: {e}")
        await websocket.close()
        return

    session_mode = "TEXT"

    try:
        while True:
            raw = await websocket.receive_text()
            ts_recv = now_ms()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error(websocket, "INTERNAL_ERROR", "Invalid JSON")
                continue

            if not isinstance(data, dict):
                await _send_error(websocket, "INTERNAL_ERROR", "Packet must be a JSON object")
                continue

            msg_type = data.get("type")

            if msg_type == "text_input":
                try:
                    packet = TextInputPacket.model_validate(data)
                except ValidationError as e:
                    await _send_error(websocket, "INTERNAL_ERROR", f"Bad text_input: {e.errors()}")
                    continue
                await _handle_text_input(websocket, gemini, packet.text, trace_id, ts_recv)

            elif msg_type == "ping":
                await _send(websocket, PongPacket())

            elif msg_type == "mode_change":
                try:
                    packet = ModeChangePacket.model_validate(data)
                    session_mode = packet.mode
                    logger.info(f"Mode change trace_id={trace_id} mode={session_mode}")
                except ValidationError as e:
                    logger.warning(f"Invalid mode_change ignored: {e.errors()}")

            elif msg_type == "audio_input":
                await _send_error(
                    websocket,
                    "AUDIO_FORMAT_INVALID",
                    "audio_input is not supported in Stage 1; use text_input",
                )

            else:
                await _send_error(websocket, "INTERNAL_ERROR", f"Unknown type: {msg_type!r}")

    except WebSocketDisconnect:
        logger.info(f"WS disconnected trace_id={trace_id}")
    except Exception:
        logger.exception(f"WS session error trace_id={trace_id}")
        try:
            await _send_error(websocket, "INTERNAL_ERROR", "Unexpected server error")
        except Exception:
            pass
    finally:
        logger.info(f"WS closed trace_id={trace_id}")


async def _handle_text_input(
    ws: WebSocket,
    gemini: GeminiSession,
    user_text: str,
    trace_id: str,
    ts_recv: int,
) -> None:
    """Flow: status(THINKING) -> text_chunk x N -> text_done."""
    harness = TurnHarness(trace_id=trace_id, ts_server_recv=ts_recv)

    await _send(ws, StatusPacket(code="THINKING"))

    try:
        harness.ts_llm_req = now_ms()
        first_chunk = True
        async for chunk_text in gemini.stream(user_text):
            if first_chunk:
                harness.ts_llm_ttft = now_ms()
                first_chunk = False
            await _send(ws, TextChunkPacket(text=chunk_text))

        await _send(ws, TextDonePacket())
        harness.ts_server_send_end = now_ms()
        harness.log()

    except Exception as e:
        logger.exception(f"Gemini stream error trace_id={trace_id}")
        await _send_error(ws, "GEMINI_API_ERROR", str(e))
