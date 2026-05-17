# Gemini Voice Chat 백엔드 요구사항 정의서 (Server PRD)

## [Epic] 대화형 AI 서비스 백엔드 핵심 파이프라인 구축 및 에이전트 고도화

### 1. Epic 개요 및 목표
본 에픽은 안드로이드 클라이언트와 연동하여 실시간 음성/텍스트 AI 대화 기능을 제공하는 비동기 백엔드 인프라를 구축하는 것을 목표로 한다. 
초기 가벼운 LLM 연동(1단계)으로 엔드투엔드 통신 및 모니터링 하네스를 검증한 후, 지식 기반 RAG, 멀티 에이전트, 엔터프라이즈 RDB 연동까지 안정적으로 스케일아웃(Scale-out)할 수 있는 구조를 확보한다.

---

### 2. 단계별 마일스톤 및 지라 컴포넌트 로드맵

| 단계 | 지라 에픽/태스크 명칭 | 핵심 인도물 (Deliverables) | 검증 지표 (KPI) |
| :--- | :--- | :--- | :--- |
| **1단계** | `INFRA-TEXT-STREAM` | FastAPI WebSocket 뼈대 구축 및 Gemini Flash 계열 모델 텍스트 스트리밍 연동 (기본 `gemini-2.5-flash-lite`) | 서버 내부 TTFT < 1.0초 |
| **2단계** | `INFRA-AUDIO-STT` | WebSocket 오디오 청크(PCM) 수신 처리 및 로컬/클라우드 STT 파이프라인 결합 | 음성 무손실 변환율 > 98% |
| **3단계** | `AGENT-RAG-CHROMA` | LangGraph 워크플로우 제어 및 Chroma DB 벡터 기반 RAG 에이전트 구축 | 문서 참조 정확도 > 90% |
| **4단계** | `AGENT-MULTI-ROUTER` | 라우터 에이전트 설계 및 도메인별(법령, 고객지원) 멀티 에이전트 분화 | 분류 오라우팅율 < 5% |
| **5단계** | `AGENT-RDB-SQL` | 정형 데이터 가독을 위한 SQL Generation 및 DB 인터페이스 에이전트 통합 | SQL 구문 오류 발생률 < 1% |

---

### 3. [현재 스프린트] 1단계 하위 유저 스토리 및 개발 명세

#### 🚀 Story 1: FastAPI 기반 전이중(Full-Duplex) WebSocket 핸들러 구현
- **User Story:** 
  - *As a* 백엔드 엔지니어, *I want to* FastAPI에 비동기 WebSocket 라우터를 구축하여, *So that* 안드로이드 클라이언트와 끊김 없는 양방향 메시지 채널을 유지하고 싶다.
- **상세 작업 내역 (Sub-tasks):**
  - `ws://[host]:[port]/ws` 엔드포인트 개설 및 상시 연결 세션 유지. (Android 클라이언트 스펙 `03_server_api.md` 준수)
  - 연결 시 쿼리 파라미터 `?api_key=<KEY>` 검증. 키가 일치하지 않으면 WebSocket close code `1008`(Policy Violation)으로 연결 거부.
  - 인증 성공 직후 즉시 `{"type": "status", "code": "READY"}` 패킷 송신.
  - Pydantic v2를 활용한 인바운드/아웃바운드 패킷 검증 인터셉터 구현.
  - 클라이언트 입력 타입 분기 로직 처리:
    - `text_input` → 텍스트 처리 파이프라인 트리거 (Story 2)
    - `ping` → 즉시 `{"type": "pong"}` 응답
    - `mode_change` → 세션 상태 갱신만 수행, 응답 없음
    - `audio_input` → 1단계에서는 미지원 (2단계 확장 항목)
  - 클라이언트 급작스러운 단절(Disconnect) 발생 시 세션 자원을 안전하게 회수하는 `try-except-finally` 클린업 로직 구현.
- **인수 조건 (Acceptance Criteria):**
  - `Given` 클라이언트가 올바른 `api_key`와 함께 연결을 요청했을 때
  - `When` 세션이 성립되면
  - `Then` 서버는 즉시 `status(READY)` 패킷을 송신하고 연결 완료 로그를 남긴 후 대기 상태로 진입해야 한다.
  - `And` 잘못된 `api_key`로 접속 시 close code `1008`로 거부해야 한다.
  - `And` 규격에 맞지 않는 포맷이 인입되면 예외로 서버가 다운되지 않고 `error` 패킷(`AUTH_FAILED` / `INTERNAL_ERROR` 등) + `status(ERROR)`를 순차 송신해야 한다.

#### 🚀 Story 2: Gemini Flash API 스트리밍 및 상태 제어 알림 연동
- **User Story:**
  - *As a* AI 엔지니어, *I want to* Google GenAI SDK를 비동기 제너레이터 형태로 호출하고 상태 패킷을 관리하여, *So that* AI가 생성하는 답변 토큰과 UI 제어 신호를 클라이언트에 실시간으로 푸시하고 싶다.
