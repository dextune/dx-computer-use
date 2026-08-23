"""Load and resolve runtime configuration from one deployment source."""

from pathlib import Path
from typing import Any, Optional

import yaml

from hpcu.gateway.gateway import ModelCallPurpose, SemanticIdentity
from hpcu.schemas.budget import TaskBudgetSpec

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _REPO_ROOT / "config" / "runtime-config.yaml"

_DEFAULTS: dict[str, Any] = {
    "semantic": {
        "default_provider": "minimax",
        "default_model": "MiniMax-M3",
        "request_limits": {
            "plan_compile_max_tokens": 1024,
            "intent_fill_max_tokens": 256,
            "action_decision_max_tokens": 512,
            "reanalysis_max_tokens": 512,
            "intent_fill_retry_attempts": 1,
            "plan_compile_retry_attempts": 2,
            "grounding_retry_attempts": 1,
            "action_decision_retry_attempts": 2,
            "reanalysis_retry_attempts": 2,
            "retry_base_delay_ms": 500,
            "retry_max_delay_ms": 8000,
        },
        "routes": {
            "intent_fill": "minimax",
            "plan_compile": "minimax",
            "grounding": "minimax",
            "situation_analysis": "minimax",
            "action_decision": "minimax",
            "post_action_reanalysis": "minimax",
            "recovery_reanalysis": "minimax",
        },
    },
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
        "observe_timeout_ms": 5000,
        "settle_poll_interval_ms": 5,
        "settle_stable_polls": 2,
        "local_reserve_cores": 1,
        "max_perception_workers": 4,
        "perception_queue_multiplier": 2,
        "max_perception_queue_depth": 0,
    },
    "tier_budget": {
        "max_model_calls_per_task": 3,
        "max_model_tokens_per_task": 4096,
        "max_model_latency_ms_per_task": 60000,
        "planning_call_ceiling": 2,
        "recovery_call_reserve": 1,
        "max_vlm_calls_per_task": 3,
    },
    "recovery": {
        "max_local_repairs_per_node": 1,
        "max_replans_per_task": 2,
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
        "ocr_cache_entries": 32,
        "dirty_roi_max_ratio": 0.35,
    },
    "targeting": {
        "max_tokens_per_field": 8,
        "min_token_length": 2,
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


def _resolved_config(config: Optional[dict[str, Any]]) -> dict[str, Any]:
    if config is None:
        return load_runtime_config()
    return _deep_merge(_DEFAULTS, config)


def configured_semantic_identity(
    config: Optional[dict[str, Any]] = None,
) -> SemanticIdentity:
    """Return the selected provider/model identity from runtime config."""
    if config is None:
        runtime = load_runtime_config()
        semantic = runtime.get("semantic", {})
        model = runtime.get("model", {})
    else:
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


def effective_task_budget(
    config: Optional[dict[str, Any]] = None,
    override: TaskBudgetSpec | None = None,
) -> TaskBudgetSpec:
    """Resolve the single production task budget before intent processing."""
    if override is not None:
        return override
    tier = _resolved_config(config).get("tier_budget", {})
    planning_raw = tier.get("planning_call_ceiling", 2)
    planning = None if planning_raw is None else int(planning_raw)
    return TaskBudgetSpec(
        max_model_calls=int(tier.get("max_model_calls_per_task", 3)),
        max_model_tokens=int(tier.get("max_model_tokens_per_task", 4096)),
        max_model_latency_ms=int(
            tier.get("max_model_latency_ms_per_task", 60000)
        ),
        planning_call_ceiling=planning,
        recovery_call_reserve=int(tier.get("recovery_call_reserve", 1)),
    )


def semantic_retry_attempts(
    config: Optional[dict[str, Any]] = None,
) -> dict[ModelCallPurpose, int]:
    """Map each semantic purpose to its actual transport retry ceiling."""
    limits = (
        _resolved_config(config)
        .get("semantic", {})
        .get("request_limits", {})
    )
    plan = int(limits.get("plan_compile_retry_attempts", 2))
    intent = int(limits.get("intent_fill_retry_attempts", 1))
    grounding = int(limits.get("grounding_retry_attempts", 1))
    action = int(limits.get("action_decision_retry_attempts", 2))
    reanalysis = int(limits.get("reanalysis_retry_attempts", 2))
    policy = {
        ModelCallPurpose.INTENT_FILL: intent,
        ModelCallPurpose.PLAN_COMPILE: plan,
        ModelCallPurpose.GROUNDING: grounding,
        ModelCallPurpose.SITUATION_ANALYSIS: action,
        ModelCallPurpose.ACTION_DECISION: action,
        ModelCallPurpose.POST_ACTION_REANALYSIS: reanalysis,
        ModelCallPurpose.RECOVERY_REANALYSIS: reanalysis,
    }
    if any(value < 0 for value in policy.values()):
        raise ValueError("semantic retry attempts must be non-negative")
    return policy


def semantic_call_timeout_ms(
    config: Optional[dict[str, Any]] = None,
) -> int:
    performance = _resolved_config(config).get("performance", {})
    timeout_ms = int(performance.get("model_call_timeout_ms", 30000))
    if timeout_ms <= 0:
        raise ValueError("model_call_timeout_ms must be positive")
    return timeout_ms


def load_runtime_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Return defaults merged with an optional YAML deployment override."""
    config_path = path if path is not None else _DEFAULT_PATH
    if not config_path.is_file():
        return _deep_merge({}, _DEFAULTS)
    with config_path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        return _deep_merge({}, _DEFAULTS)
    return _deep_merge(_DEFAULTS, loaded)
