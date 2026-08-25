"""Provider-neutral semantic interrupts for bounded ambiguity resolution."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace

from hpcu.gateway.async_gateway import call_gateway_async
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.gateway.json_response import select_json_object
from hpcu.grounder.grounder import Grounder, GroundingCandidate
from hpcu.schemas.capability import Capability
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.plan import PlanIR, PlanPatch, ReplanRequest
from hpcu.schemas.scene import Scene
from hpcu.schemas.surface import SurfaceKind


class SemanticSlotFiller:
    """Ask the configured gateway only for explicitly unresolved fields."""

    def __init__(
        self,
        gateway: Gateway,
        *,
        max_tokens: int = 256,
        timeout_ms: int | None = None,
    ):
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if timeout_ms is not None and timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self._gateway = gateway
        self._max_tokens = max_tokens
        self._timeout_ms = timeout_ms

    def __call__(self, goal: GoalEnvelope) -> dict[str, str]:
        allowed = tuple(goal.ambiguity_slots)
        if not allowed:
            return {}
        response = self._gateway.call(
            self._prompt(goal, allowed),
            system_prompt=self._system_prompt(),
            max_tokens=self._max_tokens,
            purpose=ModelCallPurpose.INTENT_FILL,
        )
        return self._parse(response, allowed)

    async def fill(self, goal: GoalEnvelope) -> dict[str, str]:
        """Fill slots without blocking capture, timers, or other task coroutines."""
        allowed = tuple(goal.ambiguity_slots)
        if not allowed:
            return {}
        response = await call_gateway_async(
            self._gateway,
            self._prompt(goal, allowed),
            system_prompt=self._system_prompt(),
            max_tokens=self._max_tokens,
            purpose=ModelCallPurpose.INTENT_FILL,
            timeout_ms=self._timeout_ms,
        )
        return self._parse(response, allowed)

    @staticmethod
    def _prompt(goal: GoalEnvelope, allowed: tuple[str, ...]) -> str:
        return json.dumps(
            {
                "instruction": goal.raw_instruction,
                "unresolved_slots": allowed,
                "allowed_intents": [
                    item.value
                    for item in IntentKind
                    if item is not IntentKind.UNKNOWN
                ],
                "allowed_surfaces": [
                    item.value
                    for item in SurfaceKind
                    if item not in (SurfaceKind.UNKNOWN, SurfaceKind.CHALLENGE)
                ],
                "contract": (
                    "Return exactly one JSON object. Use every unresolved slot "
                    "exactly once as a key with a string value. Do not return "
                    "actions, selectors, coordinates, risk or success claims."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "Fill only unresolved user-intent slots. "
            "Do not plan actions or weaken local policy."
        )

    @staticmethod
    def _parse(
        response: GatewayResponse,
        allowed: tuple[str, ...],
    ) -> dict[str, str]:
        parsed = select_json_object(
            response.content,
            required_keys=allowed,
            allowed_keys=allowed,
            schema_name="semantic slot fill",
        )
        values: dict[str, str] = {}
        for key in allowed:
            value = parsed[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"semantic slot {key!r} must be a non-empty string"
                )
            values[key] = value.strip()
        return values


@dataclass(frozen=True)
class GroundingInterruptCandidate:
    """Minimal structured evidence sent to the semantic grounding interrupt."""

    element_id: str
    role: str
    text: str
    confidence: float
    parent: str | None
    sources: tuple[str, ...]
    fingerprint: str


class SemanticGroundingInterrupt:
    """Resolve local grounding ambiguity without giving the model action authority.

    The provider may select one id from the bounded local candidate set or
    abstain. The model cannot emit selectors, coordinates, actions, policy
    decisions, or success claims. A stale/invalid/provider-error response
    yields no patch, so TaskRuntime fails closed instead of clicking a fallback.
    """

    _SUPPORTED_FAILURES = frozenset(
        {
            FailureCode.GROUNDING_AMBIGUOUS,
            FailureCode.GROUNDING_CONFIDENCE_LOW,
        }
    )

    def __init__(
        self,
        gateway: Gateway,
        *,
        scene_provider: Callable[[], Scene],
        grounder: Grounder,
        max_tokens: int = 512,
        timeout_ms: int | None = None,
        top_k: int = 5,
        min_candidate_confidence: float = 0.72,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if timeout_ms is not None and timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not 0.0 <= min_candidate_confidence <= 1.0:
            raise ValueError("min_candidate_confidence must be in [0, 1]")
        self._gateway = gateway
        self._scene_provider = scene_provider
        self._grounder = grounder
        self._max_tokens = max_tokens
        self._timeout_ms = timeout_ms
        self._top_k = top_k
        self._min_candidate_confidence = float(min_candidate_confidence)
        self._last_error: str | None = None

    @property
    def image_context_capability(self) -> Capability:
        """Image/crop fallback is intentionally not part of SUE-6 text payload."""
        return Capability.UNSUPPORTED

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def __call__(
        self,
        request: ReplanRequest,
        plan: PlanIR,
    ) -> PlanPatch | None:
        self._last_error = None
        if request.reason not in self._SUPPORTED_FAILURES:
            return None
        node = plan.nodes.get(request.failed_node_id)
        if node is None or node.target_query is None:
            return None

        scene = self._scene_provider()
        if scene.version != request.scene_version:
            self._last_error = "stale_scene_before_interrupt"
            return None
        query = node.target_query.as_dict()
        grounding = self._grounder.resolve(query, scene)
        candidates = self._candidate_payload(scene, grounding.candidates)
        if not candidates:
            return None

        try:
            response = await call_gateway_async(
                self._gateway,
                self._prompt(
                    node.id,
                    query,
                    scene.version,
                    candidates,
                ),
                system_prompt=self._system_prompt(),
                max_tokens=self._max_tokens,
                purpose=ModelCallPurpose.GROUNDING,
                timeout_ms=self._timeout_ms,
            )
            response_scene_version, selected_id = self._parse(response)
        except Exception as error:
            self._last_error = f"{type(error).__name__}:{error}"
            return None

        current_scene = self._scene_provider()
        if (
            response_scene_version != request.scene_version
            or current_scene.version != request.scene_version
        ):
            self._last_error = "stale_semantic_grounding_response"
            return None
        if selected_id is None:
            return None
        allowed_ids = {candidate.element_id for candidate in candidates}
        if selected_id not in allowed_ids:
            self._last_error = "semantic_selection_outside_candidates"
            return None
        selected = current_scene.get(selected_id)
        if selected is None or selected.scene_version != current_scene.version:
            self._last_error = "semantic_selection_not_fresh"
            return None

        bound_target = replace(
            node.action.target, element_id=selected_id, locator=None
        )
        bound_action = replace(node.action, target=bound_target)
        patched_node = replace(node, action=bound_action, target_query=None)
        return PlanPatch(
            parent_plan_hash=plan.plan_hash,
            replaced_node_ids=(node.id,),
            nodes={node.id: patched_node},
            resume_node_id=node.id,
            reason=request.reason,
        )

    def _candidate_payload(
        self,
        scene: Scene,
        ranked: tuple[GroundingCandidate, ...],
    ) -> tuple[GroundingInterruptCandidate, ...]:
        result: list[GroundingInterruptCandidate] = []
        for candidate in ranked:
            if len(result) >= self._top_k:
                break
            if candidate.confidence < self._min_candidate_confidence:
                continue
            element = scene.get(candidate.element_id)
            if element is None or element.scene_version != scene.version:
                continue
            result.append(
                GroundingInterruptCandidate(
                    element_id=element.id,
                    role=element.role,
                    text=str(element.text or element.name or "")[:240],
                    confidence=float(candidate.confidence),
                    parent=element.relations.parent,
                    sources=tuple(
                        sorted({source.type for source in element.sources})
                    ),
                    fingerprint=element.fingerprint or "",
                )
            )
        return tuple(result)

    @staticmethod
    def _prompt(
        node_id: str,
        target_query: dict,
        scene_version: int,
        candidates: tuple[GroundingInterruptCandidate, ...],
    ) -> str:
        return json.dumps(
            {
                "goal_node": node_id,
                "target_query": target_query,
                "scene_version": scene_version,
                "candidates": [
                    {
                        "element_id": candidate.element_id,
                        "role": candidate.role,
                        "text": candidate.text,
                        "confidence": candidate.confidence,
                        "relations": {"parent": candidate.parent},
                        "sources": candidate.sources,
                        "fingerprint": candidate.fingerprint,
                    }
                    for candidate in candidates
                ],
                "image_context": Capability.UNSUPPORTED.value,
                "contract": (
                    "Return exactly {scene_version:int, element_id:string|null}. "
                    "Choose only an element_id from candidates, or null to abstain. "
                    "Do not return actions, selectors, coordinates, policy decisions, "
                    "risk changes, or success claims."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "Resolve only the bounded target ambiguity in the supplied fresh scene. "
            "You have no authority to create actions or bypass local policy."
        )

    @staticmethod
    def _parse(response: GatewayResponse) -> tuple[int, str | None]:
        parsed = select_json_object(
            response.content,
            required_keys=("scene_version", "element_id"),
            allowed_keys=("scene_version", "element_id"),
            schema_name="semantic grounding interrupt",
        )
        scene_version = parsed["scene_version"]
        if isinstance(scene_version, bool) or not isinstance(scene_version, int):
            raise ValueError("semantic grounding scene_version must be an integer")
        element_id = parsed["element_id"]
        if element_id is None:
            return scene_version, None
        if not isinstance(element_id, str) or not element_id.strip():
            raise ValueError(
                "semantic grounding element_id must be a non-empty string or null"
            )
        return scene_version, element_id.strip()
