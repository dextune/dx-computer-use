"""Regression tests for PlanCompiler compatibility adapters."""

import pytest

from hpcu.planning.plan_compiler import PlanCompiler
from hpcu.schemas.goal import GoalEntity, GoalEnvelope, IntentKind
from hpcu.schemas.plan import TargetQuerySpec
from hpcu.schemas.strategy import StrategyPlan
from hpcu.schemas.surface import ExecutionMode, SurfaceKind

pytestmark = pytest.mark.unit


def _strategy() -> StrategyPlan:
    return StrategyPlan(
        id="screen",
        surface=SurfaceKind.BROWSER,
        execution_mode=ExecutionMode.SCREEN_STRICT,
        route="screen_action",
        expected_steps=4,
        evidence_strength=0.9,
        expected_reliability=0.9,
        expected_latency_ms=10,
    )


def _search_goal() -> GoalEnvelope:
    return GoalEnvelope(
        raw_instruction="search for linux",
        intent=IntentKind.SEARCH,
        terminal_state="results visible",
        entities=(GoalEntity("search_query", "linux"),),
        evidence_requirements=("query_echo", "result_candidate"),
        model_call_budget=0,
    )


def test_compile_search_preserves_blocked_tokens():
    plan = PlanCompiler().compile_search(
        _search_goal(),
        _strategy(),
        search_box=TargetQuerySpec(text="search", role="textbox"),
        result_target=TargetQuerySpec(text="linux"),
        blocked_tokens=(" captcha ", "로그인"),
    )

    assert plan.blocked_tokens == ("captcha", "로그인")
