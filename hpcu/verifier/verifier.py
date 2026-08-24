"""Verifier — deterministic evidence and transition verification."""

import re
from typing import Optional

from hpcu.schemas.action import Action, ActionOp, Postcondition, PostconditionKind
from hpcu.schemas.evidence import EvidenceCondition, EvidenceContract, EvidenceKind
from hpcu.schemas.evidence_state import (
    EvidenceObservation,
    EvidenceState,
    EvidenceStatus,
)
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement

_INT_PATTERN = re.compile(r"(\d+)")
_IDEMPOTENT_STATE_OPS = frozenset({ActionOp.LAUNCH_APPLICATION})
_SIDE_EFFECT_OPS = frozenset(
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
        ActionOp.CALL_TOOL,
        ActionOp.REQUEST_APPROVAL,
    }
)


class Verifier:
    """Pure scene evaluator; model claims are never accepted as evidence."""

    def verify(self, contract: EvidenceContract, scene: Scene) -> bool:
        return self.verify_state(contract, scene).satisfied

    def verify_state(self, contract: EvidenceContract, scene: Scene) -> EvidenceState:
        conditions = contract.all or contract.any
        if not conditions:
            return EvidenceState(
                scene_version=scene.version,
                status=EvidenceStatus.UNSATISFIED,
            )
        observations = tuple(
            EvidenceObservation(
                condition_index=index,
                status=(
                    EvidenceStatus.SATISFIED
                    if self._check_evidence_condition(condition, scene)
                    else EvidenceStatus.UNSATISFIED
                ),
                scene_version=scene.version,
            )
            for index, condition in enumerate(conditions)
        )
        checks = [item.status is EvidenceStatus.SATISFIED for item in observations]
        satisfied = all(checks) if contract.all else any(checks)
        return EvidenceState(
            scene_version=scene.version,
            status=(
                EvidenceStatus.SATISFIED
                if satisfied
                else EvidenceStatus.UNSATISFIED
            ),
            observations=observations,
        )

    def verify_postconditions(
        self,
        postconditions: tuple[Postcondition, ...],
        scene: Scene,
        last_action: Optional[object] = None,
    ) -> bool:
        del last_action
        if not postconditions:
            return True
        return all(self._check_postcondition(cond, scene) for cond in postconditions)

    def verify_transition(self, action: Action, before: Scene, after: Scene) -> bool:
        """Prove an action from fresh post-action scene evidence.

        Mutating actions require explicit postconditions that were false before
        execution and true afterward. Idempotent desired-state actions such as
        application launch may already be satisfied before execution, so they
        require a fresh post-action observation without the false-before rule.
        """
        if action.op in _IDEMPOTENT_STATE_OPS:
            return bool(action.postconditions) and self.verify_postconditions(
                action.postconditions, after
            )
        if action.op not in _SIDE_EFFECT_OPS:
            return self.verify_postconditions(action.postconditions, after)
        if not action.postconditions:
            return False
        if self.verify_postconditions(action.postconditions, before):
            return False
        return self.verify_postconditions(action.postconditions, after)

    def _check_evidence_condition(
        self, condition: EvidenceCondition, scene: Scene
    ) -> bool:
        kind = condition.kind
        target = condition.target
        product_match = condition.product_match
        value = condition.value
        if kind is EvidenceKind.ELEMENT_VISIBLE:
            return self._is_visible(self._locate(scene, target, product_match))
        if kind is EvidenceKind.ELEMENT_ABSENT:
            return target not in scene
        if kind is EvidenceKind.TEXT_EQUALS:
            element = self._locate(scene, target, product_match)
            return element is not None and self._element_text(element) == str(value)
        if kind is EvidenceKind.STATE_MATCHES:
            return self._state_matches(
                self._locate(scene, target, product_match), value
            )
        if kind is EvidenceKind.CART_CONTAINS:
            element = (
                self._find_by_product(scene, product_match)
                if product_match
                else self._locate(scene, target, None)
            )
            return element is not None
        if kind in (EvidenceKind.QUANTITY_AT_LEAST, EvidenceKind.BADGE_VALUE):
            element = (
                self._find_by_product(scene, product_match)
                if product_match
                else self._locate(scene, target, None)
            )
            return (
                element is not None
                and self._extract_int(self._element_text(element)) >= (value or 0)
            )
        if kind is EvidenceKind.NOTIFICATION_SHOWN:
            return self._is_visible(self._locate(scene, target, product_match))
        return False

    def _check_postcondition(self, postcondition: Postcondition, scene: Scene) -> bool:
        kind = postcondition.kind
        target = postcondition.target
        if kind is PostconditionKind.ELEMENT_VISIBLE:
            return self._is_visible(scene.get(target)) if target else False
        if kind is PostconditionKind.ELEMENT_ENABLED:
            element = scene.get(target) if target else None
            return element is not None and element.state.enabled
        if kind is PostconditionKind.ELEMENT_FOCUSED:
            element = scene.get(target) if target else None
            return element is not None and element.state.selected
        if kind is PostconditionKind.TEXT_EQUALS:
            element = scene.get(target) if target else None
            return (
                element is not None
                and self._element_text(element) == str(postcondition.value)
            )
        if kind is PostconditionKind.STATE_MATCHES:
            return self._state_matches(
                scene.get(target) if target else None, postcondition.value
            )
        if kind is PostconditionKind.ELEMENT_ABSENT:
            return target not in scene
        if kind is PostconditionKind.ELEMENT_COUNT_AT_LEAST:
            return self._count_matching(scene, target) >= (postcondition.value or 0)
        return False

    @staticmethod
    def _element_text(element: Optional[UIElement]) -> str:
        if element is None:
            return ""
        return str(element.text if element.text is not None else element.name or "")

    @staticmethod
    def _is_visible(element: Optional[UIElement]) -> bool:
        return element is not None and element.state.visible

    @staticmethod
    def _state_matches(element: Optional[UIElement], value: Optional[int]) -> bool:
        return element is not None and element.state.selected == bool(value)

    @staticmethod
    def _extract_int(text: str) -> int:
        match = _INT_PATTERN.search(text)
        return int(match.group(1)) if match else 0

    @classmethod
    def _locate(
        cls, scene: Scene, target: Optional[str], product_match: Optional[str]
    ) -> Optional[UIElement]:
        if target and target in scene:
            element = scene.get(target)
            if product_match is None:
                return element
            if product_match in cls._element_text(element):
                return element
        if product_match:
            return cls._find_by_product(scene, product_match)
        return scene.get(target) if target else None

    @staticmethod
    def _find_by_product(scene: Scene, product_match: str) -> Optional[UIElement]:
        needle = product_match.casefold()
        for element in scene.elements.values():
            if needle in Verifier._element_text(element).casefold():
                return element
        return None

    @staticmethod
    def _count_matching(scene: Scene, target: Optional[str]) -> int:
        if target is None:
            return len(scene.elements)
        count = 0
        for element in scene.elements.values():
            if element.role == target or element.name == target:
                count += 1
                continue
            if any(source.type == target for source in element.sources):
                count += 1
        return count
