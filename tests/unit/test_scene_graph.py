"""Unit tests for the SceneBuilder.

Verifies ObservationDelta -> Scene update and the structure/visual merge.
"""

import pytest

from hpcu.scene_graph.builder import SceneBuilder
from hpcu.schemas.scene import Scene, SceneDelta
from hpcu.schemas.ui_element import ElementSource, UIElement


def make_element(
    element_id: str,
    *,
    scene_version: int = 1,
    source_type: str,
    role: str = "button",
    name: str | None = None,
) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=scene_version,
        role=role,
        name=name,
        sources=(ElementSource(type=source_type),),
    )


def make_empty_scene(version: int = 1) -> Scene:
    return Scene(version=version)


@pytest.mark.unit
def test_update_uses_delta_version_when_monotonic():
    # Given an empty scene at version 10 and a delta at version 12
    builder = SceneBuilder()
    scene = make_empty_scene(version=10)
    delta = SceneDelta(base_version=10, new_version=12)

    # When the scene is updated
    updated = builder.update(scene, delta)

    # Then the new version follows the delta and no elements changed
    assert updated.version == 12
    assert updated.elements == {}


@pytest.mark.unit
def test_update_enforces_monotonic_version():
    # Given a scene at version 5 and a stale delta at version 3
    builder = SceneBuilder()
    scene = make_empty_scene(version=5)
    delta = SceneDelta(base_version=2, new_version=3)

    # When the scene is updated
    updated = builder.update(scene, delta)

    # Then the version is still one above the current (never decreases)
    assert updated.version == 6


@pytest.mark.unit
def test_update_adds_new_structure_elements():
    # Given an empty scene and a delta carrying two structure elements
    builder = SceneBuilder()
    scene = make_empty_scene(version=1)
    delta = SceneDelta(
        base_version=1,
        new_version=2,
        added=(
            make_element("btn_a", source_type="dom"),
            make_element("btn_b", source_type="dom"),
        ),
    )

    # When the scene is updated
    updated = builder.update(scene, delta)

    # Then both elements are added
    assert updated.version == 2
    assert set(updated.elements) == {"btn_a", "btn_b"}
    assert updated.get("btn_a").role == "button"


@pytest.mark.unit
def test_update_merges_structure_and_visual_sources():
    # Given an existing visual element (ocr) and a new structure element (dom)
    builder = SceneBuilder()
    scene = Scene(
        version=1,
        elements={
            "btn": make_element("btn", scene_version=1, source_type="ocr"),
        },
    )
    delta = SceneDelta(
        base_version=1,
        new_version=2,
        modified=(make_element("btn", scene_version=2, source_type="dom"),),
    )

    # When the structure element is merged in
    updated = builder.update(scene, delta)

    # Then the id is preserved and both source types are retained
    assert set(updated.elements) == {"btn"}
    merged = updated.get("btn")
    assert {s.type for s in merged.sources} == {"ocr", "dom"}


@pytest.mark.unit
def test_update_prefers_fresher_content():
    # Given an older visual element and a newer structure element
    builder = SceneBuilder()
    scene = Scene(
        version=1,
        elements={"btn": make_element("btn", scene_version=1, source_type="ocr", role="button", name="구버전")},
    )
    incoming = make_element("btn", scene_version=5, source_type="dom", role="link", name="새버전")
    delta = SceneDelta(base_version=1, new_version=2, modified=(incoming,))

    # When merged
    updated = builder.update(scene, delta)

    # Then the fresher (incoming) content wins
    assert updated.get("btn").role == "link"
    assert updated.get("btn").name == "새버전"


@pytest.mark.unit
def test_update_keeps_existing_when_it_is_fresher():
    # Given a newer existing element and a stale incoming structure element
    builder = SceneBuilder()
    scene = Scene(
        version=1,
        elements={"btn": make_element("btn", scene_version=9, source_type="ocr", role="textbox")},
    )
    stale = make_element("btn", scene_version=2, source_type="dom", role="link")
    delta = SceneDelta(base_version=1, new_version=2, modified=(stale,))

    # When merged
    updated = builder.update(scene, delta)

    # Then the fresher existing content is kept and sources are unioned
    merged = updated.get("btn")
    assert merged.role == "textbox"
    assert {s.type for s in merged.sources} == {"ocr", "dom"}


@pytest.mark.unit
def test_update_preserves_frame_when_delta_has_none():
    # Given a scene carrying a frame and a delta without one
    builder = SceneBuilder()
    scene = Scene(version=1, window_title="로그인")
    delta = SceneDelta(base_version=1, new_version=2)

    # When updated
    updated = builder.update(scene, delta)

    # Then the window title is preserved and frame remains None
    assert updated.window_title == "로그인"
    assert updated.frame is None


@pytest.mark.unit
def test_update_is_immutable_never_mutates_input():
    # Given an existing scene
    builder = SceneBuilder()
    original = make_empty_scene(version=1)
    delta = SceneDelta(base_version=1, new_version=2)

    # When updated
    updated = builder.update(original, delta)

    # Then the original scene object is untouched
    assert original.version == 1
    assert updated.version == 2
    assert updated is not original


@pytest.mark.unit
def test_update_removes_elements():
    builder = SceneBuilder()
    scene = Scene(
        version=1,
        elements={
            "keep": make_element("keep", source_type="dom"),
            "gone": make_element("gone", source_type="dom"),
        },
    )
    updated = builder.update(
        scene,
        SceneDelta(base_version=1, new_version=2, removed=("gone",)),
    )
    assert "gone" not in updated
    assert "keep" in updated


@pytest.mark.unit
def test_scene_elements_are_immutable():
    scene = Scene(
        version=1,
        elements={"a": make_element("a", source_type="dom")},
    )
    with pytest.raises(TypeError):
        scene.elements["b"] = make_element("b", source_type="dom")
