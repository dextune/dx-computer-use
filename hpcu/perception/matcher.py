"""GenericMatcher — pack-only scene matching, no site dictionaries.

Accepts a TargetingPack and a Scene, returns matching results driven
by the pack's tokens using normalize_ocr_text for comparison.
"""

from __future__ import annotations

from hpcu.perception.engine import normalize_ocr_text
from hpcu.perception.window_chrome import is_in_chrome, primary_window
from hpcu.schemas.scene import Scene
from hpcu.schemas.targeting import TargetingPack
from hpcu.schemas.ui_element import UIElement


def _scene_blob(scene: Scene) -> str:
    """Full OCR+window-title blob for token matching."""
    return " ".join(
        (element.text or element.name or "") for element in scene.elements.values()
    )


def _normalized_blob(scene: Scene) -> str:
    return normalize_ocr_text(_scene_blob(scene)).replace(" ", "")


def mentions(scene: Scene, tokens: tuple[str, ...]) -> bool:
    """True when any token appears in the scene blob."""
    if not tokens:
        return True
    blob = _normalized_blob(scene)
    return any(normalize_ocr_text(token).replace(" ", "") in blob for token in tokens)


class GenericMatcher:
    """Match scenes against a TargetingPack — no hardcoded 사전.

    All decisions are driven by the pack's tokens.  The same scene
    with a different pack produces different outcomes.
    """

    def __init__(self, pack: TargetingPack):
        self._pack = pack

    @property
    def pack(self) -> TargetingPack:
        return self._pack

    def mentions(self, scene: Scene, tokens: tuple[str, ...]) -> bool:
        return mentions(scene, tokens)

    def blocked(self, scene: Scene) -> bool:
        return mentions(scene, self._pack.blocked_any)

    def consent_candidates(self, scene: Scene) -> list[UIElement]:
        if not self._pack.dismiss_any:
            return []
        normalized_tokens = [
            normalize_ocr_text(token).replace(" ", "")
            for token in self._pack.dismiss_any
        ]
        matches: list[UIElement] = []
        for element in scene.elements.values():
            if element.bbox is None:
                continue
            blob = normalize_ocr_text(
                f"{element.name or ''} {element.text or ''}"
            ).replace(" ", "")
            if any(token in blob for token in normalized_tokens):
                if not any(
                    forbid in blob
                    for forbid in [
                        normalize_ocr_text(t).replace(" ", "")
                        for t in self._pack.forbid_any
                    ]
                ):
                    matches.append(element)
        return matches

    def pick_candidates(self, scene: Scene) -> list[UIElement]:
        if not self._pack.pick_query:
            return []
        query = normalize_ocr_text(self._pack.pick_query).replace(" ", "")
        window = primary_window(scene)
        if window is not None and window.role != "window":
            window = None
        matches: list[UIElement] = []
        for element in scene.elements.values():
            if element.bbox is None or (
                window is not None and is_in_chrome(element, window)
            ):
                continue
            blob = normalize_ocr_text(
                f"{element.text or ''} {element.name or ''}"
            ).replace(" ", "")
            if query in blob:
                if not any(
                    normalize_ocr_text(ignore).replace(" ", "") in blob
                    for ignore in self._pack.ignore_any
                ):
                    matches.append(element)
        matches.sort(key=lambda element: (element.bbox.y, element.bbox.x))
        return matches
