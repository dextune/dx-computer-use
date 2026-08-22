"""Unit tests for hpcu.router.router and hpcu.router.scene_summarizer."""

from dataclasses import dataclass

import pytest

from hpcu.gateway.gateway import Gateway, GatewayResponse
from hpcu.router.candidate_scoring import TargetQuery
from hpcu.router.router import (
    Decision,
    EscalationStrategy,
    ModelRouter,
)
from hpcu.router.scene_summarizer import summarize_scene
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import ElementSource, ElementState, UIElement


@dataclass(frozen=True)
class FakeNode:
    target_query: TargetQuery


def _element(
    element_id: str,
    *,
    text: str = "",
    role: str = "unknown",
    source_type: str = "dom",
    scene_version: int = 1,
) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=scene_version,
        role=role,
        text=text,
        state=ElementState(visible=True, enabled=True),
        sources=(ElementSource(type=source_type, confidence=1.0),),
    )


def _scene(*elements, title: str = "Test Window", version: int = 1) -> Scene:
    return Scene(
        version=version,
        window_id="win-1",
        window_title=title,
        elements={element.id: element for element in elements},
    )


@pytest.mark.unit
def test_choose_tier_no_failure_is_direct_tier_zero():
    """Given no failure, the router picks the direct (model-free) tier."""
    # Given
    router = ModelRouter(model_call_budget=8, direct_threshold=0.88)
    scene = _scene(_element("btn_login", text="Login", role="button"))
    # When
    strategy = router.choose_tier(None, risk=0, scene=scene)
    # Then
    assert strategy.tier == 0


@pytest.mark.unit
def test_choose_tier_grounding_ambiguity_escalates_to_text_llm():
    """Given a grounding failure, the router escalates to the text LLM."""
    # Given
    router = ModelRouter(model_call_budget=8, direct_threshold=0.88)
    scene = _scene(_element("btn_login", text="Login", role="button"))
    # When
    strategy = router.choose_tier(FailureCode.GROUNDING_AMBIGUOUS, risk=0, scene=scene)
    # Then
    assert strategy.tier == 1


@pytest.mark.unit
def test_choose_tier_high_risk_escalates():
    """Given a high-risk task, the tier is bumped up for safety."""
    # Given
    router = ModelRouter(model_call_budget=8, direct_threshold=0.88)
    scene = _scene(_element("btn_login", text="Login", role="button"))
    # When
    low = router.choose_tier(FailureCode.GROUNDING_AMBIGUOUS, risk=1, scene=scene)
    high = router.choose_tier(FailureCode.GROUNDING_AMBIGUOUS, risk=2, scene=scene)
    # Then
    assert high.tier > low.tier


@pytest.mark.unit
def test_choose_tier_empty_scene_escalates():
    """Given an empty scene, the router refuses the direct tier."""
    # Given
    router = ModelRouter(model_call_budget=8, direct_threshold=0.88)
    scene = _scene()
    # When
    strategy = router.choose_tier(None, risk=0, scene=scene)
    # Then
    assert strategy.tier > 0


@pytest.mark.unit
def test_choose_tier_recovery_exhausted_tops_out():
    """Given recovery exhausted, the tier is capped at the human tier."""
    # Given
    router = ModelRouter(model_call_budget=8, direct_threshold=0.88)
    scene = _scene(_element("btn_login", text="Login", role="button"))
    # When
    strategy = router.choose_tier(FailureCode.RECOVERY_EXHAUSTED, risk=2, scene=scene)
    # Then
    assert strategy.tier == 5


@pytest.mark.unit
def test_resolve_tier_zero_uses_high_confidence_candidate_without_model():
    """Given a strong candidate, tier 0 resolves directly with no model call."""
    # Given
    gateway = _TrackingGateway()
    router = ModelRouter(model_call_budget=8, direct_threshold=0.88, gateway=gateway)
    node = FakeNode(TargetQuery(text="Login", role="button"))
    scene = _scene(_element("btn_login", text="Login", role="button"))
    strategy = router.choose_tier(None, risk=0, scene=scene)
    candidates = list(scene.elements.values())
    remaining_before = router.model_calls_remaining
    # When
    decision = strategy.resolve(node, scene, candidates)
    # Then
    assert decision.target_id == "btn_login"
    assert decision.confidence > 0.8
    assert gateway.call_count == 0
    assert router.model_calls_remaining == remaining_before


