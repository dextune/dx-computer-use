"""Schema-directed JSON extraction for provider response text.

The semantic provider sometimes surrounds the intended object with prose,
reasoning metadata, markdown fences, or a non-semantic wrapper object.  This
module performs transport-level framing only: it never invents fields or
changes field values.  Callers still validate the selected object strictly.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


def _preview(content: str, limit: int = 200) -> str:
    return content[:limit].replace("\n", " ").replace("\r", " ")


def _walk_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_objects(nested)


def extract_json_objects(content: str) -> tuple[dict[str, Any], ...]:
    """Return distinct JSON objects found in *content*, in encounter order.

    ``json.JSONDecoder.raw_decode`` is attempted at every opening brace.  This
    handles prose, markdown fences, sequential objects, and wrapper objects
    without relying on regex or brace counting.  Nested objects are exposed as
    candidates so a provider envelope such as ``{"response": {...}}`` remains
    a transport concern rather than a schema repair.
    """
    if not isinstance(content, str):
        raise ValueError("response content must be a string")

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    fingerprints: set[str] = set()

    for start, char in enumerate(content):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(content, start)
        except json.JSONDecodeError:
            continue
        for payload in _walk_objects(value):
            fingerprint = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            candidates.append(payload)

    return tuple(candidates)


def select_json_object(
    content: str,
    *,
    required_keys: Iterable[str],
    schema_name: str = "response",
    allowed_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Select the unique object matching the caller's structural contract.

    Selection is fail-closed.  Non-matching reasoning/wrapper objects are
    ignored, identical duplicate objects are de-duplicated, and conflicting
    objects that both satisfy the required key set are rejected as ambiguous.
    """
    required = frozenset(required_keys)
    allowed = frozenset(allowed_keys) if allowed_keys is not None else None
    candidates = extract_json_objects(content)
    matches = [
        payload
        for payload in candidates
        if required <= payload.keys()
        and (allowed is None or payload.keys() <= allowed)
    ]

    if len(matches) == 1:
        return matches[0]

    preview = _preview(content)
    if not matches:
        if candidates:
            observed = sorted({key for item in candidates for key in item})
            raise ValueError(
                f"no JSON object matches {schema_name}; "
                f"required={sorted(required)!r}, observed={observed!r} "
                f"[raw_preview: {preview}]"
            )
        raise ValueError(
            f"no JSON object found for {schema_name} "
            f"[raw_preview: {preview}]"
        )

    raise ValueError(
        f"ambiguous {schema_name}: {len(matches)} matching JSON objects "
        f"[raw_preview: {preview}]"
    )


def extract_first_json_object(content: str) -> Mapping[str, Any]:
    """Compatibility helper for callers without a schema key set."""
    candidates = extract_json_objects(content)
    if not candidates:
        raise ValueError(
            f"no JSON object found in response [raw_preview: {_preview(content)}]"
        )
    return candidates[0]
