"""WebSocket smoke test for the Gemini Voice Chat Server.

Tests (run sequentially against a running server):
  1. Bad api_key   -> connection refused
  2. Good api_key  -> immediate status(READY)
  3. ping          -> pong
  4. text_input    -> status(THINKING) -> text_chunk x N -> text_done
  5. multi-turn    -> 2nd turn must recall name from 1st turn

Usage (from project root, with venv activated):
    python scripts/ws_smoke.py
    python scripts/ws_smoke.py --host 127.0.0.1 --port 8000
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


# ---- Output helpers ----------------------------------------------------------

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def header(msg: str) -> None:
    print(f"\n{YELLOW}=== {msg} ==={RESET}")


def info(msg: str) -> None:
    print(f"  {DIM}> {msg}{RESET}")


def pass_(msg: str) -> None:
    print(f"  {GREEN}[PASS]{RESET} {msg}")


class TestFailure(Exception):
    pass


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET} {msg}")
    raise TestFailure(msg)


# ---- WS helpers --------------------------------------------------------------


async def recv_packet(ws, timeout: float = 30.0) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


async def expect(ws, packet_type: str, *, code: str | None = None, timeout: float = 30.0) -> dict:
    pkt = await recv_packet(ws, timeout=timeout)
    if pkt.get("type") != packet_type:
        fail(f"expected type={packet_type!r}, got {pkt!r}")
    if code is not None and pkt.get("code") != code:
        fail(f"expected code={code!r}, got {pkt!r}")
    return pkt


async def drain_stream(ws) -> tuple[list[str], float]:
    """Consume text_chunk* until text_done. Returns (chunks, ttft_seconds)."""
    chunks: list[str] = []
    t_start = time.perf_counter()
    ttft: float | None = None
    while True:
        pkt = await recv_packet(ws, timeout=30.0)
        t = pkt.get("type")
        if t == "text_chunk":
            if ttft is None:
                ttft = time.perf_counter() - t_start
            chunks.append(pkt["text"])
        elif t == "text_done":
            return chunks, (ttft if ttft is not None else 0.0)
        else:
            fail(f"unexpected packet during streaming: {pkt!r}")


# ---- Tests -------------------------------------------------------------------


async def test_bad_auth(base_url: str) -> None:
    header("Test 1: bad api_key -> connection rejected")
    url = f"{base_url}/ws?api_key=this-is-definitely-wrong-xyz"
    rejected_reason: str | None = None
    try:
        async with connect(url) as ws:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                fail(f"server accepted bad key; got: {raw!r}")
            except ConnectionClosed as e:
                rejected_reason = f"close code={e.code} reason={e.reason!r}"
    except InvalidStatus as e:
        rejected_reason = f"handshake rejected (HTTP {e.response.status_code})"
    except ConnectionClosed as e:
        rejected_reason = f"close code={e.code} reason={e.reason!r}"
    except Exception as e:
        rejected_reason = f"{type(e).__name__}: {e}"

    if rejected_reason:
        pass_(f"server rejected: {rejected_reason}")
    else:
        fail("server did not reject bad api_key")


async def test_good_auth_and_ready(url: str) -> None:
    header("Test 2: good api_key -> immediate status(READY)")
    async with connect(url) as ws:
        pkt = await expect(ws, "status", code="READY", timeout=5.0)
        pass_(f"received {pkt}")


async def test_ping_pong(url: str) -> None:
    header("Test 3: ping -> pong")
    async with connect(url) as ws:
        await expect(ws, "status", code="READY")
        await ws.send(json.dumps({"type": "ping"}))
        pkt = await expect(ws, "pong", timeout=3.0)
        pass_(f"received {pkt}")


async def test_text_input_flow(url: str) -> None:
    header("Test 4: text_input -> THINKING -> text_chunk x N -> text_done")
    async with connect(url) as ws:
        await expect(ws, "status", code="READY")
        prompt = "안녕하세요! 한 문장으로 자기소개 해주세요."
        info(f"send: {prompt!r}")
        await ws.send(json.dumps({"type": "text_input", "text": prompt}))

        await expect(ws, "status", code="THINKING", timeout=10.0)
        chunks, ttft = await drain_stream(ws)

        if not chunks:
            fail("no text_chunk packets received")
        full = "".join(chunks)
        info(f"AI ({len(chunks)} chunks, ttft={ttft*1000:.0f}ms): {full!r}")
        pass_(f"clean text_done after {len(chunks)} chunks")


async def test_multi_turn(url: str) -> None:
    header("Test 5: multi-turn context retention")
    async with connect(url) as ws:
        await expect(ws, "status", code="READY")

        # Turn 1
        prompt1 = "내 이름은 민수야. 짧게 인사만 해줘."
        info(f"Turn 1 send: {prompt1!r}")
        await ws.send(json.dumps({"type": "text_input", "text": prompt1}))
        await expect(ws, "status", code="THINKING")
        chunks1, _ = await drain_stream(ws)
        info(f"Turn 1 AI: {''.join(chunks1)!r}")

        # Turn 2
        prompt2 = "내 이름이 뭐였지? 이름만 답해."
        info(f"Turn 2 send: {prompt2!r}")
        await ws.send(json.dumps({"type": "text_input", "text": prompt2}))
        await expect(ws, "status", code="THINKING")
        chunks2, _ = await drain_stream(ws)
        reply2 = "".join(chunks2)
        info(f"Turn 2 AI: {reply2!r}")

        if "민수" in reply2:
            pass_("AI recalled the name '민수' from Turn 1 -> multi-turn history works")
        else:
            fail(f"AI did not recall the name from Turn 1; reply={reply2!r}")


# ---- Entry point -------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=int(os.getenv("SERVER_PORT", "8000")))
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip tests that hit Gemini (run only auth/ping)",
    )
    args = parser.parse_args()

    api_key = os.getenv("WS_API_KEY")
    if not api_key:
        print(f"{RED}ERROR: WS_API_KEY not set (check .env at {PROJECT_ROOT / '.env'}){RESET}")
        return 2

    scheme = "wss" if args.port == 443 else "ws"
    base_url = f"{scheme}://{args.host}:{args.port}"
    good_url = f"{base_url}/ws?api_key={api_key}"

    print(f"{CYAN}Target: {base_url}{RESET}")
    print(f"{CYAN}WS_API_KEY: {api_key[:4]}***{RESET}")

    tests = [
        ("bad_auth", test_bad_auth(base_url)),
        ("ready", test_good_auth_and_ready(good_url)),
        ("ping_pong", test_ping_pong(good_url)),
    ]
    if not args.skip_llm:
        tests += [
            ("text_input_flow", test_text_input_flow(good_url)),
            ("multi_turn", test_multi_turn(good_url)),
        ]

    failures: list[str] = []
    for name, coro in tests:
        try:
            await coro
        except TestFailure:
            failures.append(name)
        except (ConnectionRefusedError, OSError) as e:
            print(f"  {RED}[FAIL]{RESET} cannot reach server at {base_url}: {e}")
            print(f"  {YELLOW}HINT:{RESET} start the server first: "
                  f"uvicorn app.main:app --host 0.0.0.0 --port {args.port}")
            return 2
        except Exception as e:
            failures.append(name)
            print(f"  {RED}[FAIL]{RESET} uncaught exception: {type(e).__name__}: {e}")

    print()
    if not failures:
        print(f"{GREEN}ALL TESTS PASSED ({len(tests)}/{len(tests)}){RESET}")
        return 0
    print(f"{RED}FAILED: {', '.join(failures)} ({len(failures)}/{len(tests)}){RESET}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
