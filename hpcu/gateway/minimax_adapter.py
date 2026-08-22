"""MiniMax M3 adapter — OpenAI-compatible chat completions over httpx.

Handles MiniMax-specific response quirks observed in production:

- reasoning may be serialised inside ``message.content`` as a legacy
  `` thinking... response`` block or XML-style thinking tags;
- JSON may be wrapped in markdown/prose;
- non-authoritative ``reason_code`` metadata may be emitted as JSON null.

The adapter removes only provider presentation noise and normalizes nullable
provider diagnostics. Action-bearing fields remain untouched and are still
validated by the provider-neutral decision schema.

``httpx`` is imported only when a client is not injected, so unit tests can
use a fake client without network access.
"""

import os
import re
from pathlib import Path
from typing import Optional

from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose

_PROVIDER_ID = "minimax"

_THINK_START = " thinking"
_THINK_END = " response"
_DEFAULT_BASE_URL = "https://api.minimax.io/v1"
_DEFAULT_MODEL = "MiniMax-M3"
_REASON_CODE_NULL = re.compile(
    r'("reason_code"\s*:\s*)null(?=\s*[,}])'
)


def strip_thinking(content: str) -> str:
    """Normalize MiniMax reasoning and JSON presentation wrappers.

    MiniMax responses may contain legacy `` thinking``/`` response`` blocks,
    XML-style ``<think>`` blocks, or a fenced JSON object. These are transport
    quirks of this concrete adapter; the common decision parser still receives
    strict JSON candidates and performs schema-directed selection.
    """
    content = re.sub(
        r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?>",
        "",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    content = re.sub(
        r"^\s*```(?:json)?\s*|\s*```\s*$",
        "",
        content,
        flags=re.IGNORECASE,
    )
    # Some MiniMax responses omit the legacy `` thinking`` marker but still
    # prepend prose before the JSON object. This transport cleanup stays in
    # the concrete adapter; schema selection remains provider-neutral.
    first_object = content.find("{")
    if first_object > 0:
        content = content[first_object:]
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


def normalize_nullable_diagnostics(content: str) -> str:
    """Normalize MiniMax's nullable audit metadata, not decision semantics.

    The 2026-08-22 reliability run contained an otherwise valid reanalysis
    object with ``"reason_code": null``. ``reason_code`` is diagnostic only;
    it does not choose an action, target, scene, goal state, or confidence.
    Replacing this one provider-specific null with a stable marker prevents a
    false schema failure while preserving fail-closed validation for every
    action-bearing field.
    """
    return _REASON_CODE_NULL.sub(
        r'\1"provider_unspecified"',
        content,
    )


def schema_validate(response: dict, expected_schema: dict) -> bool:
    """Return True when ``response`` conforms to the JSON schema.

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
        timeout_s: float = 30.0,
    ):
        """Initialize the adapter.

        ``client`` is any object with a ``.post(url, headers=..., json=...)``
        method returning an object with ``.raise_for_status()`` and ``.json()``.
        When None, a synchronous ``httpx.Client`` is created.
        """
        if not api_key:
            raise ValueError("api_key must not be empty")
        if model != _DEFAULT_MODEL:
            raise ValueError(f"only {_DEFAULT_MODEL} is supported")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = _DEFAULT_MODEL
        if client is not None:
            self._client = client
        else:
            import httpx

            self._client = httpx.Client(timeout=timeout_s)

    @property
    def provider_id(self) -> str:
        return _PROVIDER_ID

    @property
    def model_id(self) -> str:
        return self._model

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        """Invoke MiniMax-M3 and return normalized provider content."""
        if not isinstance(purpose, ModelCallPurpose):
            raise ValueError("purpose must be a ModelCallPurpose")
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
        content = normalize_nullable_diagnostics(strip_thinking(content))
        tokens_used = int(data.get("usage", {}).get("total_tokens", 0))

        response_model = data.get("model", self._model)
        if response_model != _DEFAULT_MODEL:
            raise ValueError(f"unexpected model response: {response_model!r}")
        return GatewayResponse(
            content=content,
            model=response_model,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            provider=_PROVIDER_ID,
        )

    @classmethod
    def from_env(
        cls,
        *,
        model: str = _DEFAULT_MODEL,
        client: Optional[object] = None,
        env_path: Optional[Path] = None,
        timeout_s: float = 30.0,
    ) -> "MiniMaxAdapter":
        """Build an adapter using MINIMAX_API_KEY from environment or ``.env``."""
        api_key = load_minimax_api_key(env_path=env_path)
        if not api_key:
            raise ValueError("MINIMAX_API_KEY is not set")
        return cls(
            api_key=api_key,
            model=model,
            client=client,
            timeout_s=timeout_s,
        )


def load_minimax_api_key(*, env_path: Optional[Path] = None) -> str:
    """Read MINIMAX_API_KEY from environment, then repo ``.env``."""
    existing = os.environ.get("MINIMAX_API_KEY", "").strip()
    if existing:
        return existing
    path = (
        env_path
        if env_path is not None
        else Path(__file__).resolve().parents[2] / ".env"
    )
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
