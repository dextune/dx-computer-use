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


def _bracket_count_extract(content: str, start: int) -> str | None:
    """Extract a balanced-brace substring from *start*, or None."""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(content)):
        ch = content[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    return None


def extract_json_objects(content: str) -> tuple[dict[str, Any], ...]:
    """Return distinct JSON objects found in *content*, in encounter order.

    ``json.JSONDecoder.raw_decode`` is attempted at every opening brace.  When
    ``raw_decode`` fails, a bracket-counting fallback extracts the balanced
    brace span and retries via ``json.loads``.  This handles prose, markdown
    fences, sequential objects, and wrapper objects without relying on regex.
    Nested objects are exposed as candidates so a provider envelope such as
    ``{"response": {...}}`` remains a transport concern rather than a schema
    repair.
    """
    if not isinstance(content, str):
        raise ValueError("response content must be a string")

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    fingerprints: set[str] = set()

    for start, char in enumerate(content):
        if char != "{":
            continue
        value = None
        try:
            value, _end = decoder.raw_decode(content, start)
        except json.JSONDecodeError:
            # Fall back to bracket-counted extraction for malformed provider
            # responses that raw_decode cannot parse (e.g. extra trailing
            # commas, unquoted keys, or deeply nested prose).
            span = _bracket_count_extract(content, start)
            if span is not None:
                try:
                    value = json.loads(span)
                except json.JSONDecodeError:
                    continue
        if value is None:
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

    Selection is fail-closed: non-matching reasoning/wrapper objects are
    ignored and identical duplicate objects are de-duplicated.  Extra keys
    beyond *required_keys* are tolerated — provider responses may include
    optional metadata or diagnostics that do not affect the schema contract.
    When *allowed_keys* is supplied it acts as a tie-breaker hint for
    ambiguous matches, not as a strict key‑set equivalence check.
    """
    required = frozenset(required_keys)
    allowed = frozenset(allowed_keys) if allowed_keys is not None else None
    candidates = extract_json_objects(content)
    if allowed is not None:
        # Prefer candidates whose keys are a subset of *allowed*, but also
        # accept any candidate that satisfies the required keys alone.
        perfect = [
            payload
            for payload in candidates
            if required <= payload.keys() and payload.keys() <= allowed
        ]
        matches = perfect or [
            payload
            for payload in candidates
            if required <= payload.keys()
        ]
    else:
        matches = [
            payload
            for payload in candidates
            if required <= payload.keys()
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
