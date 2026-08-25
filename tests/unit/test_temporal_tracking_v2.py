"""SUE-4 composite temporal identity qualification coverage."""

from dataclasses import replace

import pytest

from hpcu.observation.facade import CompositeObserver
from hpcu.observation.structure_observer import (
    StructureCapabilities,
    StructureObserver,
)
from hpcu.scene_graph.tracker import ElementTracker
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.ui_element import ElementSource, UIElement

pytestmark = pytest.mark.unit
_SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


def _box(
    x: float,
    y: float = 0,
    width: float = 100,
    height: float = 40,
) -> BoundingBox:
    return BoundingBox(_SPACE, x, y, width, height)


def _element(
    element_id: str,
    *,
    x: float = 0,
    y: float = 0,
    text: str = "Save",
    role: str = "button",
    source_ref: str | None = None,
    fingerprint: str | None = None,
    stable_frames: int = 0,
) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=1,
        role=role,
        name=text,
        text=text,
        bbox=_box(x, y),
        sources=(ElementSource(type="atspi", ref=source_ref),),
        fingerprint=fingerprint,
        stable_frames=stable_frames,
    )


def test_exact_source_ref_survives_native_id_fingerprint_and_geometry_change():
    tracker = ElementTracker()
    current = {
        "native-new": _element(
            "native-new",
            x=500,
            source_ref="save-control",
            fingerprint="fp-new",
        )
    }
    previous = {
        "stable": _element(
            "stable",
            source_ref="save-control",
            fingerprint="fp-old",
        )
    }

    assert tracker.match(current, previous) == {"native-new": "stable"}


def test_exact_fingerprint_survives_geometry_change_without_conflicting_evidence():
    tracker = ElementTracker()
    current = {
        "native-new": _element(
            "native-new",
            x=500,
            source_ref=None,
            fingerprint="stable-fp",
        )
    }
    previous = {
        "stable": _element(
            "stable",
            source_ref=None,
            fingerprint="stable-fp",
        )
    }

    assert tracker.match(current, previous) == {"native-new": "stable"}


def test_composite_tracking_keeps_identity_across_small_text_and_position_change():
    tracker = ElementTracker()
    current = {
        "native-new": _element(
            "native-new",
            x=25,
            text="Results 11",
        )
    }
    previous = {
        "stable": _element(
            "stable",
            text="Results 10",
        )
    }

    assert tracker.match(current, previous) == {"native-new": "stable"}


def test_composite_tracking_rejects_far_replacement_with_same_text_and_role():
    tracker = ElementTracker()
    current = {"native-new": _element("native-new", x=600)}
    previous = {"stable": _element("stable")}

    assert tracker.match(current, previous) == {}


def test_dirty_roi_bounds_composite_assignment_to_impacted_area():
    tracker = ElementTracker()
    current = {"native-new": _element("native-new", x=10)}
    previous = {"stable": _element("stable")}
    dirty = BoundingBox(_SPACE, 600, 500, 20, 20)

    assert tracker.match_composite(
        current,
        previous,
        dirty_rects=(dirty,),
    ) == {}


def test_ambiguous_composite_assignment_is_rejected_instead_of_forced():
    tracker = ElementTracker()
    current = {
        "c1": _element("c1"),
        "c2": _element("c2"),
    }
    previous = {
        "p1": _element("p1"),
        "p2": _element("p2"),
    }

    assert tracker.match_composite(current, previous) == {}


def test_100_frame_fixture_keeps_one_stable_identity_without_false_carry_over():
    tracker = ElementTracker()
    previous = {"stable": _element("stable", text="Timer 0", stable_frames=1)}

    for frame_index in range(1, 101):
        observed = _element(
            f"native-{frame_index}",
            x=float(frame_index % 4),
            text=f"Timer {frame_index}",
        )
        current = {observed.id: observed}
        match = tracker.match(current, previous)
        assert match == {observed.id: "stable"}
        previous = {
            "stable": replace(
                observed,
                id="stable",
                stable_frames=frame_index + 1,
            )
        }


class _MovingStructure(StructureObserver):
    def __init__(self) -> None:
        super().__init__("temporal-session")
        self.calls = 0

    async def observe_structure(self) -> tuple[UIElement, ...]:
        self.calls += 1
        return (
            _element(
                f"native-{self.calls}",
                x=float(self.calls * 4),
                text=f"Count {self.calls}",
            ),
        )

    def capabilities(self) -> StructureCapabilities:
        return StructureCapabilities(tree=Capability.SUPPORTED)


async def test_observer_uses_composite_tracking_before_scene_delta_diff():
    observer = CompositeObserver(
        "temporal-session",
        structure_observer=_MovingStructure(),
    )

    first = await observer.observe()
    second = await observer.observe()

    assert len(first.added) == 1
    assert first.added[0].id == "native-1"
    assert first.added[0].stable_frames == 1
    assert second.added == ()
    assert second.removed == ()
    assert len(second.modified) == 1
    assert second.modified[0].id == "native-1"
    assert second.modified[0].stable_frames == 2
    assert second.modified[0].first_seen_at == first.added[0].first_seen_at


class _ReusedNativeIdStructure(StructureObserver):
    def __init__(self) -> None:
        super().__init__("temporal-session")
        self.calls = 0

    async def observe_structure(self) -> tuple[UIElement, ...]:
        self.calls += 1
        return (
            _element(
                "slot",
                x=0 if self.calls == 1 else 700,
                text="Save",
            ),
        )

    def capabilities(self) -> StructureCapabilities:
        return StructureCapabilities(tree=Capability.SUPPORTED)


async def test_observer_marks_reused_native_id_as_remove_plus_add():
    observer = CompositeObserver(
        "temporal-session",
        structure_observer=_ReusedNativeIdStructure(),
    )

    first = await observer.observe()
    second = await observer.observe()

    assert first.added[0].id == "slot"
    assert second.removed == ("slot",)
    assert len(second.added) == 1
    assert second.added[0].id.startswith("slot@2")
    assert second.added[0].stable_frames == 1
