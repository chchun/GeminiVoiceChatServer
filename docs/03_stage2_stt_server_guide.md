# Stage 2 구현 가이드 — 서버 사이드 STT (Google Cloud Speech-to-Text)

> **작성일:** 2026-05-18  
> **대상:** 신규 Claude 세션 (이전 컨텍스트 없음)  
> **현재 상태:** Stage 1 완료, Cloud Run 배포 중  
> **이 문서의 목적:** Stage 2 구현에 필요한 모든 정보를 자기완결적으로 제공

---

## 1. 배경 및 설계 원칙

### Stage 1 현황
- FastAPI WebSocket 서버가 Google Cloud Run에 배포되어 운영 중
- `text_input` 패킷만 처리, `audio_input` 수신 시 error 응답 반환 (Stage 2 예약)
- `app/services/gemini_service.py`는 독립 모듈로 설계됨 (LLM 교체 용이)

### Stage 2 설계 결정 사항
- **STT 처리 위치: 서버 사이드** (보안상 이유 — Android APK에 STT API 키 노출 방지)
- **Google Cloud Speech-to-Text API** 사용 (gRPC StreamingRecognize)
- **`stt_service.py`를 독립 모듈로 구현** — 향후 Whisper, Clova 등으로 교체 가능
- **LLM 레이어 변경 없음** — STT 결과 텍스트를 기존 `_handle_text_input()`으로 전달

### Stage 2 전체 흐름

```
Android              FastAPI (main.py)         stt_service.py    gemini_service.py
   │                      │                         │                  │
   │─mode_change(VOICE)──►│                         │                  │
   │─audio_input(chunk)──►│                         │                  │
   │─audio_input(chunk)──►│──push_audio()──────────►│                  │
   │─audio_input(chunk)──►│  (PCM 청크 누적)          │                  │
   │─audio_input(final)──►│                         │──gRPC──►STT API  │
   │                      │◄──transcript────────────│                  │
   │                      │──stream(transcript)─────────────────────►  │
   │◄─status(THINKING)────│◄──────────────────────────────────────────│
   │◄─text_chunk × N ─────│◄──streaming response──────────────────────│
   │◄─text_done ──────────│                                           │
```

---

## 2. Google Cloud 사전 준비

### 2-1. Speech-to-Text API 활성화

```powershell
gcloud config set project gemini-voice-chat-chchun
gcloud services enable speech.googleapis.com
```

### 2-2. 서비스 계정 생성 및 권한 부여

```powershell
# 서비스 계정 생성
gcloud iam service-accounts create stt-invoker `
  --display-name="STT API Invoker"

# Cloud Speech 권한 부여
gcloud projects add-iam-policy-binding gemini-voice-chat-chchun `
  --member="serviceAccount:stt-invoker@gemini-voice-chat-chchun.iam.gserviceaccount.com" `
  --role="roles/cloudsp speech.client"

# 키 파일 다운로드 (로컬 개발용)
gcloud iam service-accounts keys create stt-key.json `
  --iam-account="stt-invoker@gemini-voice-chat-chchun.iam.gserviceaccount.com"
```

> **Cloud Run 배포 시:** 키 파일 대신 Cloud Run 서비스 계정에 직접 권한 부여 (§6 참조)

### 2-3. 로컬 `.env`에 추가

```env
# 기존 항목 유지
WS_API_KEY=dev-local-key-change-me
GEMINI_API_KEY=AIzaSy...

# Stage 2 추가
GOOGLE_APPLICATION_CREDENTIALS=./stt-key.json
STT_LANGUAGE_CODE=ko-KR
STT_SAMPLE_RATE=16000
```

> `stt-key.json`은 `.gitignore`에 이미 포함된 패턴(`*.json` 계열)으로 커밋되지 않음.  
> 확인 후 필요하면 `.gitignore`에 `stt-key.json` 명시적으로 추가.

---

## 3. 의존성 추가

### 3-1. 패키지 설치

```powershell
venv\Scripts\activate
pip install google-cloud-speech
pip freeze > requirements.txt
```

### 3-2. `requirements.txt` 추가 확인

```
google-cloud-speech==2.x.x   # 설치 후 정확한 버전 확인
```

---

## 4. 수정/추가 파일 상세

### 4-1. `app/core/config.py` — STT 설정 추가

**현재:**
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    WS_API_KEY: str
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
```

**수정 후:**
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    WS_API_KEY: str
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"

    # Stage 2 — STT
    GOOGLE_APPLICATION_CREDENTIALS: str = ""  # 로컬: 파일 경로, Cloud Run: 빈 문자열(자동 인증)
    STT_LANGUAGE_CODE: str = "ko-KR"
    STT_SAMPLE_RATE: int = 16000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
```

---

### 4-2. `app/services/stt_service.py` — 신규 파일

