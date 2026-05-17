# CHANGELOG

## [1.1.0] - 2026-05-18

### Added
- **Google Cloud Run 배포 지원**
  - `Dockerfile` 추가 (python:3.12-slim 기반)
  - `.dockerignore` 추가
  - `--session-affinity` 활성화로 WebSocket 연결 안정성 확보
- **`scripts/ws_smoke.py` Cloud Run 대응**
  - `--port 443` 지정 시 `wss://` 스킴 자동 적용

### Changed
- **WebSocket Keepalive 전략 변경**
  - 서버 측 30초 text ping 제거
  - Android 클라이언트 OkHttp `pingInterval(20s)` 프로토콜 레벨 PING으로 대체
  - 서버는 uvicorn/Starlette의 자동 PONG 응답 활용 (코드 변경 없음)
- **문서 업데이트**
  - `README.md`: Cloud Run 배포 가이드, Android `local.properties` 설정, `wss://` 주의사항 추가
  - `docs/02_server_architecture.md`: 디렉토리 구조에 `Dockerfile`, `scripts/` 추가 및 Cloud Run 배포 섹션(§6) 신설

### Fixed
- `ws_smoke.py`: port 443 사용 시 `ws://` 로 연결 시도하던 버그 수정 → `wss://` 자동 전환

---

## [1.0.0] - 2026-05-17

### Added
- **Stage 1 MVP 초기 구현**
  - FastAPI WebSocket 서버 (`app/main.py`)
    - 엔드포인트: `/ws?api_key=<KEY>`
    - 인증 실패 시 close code `1008`
    - 연결 직후 `status(READY)` 전송
    - 패킷 타입: `text_input`, `ping`, `mode_change`, `audio_input`(Stage 2 예약)
  - Gemini API 비동기 스트리밍 래퍼 (`app/services/gemini_service.py`)
    - `google-genai` SDK 사용 (구 `google-generativeai` 대체)
    - 기본 모델: `gemini-2.5-flash-lite` (`GEMINI_MODEL` env로 override)
    - WebSocket 세션 단위 멀티턴 히스토리 메모리 보관
  - Pydantic v2 패킷 스키마 (`app/schemas/protocol.py`)
  - pydantic-settings 환경 변수 로더 (`app/core/config.py`)
  - 밀리초 정밀도 레이턴시 하네스 (`app/core/harness.py`)
    - 4개 타임스탬프: `ts_server_recv`, `ts_llm_req`, `ts_llm_ttft`, `ts_server_send_end`
    - KPI: `ttft_ms` < 1000 ms
  - WebSocket 스모크 테스트 (`scripts/ws_smoke.py`)
    - 5개 시나리오: 인증 거부, READY, ping/pong, 스트리밍, 멀티턴
  - `.env.example`, `.gitignore`, `requirements.txt`
  - `README.md` 초기 작성