@pytest.mark.unit
def test_resolve_tier_one_calls_model_and_consumes_budget():
    """Given no high-confidence candidate, tier 1 calls the model."""
    # Given
    gateway = _TrackingGateway()
    router = ModelRouter(model_call_budget=3, direct_threshold=0.99, gateway=gateway)
    node = FakeNode(TargetQuery(text="Login", role="button"))
    # Deliberately low-confidence element (wrong text/role).
    scene = _scene(_element("img_logo", text="Logo", role="image"))
    strategy = router.choose_tier(FailureCode.GROUNDING_AMBIGUOUS, risk=0, scene=scene)
    candidates = list(scene.elements.values())
    # When
    decision = strategy.resolve(node, scene, candidates)
    # Then
    assert gateway.call_count == 1
    assert router.model_calls_remaining == 2
    assert decision.target_id == "model_target"


@pytest.mark.unit
def test_resolve_budget_exhaustion_returns_no_decision():
    """Given the budget is exhausted, resolve stays stable (no decision)."""
    # Given
    gateway = _TrackingGateway()
    router = ModelRouter(model_call_budget=1, direct_threshold=0.99, gateway=gateway)
    node = FakeNode(TargetQuery(text="Login", role="button"))
    scene = _scene(_element("img_logo", text="Logo", role="image"))
    candidates = list(scene.elements.values())
    # First resolve consumes the single call.
    first = router.choose_tier(FailureCode.GROUNDING_AMBIGUOUS, risk=0, scene=scene)
    first.resolve(node, scene, candidates)
    # When — budget is now 0; the next resolve must not crash.
    second = router.choose_tier(FailureCode.GROUNDING_AMBIGUOUS, risk=0, scene=scene)
    decision = second.resolve(node, scene, candidates)
    # Then
    assert router.model_calls_remaining == 0
    assert decision.target_id is None


@pytest.mark.unit
def test_decision_is_stale():
    """Given a decision grounded on scene v1, it is stale for v2."""
    # Given
    decision = Decision(target_id="btn_login", confidence=0.9, scene_version=1)
    # When/Then
    assert decision.is_stale(2) is True
    assert decision.is_stale(1) is False
    assert decision.is_stale(0) is False


@pytest.mark.unit
def test_router_negative_budget_rejected():
    """Given a negative budget, construction is rejected."""
    # When/Then
    with pytest.raises(ValueError):
        ModelRouter(model_call_budget=-1, direct_threshold=0.88)


@pytest.mark.unit
def test_escalation_strategy_rejects_out_of_range_tier():
    """Given an out-of-range tier, the strategy is rejected."""
    # When/Then
    with pytest.raises(ValueError):
        EscalationStrategy(tier=9)


@pytest.mark.unit
def test_summarize_scene_selects_top_k_candidates():
    """Given a scene, the summary exposes top candidates and counts."""
    # Given
    scene = _scene(
        _element("btn_login", text="Login", role="button"),
        _element("btn_cancel", text="Cancel", role="button"),
        _element("lbl_title", text="Welcome", role="text"),
    )
    # When
    summary = summarize_scene(scene, top_k=2, target_query=TargetQuery(text="Login"))
    # Then
    assert summary.window_title == "Test Window"
    assert summary.candidate_count == 3
    assert len(summary.top_candidates) == 2
    assert summary.top_candidates[0].element_id == "btn_login"
    assert summary.scene_version == 1


@pytest.mark.unit
def test_summarize_scene_negative_top_k_rejected():
    """Given a negative top_k, summarization is rejected."""
    # When/Then
    with pytest.raises(ValueError):
        summarize_scene(_scene(), top_k=-1)


class _TrackingGateway(Gateway):
    """Fake Gateway that records calls and returns a fixed target."""

    def __init__(self):
        self.call_count = 0

    def call(self, prompt: str, system_prompt: str = "") -> GatewayResponse:
        self.call_count += 1
        return GatewayResponse(
            content='{"target_id": "model_target", "confidence": 0.9}',
            model="MiniMax-M3",
            tokens_used=10,
            latency_ms=5,
        )
