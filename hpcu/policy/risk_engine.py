"""Risk evaluation for actions and approval requirements.

RiskEngine classifies an Action against the current Scene into one of
four RiskLevels.  High and critical risks require human approval.
"""

from enum import Enum

from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement

# Ops that never mutate durable user data and are therefore LOW risk.
LOW_RISK_OPS = frozenset(
    {
        ActionOp.LAUNCH_APPLICATION,
        ActionOp.FOCUS_WINDOW,
        ActionOp.NAVIGATE,
        ActionOp.ASSERT,
        ActionOp.READ,
        ActionOp.WAIT_UNTIL,
        ActionOp.CHECKPOINT,
    }
)

# Ops that can mutate the world by arbitrary side effects.
HIGH_RISK_OPS = frozenset({ActionOp.CALL_TOOL})

# Semantic tags that flag an element as destructive or dangerous.
DESTRUCTIVE_SEMANTIC_TAGS = ("destructive", "danger", "dangerous", "irreversible")

# Element labels that flag an irreversible, high-stakes action.
IRREVERSIBLE_TEXT_HINTS = ("delete", "remove", "destroy", "deactivate", "submit")

# Element labels that flag a financial/transactional action (highest risk).
PAYMENT_TEXT_HINTS = ("purchase", "buy", "pay", "checkout", "order", "checkout now")


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskEngine:
    """Evaluate the risk level of an Action and whether approval is needed."""

    def assess(self, action: Action, scene: Scene) -> RiskLevel:
        """Return the RiskLevel of an Action against the current Scene."""
        if action.op in LOW_RISK_OPS:
            return RiskLevel.LOW

        element = self._target_element(action, scene)
        text = self._element_text(element)
        tags = element.semantic_tags if element is not None else ()

        if action.op in HIGH_RISK_OPS:
            if self._has_payment_text(text):
                return RiskLevel.CRITICAL
            if self._has_destructive_signal(text, tags):
                return RiskLevel.HIGH
            return RiskLevel.HIGH

        if self._has_payment_text(text):
            return RiskLevel.CRITICAL
        if self._has_destructive_signal(text, tags):
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM

    def require_approval(self, risk_level: RiskLevel) -> bool:
        """True when an action at this risk level needs human approval."""
        return risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

    def allow(self, action: Action, scene: Scene) -> bool:
        """True when the action may execute without a human pause."""
        return not self.require_approval(self.assess(action, scene))

    @staticmethod
    def _target_element(action: Action, scene: Scene) -> UIElement | None:
        target_id = action.target.element_id
        if target_id is None:
            return None
        return scene.elements.get(target_id)

    @staticmethod
    def _element_text(element: UIElement | None) -> str:
        if element is None:
            return ""
        parts = [part for part in (element.name, element.text) if part]
        return " ".join(parts).lower()

    @staticmethod
    def _has_payment_text(text: str) -> bool:
        return any(hint in text for hint in PAYMENT_TEXT_HINTS)

    @staticmethod
    def _has_destructive_signal(text: str, tags) -> bool:
        if any(tag in tags for tag in DESTRUCTIVE_SEMANTIC_TAGS):
            return True
        return any(hint in text for hint in IRREVERSIBLE_TEXT_HINTS)
