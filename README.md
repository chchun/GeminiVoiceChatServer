# GeminiVoiceChatServer

FastAPI WebSocket 서버 — Android 클라이언트와 Google Gemini API를 연결하는 실시간 AI 채팅 백엔드입니다.

현재 **Stage 1 (MVP)** 구현 완료: 텍스트 스트리밍, Cloud Run 배포, KPI TTFT < 1.0 s.

---

## 목차

- [아키텍처](#아키텍처)
- [소스 구조](#소스-구조)
- [요구사항](#요구사항)
- [로컬 설치 및 실행](#로컬-설치-및-실행)
- [환경 변수 설정](#환경-변수-설정)
- [Google Cloud Run 배포](#google-cloud-run-배포)
- [WebSocket 프로토콜](#websocket-프로토콜)
- [스모크 테스트](#스모크-테스트)
- [레이턴시 하네스](#레이턴시-하네스)
- [Stage 로드맵](#stage-로드맵)

---

## 아키텍처

```
Android Client
     │  WebSocket
     │  로컬:      ws://host:8000/ws?api_key=<KEY>
     │  Cloud Run: wss://your-app.run.app/ws?api_key=<KEY>
     ▼
┌─────────────────────────────┐
│  FastAPI (uvicorn)          │
│  ┌──────────────────────┐   │
│  │  WebSocket Router    │   │
│  │  app/main.py         │   │
│  └──────────┬───────────┘   │
│             │               │
│  ┌──────────▼───────────┐   │
│  │  GeminiSession       │   │
│  │  (per-connection)    │   │
│  │  app/services/       │   │
│  │  gemini_service.py   │   │
│  └──────────┬───────────┘   │
└────────────┼────────────────┘
             │  google-genai SDK (async streaming)
             ▼
     Google Gemini API
     (gemini-2.5-flash-lite)
```

- **멀티턴 히스토리**: WebSocket 세션 단위로 메모리 내 보관, 연결 해제 시 소멸
- **레이턴시 KPI**: `ts_llm_ttft - ts_server_recv` < 1000 ms
- **SDK**: `google-genai` (최신) — `google-generativeai` (deprecated) 사용 금지

---

## 소스 구조

```
GeminiVoiceChatServer/
├── app/
│   ├── main.py                  # FastAPI 앱 초기화 + WebSocket 라우터
│   ├── core/
│   │   ├── config.py            # pydantic-settings BaseSettings (.env 로더)
│   │   └── harness.py           # 밀리초 정밀도 레이턴시 타임스탬프 로거
│   ├── schemas/
│   │   └── protocol.py          # 클라이언트↔서버 패킷 Pydantic v2 모델
│   └── services/
│       └── gemini_service.py    # Gemini API 비동기 스트리밍 래퍼
├── scripts/
│   └── ws_smoke.py              # WebSocket 통합 스모크 테스트
├── docs/
│   ├── 01_server_requirements.md
│   └── 02_server_architecture.md
├── Dockerfile                   # Cloud Run 컨테이너 빌드
├── .dockerignore
├── .env.example                 # 환경 변수 템플릿
├── .gitignore
├── requirements.txt
└── CLAUDE.md                    # Claude Code 개발 지침
```

### 주요 파일 설명

| 파일 | 역할 |
|------|------|
| `app/main.py` | WebSocket 엔드포인트 `/ws`, 인증, 패킷 디스패치, 에러 핸들링 |
| `app/core/config.py` | `.env` 파일에서 설정 로드 (BaseSettings) |
| `app/core/harness.py` | 턴당 4개 타임스탬프 수집 및 구조화 로그 출력 |
| `app/schemas/protocol.py` | 인바운드/아웃바운드 패킷 타입 정의 |
| `app/services/gemini_service.py` | `GeminiSession` — 멀티턴 채팅 + 토큰 스트리밍 |
| `scripts/ws_smoke.py` | 5개 시나리오 자동 테스트 (인증, ping, 스트리밍, 멀티턴) |
| `Dockerfile` | Python 3.12-slim 기반 컨테이너 이미지 |

---

## 요구사항

- **Python** 3.11 이상 (개발 환경: 3.14, 컨테이너: 3.12-slim)
- **Google Gemini API 키** — [Google AI Studio](https://aistudio.google.com) 에서 발급
- Windows / macOS / Linux 모두 지원

---

## 로컬 설치 및 실행

```powershell
# 1. 저장소 클론
git clone https://github.com/chchun/GeminiVoiceChatServer.git
cd GeminiVoiceChatServer

# 2. 가상환경 생성 및 활성화
python -m venv venv

# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# 3. 의존성 설치
pip install -r requirements.txt

# 4. .env 파일 생성 (.env.example 참고)
copy .env.example .env   # Windows
# cp .env.example .env   # macOS / Linux

# 5. 서버 실행
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

헬스 체크:
```powershell
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## 환경 변수 설정

프로젝트 루트에 `.env` 파일 생성 (`.env.example` 참고):

```env
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
LOG_LEVEL=INFO

# WebSocket 인증 키 (Android 클라이언트와 동일한 값)
WS_API_KEY=your-secret-key-here

# Gemini API 키 (Google AI Studio에서 발급)
GEMINI_API_KEY=AIzaSy...your-key...

# 사용할 Gemini 모델 (기본값: gemini-2.5-flash-lite)
GEMINI_MODEL=gemini-2.5-flash-lite
```

> `.env` 파일은 `.gitignore`에 포함되어 커밋되지 않습니다.

---

## Google Cloud Run 배포

### 사전 준비

```powershell
# gcloud CLI 설치 후 로그인
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>
gcloud services enable run.googleapis.com artifactregistry.googleapis.com
```

### 배포 명령어

```powershell
cd GeminiVoiceChatServer

gcloud run deploy gemini-voice-chat-server `
  --source . `
  --region asia-northeast3 `
  --platform managed `
  --allow-unauthenticated `
  --port 8000 `
  --set-env-vars "LOG_LEVEL=INFO,GEMINI_MODEL=gemini-2.5-flash-lite" `
  --set-env-vars "WS_API_KEY=<YOUR_KEY>,GEMINI_API_KEY=<YOUR_GEMINI_KEY>" `
  --timeout 3600 `
  --min-instances 0 `
  --max-instances 3 `
  --session-affinity
```

> `--source .` 명령어가 Docker 빌드 → Artifact Registry 푸시 → Cloud Run 배포를 자동 처리합니다.

### 배포 후 확인

```powershell
# 헬스 체크
curl.exe https://YOUR-APP.asia-northeast3.run.app/health

# 스모크 테스트
python scripts/ws_smoke.py --host YOUR-APP.asia-northeast3.run.app --port 443
```

### Android 클라이언트 연결 (local.properties)

```properties
USE_REMOTE=true
SERVER_URL=wss://YOUR-APP.asia-northeast3.run.app/ws
WS_API_KEY=your-secret-key
```

> 로컬은 `ws://`, Cloud Run은 반드시 `wss://` (TLS)를 사용해야 합니다.

---

## WebSocket 프로토콜

**엔드포인트:** `ws(s)://[host]:[port]/ws?api_key=<WS_API_KEY>`

- `api_key` 불일치 시 close code `1008`으로 즉시 거부
- 인증 성공 시 즉시 `status(READY)` 전송

### 클라이언트 → 서버

```jsonc
{"type": "text_input", "text": "안녕하세요!"}
{"type": "ping"}
{"type": "mode_change", "mode": "TEXT" | "VOICE"}
// audio_input은 Stage 2 — Stage 1에서는 error 응답
```

### 서버 → 클라이언트

```jsonc
{"type": "status", "code": "READY" | "THINKING" | "ERROR"}
{"type": "text_chunk", "text": "안녕"}   // N회
{"type": "text_done"}
{"type": "pong"}
{"type": "error", "code": "GEMINI_API_ERROR", "message": "..."}
```

### text_input 흐름

```
Client                          Server
  │── text_input ──────────────►│
  │◄── status(THINKING) ────────│
  │◄── text_chunk x N ──────────│
  │◄── text_done ───────────────│
```

### 에러 코드

| 코드 | 의미 |
|------|------|
| `AUTH_FAILED` | API 키 인증 실패 |
| `AUDIO_FORMAT_INVALID` | Stage 1에서 audio_input 수신 시 |
| `GEMINI_API_ERROR` | Gemini API 호출 실패 |
| `INTERNAL_ERROR` | 서버 내부 오류 |

---

## 스모크 테스트

```powershell
# 로컬 서버 테스트
python scripts/ws_smoke.py

# Cloud Run 테스트 (wss:// 자동 적용)
python scripts/ws_smoke.py --host YOUR-APP.asia-northeast3.run.app --port 443

# Gemini API 호출 없이 인증/ping만 테스트
python scripts/ws_smoke.py --skip-llm
```

| # | 테스트 | 검증 내용 |
|---|--------|-----------|
| 1 | bad_auth | 잘못된 api_key → close 1008 |
| 2 | ready | 올바른 api_key → `status(READY)` |
| 3 | ping_pong | `ping` → `pong` |
| 4 | text_input_flow | THINKING → chunks → text_done |
| 5 | multi_turn | 2턴에서 1턴 컨텍스트 유지 확인 |

---

## 레이턴시 하네스

| 키 | 측정 시점 |
|----|-----------|
| `ts_server_recv` | 클라이언트 메시지 수신 |
| `ts_llm_req` | Gemini API 호출 시작 |
| `ts_llm_ttft` | 첫 번째 토큰 수신 |
| `ts_server_send_end` | `text_done` 전송 완료 |

**KPI:** `ttft_ms = ts_llm_ttft - ts_server_recv` < **1000 ms**

로그 출력 예시:
```json
{
  "event": "turn_latency",
  "trace_id": "a1b2c3d4-...",
  "ttft_ms": 320,
  "total_ms": 2100
}
```

---

## Stage 로드맵

| Stage | 기능 | 상태 |
|-------|------|------|
| **1** | FastAPI WebSocket + Gemini 텍스트 스트리밍 + Cloud Run 배포 | **완료** |
| 2 | PCM 오디오 입력 + STT 파이프라인 | 예정 |
| 3 | LangGraph + Chroma DB 벡터 RAG | 예정 |
| 4 | 멀티 에이전트 라우터 | 예정 |
| 5 | SQL 생성 + RDB 에이전트 | 예정 |

---

## 관련 저장소

- **Android 클라이언트:** `GeminiVoiceChat` — WebSocket 프로토콜 스펙의 권위적 출처
