"""Static regression guards for the command-to-plan runtime boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.AST:
    return ast.parse(_source(relative), filename=relative)


def test_case_runner_is_only_a_command_runtime_adapter():
    path = "hpcu/cases/runner.py"
    tree = _tree(path)
    forbidden_modules = {
        "hpcu.executor",
        "hpcu.gateway",
        "hpcu.grounder",
        "hpcu.input",
        "hpcu.observation",
        "hpcu.verifier",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not {
        module
        for module in imported
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in forbidden_modules
        )
    }

    forbidden_attributes = {
        "call",
        "execute",
        "inject_physical",
        "observe",
        "physical",
        "semantic",
    }
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not (attributes & forbidden_attributes)
    assert "CommandRuntime" in _source(path)
    assert "_case_model_calls" not in _source(path)


def test_product_composition_root_is_unique_for_cases():
    source = _source("hpcu/cases/__main__.py")
    assert source.count("CommandRuntime(") == 1
    assert source.count("CaseRunner(") == 1
    assert "inject_physical(" not in source


@pytest.mark.parametrize(
    "relative",
    [
        "hpcu/compiler/targeting_compiler.py",
        "hpcu/planning/semantic_interrupt.py",
        "hpcu/router/router.py",
    ],
)
def test_provider_response_paths_use_shared_json_framing(relative: str):
    source = _source(relative)
    assert "select_json_object" in source
    assert "json.loads(" not in source
    assert "json.JSONDecoder" not in source
    assert "extract_json_objects" not in source


def test_router_has_no_private_model_call_counter():
    source = _source("hpcu/router/router.py")
    assert "_model_calls_remaining" not in source
    assert "TaskBudgetLedger" in source


def test_control_loop_never_invokes_loop_breaker_with_empty_history():
    source = _source("hpcu/runtime_core/control_loop.py")
    compact = "".join(source.split())
    assert "detect((),())" not in compact
    assert "self._action_history" in source
    assert "self._scene_history" in source


def test_task_results_are_constructed_only_by_terminal_commit():
    tree = _tree("hpcu/runtime_core/task_runtime.py")
    calls_by_function: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.function = "<module>"

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            previous = self.function
            self.function = node.name
            self.generic_visit(node)
            self.function = previous

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == "TaskRunResult":
                calls_by_function.append(self.function)
            self.generic_visit(node)

    Visitor().visit(tree)
    assert calls_by_function == ["_commit_terminal"]


def test_legacy_duplicate_runtime_modules_do_not_reappear():
    forbidden = (
        ".runtime-remediation-ready",
        "hpcu/gateway/budget.py",
        "hpcu/planning/compiler.py",
        "hpcu/planning/interpreter.py",
        "hpcu/runtime_core/performance_trace.py",
        "hpcu/schemas/planning.py",
        "schemas/plan.schema.json",
    )
    assert not [relative for relative in forbidden if (_ROOT / relative).exists()]

    planning = _source("hpcu/planning/__init__.py")
    assert "hpcu.planning.goal_interpreter" in planning
    assert "hpcu.planning.plan_compiler" in planning

    runtime = _source("hpcu/runtime_core/__init__.py")
    assert "hpcu.runtime_core.performance" in runtime
    assert "hpcu.runtime_core.task_budget" in runtime
