"""Deterministic compiler from GoalEnvelope and strategy to typed PlanIR.

The compiler never invents selectors, current element IDs, or coordinates.
Surface-specific target roles arrive through :class:`PlanningContext`; the
compiler emits one capability-checked graph with each independent evidence
requirement assigned to exactly one node.
"""

from __future__ import annotations

from collections.abc import Callable

from hpcu.schemas.action import Action, ActionOp, Postcondition, PostconditionKind
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.goal import GoalEnvelope, IntentKind, Reversibility
from hpcu.schemas.plan import (
    GroundingHints,
    PlanIR,
    PlanningContext,
    PlanNode,
    TargetQuerySpec,
)
from hpcu.schemas.strategy import StrategyPlan

Compiler = Callable[
    [GoalEnvelope, StrategyPlan, PlanningContext],
    tuple[dict[str, PlanNode], str],
]


class PlanCompiler:
    compiler_version = "3"

    def compile(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        context: PlanningContext,
        task_budget: TaskBudgetSpec | None = None,
    ) -> PlanIR:
        if goal.intent is IntentKind.UNKNOWN:
            raise ValueError("cannot compile an unresolved intent")
        budget = self._task_budget(goal, task_budget)
        if strategy.route == "tool_action":
            nodes, entry = self._compile_tool(goal, strategy, context)
        else:
            compilers: dict[IntentKind, Compiler] = {
                IntentKind.NAVIGATE: self._compile_navigate,
                IntentKind.SEARCH: self._compile_search,
                IntentKind.SELECT: self._compile_select,
                IntentKind.COMPARE: self._compile_compare,
                IntentKind.EDIT: self._compile_edit,
                IntentKind.SUBMIT: self._compile_submit,
            }
            nodes, entry = compilers[goal.intent](goal, strategy, context)
        self._validate_ops(goal, context, nodes)
        self._validate_evidence(goal, nodes)
        return PlanIR(
            goal=goal,
            strategy_id=strategy.id,
            entry_node_id=entry,
            nodes=nodes,
            task_budget=budget,
            compiler_version=self.compiler_version,
            blocked_tokens=context.blocked_tokens,
        )

    def compile_search(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        *,
        search_box: TargetQuerySpec,
        result_target: TargetQuerySpec,
        task_budget: TaskBudgetSpec | None = None,
    ) -> PlanIR:
        """Compatibility adapter for the original search-only public API."""
        return self.compile(
            goal,
            strategy,
            PlanningContext(
                target_queries={
                    "search_field": search_box,
                    "search_result": result_target,
                }
            ),
            task_budget,
        )

    @staticmethod
    def _task_budget(
        goal: GoalEnvelope,
        task_budget: TaskBudgetSpec | None,
    ) -> TaskBudgetSpec:
        budget = task_budget or TaskBudgetSpec(
            max_model_calls=goal.model_call_budget,
            max_model_latency_ms=goal.latency_budget_ms,
        )
        if budget.max_model_calls > goal.model_call_budget:
            raise ValueError("PlanIR model-call budget exceeds GoalEnvelope budget")
        if budget.max_model_latency_ms > goal.latency_budget_ms:
            raise ValueError("PlanIR model-latency budget exceeds GoalEnvelope budget")
        return budget

    @staticmethod
    def _validate_ops(
        goal: GoalEnvelope,
        context: PlanningContext,
        nodes: dict[str, PlanNode],
    ) -> None:
        forbidden = set(goal.forbidden_actions)
        for node in nodes.values():
            operation = node.action.op
            if operation not in context.allowed_ops:
                raise ValueError(
                    f"selected capability cannot execute {operation.value!r}"
                )
            if operation in forbidden:
                raise ValueError(
                    f"compiled action {operation.value!r} is forbidden by the goal"
                )

    @staticmethod
    def _validate_evidence(
        goal: GoalEnvelope,
        nodes: dict[str, PlanNode],
    ) -> None:
        assigned = tuple(
            requirement
            for node in nodes.values()
            for requirement in node.evidence_requirements
        )
        if len(assigned) != len(set(assigned)):
            raise ValueError("each evidence requirement must be assigned once")
        expected = set(goal.evidence_requirements)
        actual = set(assigned)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                "compiled evidence contract does not match GoalEnvelope: "
                f"missing={missing!r}, extra={extra!r}"
            )

    @staticmethod
    def _query_text(goal: GoalEnvelope) -> str:
        values = goal.entity_values("search_query")
        if len(values) != 1:
            raise ValueError("search planning requires exactly one search_query")
        return values[0]

    @staticmethod
    def _url(goal: GoalEnvelope, context: PlanningContext) -> str:
        values = goal.entity_values("url")
        if len(values) == 1:
            return values[0]
        if len(values) > 1:
            raise ValueError("navigate planning accepts exactly one URL")
        return context.require_value("url")

    @staticmethod
    def _replacement_value(goal: GoalEnvelope, context: PlanningContext) -> str:
        values = goal.entity_values("replacement_value")
        if len(values) == 1:
            return values[0]
        if len(values) > 1:
            raise ValueError("edit planning accepts one replacement value")
        return context.require_value("replacement_value")

    @staticmethod
    def _evidence(goal: GoalEnvelope, *requirements: str) -> tuple[str, ...]:
        expected = set(goal.evidence_requirements)
        missing = set(requirements) - expected
        if missing:
            raise ValueError(
                f"goal evidence contract is missing {sorted(missing)!r}"
            )
        return tuple(requirements)

    def _compile_navigate(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        context: PlanningContext,
    ) -> tuple[dict[str, PlanNode], str]:
        surface = context.require_query("surface")
        destination = context.require_query("destination")
        url = self._url(goal, context)
        nodes = {
            "inspect-surface": PlanNode(
                id="inspect-surface",
                action=self._assert_visible("inspect-surface"),
                surface=strategy.surface,
                target_query=surface,
                success_edge="focus-surface",
            ),
            "focus-surface": PlanNode(
                id="focus-surface",
                action=Action(
                    id="focus-surface",
                    op=ActionOp.FOCUS_WINDOW,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.ELEMENT_FOCUSED,
                            target="$target",
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=surface,
                success_edge="navigate-destination",
            ),
            "navigate-destination": PlanNode(
                id="navigate-destination",
                action=Action(
                    id="navigate-destination",
                    op=ActionOp.NAVIGATE,
                    value=url,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.ELEMENT_VISIBLE,
                            target="$verify",
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=surface,
                verification_query=destination,
                grounding_hints=GroundingHints(tokens=(url,), source="goal"),
                success_edge="verify-destination",
            ),
            "verify-destination": PlanNode(
                id="verify-destination",
                action=self._assert_visible("verify-destination"),
                surface=strategy.surface,
                target_query=destination,
                evidence_requirements=self._evidence(goal, "surface_identity"),
            ),
        }
        return nodes, "inspect-surface"

    def _compile_search(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        context: PlanningContext,
    ) -> tuple[dict[str, PlanNode], str]:
        search_field = context.require_query("search_field")
        search_result = context.require_query("search_result")
        query_text = self._query_text(goal)
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
                target_query=search_field,
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
                target_query=search_field,
                grounding_hints=hints,
                success_edge="submit-query",
                evidence_requirements=self._evidence(goal, "query_echo"),
            ),
            "submit-query": PlanNode(
                id="submit-query",
                action=Action(
                    id="submit-query",
                    op=ActionOp.HOTKEY,
                    key="Enter",
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.ELEMENT_VISIBLE,
                            target="$verify",
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=search_field,
                verification_query=search_result,
                grounding_hints=GroundingHints(
                    tokens=(query_text,),
                    source="goal",
                ),
                success_edge="verify-result",
            ),
            "verify-result": PlanNode(
                id="verify-result",
                action=self._assert_visible("verify-result"),
                surface=strategy.surface,
                target_query=search_result,
                evidence_requirements=self._evidence(goal, "result_candidate"),
            ),
        }
        return nodes, "focus-search"

    def _compile_select(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        context: PlanningContext,
    ) -> tuple[dict[str, PlanNode], str]:
        candidate = context.require_query("candidate")
        selected_state = context.require_query("selected_state")
        nodes = {
            "inspect-candidate": PlanNode(
                id="inspect-candidate",
                action=self._assert_visible("inspect-candidate"),
                surface=strategy.surface,
                target_query=candidate,
                success_edge="read-candidate",
            ),
            "read-candidate": PlanNode(
                id="read-candidate",
                action=self._read_visible("read-candidate"),
                surface=strategy.surface,
                target_query=candidate,
                success_edge="select-candidate",
            ),
            "select-candidate": PlanNode(
                id="select-candidate",
                action=Action(
                    id="select-candidate",
                    op=ActionOp.SELECT,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.STATE_MATCHES,
                            target="$verify",
                            value=1,
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=candidate,
                verification_query=selected_state,
                success_edge="verify-selection",
            ),
            "verify-selection": PlanNode(
                id="verify-selection",
                action=Action(
                    id="verify-selection",
                    op=ActionOp.ASSERT,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.STATE_MATCHES,
                            target="$target",
                            value=1,
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=selected_state,
                evidence_requirements=self._evidence(goal, "selected_state"),
            ),
        }
        return nodes, "inspect-candidate"

    def _compile_compare(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        context: PlanningContext,
    ) -> tuple[dict[str, PlanNode], str]:
        first = context.require_query("candidate_a")
        second = context.require_query("candidate_b")
        nodes = {
            "read-candidate-a": PlanNode(
                id="read-candidate-a",
                action=self._read_visible("read-candidate-a"),
                surface=strategy.surface,
                target_query=first,
                success_edge="verify-candidate-a",
            ),
            "verify-candidate-a": PlanNode(
                id="verify-candidate-a",
                action=self._assert_visible("verify-candidate-a"),
                surface=strategy.surface,
                target_query=first,
                success_edge="read-candidate-b",
                evidence_requirements=self._evidence(goal, "candidate_a"),
            ),
            "read-candidate-b": PlanNode(
                id="read-candidate-b",
                action=self._read_visible("read-candidate-b"),
                surface=strategy.surface,
                target_query=second,
                success_edge="verify-comparison",
            ),
            "verify-comparison": PlanNode(
                id="verify-comparison",
                action=self._assert_visible("verify-comparison"),
                surface=strategy.surface,
                target_query=second,
                evidence_requirements=self._evidence(goal, "candidate_b"),
            ),
        }
        return nodes, "read-candidate-a"

    def _compile_edit(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        context: PlanningContext,
    ) -> tuple[dict[str, PlanNode], str]:
        target = context.require_query("editable_target")
        edited = context.target_queries.get("edited_state", target)
        value = self._replacement_value(goal, context)
        nodes = {
            "inspect-edit-target": PlanNode(
                id="inspect-edit-target",
                action=self._assert_visible("inspect-edit-target"),
                surface=strategy.surface,
                target_query=target,
                success_edge="read-current-value",
            ),
            "read-current-value": PlanNode(
                id="read-current-value",
                action=self._read_visible("read-current-value"),
                surface=strategy.surface,
                target_query=target,
                success_edge="replace-value",
                evidence_requirements=self._evidence(goal, "old_value"),
            ),
            "replace-value": PlanNode(
                id="replace-value",
                action=Action(
                    id="replace-value",
                    op=ActionOp.REPLACE_TEXT,
                    value=value,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.TEXT_EQUALS,
                            target="$verify",
                            value=value,
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=target,
                verification_query=edited,
                success_edge="verify-edit",
            ),
            "verify-edit": PlanNode(
                id="verify-edit",
                action=Action(
                    id="verify-edit",
                    op=ActionOp.ASSERT,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.TEXT_EQUALS,
                            target="$target",
                            value=value,
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=edited,
                evidence_requirements=self._evidence(goal, "edited_value"),
            ),
        }
        return nodes, "inspect-edit-target"

    def _compile_submit(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        context: PlanningContext,
    ) -> tuple[dict[str, PlanNode], str]:
        form = context.require_query("form")
        control = context.require_query("submit_control")
        confirmation = context.require_query("submission_confirmation")
        irreversible = goal.reversibility is Reversibility.IRREVERSIBLE
        nodes = {
            "inspect-form": PlanNode(
                id="inspect-form",
                action=self._assert_visible("inspect-form"),
                surface=strategy.surface,
                target_query=form,
                success_edge="inspect-submit-control",
            ),
            "inspect-submit-control": PlanNode(
                id="inspect-submit-control",
                action=Action(
                    id="inspect-submit-control",
                    op=ActionOp.ASSERT,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.ELEMENT_ENABLED,
                            target="$target",
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=control,
                success_edge="submit-form",
            ),
            "submit-form": PlanNode(
                id="submit-form",
                action=Action(
                    id="submit-form",
                    op=ActionOp.CLICK,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.ELEMENT_VISIBLE,
                            target="$verify",
                        ),
                    ),
                ),
                surface=strategy.surface,
                target_query=control,
                verification_query=confirmation,
                success_edge="verify-submission",
                irreversible=irreversible,
            ),
            "verify-submission": PlanNode(
                id="verify-submission",
                action=self._assert_visible("verify-submission"),
                surface=strategy.surface,
                target_query=confirmation,
                evidence_requirements=self._evidence(
                    goal,
                    "submission_confirmation",
                ),
            ),
        }
        return nodes, "inspect-form"

    def _compile_tool(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        context: PlanningContext,
    ) -> tuple[dict[str, PlanNode], str]:
        result = context.require_query("tool_result")
        nodes = {
            "call-tool": PlanNode(
                id="call-tool",
                action=Action(
                    id="call-tool",
                    op=ActionOp.CALL_TOOL,
                    value=goal.intent.value,
                    postconditions=(
                        Postcondition(
                            kind=PostconditionKind.ELEMENT_VISIBLE,
                            target="$verify",
                        ),
                    ),
                ),
                surface=strategy.surface,
                verification_query=result,
                success_edge="verify-tool-result",
            ),
            "verify-tool-result": PlanNode(
                id="verify-tool-result",
                action=self._assert_visible("verify-tool-result"),
                surface=strategy.surface,
                target_query=result,
                evidence_requirements=goal.evidence_requirements,
            ),
        }
        return nodes, "call-tool"

    @staticmethod
    def _assert_visible(action_id: str) -> Action:
        return Action(
            id=action_id,
            op=ActionOp.ASSERT,
            postconditions=(
                Postcondition(
                    kind=PostconditionKind.ELEMENT_VISIBLE,
                    target="$target",
                ),
            ),
        )

    @staticmethod
    def _read_visible(action_id: str) -> Action:
        return Action(
            id=action_id,
            op=ActionOp.READ,
            postconditions=(
                Postcondition(
                    kind=PostconditionKind.ELEMENT_VISIBLE,
                    target="$target",
                ),
            ),
        )
