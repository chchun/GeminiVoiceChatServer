```markdown
# 서버 기술 아키텍처 및 구현 가이드 - v1.3

## 1. 사전 준비 사항 (Prerequisites)
본 프로젝트를 기동하기 전, 외부 LLM 연동을 위해 다음 자격 증명이 사전에 준비되어야 한다.
- **Google Gemini API Key:** [Google AI Studio](https://aistudio.google.com/)에 접속하여 프로젝트를 생성하고 API Key를 발급받아야 한다. 발급받은 키는 소스 코드에 직접 노출하지 않고 아래 `.env` 환경 변수 관리 정책을 따른다.

---

## 2. 환경 변수 관리 정책 (`.env`)
보안 데이터 및 환경별 가변 설정값은 프로젝트 루트 디렉토리의 `.env` 파일에서 관리하며, `pydantic-settings` 라이브러리를 통해 구조화된 객체로 읽어 들인다. `.env` 파일은 절대 Git 저장소에 커밋하지 않는다 (`.gitignore` 필수 등록).

### 2.1 `.env` 파일 예시
```env
# [App Configuration]
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
LOG_LEVEL=INFO

# [Client Authentication]
# Android 클라이언트가 ws 연결 시 ?api_key=<KEY> 쿼리로 전달하는 인증 키
WS_API_KEY=dev-local-key-change-me

# [AI Client Configuration]
GEMINI_API_KEY=AIzaSyYourActualGeminiApiKeyHere_xxxxxxxx

# [Future Scalability Placeholders - 3~5단계 확장용]
# CHROMA_DB_PATH=./data/chromadb
# DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/dbname

```

### 2.2 Python 설정 로더 예시 (`app/core/config.py`)

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    WS_API_KEY: str            # 클라이언트 인증용 키
    GEMINI_API_KEY: str        # Gemini API 호출용 키

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()

```

---

## 3. 디렉토리 구조도 (Directory Structure)

1단계 MVP 빌드를 타깃으로 하되, 3~5단계의 LangGraph 및 멀티 에이전트 아키텍처가 추가될 때 기존 폴더 구조를 깨지 않고 컴포넌트 단위로 플러그인할 수 있도록 계층형 구조(Layered Architecture)를 강제한다.

```text
GeminiVoiceChatServer/
├── docs/                           # 아키텍처 및 요구사항 정의 문서 폴더
│   ├── 01_server_requirements.md
│   └── 02_server_architecture.md
├── app/                            # 애플리케이션 메인 소스 폴더
│   ├── __init__.py
│   ├── main.py                     # FastAPI 앱 초기화 및 WebSocket 라우터
│   ├── core/                       # 시스템 핵심 설정 및 하네스 모니터링 모듈
│   │   ├── __init__.py
│   │   ├── config.py               # .env 로더 객체
│   │   └── harness.py              # 고정밀 타임스탬프 로깅 헬퍼
│   ├── schemas/                    # Pydantic 기반 데이터 직렬화 및 검증 규격
│   │   ├── __init__.py
│   │   └── protocol.py             # 클라이언트-서버 간 통신 패킷 스펙
│   ├── services/                   # 순수 비즈니스 로직 계층 (1~2단계 중심)
│   │   ├── __init__.py
│   │   └── gemini_service.py       # Gemini API 스트리밍 래퍼 클래스
│   └── agents/                     # 고급 AI 에기전트 워크플로우 계층 (3~5단계 확장용)
│       ├── __init__.py
│       ├── router_agent.py         # 4단계: 멀티 에이전트 라우팅 노드
│       ├── rag_agent.py            # 3단계: LangGraph + Chroma DB RAG 노드
│       └── rdb_agent.py            # 5단계: SQL Generation 및 DB 조회 노드
├── .env                            # 환경 변수 로컬 설정 파일 (Git 제외)
├── .env.example                    # 환경 변수 공유용 샘플 파일
├── .gitignore                      # venv, .env, __pycache__ 제외 설정
├── requirements.txt                # 의존성 라이브러리 목록
└── venv/                           # Python 파이썬 가상환경 폴더

```

---

## 4. 단계별 아키텍처 확장 로드맵 (Evolutionary Roadmap)

본 프로젝트는 시스템 안정성과 확장성을 확보하기 위해 **[1단계]의 기초 뼈대 구조 작업을 최우선으로 완료한 후**, 순차적으로 상위 단계로 확장하는 전략을 채택한다. Claude Code는 코딩 시 현재 단계를 명확히 인지하고 개발해야 한다.

### 1단계 : 핵심 통신 및 경량 LLM 연동 (현재 목표)

* **목표:** 단순 텍스트 입력을 받아 Gemini Flash 계열 모델(기본 `gemini-2.5-flash-lite`, `.env`의 `GEMINI_MODEL`로 override)에 요청하고 답변을 스트리밍으로 반환하는 기본 파이프라인 구축. SDK는 신규 공식 `google-genai`를 사용한다.
* **의의:** 전체 시스템의 WebSocket 핸들러 안정성과 비동기 타임스탬프 하네스 모니터링 구조를 검증하는 최소 기능 제품(MVP) 단계.

### 2단계 : 오디오 스트리밍 및 STT 파이프라인 확장

