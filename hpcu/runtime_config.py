"""Load runtime YAML config. Thresholds live here, not in call sites.

If the file is missing, documented defaults from plan 06/11 are used.
"""

from pathlib import Path
from typing import Any, Optional

import yaml

from hpcu.gateway.gateway import SemanticIdentity

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _REPO_ROOT / "config" / "runtime-config.yaml"

_DEFAULTS: dict[str, Any] = {
    "semantic": {
        "default_provider": "minimax",
        "default_model": "MiniMax-M3",
        "request_limits": {
            "plan_compile_max_tokens": 1024,
            "action_decision_max_tokens": 512,
            "reanalysis_max_tokens": 512,
            "plan_compile_retry_attempts": 2,
        },
        "routes": {
            "plan_compile": "minimax",
            "situation_analysis": "minimax",
            "action_decision": "minimax",
            "post_action_reanalysis": "minimax",
            "recovery_reanalysis": "minimax",
        },
    },
    # Backward-compatible alias for callers that only need the selected model.
    "model": {
        "semantic_model_id": "MiniMax-M3",
        "provider": "minimax",
    },
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
    "perception": {
        "ocr_languages": "kor+eng",
        "ocr_psm": 6,
        "ocr_sparse_psm": 11,
        "ocr_min_regions_for_dense": 8,
        "line_y_tolerance_px": 8,
    },
    "cases": {
        "navigate_settle_timeout_ms": 8000,
        "navigate_poll_interval_ms": 400,
        "post_click_timeout_ms": 5000,
        "post_click_poll_interval_ms": 300,
        "minimum_evidence_token_matches": 2,
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


def configured_semantic_identity(
    config: Optional[dict[str, Any]] = None,
) -> SemanticIdentity:
    """Return the selected provider/model identity from runtime config."""
    if config is None:
        runtime = load_runtime_config()
        semantic = runtime.get("semantic", {})
        model = runtime.get("model", {})
    else:
        # Explicit injected model settings take precedence over repository
        # defaults when the caller has not supplied the newer semantic block.
        runtime = _deep_merge(_DEFAULTS, config)
        explicit_semantic = config.get("semantic") or {}
        explicit_model = config.get("model") or {}
        if explicit_semantic:
            semantic = _deep_merge(runtime.get("semantic", {}), explicit_semantic)
            model = explicit_model
        elif explicit_model:
            semantic = {}
            model = explicit_model
        else:
            semantic = runtime.get("semantic", {})
            model = runtime.get("model", {})
    provider_id = str(
        semantic.get("default_provider", model.get("provider", ""))
    ).strip()
    model_id = str(
        semantic.get("default_model", model.get("semantic_model_id", ""))
    ).strip()
    return SemanticIdentity(provider_id=provider_id, model_id=model_id)


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
