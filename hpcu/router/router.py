"""CPU-first escalation policy and typed semantic target resolution.

The router decides *whether* a semantic interrupt is justified. The resolver
owns the typed provider boundary. Both production and compatibility paths use a
single :class:`TaskBudgetLedger`; no independent call counter or regex fallback
exists in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from hpcu.gateway.gateway import Gateway, ModelCallPurpose
from hpcu.gateway.json_response import select_json_object
from hpcu.router.candidate_scoring import TargetQuery, score_candidates
from hpcu.runtime_config import configured_semantic_identity, load_runtime_config
from hpcu.runtime_core.task_budget import (
    BudgetedGateway,
    ModelBudgetExceeded,
    TaskBudgetLedger,
)
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement


class ModelCallBudgetError(ModelBudgetExceeded):
    """Compatibility alias for task-global semantic budget exhaustion."""


@dataclass(frozen=True)
class Decision:
    """A grounded target bound to the scene used for the decision."""

    target_id: Optional[str]
    confidence: float
    scene_version: int

    def is_stale(self, current_scene_version: int) -> bool:
        return self.scene_version < current_scene_version


def _has_unique_confidence_margin(
    scored: list[object], minimum_margin: float = 0.20
) -> bool:
    if not scored:
        return False
    if len(scored) == 1:
        return True
    return float(scored[0].confidence) - float(scored[1].confidence) >= minimum_margin


def _has_observable_text(candidate: UIElement | None) -> bool:
    return bool(candidate and (candidate.text or candidate.name or "").strip())


def _element_is_current(candidate: UIElement | None, scene_version: int) -> bool:
    return bool(
        candidate
        and candidate.scene_version == scene_version
        and candidate.state.visible
        and candidate.state.enabled
        and not candidate.state.occluded
    )


def _query_from(node: object) -> TargetQuery:
    if isinstance(node, TargetQuery):
        return node
    attribute = getattr(node, "target_query", None)
    if attribute is not None:
        if isinstance(attribute, TargetQuery):
            return attribute
        text = getattr(attribute, "text", attribute)
        role = getattr(attribute, "role", None)
        return TargetQuery(text=str(text or ""), role=role)
    return TargetQuery(text=str(node or ""))


def _parse_target(content: str) -> dict[str, object]:
    """Parse the unique schema-matching target object, fail closed otherwise."""
    payload = select_json_object(
        content,
        required_keys=("target_id", "confidence"),
        allowed_keys=("target_id", "confidence"),
        schema_name="semantic target decision",
    )
    target_id = payload["target_id"]
    confidence = payload["confidence"]
    if target_id is not None and (
        not isinstance(target_id, str) or not target_id.strip()
    ):
        raise ValueError("semantic target_id must be a non-empty string or null")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("semantic confidence must be numeric")
    normalized = float(confidence)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError("semantic confidence must be in [0, 1]")
    return {
        "target_id": target_id.strip() if isinstance(target_id, str) else None,
        "confidence": normalized,
    }


class SemanticTargetResolver:
    """Typed semantic interrupt for unresolved target selection only."""

    def __init__(
        self,
        gateway: Gateway,
        *,
        system_prompt: str = "",
    ) -> None:
        self._gateway = gateway
        self._system_prompt = system_prompt
        self._identity = configured_semantic_identity()
        gateway.require_configured_identity(self._identity)

    def __call__(
        self,
        node: object,
        scene: Scene,
        candidates: list[UIElement],
    ) -> Decision:
        query = _query_from(node)
        lines = [
            f"scene_version: {scene.version}",
            f"window_title: {scene.window_title or ''}",
            f"query: {query.text or ''} (role={query.role or 'any'})",
        ]
        for candidate in candidates:
            lines.append(
                f"- {candidate.id} role={candidate.role} text={candidate.text!r}"
            )
        lines.append(
            'Return exactly one JSON object: '
            '{"target_id":"existing-id-or-null","confidence":0.0}'
        )
        response = self._gateway.call(
            "\n".join(lines),
            system_prompt=self._system_prompt,
            purpose=ModelCallPurpose.ACTION_DECISION,
        )
        if response.model != self._identity.model_id:
            raise ValueError(f"unexpected semantic model: {response.model!r}")
        if response.provider and response.provider != self._identity.provider_id:
            raise ValueError(f"unexpected semantic provider: {response.provider!r}")
        parsed = _parse_target(response.content)
        target_id = parsed["target_id"]
        if target_id is not None and scene.get(str(target_id)) is None:
            raise ValueError("semantic target_id is not present in the bound scene")
        return Decision(
            target_id=str(target_id) if target_id is not None else None,
            confidence=float(parsed["confidence"]),
            scene_version=scene.version,
        )


class EscalationStrategy:
    """Chosen escalation tier with an optional typed semantic resolver."""

    def __init__(
        self,
        tier: int,
        direct_threshold: float = 0.88,
        model_caller: Optional[
            Callable[[object, Scene, list[UIElement]], Decision]
        ] = None,
    ):
        if not 0 <= tier <= 5:
            raise ValueError(f"tier must be in [0, 5], got {tier}")
        self.tier = tier
        self.direct_threshold = direct_threshold
        self._model_caller = model_caller

    def resolve(
        self,
        node: object,
        scene: Scene,
        candidates: list[UIElement],
    ) -> Decision:
        query = _query_from(node)
        scored = score_candidates(candidates, query)
        best = scored[0] if scored else None

        if (
            best is not None
            and best.confidence >= self.direct_threshold
            and _has_unique_confidence_margin(scored)
            and _has_observable_text(scene.get(best.element_id))
            and _element_is_current(scene.get(best.element_id), scene.version)
        ):
            return Decision(
                target_id=best.element_id,
                confidence=best.confidence,
                scene_version=scene.version,
            )

        if self._model_caller is not None:
            try:
                return self._model_caller(node, scene, candidates)
            except (ModelBudgetExceeded, ModelCallBudgetError):
                return Decision(
                    target_id=None,
                    confidence=best.confidence if best else 0.0,
                    scene_version=scene.version,
                )
            except Exception:
                return Decision(
                    target_id=None,
                    confidence=best.confidence if best else 0.0,
                    scene_version=scene.version,
                )

        return Decision(
            target_id=None,
            confidence=best.confidence if best else 0.0,
            scene_version=scene.version,
        )


_BASE_TIER_BY_FAILURE: dict[FailureCode, int] = {
    FailureCode.GROUNDING_AMBIGUOUS: 1,
    FailureCode.GROUNDING_NO_CANDIDATES: 1,
    FailureCode.GROUNDING_CONFIDENCE_LOW: 1,
    FailureCode.TARGET_NOT_FOUND: 1,
    FailureCode.TARGET_OCCLUDED: 1,
    FailureCode.STALE_DECISION: 1,
    FailureCode.ACTION_TIMEOUT: 1,
    FailureCode.MODEL_TIMEOUT: 2,
    FailureCode.MODEL_SCHEMA_INVALID: 2,
    FailureCode.MODEL_RESPONSE_INVALID: 2,
    FailureCode.VERIFICATION_FAILED: 2,
    FailureCode.POSTCONDITION_UNMET: 2,
    FailureCode.LOOP_DETECTED: 2,
    FailureCode.CAPTURE_BACKEND_UNAVAILABLE: 2,
    FailureCode.STRUCTURE_TREE_EMPTY: 2,
    FailureCode.RECOVERY_EXHAUSTED: 5,
}


def _base_tier_for_failure(failure_code: Optional[FailureCode]) -> int:
    if failure_code is None:
        return 0
    return _BASE_TIER_BY_FAILURE.get(failure_code, 1)


class ModelRouter:
    """Pure escalation policy backed by a task-global semantic ledger.

    ``model_call_budget`` remains as a compatibility constructor argument. New
    product code injects ``budget_ledger`` created at task start.
    """

    def __init__(
        self,
        *,
        model_call_budget: Optional[int] = None,
        direct_threshold: Optional[float] = None,
        gateway: Optional[Gateway] = None,
        system_prompt: str = "",
        budget_ledger: TaskBudgetLedger | None = None,
        semantic_resolver: Optional[
            Callable[[object, Scene, list[UIElement]], Decision]
        ] = None,
    ):
        config = load_runtime_config()
        tier_config = config.get("tier_budget", {})
        confidence_config = config.get("confidence", {})

        configured_calls = int(tier_config.get("max_model_calls_per_task", 8))
        requested_calls = configured_calls if model_call_budget is None else model_call_budget
        if requested_calls < 0:
            raise ValueError(f"model_call_budget must be >= 0, got {requested_calls}")

        if budget_ledger is None:
            budget_ledger = TaskBudgetLedger(
                TaskBudgetSpec(max_model_calls=requested_calls)
            )
        elif model_call_budget is not None and (
            budget_ledger.spec.max_model_calls != model_call_budget
        ):
            raise ValueError("model_call_budget does not match the task ledger")
        self._budget_ledger = budget_ledger

        if direct_threshold is None:
            direct_threshold = float(
                confidence_config.get("local_execute_threshold", 0.88)
            )
        if not 0.0 <= direct_threshold <= 1.0:
            raise ValueError(
                "direct_threshold must be in [0, 1], "
                f"got {direct_threshold}"
            )
        self._direct_threshold = direct_threshold

        if semantic_resolver is not None and gateway is not None:
            raise ValueError("pass semantic_resolver or gateway, not both")
        if semantic_resolver is not None:
            self._semantic_resolver = semantic_resolver
        elif gateway is not None:
            budgeted = (
                gateway
                if isinstance(gateway, BudgetedGateway)
                else BudgetedGateway(gateway, budget_ledger)
            )
            self._semantic_resolver = SemanticTargetResolver(
                budgeted,
                system_prompt=system_prompt,
            )
        else:
            self._semantic_resolver = None

    @property
    def budget_ledger(self) -> TaskBudgetLedger:
        return self._budget_ledger

    @property
    def model_call_budget(self) -> int:
        return self._budget_ledger.spec.max_model_calls

    @property
    def model_calls_remaining(self) -> int:
        return self._budget_ledger.remaining_calls

    def choose_tier(
        self,
        failure_code: Optional[FailureCode],
        risk: int,
        scene: Scene,
    ) -> EscalationStrategy:
        tier = _base_tier_for_failure(failure_code)
        if risk >= 2:
            tier = min(tier + 1, 5)
        if scene is None or not scene.elements:
            tier = min(tier + 1, 5)

        resolver = None if tier == 0 else self._semantic_resolver
        return EscalationStrategy(
            tier=tier,
            direct_threshold=self._direct_threshold,
            model_caller=resolver,
        )
