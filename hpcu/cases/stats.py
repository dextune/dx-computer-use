"""Action log and MiniMax M3 usage counters.

Counts happen on the gateway wrapper, not a parallel fake.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from hpcu.gateway.gateway import Gateway, GatewayResponse

MINIMAX_MODEL = "MiniMax-M3"


@dataclass
class ActionRecord:
    kind: str
    detail: str
    ok: bool = True


@dataclass
class CaseStats:
    case_id: str
    success: bool = False
    attempts: int = 0
    action_count: int = 0
    minimax_call_count: int = 0
    minimax_error_count: int = 0
    minimax_tokens: int = 0
    compile_call_count: int = 0
    grounding_call_count: int = 0
    model: str = MINIMAX_MODEL
    selected_text: str = ""
    failure: str = ""
    actions: list[ActionRecord] = field(default_factory=list)


class CountingGateway(Gateway):
    """Wraps a Gateway, forcing MiniMax-M3 and counting calls/errors."""

    def __init__(self, inner: Gateway):
        self._inner = inner
        self.call_count = 0
        self.error_count = 0
        self.tokens = 0
        self.model = MINIMAX_MODEL
        self.http_log: list[str] = []

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
    ) -> GatewayResponse:
        self.call_count += 1
        self.http_log.append(f"MODEL_CALL MiniMax-M3 #{self.call_count}")
        try:
            kwargs: dict[str, Any] = {"system_prompt": system_prompt}
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            try:
                response = self._inner.call(prompt, **kwargs)
            except TypeError:
                response = self._inner.call(prompt, system_prompt)
        except Exception:
            self.error_count += 1
            self.http_log.append(f"MODEL_ERROR MiniMax-M3 #{self.call_count}")
            raise
        self.tokens += int(response.tokens_used)
        return GatewayResponse(
            content=response.content,
            model=MINIMAX_MODEL,
            tokens_used=response.tokens_used,
            latency_ms=response.latency_ms,
        )


def aggregate(rows: list[CaseStats]) -> dict[str, Any]:
    return {
        "model": MINIMAX_MODEL,
        "cases": [ _case_dict(row) for row in rows ],
        "totals": {
            "cases": len(rows),
            "successes": sum(1 for row in rows if row.success),
            "action_count": sum(row.action_count for row in rows),
            "minimax_call_count": sum(row.minimax_call_count for row in rows),
            "minimax_error_count": sum(row.minimax_error_count for row in rows),
            "minimax_tokens": sum(row.minimax_tokens for row in rows),
            "compile_call_count": sum(row.compile_call_count for row in rows),
            "grounding_call_count": sum(row.grounding_call_count for row in rows),
        },
    }


def _case_dict(row: CaseStats) -> dict[str, Any]:
    payload = asdict(row)
    return payload


def dumps_stats(rows: list[CaseStats]) -> str:
    return json.dumps(aggregate(rows), ensure_ascii=False, indent=2)
