"""Deterministic PlanIR compiler for locally-resolved strategy inputs.

The compiler does not invent site selectors.  Surface-specific target queries
must come from current context/workflow data or an explicit semantic planning
result; this module only turns those typed facts into a runtime graph.
"""

from hpcu.schemas.action import (
    Action,
    ActionOp,
    Postcondition,
    PostconditionKind,
)
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.plan import GroundingHints, PlanIR, PlanNode, TargetQuerySpec
from hpcu.schemas.strategy import StrategyPlan


class PlanCompiler:
    compiler_version = "1"

    def compile_search(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        *,
        search_box: TargetQuerySpec,
        result_target: TargetQuerySpec,
        task_budget: TaskBudgetSpec | None = None,
    ) -> PlanIR:
        if goal.intent is not IntentKind.SEARCH:
            raise ValueError("compile_search requires a SEARCH goal")
        queries = goal.entity_values("search_query")
        if len(queries) != 1:
            raise ValueError("search planning requires exactly one search_query entity")
        query_text = queries[0]
        hints = GroundingHints(tokens=("search", "검색"), source="plan_context")
        nodes = {
            "focus-search": PlanNode(
                id="focus-search",
                action=Action(
                    id="focus-search",
                    op=ActionOp.CLICK,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.ELEMENT_FOCUSED,
                            target="$target",
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=search_box,
                grounding_hints=hints,
                success_edge="type-query",
            ),
            "type-query": PlanNode(
                id="type-query",
                action=Action(
                    id="type-query",
                    op=ActionOp.REPLACE_TEXT,
                    value=query_text,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.TEXT_EQUALS,
                            target="$target",
                            value=query_text,
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=search_box,
                grounding_hints=hints,
                success_edge="submit-query",
            ),
            "submit-query": PlanNode(
                id="submit-query",
                action=Action(
                    id="submit-query",
                    op=ActionOp.HOTKEY,
                    key="Enter",
                    # The submit itself is not declared successful merely
                    # because input was injected.  A result target must appear.
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.ELEMENT_VISIBLE,
                            target="$verify",
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=search_box,
                verification_query=result_target,
                grounding_hints=GroundingHints(
                    tokens=(query_text,), source="plan_context"
                ),
                success_edge="verify-result",
            ),
            "verify-result": PlanNode(
                id="verify-result",
                action=Action(
                    id="verify-result",
                    op=ActionOp.ASSERT,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.ELEMENT_VISIBLE,
                            target="$target",
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=result_target,
            ),
        }
        # Planning and runtime semantic interrupts must share one immutable
        # budget spec.  Callers that already created a ledger before intent
        # filling pass its spec here; otherwise derive a conservative default
        # from the GoalEnvelope.
        budget = task_budget or TaskBudgetSpec(
            max_model_calls=goal.model_call_budget,
            max_model_latency_ms=goal.latency_budget_ms,
        )
        if budget.max_model_calls > goal.model_call_budget:
            raise ValueError("PlanIR model-call budget exceeds GoalEnvelope budget")
        if budget.max_model_latency_ms > goal.latency_budget_ms:
            raise ValueError("PlanIR model-latency budget exceeds GoalEnvelope budget")
        return PlanIR(
            goal=goal,
            strategy_id=strategy.id,
            entry_node_id="focus-search",
            nodes=nodes,
            task_budget=budget,
            compiler_version=self.compiler_version,
        )