- **상세 작업 내역 (Sub-tasks):**
  - `google-genai`(신규 공식 SDK) 라이브러리를 활용한 `GeminiSession` 래퍼 클래스 구현. 기본 모델은 `gemini-2.5-flash-lite` (`.env`의 `GEMINI_MODEL`로 override 가능; 구 `google-generativeai`는 deprecated 이므로 사용 금지).
  - **세션 단위 멀티턴 대화 히스토리 관리:** 동일 WebSocket 세션 내에서는 이전 사용자 발화 및 모델 응답을 컨텍스트로 누적 보관하여 Gemini API에 전달. WebSocket 연결 종료 시 히스토리는 폐기 (메모리 인스턴스 단위 보관, DB 비영속).
  - 유저 질문 수신 직후, 서버는 즉시 클라이언트에게 `{"type": "status", "code": "THINKING"}` 상태 패킷을 전송하여 클라이언트의 로딩 애니메이션을 제어함.
  - Gemini API의 `generate_content_stream` 호출 및 비동기 루프 처리.
  - 들어오는 토큰들을 `{"type": "text_chunk", "text": "..."}` 구조로 쪼개어 실시간으로 푸시 (클라이언트 로컬 TTS가 부드럽게 읽을 수 있도록 단어/문장 부호 단위 정형화 유도).
  - **스트리밍 완료 시 별도 메시지 타입인 `{"type": "text_done"}` 패킷을 1회 송신** 후 세션 유지. (Android `RemoteAiRepository`가 이 신호로 Flow를 close 처리)
  - Gemini API 호출 실패 시 `{"type": "error", "code": "GEMINI_API_ERROR", "message": "..."}` → `{"type": "status", "code": "ERROR"}` 순으로 송신하고 세션은 유지.
  - **`SPEAKING` 상태 패킷은 사용하지 않음** (Android 클라이언트 API 스펙 미정의 — `text_chunk` 자체가 SPEAKING 신호 역할).
- **인수 조건 (Acceptance Criteria):**
  - `Given` 유저가 `text_input` 메시지를 송신했을 때
  - `When` 서버가 이를 처리하는 전체 주기 동안
  - `Then` 응답 흐름은 `status(THINKING)` → `text_chunk × N` → `text_done` 순서로 끊김 없이 클라이언트에 전송되어야 한다.
  - `And` 동일 세션의 두 번째 `text_input`은 첫 번째 턴의 컨텍스트를 인지한 응답이어야 한다.

#### 🚀 Story 3: 하네스 엔지니어링용 고정밀 구간별 타임스탬프 로깅 시스템 구축
- **User Story:**
  - *As a* 시스템 아키텍트, *I want to* 주요 파이프라인 구간마다 밀리초 단위의 타임스탬프를 캡처하여, *So that* 전체 Latency 병목 구간을 정량적으로 모니터링하고 성능 목표를 달성하고 싶다.
- **상세 작업 내역 (Sub-tasks):**
  - 지연 시간(Latency) 추적을 위해 서버 메인 루프 내부 핵심 구간에 아래 고정밀 타임스탬프 로깅 로직을 삽입 (Console 및 내부 메모리 스토어 저장):
    - `ts_server_recv`: 클라이언트로부터 메시지를 수신한 시점
    - `ts_llm_req`: Gemini API 호출을 시작한 시점
    - `ts_llm_ttft`: Gemini로부터 첫 번째 토큰(Chunk)을 수신한 시점 (Time To First Token)
    - `ts_server_send_end`: 스트리밍 완료 후 `text_done` 패킷을 송신한 시점
  - 각 연결 세션(`trace_id`)별로 수집된 지연 시간 지표를 구조화된 JSON 포맷(Structured Console Log)으로 출력 처리.
- **성능 목표 (KPI):**
  - 서버 내부 핵심 지연 지표인 TTFT(ts_llm_ttft - ts_server_recv) 값은 **1.0초 이내**를 유지해야 함.
- **인수 조건 (Acceptance Criteria):**
  - `Given` 하나의 대화 턴(Turn)이 완전히 종료되었을 때
  - `When` 서버 로그 콘솔을 확인하면
  - `Then` 지정된 4가지 핵심 타임스탬프와 최종 계산된 서버 내부 TTFT 성능 지표가 밀리초(ms) 단위로 정확하게 누락 없이 기록되어야 한다.

---

### 4. [향후 스프린트] 2~5단계 백로그 기능 요구사항 (High-Level)

#### 4.1 [2단계] 음성 처리 (STT) 백로그
- 클라이언트 오디오 스트림 수신 버퍼링 구조 설계 (100ms 윈도우 청크 제어)
- `audio_input` 타입으로 인입되는 Base64 디코딩 및 Raw PCM(16kHz, 16bit, Mono) 스트림 파이프라인 구축
- 오디오 바이너리 스트림을 실시간으로 STT 엔진에 피딩(Feeding)하여 텍스트로 변환 후 내부 LLM 큐(Queue) 연동

#### 4.2 [3단계] LangGraph + RAG 백로그
- `LangGraph` StateGraph 객체 생성 및 컴포넌트 수명 주기 제어
- 유저 질문 벡터화(Embedding) 및 `Chroma DB` 유사도 검색(Top-K) 서브루틴 연동
- 컨텍스트 주입 프롬프트 팩토리 구현

#### 4.3 [4단계] 멀티 에이전트 라우팅 백로그
- 유저 의도 분류(Intent Classification) 전용 경량 라우터 모델 구성
- '법령 검색 에이전트 노드'와 '고객 지원 에이전트 노드' 간의 가변적 컨텍스트 라우팅 맵 설계
- 독립된 프롬프트셋 분리 관리

#### 4.4 [5단계] RDB 에이전트 백로그
- 정형 데이터베이스(PostgreSQL 등) 스키마 정보를 메타데이터 형태로 LLM에 전달하는 Context Builder 구현
- 생성된 SQL 인젝션 방어용 SQL Validator 및 가상 실행 샌드박스 레이어 구축
- 데이터셋 결과의 JSON 변환 및 자연어 요약 파이프라인 결합