```python
"""Google Cloud Speech-to-Text 비동기 래퍼.

설계 원칙:
- gemini_service.py와 동일하게 독립 모듈로 유지
- 향후 Whisper, Clova 등 다른 STT 엔진으로 교체 시 이 파일만 수정
- WebSocket/FastAPI 레이어에 의존하지 않음
"""
import base64
import os
from typing import Optional

from google.cloud import speech


class SttSession:
    """WebSocket 세션 단위 STT 처리기.

    Android에서 보내는 PCM 청크(Base64)를 누적하다가
    is_final=True 수신 시 Google STT API에 일괄 전송, transcript 반환.
    """

    def __init__(self, language_code: str = "ko-KR", sample_rate: int = 16000) -> None:
        # GOOGLE_APPLICATION_CREDENTIALS 환경변수가 설정되어 있으면 자동으로 사용됨
        # Cloud Run에서는 서비스 계정 자동 인증
        self._client = speech.SpeechAsyncClient()
        self._language_code = language_code
        self._sample_rate = sample_rate
        self._audio_buffer: list[bytes] = []

    async def push_audio(self, pcm_b64: str, is_final: bool) -> Optional[str]:
        """PCM Base64 청크를 누적. is_final=True 시 STT API 호출 후 transcript 반환.

        Args:
            pcm_b64: Base64 인코딩된 PCM 오디오 데이터
            is_final: True이면 STT 호출 트리거

        Returns:
            transcript 문자열 (is_final=True 이고 인식 성공 시)
            None (청크 누적 중 또는 인식 결과 없음)
        """
        audio_bytes = base64.b64decode(pcm_b64)
        self._audio_buffer.append(audio_bytes)

        if not is_final:
            return None

        # 버퍼에 쌓인 PCM 데이터를 STT API에 전송
        audio_data = b"".join(self._audio_buffer)
        self._audio_buffer.clear()

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=self._sample_rate,
            language_code=self._language_code,
            enable_automatic_punctuation=True,
        )
        audio = speech.RecognitionAudio(content=audio_data)

        response = await self._client.recognize(config=config, audio=audio)

        if response.results:
            return response.results[0].alternatives[0].transcript

        return None

    def reset(self) -> None:
        """버퍼를 초기화. 연속 발화 세션 사이에 호출."""
        self._audio_buffer.clear()
```

---

### 4-3. `app/main.py` — audio_input 핸들러 구현

**변경 1: import 추가**

```python
# 기존 imports 아래에 추가
from app.services.stt_service import SttSession
```

**변경 2: WebSocket 세션 초기화 블록에 SttSession 추가**

```python
# 기존 (GeminiSession 초기화 직후)
try:
    gemini = GeminiSession(
        api_key=settings.GEMINI_API_KEY,
        model_name=settings.GEMINI_MODEL,
    )
except Exception as e:
    ...

# 아래 추가
try:
    stt = SttSession(
        language_code=settings.STT_LANGUAGE_CODE,
        sample_rate=settings.STT_SAMPLE_RATE,
    )
except Exception as e:
    logger.exception("Failed to initialize SttSession")
    await _send_error(websocket, "INTERNAL_ERROR", f"STT init failed: {e}")
    await websocket.close()
    return
```

**변경 3: audio_input 핸들러 교체**

```python
# 기존 (Stage 1 — error 반환)
elif msg_type == "audio_input":
    await _send_error(
        websocket,
        "AUDIO_FORMAT_INVALID",
        "audio_input is not supported in Stage 1; use text_input",
    )

# Stage 2 — 실구현으로 교체
elif msg_type == "audio_input":
    try:
        packet = AudioInputPacket.model_validate(data)
    except ValidationError as e:
        await _send_error(websocket, "INTERNAL_ERROR", f"Bad audio_input: {e.errors()}")
        continue

    try:
        transcript = await stt.push_audio(packet.payload, packet.is_final)
    except Exception as e:
        logger.exception(f"STT error trace_id={trace_id}")
        await _send_error(websocket, "INTERNAL_ERROR", f"STT failed: {e}")
        continue

    if transcript:
        logger.info(f"STT transcript trace_id={trace_id} text={transcript!r}")
        await _handle_text_input(websocket, gemini, transcript, trace_id, ts_recv)
```

---

### 4-4. `app/main.py` 최종 전체 구조 (참고용)

```python
# 세션 초기화 순서
await websocket.accept()
await _send(websocket, StatusPacket(code="READY"))

gemini = GeminiSession(...)     # Gemini 세션
stt    = SttSession(...)         # STT 세션 (Stage 2 신규)
session_mode = "TEXT"

# 메시지 루프
while True:
    raw = await websocket.receive_text()
    msg_type = ...

    if   msg_type == "text_input":   → _handle_text_input(gemini)
    elif msg_type == "audio_input":  → stt.push_audio() → _handle_text_input(gemini)
    elif msg_type == "ping":         → pong
    elif msg_type == "mode_change":  → session_mode 갱신
    else:                            → error
```

