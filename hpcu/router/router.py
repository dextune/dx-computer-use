"""ModelRouter — decide when the expensive model must be interrupted.

The model is a rare, costly interrupt.  The normal path executes a
high-confidence candidate with zero model calls.  Only when grounding is
ambiguous, confidence is low, or an earlier attempt failed does the router
escalate to a tier that may consult the model.

Tiers (0-5):

- 0  direct:        high-confidence candidate only, model never called
- 1  text-llm:      single text-LLM call to disambiguate
- 2  tool-calling:  text-LLM with schema constraints / tool use
- 3  small-vlm:     small vision-language model on the crop
- 4  large-vlm:     large VLM fallback (coarse-to-fine)
- 5  human:         human approval / recovery exhausted
"""

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from hpcu.gateway.gateway import Gateway
from hpcu.router.candidate_scoring import TargetQuery, score_candidates
from hpcu.runtime_config import load_runtime_config
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement


class ModelCallBudgetError(Exception):
    """Raised when the per-task model-call budget is exhausted."""


@dataclass(frozen=True)
class Decision:
    """The router's grounded target.

    `target_id` is None when the scene could not be resolved (e.g. the model
    failed or the budget was exhausted); `is_stale` reports whether the
    decision was grounded against a scene older than the passed version.
    """

    target_id: Optional[str]
    confidence: float
    scene_version: int

    def is_stale(self, current_scene_version: int) -> bool:
        """True when the decision's grounding is older than the current scene."""
        return self.scene_version < current_scene_version


def _query_from(node: object) -> TargetQuery:
    """Extract a TargetQuery from a workflow node or a bare value."""
    if isinstance(node, TargetQuery):
        return node
    attribute = getattr(node, "target_query", None)
    if attribute is not None:
        if isinstance(attribute, TargetQuery):
            return attribute
        return TargetQuery(text=str(attribute or ""))
    return TargetQuery(text=str(node or ""))


def _parse_target(content: str) -> dict:
    """Parse a model response into a target_id / confidence dict."""
    content = content.strip()
    if content:
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return {
                    "target_id": data.get("target_id"),
                    "confidence": float(data.get("confidence", 0.0)),
                }
        except (ValueError, TypeError):
            pass
        match = re.search(r'"target_id"\s*:\s*"?([^",}\s]+)"?', content)
        if match:
            return {"target_id": match.group(1), "confidence": 1.0}
    return {"target_id": None, "confidence": 0.0}


class EscalationStrategy:
    """A chosen escalation tier, carrying its resolution behaviour.

    `resolve` grounds the node against the scene.  At tier 0 it only runs
    direct scoring; at tier 1+ it falls back to the injected model caller
    (which consumes the shared model-call budget) when no high-confidence
    candidate exists.
    """

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
        """Ground the node and produce a Decision, never crashing the loop."""
        query = _query_from(node)
        scored = score_candidates(candidates, query)
        best = scored[0] if scored else None

        if best is not None and best.confidence >= self.direct_threshold:
            return Decision(
                target_id=best.element_id,
                confidence=best.confidence,
                scene_version=scene.version,
            )

        if self._model_caller is not None:
            try:
                return self._model_caller(node, scene, candidates)
            except ModelCallBudgetError:
                return Decision(
                    target_id=None,
                    confidence=best.confidence if best else 0.0,
                    scene_version=scene.version,
                )
            except Exception:
                # Model timeout / schema failure keeps the executor stable.
                return Decision(
                    target_id=None,
                    confidence=best.confidence if best else 0.0,
                    scene_version=scene.version,
                )

        # No model path allowed at this tier (or none configured).
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
    """Routes grounding failures to an escalation strategy under a budget."""

    def __init__(
        self,
        *,
        model_call_budget: Optional[int] = None,
        direct_threshold: Optional[float] = None,
        gateway: Optional[Gateway] = None,
        system_prompt: str = "",
    ):
        config = load_runtime_config()
        tier_config = config.get("tier_budget", {})
        confidence_config = config.get("confidence", {})

        if model_call_budget is None:
            model_call_budget = int(tier_config.get("max_model_calls_per_task", 8))
        if model_call_budget < 0:
            raise ValueError(f"model_call_budget must be >= 0, got {model_call_budget}")

        if direct_threshold is None:
            direct_threshold = float(
                confidence_config.get("local_execute_threshold", 0.88)
            )
        if not 0.0 <= direct_threshold <= 1.0:
            raise ValueError(
                "direct_threshold must be in [0, 1], "
                f"got {direct_threshold}"
            )

        self._model_call_budget = model_call_budget
        self._model_calls_remaining = model_call_budget
        self._direct_threshold = direct_threshold
        self._gateway = gateway
        self._system_prompt = system_prompt

    @property
    def model_call_budget(self) -> int:
        """The configured max model calls per task."""
        return self._model_call_budget

    @property
    def model_calls_remaining(self) -> int:
        """Model calls still available in this task."""
        return self._model_calls_remaining

    def choose_tier(
        self,
        failure_code: Optional[FailureCode],
        risk: int,
        scene: Scene,
    ) -> EscalationStrategy:
        """Pick the escalation tier for a failure/risk/scene combination."""
        tier = _base_tier_for_failure(failure_code)
        if risk >= 2:
            tier = min(tier + 1, 5)
        if scene is None or not scene.elements:
            tier = min(tier + 1, 5)

        model_caller = None if tier == 0 else self._default_model_caller
        return EscalationStrategy(
            tier=tier,
            direct_threshold=self._direct_threshold,
            model_caller=model_caller,
        )

    def _build_prompt(
        self,
        node: object,
        scene: Scene,
        candidates: list[UIElement],
    ) -> str:
        query = _query_from(node)
        lines = [
            f"window_title: {scene.window_title or ''}",
            f"query: {query.text or ''} (role={query.role or 'any'})",
        ]
        for candidate in candidates:
            lines.append(
                f"- {candidate.id} role={candidate.role} text={candidate.text!r}"
            )
        lines.append('Return JSON: {"target_id": "...", "confidence": 0..1}')
        return "\n".join(lines)

    def _default_model_caller(
        self,
        node: object,
        scene: Scene,
        candidates: list[UIElement],
    ) -> Decision:
        if self._model_calls_remaining <= 0:
            raise ModelCallBudgetError("model call budget exhausted")
        if self._gateway is None:
            raise RuntimeError("no gateway configured for model tier")

        prompt = self._build_prompt(node, scene, candidates)
        response = self._gateway.call(prompt, system_prompt=self._system_prompt)
        self._model_calls_remaining -= 1

        parsed = _parse_target(response.content)
        return Decision(
            target_id=parsed.get("target_id"),
            confidence=float(parsed.get("confidence", 0.0)),
            scene_version=scene.version,
        )
