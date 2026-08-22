"""Command→GoalEnvelope→StrategyPlan→PlanIR integration contract."""

import pytest

from hpcu.planning.goal_interpreter import GoalInterpreter
from hpcu.planning.plan_compiler import PlanCompiler
from hpcu.planning.strategy_planner import StrategyPlanner
from hpcu.schemas.capability import Capability
from hpcu.schemas.plan import TargetQuerySpec
from hpcu.schemas.strategy import CapabilitySnapshot
from hpcu.schemas.surface import ExecutionMode, SurfaceKind

pytestmark = pytest.mark.integration


def test_simple_search_compiles_without_semantic_provider():
    goal = GoalInterpreter().interpret("브라우저에서 노트북을 검색해줘")
    capability = CapabilitySnapshot(
        active_surface=SurfaceKind.BROWSER,
        execution_mode=ExecutionMode.SCREEN_STRICT,
        capture=Capability.SUPPORTED,
        structure=Capability.SUPPORTED,
        semantic_input=Capability.SUPPORTED,
        physical_input=Capability.SUPPORTED,
    )
    strategy = StrategyPlanner().select(goal, capability)
    plan = PlanCompiler().compile_search(
        goal,
        strategy,
        search_box=TargetQuerySpec(text="Search", role="textbox"),
        result_target=TargetQuerySpec(text="노트북"),
    )
    assert plan.goal.ambiguity_slots == ()
    assert strategy.model_calls_expected == 0
    assert len(plan.nodes) == 4
    assert plan.nodes["submit-query"].verification_query is not None
