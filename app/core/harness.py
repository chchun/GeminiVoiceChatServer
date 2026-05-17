import json
import logging
import time
from dataclasses import asdict, dataclass

logger = logging.getLogger("harness")


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class TurnHarness:
    """Captures the four critical timestamps for a single conversation turn.

    KPI: ts_llm_ttft - ts_server_recv < 1000ms
    """

    trace_id: str
    ts_server_recv: int = 0
    ts_llm_req: int = 0
    ts_llm_ttft: int = 0
    ts_server_send_end: int = 0

    def log(self) -> None:
        ttft_ms = self.ts_llm_ttft - self.ts_server_recv if self.ts_llm_ttft else None
        total_ms = (
            self.ts_server_send_end - self.ts_server_recv if self.ts_server_send_end else None
        )
        payload = {
            "event": "turn_latency",
            **asdict(self),
            "ttft_ms": ttft_ms,
            "total_ms": total_ms,
        }
        logger.info(json.dumps(payload, ensure_ascii=False))
