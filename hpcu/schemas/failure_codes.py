"""Failure codes for the HPCU Runtime.

All failure codes are defined here as a single source of truth.
"""

from enum import Enum


class FailureCode(str, Enum):
    """Machine-readable failure codes used across the runtime.

    Every error path MUST produce a FailureCode, not a bare string.
    """

    # --- observer / capture ---
    CAPTURE_PERMISSION_DENIED = "capture_permission_denied"
    CAPTURE_BACKEND_UNAVAILABLE = "capture_backend_unavailable"

    # --- structure ---
    STRUCTURE_TREE_EMPTY = "structure_tree_empty"

    # --- application lifecycle ---
    APPLICATION_LAUNCH_UNSUPPORTED = "application_launch_unsupported"
    APPLICATION_NOT_FOUND = "application_not_found"
    APPLICATION_LAUNCH_FAILED = "application_launch_failed"
    APPLICATION_NOT_OBSERVED = "application_not_observed"

    # --- input ---
    INPUT_PERMISSION_DENIED = "input_permission_denied"
    INPUT_SEMANTIC_UNSUPPORTED = "input_semantic_unsupported"
    INPUT_PHYSICAL_UNSUPPORTED = "input_physical_unsupported"

    # --- capability ---
    CAPABILITY_MISSING = "capability_missing"

    # --- grounding ---
    GROUNDING_NO_CANDIDATES = "grounding_no_candidates"
    GROUNDING_AMBIGUOUS = "grounding_ambiguous"
    GROUNDING_CONFIDENCE_LOW = "grounding_confidence_low"

    # --- execution ---
    STALE_DECISION = "stale_decision"
    TARGET_OCCLUDED = "target_occluded"
    TARGET_NOT_FOUND = "target_not_found"
    ACTION_TIMEOUT = "action_timeout"
    ACTION_REJECTED_BY_POLICY = "action_rejected_by_policy"
    ACTION_UNSUPPORTED = "action_unsupported"

    # --- verification ---
    VERIFICATION_FAILED = "verification_failed"
    POSTCONDITION_UNMET = "postcondition_unmet"

    # --- model ---
    MODEL_TIMEOUT = "model_timeout"
    MODEL_SCHEMA_INVALID = "model_schema_invalid"
    MODEL_RESPONSE_INVALID = "model_response_invalid"
    MODEL_FAILED = "model_failed"
    MODEL_BUDGET_EXHAUSTED = "model_budget_exhausted"
    DECISION_REQUIRED = "decision_required"

    # --- challenge / human handoff ---
    CAPTCHA_DETECTED = "captcha_detected"
    LOGIN_REQUIRED = "login_required"
    SECURITY_CHECK_REQUIRED = "security_check_required"
    HUMAN_HANDOFF_REQUIRED = "human_handoff_required"
    ACCESS_CONTROL_BLOCKED = "access_control_blocked"

    # --- loop / recovery ---
    LOOP_DETECTED = "loop_detected"
    RECOVERY_EXHAUSTED = "recovery_exhausted"

    # --- general ---
    UNKNOWN = "unknown"
