import pytest

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.region import RegionKind, RegionNode

pytestmark = pytest.mark.unit


def _box():
    return BoundingBox(CoordinateSpace.SCREEN_PHYSICAL_PX, 10, 20, 100, 40)


def test_region_node_accepts_only_neutral_layout_kind():
    region = RegionNode(
        id="region-1",
        scene_version=3,
        bbox=_box(),
        kind="text_cluster",
        child_ids=("child-a", "child-b"),
    )
    assert region.kind is RegionKind.TEXT_CLUSTER
    assert region.child_ids == ("child-a", "child-b")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"id": " "}, "non-empty"),
        ({"scene_version": -1}, "scene_version"),
        ({"bbox": BoundingBox(CoordinateSpace.SCREEN_PHYSICAL_PX, 0, 0, 0, 10)}, "positive"),
        ({"parent_id": "region-1"}, "own parent"),
        ({"child_ids": ("region-1",)}, "contain itself"),
        ({"child_ids": ("x", "x")}, "unique"),
        ({"source": " "}, "source"),
        ({"stable_frames": -1}, "stable_frames"),
    ],
)
def test_region_node_fails_closed_on_invalid_geometry_or_identity(kwargs, message):
    base = dict(id="region-1", scene_version=1, bbox=_box())
    base.update(kwargs)
    with pytest.raises(ValueError, match=message):
        RegionNode(**base)


def test_region_node_rejects_semantic_pixel_only_kind():
    with pytest.raises(ValueError):
        RegionNode(id="r", scene_version=1, bbox=_box(), kind="button")
