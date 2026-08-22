"""MiniMax M3 adapter — OpenAI-compatible chat completions over httpx.

Handles the two MiniMax-specific quirks observed in production:

- reasoning is serialised INSIDE `message.content` as a
  ` thinking... response` block, which must be stripped before use;
- the reasoning block can split across chunks, so `strip_thinking` uses a
  small state machine rather than a naive split.

`httpx` is only imported when a client is not injected, so unit tests can
inject a fake client and never touch the network.
"""

import os
from pathlib import Path
from typing import Optional

from hpcu.gateway.gateway import Gateway, GatewayResponse

_THINK_START = " thinking"
_THINK_END = " response"
_DEFAULT_BASE_URL = "https://api.minimax.io/v1"
_DEFAULT_MODEL = "MiniMax-M3"


def strip_thinking(content: str) -> str:
    """Strip ` thinking...` reasoning blocks from model content.

    The MiniMax M3 model embeds its reasoning inside `message.content` as
    `` thinking`` ... `` response``.  Everything between (and including)
    those markers is removed.  An in-progress block (no closing marker) is
    dropped entirely; a lone `` response`` with no preceding `` thinking`` is
    treated as ordinary text and preserved.
    """
    output: list[str] = []
    position = 0
    state = "normal"
    buffer = content

    while position < len(buffer):
        if state == "normal":
            start = buffer.find(_THINK_START, position)
            if start == -1:
                output.append(buffer[position:])
                break
            output.append(buffer[position:start])
            position = start + len(_THINK_START)
            state = "in_think"
        else:  # in_think
            end = buffer.find(_THINK_END, position)
            if end == -1:
                # Unclosed reasoning block — drop the tail, nothing to emit.
                break
            position = end + len(_THINK_END)
            state = "normal"
            # MiniMax frames the reply with a blank line after the marker;
            # skip that framing whitespace so the reply starts clean.
            while position < len(buffer) and buffer[position] in "\r\n":
                position += 1

    return "".join(output)


def schema_validate(response: dict, expected_schema: dict) -> bool:
    """Return True when `response` conforms to the JSON schema.

    A malformed schema itself is treated as invalid (returns False) rather
    than raising.
    """
    from jsonschema import Draft7Validator
    from jsonschema.exceptions import SchemaError

    try:
        Draft7Validator.check_schema(expected_schema)
    except SchemaError:
        return False

    validator = Draft7Validator(expected_schema)
    return not list(validator.iter_errors(response))


class MiniMaxAdapter(Gateway):
    """chat/completions client for MiniMax M3 (OpenAI-compatible endpoint)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        client: Optional[object] = None,
    ):
        """Initialize the adapter.

        `client` is any object with a `.post(url, headers=..., json=...)`
        method returning an object with `.raise_for_status()` and `.json()`.
        When None, a synchronous `httpx.Client` is created (the only place
        httpx is imported).
        """
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        if client is not None:
            self._client = client
        else:
            import httpx

            self._client = httpx.Client()

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
    ) -> GatewayResponse:
        """Invoke the model and return a thinking-stripped response."""
        import time

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()
        response = self._client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        latency_ms = int((time.perf_counter() - started) * 1000)

        content = data["choices"][0]["message"]["content"]
        content = strip_thinking(content)
        tokens_used = int(data.get("usage", {}).get("total_tokens", 0))

        return GatewayResponse(
            content=content,
            model=data.get("model", self._model),
            tokens_used=tokens_used,
            latency_ms=latency_ms,
        )

    @classmethod
    def from_env(
        cls,
        *,
        client: Optional[object] = None,
        env_path: Optional[Path] = None,
    ) -> "MiniMaxAdapter":
        """Build an adapter using MINIMAX_API_KEY from the environment or `.env`."""
        api_key = load_minimax_api_key(env_path=env_path)
        if not api_key:
            raise ValueError("MINIMAX_API_KEY is not set")
        return cls(api_key=api_key, client=client)


def load_minimax_api_key(*, env_path: Optional[Path] = None) -> str:
    """Read MINIMAX_API_KEY from os.environ, then repo `.env`. Never logs the value."""
    existing = os.environ.get("MINIMAX_API_KEY", "").strip()
    if existing:
        return existing
    path = env_path if env_path is not None else Path(__file__).resolve().parents[2] / ".env"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == "MINIMAX_API_KEY":
            return value.strip().strip('"').strip("'")
    return ""
