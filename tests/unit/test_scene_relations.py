import pytest

from hpcu.scene_graph.relations import RelationPolicy, derive_spatial_relations
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.ui_element import ElementRelations, UIElement

pytestmark = pytest.mark.unit

POLICY = RelationPolicy(
    containment_ratio=0.95,
    row_center_tolerance_px=6,
    column_center_tolerance_px=6,
    axis_overlap_ratio=0.50,
    gap_tolerance_px=2,
)


def _element(element_id, x, y, width, height, *, space=CoordinateSpace.SCREEN_PHYSICAL_PX, relations=None):
    return UIElement(
        id=element_id,
        scene_version=4,
        bbox=BoundingBox(space, x, y, width, height),
        relations=relations or ElementRelations(),
    )


def _by_id(elements):
    return {element.id: element for element in elements}


def test_relations_derive_smallest_parent_and_contains():
    elements = derive_spatial_relations(
        (
            _element("window", 0, 0, 400, 300),
            _element("panel", 20, 20, 200, 120),
            _element("child", 40, 40, 40, 20),
        ),
        POLICY,
    )
    by_id = _by_id(elements)
    assert by_id["child"].relations.parent == "panel"
    assert by_id["panel"].relations.parent == "window"
    assert by_id["panel"].relations.contains == ("child",)
    assert by_id["window"].relations.contains == ("child", "panel")


def test_relations_derive_row_direction_and_column_direction():
    elements = derive_spatial_relations(
        (
            _element("left", 10, 10, 40, 20),
            _element("right", 80, 10, 40, 20),
            _element("bottom", 10, 70, 40, 20),
        ),
        POLICY,
    )
    by_id = _by_id(elements)
    assert "right" in by_id["left"].relations.same_row
    assert by_id["left"].relations.left_of == ("right",)
    assert by_id["right"].relations.right_of == ("left",)
    assert "bottom" in by_id["left"].relations.same_column
    assert by_id["left"].relations.above == ("bottom",)
    assert by_id["bottom"].relations.below == ("left",)


def test_relations_do_not_cross_coordinate_spaces():
    elements = derive_spatial_relations(
        (
            _element("screen", 10, 10, 20, 20),
            _element("css", 40, 10, 20, 20, space=CoordinateSpace.CSS_PX),
        ),
        POLICY,
    )
    by_id = _by_id(elements)
    assert by_id["screen"].relations.left_of == ()
    assert by_id["css"].relations.right_of == ()


def test_relations_preserve_structure_owned_relations():
    explicit = ElementRelations(parent="dom-parent", label_for="field")
    elements = derive_spatial_relations(
        (
            _element("label", 10, 10, 40, 20, relations=explicit),
            _element("other", 80, 10, 40, 20),
        ),
        POLICY,
    )
    by_id = _by_id(elements)
    assert by_id["label"].relations.parent == "dom-parent"
    assert by_id["label"].relations.label_for == "field"
    assert by_id["label"].relations.left_of == ("other",)


def test_relation_policy_reads_runtime_configuration_and_validates_thresholds():
    config = {
        "scene_graph": {
            "relations": {
                "containment_ratio": 0.9,
                "row_center_tolerance_px": 8,
                "column_center_tolerance_px": 8,
                "axis_overlap_ratio": 0.5,
                "gap_tolerance_px": 3,
            }
        }
    }
    policy = RelationPolicy.from_config(config)
    assert policy.containment_ratio == 0.9
    with pytest.raises(ValueError, match="axis_overlap_ratio"):
        RelationPolicy(0.9, 1, 1, 1.1, 0)


def test_equal_geometry_does_not_create_parent_cycle():
    elements = derive_spatial_relations(
        (
            _element("a", 10, 10, 40, 20),
            _element("b", 10, 10, 40, 20),
        ),
        POLICY,
    )
    by_id = _by_id(elements)
    assert by_id["a"].relations.parent is None
    assert by_id["b"].relations.parent is None
    assert by_id["a"].relations.contains == ()
    assert by_id["b"].relations.contains == ()
