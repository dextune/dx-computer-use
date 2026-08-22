"""Consent / cookie-banner targets from scene text. Platform-agnostic.

These are thin wrappers over GenericMatcher — no hardcoded site dictionaries.
"""

from hpcu.perception.matcher import GenericMatcher
from hpcu.schemas.scene import Scene
from hpcu.schemas.targeting import TargetingPack
from hpcu.schemas.ui_element import UIElement


def is_blocked_scene(scene: Scene, pack: TargetingPack) -> bool:
    """True when the scene matches the pack's blocked_any tokens."""
    if not pack.blocked_any:
        return False
    matcher = GenericMatcher(pack)
    return matcher.blocked(scene)


def consent_elements(scene: Scene, pack: TargetingPack) -> list[UIElement]:
    """Elements whose text matches the pack's dismiss_any tokens."""
    matcher = GenericMatcher(pack)
    return matcher.consent_candidates(scene)