* **목표:** 클라이언트가 WebSocket으로 실시간 전달하는 음성(PCM) 데이터를 서버에서 수신, STT 엔진을 거쳐 텍스트로 변환 후 Gemini LLM에 전달.
* **의의:** 전이중(Full-Duplex) 실시간 오디오/텍스트 퓨전 처리 능력 확보.

### 3단계 : 지식 기반 에이전트 (LangGraph + RAG) 도입

* **목표:** `LangGraph` 프레임워크를 도입하여 워크플로우를 제어하고, `Chroma DB`를 벡터 스토어로 활용하여 RAG(Retrieval-Augmented Generation) 시스템 구축.
* **의의:** LLM이 단순 대화를 넘어 외부 도메인 지식(Vector 데이터)을 참조하여 정확한 답변을 도출하는 내부 에이전트 구조 안착.

### 4단계 : 도메인별 멀티 에이전트 시스템 (Multi-Agent System) 고도화

* **목표:** 라우터 에이전트를 중심으로 특정 역할에 특화된 멀티 에이전트(예: 법령 검색 에이전트, 고객 지원 에이전트 등)로 분화 및 협업 구조 설계.
* **의의:** 서비스 영역별 독립적인 프롬프트 및 컨텍스트 제어로 답변의 정밀도 극대화.

### 5단계 : 엔터프라이즈 데이터 인터페이스 (RDB Agent) 통합

* **목표:** 정형 데이터를 직접 조회하고 분석할 수 있는 SQL Query Generation 및 RDB 조회 전용 에이전트 파이프라인 추가.
* **의의:** 비정형(RAG)과 정형(RDB) 데이터를 모두 아우르는 엔터프라이즈급 AI 비서 시스템 완성.

---

## 5. 인터페이스 스펙 (Android 클라이언트 연동)

본 서버의 WebSocket 통신 규격은 Android 클라이언트 저장소의 `D:\dev\GeminiVoiceChat\app\docs\03_server_api.md`(이하 *클라이언트 API 스펙*)를 **단일 진실 원천(Single Source of Truth)** 으로 한다. 본 서버 문서의 내용이 클라이언트 API 스펙과 충돌할 경우, 클라이언트 API 스펙을 따른다.

### 5.1 핵심 합의 사항 (요약)

| 항목 | 값 | 비고 |
|---|---|---|
| WebSocket 엔드포인트 | `ws://<host>:<port>/ws` | `/ws/chat` 아님 |
| 인증 | `?api_key=<WS_API_KEY>` 쿼리 파라미터 | 불일치 시 close `1008` |
| 연결 직후 송신 | `status(READY)` | 자동 송신 |
| 스트리밍 종료 신호 | `{"type": "text_done"}` | `[FINISH]` 텍스트 사용 금지 |
| `SPEAKING` 상태 | **사용하지 않음** | `text_chunk` 자체가 SPEAKING 신호 역할 |
| `mode_change` 응답 | **없음 (ACK 없음)** | 세션 상태만 갱신 |
| `ping` 응답 | `{"type": "pong"}` | 즉시 응답 |
| 대화 히스토리 | 세션 단위 멀티턴 유지 | WebSocket 종료 시 폐기 |
| 에러 응답 순서 | `error` → `status(ERROR)` | 세션은 유지 |

### 5.2 1단계 구현 범위
- 지원 인바운드 메시지: `text_input`, `ping`, `mode_change`
- 인바운드 `audio_input` 메시지는 2단계 항목으로 1단계에서는 미지원. 수신 시 `error` 패킷 송신(예: `code: "AUDIO_FORMAT_INVALID"` 또는 별도 미구현 코드) 후 정상 대기 상태 유지.
- 지원 아웃바운드 메시지: `status(READY/THINKING/ERROR)`, `text_chunk`, `text_done`, `error`, `pong`.

### 5.3 세션 멀티턴 대화 히스토리 정책
- 각 WebSocket 연결마다 메모리 상에 대화 히스토리(예: `list[Content]`)를 보관하며 `GeminiClient`의 `start_chat(history=...)` 또는 동등한 메커니즘으로 컨텍스트를 누적 전달한다.
- 히스토리는 **WebSocket 세션 인스턴스의 라이프사이클에 종속**되며, 연결 종료 시 GC로 폐기된다. 1단계에서는 RDB/Redis 등 외부 영속화를 적용하지 않는다.
- 동일한 `trace_id`(연결 세션)로 발생한 모든 턴은 같은 히스토리를 공유한다.

---

## 6. 코드 작성 제약 조건

* Claude Code는 현재 **[1단계]** 구현에 집중하되, 3단계 이상의 `LangGraph` 및 에이전트 아키텍처가 플러그인 형태로 유연하게 결합될 수 있도록 `services/gemini_service.py`를 독립적인 모듈로 추상화하여 구현해야 한다.
* 모든 I/O 작업(WebSocket 수신/송신, Gemini API 호출)은 반드시 `async/await` 패턴을 사용하여 비동기로 처리해야 하며, 서버 스레드가 블로킹되는 코드를 작성해서는 안 된다.
* 예기치 못한 에러가 발생하더라도 세션이 폭발하지 않도록 단계별 라우터와 핵심 서비스 레이어에 에러 핸들링 메커니즘을 엄격히 적용한다.
* 인터페이스 스펙(5장)과 본 문서의 다른 서술이 충돌할 경우 5장(=클라이언트 API 스펙)을 따른다.

```

```