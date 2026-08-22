"""Load runtime YAML config. Thresholds live here, not in call sites.

If the file is missing, documented defaults from plan 06/11 are used.
"""

from pathlib import Path
from typing import Any, Optional

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _REPO_ROOT / "config" / "runtime-config.yaml"

_DEFAULTS: dict[str, Any] = {
    "confidence": {
        "local_execute_threshold": 0.88,
        "local_margin_min": 0.20,
        "text_llm_threshold": 0.72,
    },
    "performance": {
        "settle_timeout_ms": 2000,
        "action_timeout_ms": 5000,
        "model_call_timeout_ms": 30000,
        "settle_poll_interval_ms": 5,
        "settle_stable_polls": 2,
    },
    "tier_budget": {
        "max_model_calls_per_task": 8,
        "max_vlm_calls_per_task": 3,
    },
    "policy": {
        "high_risk_require_approval": True,
        "max_retry_attempts": 3,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_runtime_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Return the runtime config dict.

    `path` may be injected in tests. Missing files yield `_DEFAULTS` only.
    """
    config_path = path if path is not None else _DEFAULT_PATH
    if not config_path.is_file():
        return dict(_DEFAULTS)
    with config_path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        return dict(_DEFAULTS)
    return _deep_merge(_DEFAULTS, loaded)