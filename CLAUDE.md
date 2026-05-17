# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

This project is in the **Stage 1 (MVP) implementation phase**. The `app/` source directory does not yet exist — only the `docs/` folder and `venv/` are present. Implement Stage 1 before considering any Stage 2+ features.

## Environment Setup

Python 3.14.4 virtual environment is already configured at `venv/`. All dependencies are installed.

```powershell
# Activate venv (Windows)
venv\Scripts\activate

# Run the server (once app/ is created)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Freeze dependencies after adding new packages
pip freeze > requirements.txt
```

The `.env` file (not committed) must be created at the project root:
```env
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
LOG_LEVEL=INFO
WS_API_KEY=<shared_with_android_client>
GEMINI_API_KEY=<your_key_from_aistudio.google.com>
```

## Architecture

**Stage 1 (current target):** FastAPI + Pydantic v2 WebSocket backend streaming Gemini (default: `gemini-2.5-flash-lite`, overridable via `GEMINI_MODEL` env) responses to an Android client. KPI: server-internal TTFT < 1.0s. Uses the `google-genai` SDK (the legacy `google-generativeai` is deprecated).

**Authoritative interface spec:** `D:\dev\GeminiVoiceChat\app\docs\03_server_api.md` (Android client repo). If this file conflicts with that one, the Android spec wins.

**Planned directory layout:**
```
app/
├── main.py               # FastAPI app init + WebSocket router
├── core/
│   ├── config.py         # pydantic-settings BaseSettings loader for .env
│   └── harness.py        # millisecond-precision latency timestamp logger
├── schemas/
│   └── protocol.py       # Pydantic v2 models for all client↔server packets
├── services/
│   └── gemini_service.py # Gemini API async streaming wrapper (keep as independent module for Stage 3+ plug-in)
└── agents/               # Stage 3–5 only: LangGraph, RAG, RDB agents
```

## WebSocket Protocol

Endpoint: `ws://[host]:[port]/ws?api_key=<WS_API_KEY>`

- Reject connections with missing/wrong `api_key` via close code `1008`.
- Immediately after `accept()`, send `{"type": "status", "code": "READY"}`.

**Client → Server (Stage 1 supported):**
```json
{"type": "text_input", "text": "..."}
{"type": "ping"}
{"type": "mode_change", "mode": "TEXT" | "VOICE"}   // no ACK; just update session state
```
`audio_input` is Stage 2 — on receipt in Stage 1, respond with an `error` packet and keep the session open.

**Server → Client (text_input flow):**
```json
{"type": "status", "code": "THINKING"}
{"type": "text_chunk", "text": "..."}    // one per streamed token, N times
{"type": "text_done"}                    // end-of-stream signal
```
- Do **not** send `status(SPEAKING)` — the Android spec doesn't define it; `text_chunk` itself signals SPEAKING.
- Do **not** embed `[FINISH]` inside a `text_chunk` — use the dedicated `text_done` message type.
- `ping` → reply `{"type": "pong"}` immediately.
- Error path: `{"type": "error", "code": "...", "message": "..."}` → `{"type": "status", "code": "ERROR"}`, then keep the session alive. Error codes: `AUTH_FAILED`, `AUDIO_FORMAT_INVALID`, `GEMINI_API_ERROR`, `INTERNAL_ERROR`.

## Conversation History

Maintain multi-turn history **per WebSocket session** in memory (e.g. `GeminiClient.start_chat(history=[...])` or equivalent). Discard on disconnect — no DB/Redis persistence in Stage 1. The same connection's second `text_input` must see context from the first turn.

## Latency Harness

Four timestamps must be captured per conversation turn (stored in structured console log with `trace_id`):

| Key | Moment |
|-----|--------|
| `ts_server_recv` | Message received from client |
| `ts_llm_req` | Gemini API call initiated |
| `ts_llm_ttft` | First token received from Gemini |
| `ts_server_send_end` | `text_done` packet sent to client |

**KPI:** `ts_llm_ttft - ts_server_recv` < 1000ms

## Coding Constraints

- All I/O (WebSocket send/recv, Gemini API calls) **must** use `async/await`. No blocking code.
- Every router and service layer must have `try-except-finally` error handling so client disconnects never crash the server. On bad packet formats, send an `error` + `status(ERROR)` pair — do not raise.
- Use `pydantic-settings` `BaseSettings` for all config; never hardcode env values.
- `gemini_service.py` must remain a standalone module so Stage 3 LangGraph agents can replace it without touching the WebSocket layer.

## Stage Roadmap (do not implement ahead of schedule)

| Stage | Jira | Feature |
|-------|------|---------|
| 1 ✅ current | `INFRA-TEXT-STREAM` | FastAPI WebSocket + Gemini text streaming |
| 2 | `INFRA-AUDIO-STT` | PCM audio input + STT pipeline |
| 3 | `AGENT-RAG-CHROMA` | LangGraph + Chroma DB vector RAG |
| 4 | `AGENT-MULTI-ROUTER` | Multi-agent router (legal search, customer support) |
| 5 | `AGENT-RDB-SQL` | SQL generation + RDB agent |
