"""Popup, overlay and modal detection and dismissal.

PopupRecovery is a common, platform-independent layer.  It inspects the
current Scene for popup-like elements, selects the top-most one and
dispatches a dismissal through an injected executor (never the OS).
"""

from typing import Protocol

from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement

# Roles that identify popup-like elements in the scene graph.
POPUP_ROLES = ("dialog", "popup", "modal", "tooltip", "menu", "overlay")

# Name/text hints that identify a dismiss/close affordance.
CLOSE_HINTS = ("close", "dismiss", "x", "\u00d7", "cancel", "done", "ok", "confirm")


class PopupExecutor(Protocol):
    """The minimal executor surface PopupRecovery needs for dismissal."""

    def dismiss(self, element_id: str) -> bool:
        """Attempt to dismiss the given element; return whether it succeeded."""
        ...

    def click(self, element_id: str) -> bool:
        """Fallback: click the given element; return whether it succeeded."""
        ...


def is_popup_element(element: UIElement) -> bool:
    """Return True if a scene element looks like a popup/overlay/modal."""
    if element.role.lower() in POPUP_ROLES:
        return True
    if element.relations.overlays:
        return True
    return element.relations.modal_owner is not None


class PopupRecovery:
    """Detect and dismiss popups, overlays and modals in a captured Scene."""

    def detect(self, scene: Scene) -> list[UIElement]:
        """Return the popup-like elements of a scene, condensed deterministic."""
        candidates = [
            element
            for element in scene.elements.values()
            if is_popup_element(element)
        ]
        # Deterministic order: top-most first (covers the most), then by id.
        return sorted(
            candidates,
            key=lambda element: (-len(element.relations.overlays), element.id),
        )

    def dismiss(self, scene: Scene, executor: PopupExecutor) -> bool:
        """Dismiss the top-most popup via ``executor``.

        Returns True if a popup was found and a dismissal was attempted
        (and reported successful), otherwise False.
        """
        popups = self.detect(scene)
        if not popups:
            return False
        primary = popups[0]
        target = self._resolve_dismiss_target(scene, primary)
        method = getattr(executor, "dismiss", None)
        if method is None:
            method = getattr(executor, "click", None)
        if method is None:
            raise ValueError("executor must implement dismiss() or click()")
        return bool(method(target.id))

    def _resolve_dismiss_target(self, scene: Scene, popup: UIElement) -> UIElement:
        """Find the closest close-button for a popup, else the popup itself."""
        covered = set(popup.relations.overlays)
        for element in sorted(scene.elements.values(), key=lambda e: e.id):
            if element.id == popup.id:
                continue
            if element.role.lower() != "button":
                continue
            label = self._label(element)
            if any(hint in label for hint in CLOSE_HINTS):
                if element.id in covered or covered == set():
                    return element
        return popup

    @staticmethod
    def _label(element: UIElement) -> str:
        parts = [part for part in (element.name, element.text) if part]
        return " ".join(parts).lower()
