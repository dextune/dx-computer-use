"""Case data adapter that produces typed planning context, not actions."""

from __future__ import annotations

from hpcu.compiler.targeting_compiler import TargetingCompiler
from hpcu.gateway.gateway import Gateway
from hpcu.runtime_core.product_runtime import CommandRequest
from hpcu.runtime_core.task_budget import TaskBudgetLedger
from hpcu.schemas.action import ActionOp
from hpcu.schemas.capability import Capability
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.plan import PlanningContext, TargetQuerySpec
from hpcu.schemas.strategy import StrategyPlan

_LOCAL_OPS = frozenset(
    {
        ActionOp.ASSERT,
        ActionOp.READ,
        ActionOp.WAIT_UNTIL,
        ActionOp.CHECKPOINT,
    }
)
_INTERACTION_OPS = frozenset(
    {
        ActionOp.FOCUS_WINDOW,
        ActionOp.NAVIGATE,
        ActionOp.INVOKE,
        ActionOp.CLICK,
        ActionOp.DOUBLE_CLICK,
        ActionOp.RIGHT_CLICK,
        ActionOp.TYPE,
        ActionOp.REPLACE_TEXT,
        ActionOp.HOTKEY,
        ActionOp.SELECT,
        ActionOp.TOGGLE,
        ActionOp.SCROLL,
        ActionOp.DRAG,
        ActionOp.REQUEST_APPROVAL,
    }
)


class CasePlanningContextProvider:
    """Map a data-only case request to abstract target-query roles."""

    def __init__(self, *, config: dict | None = None) -> None:
        self._config = config

    def __call__(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        request: CommandRequest,
        gateway: Gateway | None,
        ledger: TaskBudgetLedger,
    ) -> PlanningContext:
        del ledger
        case_id = request.context_metadata.get("case_id", "case")
        start_url = request.context_metadata.get("start_url", "")
        compiler = TargetingCompiler(gateway, config=self._config)
        pack = compiler.compile(case_id, goal.raw_instruction, start_url)

        success_text = self._joined(pack.success_any) or pack.pick_query
        ready_text = self._joined(pack.ready_any) or success_text
        pick_text = pack.pick_query or goal.raw_instruction
        query_text = self._single_entity(goal, "search_query") or pick_text
        replacement = (
            request.context_metadata.get("replacement_value", "")
            or self._single_entity(goal, "replacement_value")
        )

        queries = {
            "surface": TargetQuerySpec(role="window"),
            "destination": TargetQuerySpec(text=ready_text),
            "entry_ready": TargetQuerySpec(text=ready_text),
            "search_field": TargetQuerySpec(text="search", role="textbox"),
            "search_result": TargetQuerySpec(text=query_text),
            "candidate": TargetQuerySpec(text=pick_text),
            "selected_state": TargetQuerySpec(text=success_text),
            "candidate_a": TargetQuerySpec(text=pick_text),
            "candidate_b": TargetQuerySpec(text=success_text),
            "editable_target": TargetQuerySpec(text=pick_text),
            "edited_state": TargetQuerySpec(text=replacement or pick_text),
            "form": TargetQuerySpec(role="form"),
            "submit_control": TargetQuerySpec(text=pick_text, role="button"),
            "submission_confirmation": TargetQuerySpec(text=success_text),
            "tool_result": TargetQuerySpec(text=success_text),
        }
        values: dict[str, str] = {}
        if start_url:
            values["entry_url"] = start_url
            values["url"] = start_url
        if replacement:
            values["replacement_value"] = replacement

        allowed = set(_LOCAL_OPS)
        capability = request.capability
        if strategy.route == "tool_action":
            allowed.add(ActionOp.CALL_TOOL)
        if (
            capability.semantic_input is not Capability.UNSUPPORTED
            or capability.physical_input is not Capability.UNSUPPORTED
        ):
            allowed.update(_INTERACTION_OPS)
        return PlanningContext(
            target_queries=queries,
            values=values,
            allowed_ops=frozenset(allowed),
        )

    @staticmethod
    def _joined(values: tuple[str, ...]) -> str:
        return " ".join(item.strip() for item in values if item.strip())

    @staticmethod
    def _single_entity(goal: GoalEnvelope, kind: str) -> str:
        values = goal.entity_values(kind)
        return values[0] if len(values) == 1 else ""
