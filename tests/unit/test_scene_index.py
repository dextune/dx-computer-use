import pytest

from hpcu.scene_graph.index import IndexedScene, SceneIndex, StaleSceneIndexError
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement

pytestmark = pytest.mark.unit


def _element(element_id, x, y, width=40, height=20, *, role="text", text=None, space=CoordinateSpace.SCREEN_PHYSICAL_PX):
    return UIElement(
        id=element_id,
        scene_version=7,
        role=role,
        text=text,
        bbox=BoundingBox(space, x, y, width, height),
    )


def _scene():
    elements = {
        "save": _element("save", 140, 20, role="button", text="저 장"),
        "cancel": _element("cancel", 220, 20, role="button", text="취소"),
        "label": _element("label", 20, 20, role="text", text="Save changes"),
        "lower": _element("lower", 20, 180, role="button", text="Save"),
        "css": _element("css", 10, 10, role="button", text="Save", space=CoordinateSpace.CSS_PX),
    }
    return Scene(version=7, elements=elements)


def test_role_and_text_indexes_are_deterministic_and_normalized():
    index = SceneIndex(_scene(), cell_size_px=64)
    assert index.by_role(" BUTTON ") == ("css", "save", "cancel", "lower")
    assert index.by_text_token("저장") == ("save",)
    assert index.by_text_token("SAVE") == ("css", "label", "lower")
    assert index.by_text_token("save changes") == ("label",)


def test_spatial_queries_use_grid_but_preserve_coordinate_space():
    index = SceneIndex(_scene(), cell_size_px=64)
    query = BoundingBox(CoordinateSpace.SCREEN_PHYSICAL_PX, 0, 0, 300, 80)
    assert index.inside(query) == ("label", "save", "cancel")
    assert index.intersects(query) == ("label", "save", "cancel")
    assert "css" not in index.inside(query)


def test_near_matches_bruteforce_distance_boundary_and_rejects_negative_radius():
    index = SceneIndex(_scene(), cell_size_px=32)
    anchor = BoundingBox(CoordinateSpace.SCREEN_PHYSICAL_PX, 80, 20, 20, 20)
    assert index.near(anchor, 40) == ("label", "save")
    with pytest.raises(ValueError, match="radius_px"):
        index.near(anchor, -1)


def test_right_of_orders_by_horizontal_gap_then_vertical_distance():
    index = SceneIndex(_scene(), cell_size_px=64)
    assert index.right_of("label")[:2] == ("save", "cancel")
    assert index.right_of("missing") == ()


def test_indexed_scene_rejects_stale_scene_version():
    indexed = IndexedScene.build(_scene(), cell_size_px=64)
    indexed.assert_scene_version(7)
    with pytest.raises(StaleSceneIndexError, match="expected 8"):
        indexed.assert_scene_version(8)


def test_index_configuration_is_explicit():
    config = {"scene_graph": {"index_cell_size_px": 96}}
    indexed = IndexedScene.from_config(_scene(), config)
    assert indexed.index.cell_size_px == 96
