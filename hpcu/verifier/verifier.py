"""Verifier — evaluate EvidenceContract and postconditions against a Scene.

Every EvidenceKind maps to a deterministic scene check.  Completion kinds
that a bare Scene cannot prove (file existence, API status) evaluate to
False — the runtime never claims success it cannot substantiate from the
current screen state.
"""

import re
from typing import Optional

from hpcu.schemas.action import Postcondition, PostconditionKind
from hpcu.schemas.evidence import EvidenceCondition, EvidenceContract, EvidenceKind
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement

_INT_PATTERN = re.compile(r"(\d+)")


class Verifier:
    """Pure scene evaluator for completion and postcondition evidence.

    Carries no platform dependencies and never calls the model.
    """

    def verify(self, contract: EvidenceContract, scene: Scene) -> bool:
        """Return True when the contract is fully satisfied by the scene.

        All conditions in `contract.all` must hold; otherwise any condition
        in `contract.any` must hold.  An empty-conditions contract cannot be
        constructed (the schema rejects it) and evaluates to False.
        """
        if contract.all:
            return all(
                self._check_evidence_condition(cond, scene) for cond in contract.all
            )
        if contract.any:
            return any(
                self._check_evidence_condition(cond, scene) for cond in contract.any
            )
        return False

    def verify_postconditions(
        self,
        postconditions: tuple[Postcondition, ...],
        scene: Scene,
        last_action: Optional[object] = None,
    ) -> bool:
        """Return True when every postcondition holds on the scene.

        `last_action` is accepted for context/trace purposes but the
        verdict depends only on the current scene state.
        """
        if not postconditions:
            return True
        return all(
            self._check_postcondition(cond, scene) for cond in postconditions
        )

    # ------------------------------------------------------------------
    # EvidenceCondition -> scene check
    # ------------------------------------------------------------------

    def _check_evidence_condition(
        self, condition: EvidenceCondition, scene: Scene
    ) -> bool:
        kind = condition.kind
        target = condition.target
        product_match = condition.product_match
        value = condition.value

        if kind is EvidenceKind.ELEMENT_VISIBLE:
            element = self._locate(scene, target, product_match)
            return self._is_visible(element)

        if kind is EvidenceKind.ELEMENT_ABSENT:
            return target not in scene

        if kind is EvidenceKind.TEXT_EQUALS:
            element = self._locate(scene, target, product_match)
            if element is None:
                return False
            return self._element_text(element) == str(value)

        if kind is EvidenceKind.STATE_MATCHES:
            element = self._locate(scene, target, product_match)
            return self._state_matches(element, value)

        if kind is EvidenceKind.CART_CONTAINS:
            if product_match:
                element = self._find_by_product(scene, product_match)
            else:
                element = self._locate(scene, target, None)
            return element is not None

        if kind in (EvidenceKind.QUANTITY_AT_LEAST, EvidenceKind.BADGE_VALUE):
            if product_match:
                element = self._find_by_product(scene, product_match)
            else:
                element = self._locate(scene, target, None)
            if element is None:
                return False
            return self._extract_int(self._element_text(element)) >= (value or 0)

        if kind is EvidenceKind.NOTIFICATION_SHOWN:
            element = self._locate(scene, target, product_match)
            return self._is_visible(element)

        # file / API evidence cannot be proven from a bare Scene snapshot.
        return False

    # ------------------------------------------------------------------
    # Postcondition -> scene check
    # ------------------------------------------------------------------

    def _check_postcondition(
        self, postcondition: Postcondition, scene: Scene
    ) -> bool:
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
            if element is None:
                return False
            return self._element_text(element) == str(postcondition.value)

        if kind is PostconditionKind.STATE_MATCHES:
            # value truthiness maps to the element's selected state.
            return self._state_matches(
                scene.get(target) if target else None, postcondition.value
            )

        if kind is PostconditionKind.ELEMENT_ABSENT:
            return target not in scene

        if kind is PostconditionKind.ELEMENT_COUNT_AT_LEAST:
            count = self._count_matching(scene, target)
            return count >= (postcondition.value or 0)

        # file / API postconditions are not provable from the scene snapshot.
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _element_text(element: Optional[UIElement]) -> str:
        if element is None:
            return ""
        return str(element.text if element.text is not None else element.name or "")

    @staticmethod
    def _is_visible(element: Optional[UIElement]) -> bool:
        return element is not None and element.state.visible

    @staticmethod
    def _state_matches(
        element: Optional[UIElement], value: Optional[int]
    ) -> bool:
        if element is None:
            return False
        return element.state.selected == bool(value)

    @staticmethod
    def _extract_int(text: str) -> int:
        match = _INT_PATTERN.search(text)
        return int(match.group(1)) if match else 0

    @classmethod
    def _locate(
        cls, scene: Scene, target: Optional[str], product_match: Optional[str]
    ) -> Optional[UIElement]:
        """Resolve an element by id or product name, with id precedence."""
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
        """Find the first element whose text/name mentions the product."""
        needle = product_match.casefold()
        for element in scene.elements.values():
            if needle in Verifier._element_text(element).casefold():
                return element
        return None

    @staticmethod
    def _count_matching(scene: Scene, target: Optional[str]) -> int:
        """Count elements whose role, name, or source type equals target."""
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