---

## 5. 오디오 포맷 규격

Android에서 서버로 전송하는 오디오 포맷:

| 항목 | 값 |
|------|-----|
| 인코딩 | PCM LINEAR16 (16-bit signed integer) |
| 샘플레이트 | 16,000 Hz |
| 채널 | Mono (1채널) |
| 전송 단위 | Base64 인코딩 문자열 (`audio_input.payload`) |
| 청크 크기 | 권장 100ms 단위 (1,600 샘플 = 3,200 bytes raw) |
| 종료 신호 | 마지막 청크에서 `is_final: true` |

---

## 6. Cloud Run 재배포

### 6-1. Cloud Run 서비스 계정에 STT 권한 부여 (키 파일 불필요)

```powershell
# Cloud Run이 사용하는 기본 서비스 계정에 STT 권한 부여
gcloud projects add-iam-policy-binding gemini-voice-chat-chchun `
  --member="serviceAccount:401388719515-compute@developer.gserviceaccount.com" `
  --role="roles/speech.client"
```

### 6-2. 재배포 (GOOGLE_APPLICATION_CREDENTIALS 없이 자동 인증)

```powershell
gcloud run deploy gemini-voice-chat-server `
  --source . `
  --region asia-northeast3 `
  --platform managed `
  --allow-unauthenticated `
  --port 8000 `
  --set-env-vars "LOG_LEVEL=INFO,GEMINI_MODEL=gemini-2.5-flash-lite" `
  --set-env-vars "WS_API_KEY=dev-local-key-change-me,GEMINI_API_KEY=AIzaSyB8iguwu96M1NAPjc2NFt7IW7d2u0dGOB8" `
  --set-env-vars "STT_LANGUAGE_CODE=ko-KR,STT_SAMPLE_RATE=16000" `
  --timeout 3600 `
  --min-instances 0 `
  --max-instances 3 `
  --session-affinity
```

> Cloud Run 환경에서는 `GOOGLE_APPLICATION_CREDENTIALS` 환경변수 없이  
> Compute Engine 서비스 계정 자동 인증이 적용됩니다.

---

## 7. 로컬 테스트

### 7-1. STT 단독 테스트

```python
# scripts/test_stt.py (임시 테스트용)
import asyncio, base64, wave
from app.services.stt_service import SttSession

async def main():
    stt = SttSession(language_code="ko-KR", sample_rate=16000)

    with wave.open("test.wav", "rb") as wf:
        pcm = wf.readframes(wf.getnframes())

    b64 = base64.b64encode(pcm).decode()
    result = await stt.push_audio(b64, is_final=True)
    print(f"Transcript: {result}")

asyncio.run(main())
```

### 7-2. 스모크 테스트 (기존 스크립트 활용)

```powershell
# 로컬 서버 실행
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# text_input 흐름은 기존과 동일하게 테스트
python scripts/ws_smoke.py
```

---

## 8. 오류 처리 정책

| 상황 | 처리 |
|------|------|
| STT API 인증 실패 | `INTERNAL_ERROR` + `status(ERROR)`, 세션 유지 |
| 오디오 디코딩 실패 (잘못된 Base64) | `AUDIO_FORMAT_INVALID` + `status(ERROR)`, 세션 유지 |
| STT 결과 없음 (무음 등) | 응답 없이 조용히 무시, 다음 입력 대기 |
| STT API 타임아웃 | `GEMINI_API_ERROR` (재사용) 또는 별도 `STT_ERROR` 코드 추가 |

> `STT_ERROR` 코드를 추가할 경우 `app/schemas/protocol.py`의 `ErrorCode` Literal에 추가 필요:  
> `ErrorCode = Literal["AUTH_FAILED", "AUDIO_FORMAT_INVALID", "GEMINI_API_ERROR", "STT_ERROR", "INTERNAL_ERROR"]`

---

## 9. 작업 체크리스트

- [ ] `gcloud services enable speech.googleapis.com`
- [ ] Cloud Run 서비스 계정에 `roles/speech.client` 부여
- [ ] `pip install google-cloud-speech` 후 `pip freeze > requirements.txt`
- [ ] `app/core/config.py` — STT 설정 3개 추가
- [ ] `app/services/stt_service.py` — 신규 파일 생성
- [ ] `app/main.py` — SttSession import, 초기화, audio_input 핸들러 구현
- [ ] 로컬에서 `GOOGLE_APPLICATION_CREDENTIALS` 설정 후 테스트
- [ ] `gcloud run deploy` 재배포
- [ ] Android 클라이언트와 통합 테스트
