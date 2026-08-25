"""SUE-6 structured semantic interrupt qualification."""

from __future__ import annotations

import json

import pytest

from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.grounder.grounder import Grounder
from hpcu.planning.semantic_interrupt import SemanticGroundingInterrupt
from hpcu.runtime_core.task_budget import BudgetedGateway, TaskBudgetLedger
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.plan import (
    PlanIR,
    PlanNode,
    ReplanRequest,
    TargetQuerySpec,
    TaskBudgetSnapshot,
)
from hpcu.schemas.scene import Scene
from hpcu.schemas.surface import SurfaceKind
from hpcu.schemas.ui_element import ElementSource, UIElement

pytestmark = pytest.mark.unit
_SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


class _Gateway(Gateway):
    def __init__(
        self,
        *,
        selected: str | None = "pay-b",
        scene_version: int = 1,
        invalid: bool = False,
    ) -> None:
        self.selected = selected
        self.scene_version = scene_version
        self.invalid = invalid
        self.calls: list[tuple[dict, ModelCallPurpose]] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return "fake-model"

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        del system_prompt, max_tokens
        self.calls.append((json.loads(prompt), purpose))
        if self.invalid:
            return GatewayResponse(content="not-json", model="fake")
        return GatewayResponse(
            content=json.dumps(
                {
                    "scene_version": self.scene_version,
                    "element_id": self.selected,
                }
            ),
            model="fake",
        )


def _element(element_id: str, x: float) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=1,
        role="button",
        name="Pay",
        text="Pay",
        bbox=BoundingBox(_SPACE, x, 0, 40, 20),
        sources=(ElementSource(type="dom"),),
        fingerprint=f"fp-{element_id}",
        stable_frames=2,
    )


def _scene() -> Scene:
    first = _element("pay-a", 0)
    second = _element("pay-b", 60)
    return Scene(version=1, elements={first.id: first, second.id: second})


def _plan() -> PlanIR:
    goal = GoalEnvelope(
        raw_instruction="Pay using the requested control",
        intent=IntentKind.SUBMIT,
        terminal_state="submitted",
    )
    node = PlanNode(
        id="submit",
        action=Action(id="submit-action", op=ActionOp.CLICK),
        surface=SurfaceKind.BROWSER,
        target_query=TargetQuerySpec(text="Pay", role="button"),
    )
    return PlanIR(
        goal=goal,
        strategy_id="screen",
        entry_node_id=node.id,
        nodes={node.id: node},
        task_budget=TaskBudgetSpec(),
    )


def _request() -> ReplanRequest:
    return ReplanRequest(
        reason=FailureCode.GROUNDING_AMBIGUOUS,
        failed_node_id="submit",
        scene_version=1,
        unresolved_slots=(),
        completed_nodes=(),
        remaining_budget=TaskBudgetSnapshot(1, 1024, 1000),
    )


@pytest.mark.asyncio
async def test_interrupt_sends_only_structured_topk_and_binds_selected_id():
    scene = _scene()
    gateway = _Gateway(selected="pay-b")
    interrupt = SemanticGroundingInterrupt(
        gateway,
        scene_provider=lambda: scene,
        grounder=Grounder(confidence_threshold=0.5, min_margin=0.2),
        top_k=2,
    )

    patch = await interrupt(_request(), _plan())

    assert patch is not None
    node = patch.nodes["submit"]
    assert node.action.target.element_id == "pay-b"
    assert node.target_query is None
    payload, purpose = gateway.calls[0]
    assert purpose is ModelCallPurpose.GROUNDING
    assert payload["scene_version"] == 1
    assert len(payload["candidates"]) == 2
    assert "coordinates" not in payload
    assert payload["image_context"] == Capability.UNSUPPORTED.value


@pytest.mark.asyncio
async def test_interrupt_accounts_grounding_purpose_in_task_budget():
    scene = _scene()
    ledger = TaskBudgetLedger(
        TaskBudgetSpec(
            max_model_calls=1,
            max_model_tokens=1024,
            max_model_latency_ms=1000,
        )
    )
    interrupt = SemanticGroundingInterrupt(
        BudgetedGateway(_Gateway(selected="pay-a"), ledger),
        scene_provider=lambda: scene,
        grounder=Grounder(confidence_threshold=0.5, min_margin=0.2),
    )

    patch = await interrupt(_request(), _plan())

    assert patch is not None
    assert ledger.calls_by_purpose[ModelCallPurpose.GROUNDING] == 1


@pytest.mark.asyncio
async def test_stale_semantic_selection_is_rejected_without_patch():
    scene = _scene()
    interrupt = SemanticGroundingInterrupt(
        _Gateway(selected="pay-b", scene_version=0),
        scene_provider=lambda: scene,
        grounder=Grounder(confidence_threshold=0.5, min_margin=0.2),
    )

    assert await interrupt(_request(), _plan()) is None
    assert interrupt.last_error == "stale_semantic_grounding_response"


@pytest.mark.asyncio
async def test_schema_error_has_no_arbitrary_fallback_selection():
    scene = _scene()
    interrupt = SemanticGroundingInterrupt(
        _Gateway(invalid=True),
        scene_provider=lambda: scene,
        grounder=Grounder(confidence_threshold=0.5, min_margin=0.2),
    )

    patch = await interrupt(_request(), _plan())

    assert patch is None
    assert interrupt.last_error is not None


@pytest.mark.asyncio
async def test_provider_cannot_select_id_outside_local_candidate_set():
    scene = _scene()
    interrupt = SemanticGroundingInterrupt(
        _Gateway(selected="invented-id"),
        scene_provider=lambda: scene,
        grounder=Grounder(confidence_threshold=0.5, min_margin=0.2),
    )

    assert await interrupt(_request(), _plan()) is None
    assert interrupt.last_error == "semantic_selection_outside_candidates"


def test_command_runtime_wires_structured_grounding_interrupt_by_default():
    from types import SimpleNamespace

    from hpcu.runtime_core.product_runtime import CommandRuntime

    runtime = CommandRuntime(
        lambda: object(),
        provider_gateway=_Gateway(),
        config={
            "semantic": {
                "default_provider": "fake",
                "default_model": "fake-model",
                "request_limits": {"grounding_retry_attempts": 0},
            },
            "confidence": {
                "local_execute_threshold": 0.88,
                "local_margin_min": 0.20,
            },
        },
    )
    ledger = TaskBudgetLedger(TaskBudgetSpec())
    gateway = runtime._semantic_gateway(ledger)
    scene = _scene()
    loop = SimpleNamespace(
        scene=scene,
        grounder=Grounder(confidence_threshold=0.5, min_margin=0.2),
    )

    repairer = runtime._semantic_replanner(gateway, ledger, loop)

    assert isinstance(repairer, SemanticGroundingInterrupt)
