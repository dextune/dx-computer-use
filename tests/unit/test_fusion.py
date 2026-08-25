"""Unit tests for deterministic cross-source UI object fusion."""

import pytest

from hpcu.perception.fusion import FusionEngine, FusionPolicy
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.ui_element import ElementRelations, ElementSource, UIElement

pytestmark = pytest.mark.unit


POLICY = FusionPolicy(
    min_score=0.72,
    min_geometry_overlap=0.50,
    min_assignment_margin=0.08,
    max_center_distance_px=96,
    spatial_cell_size_px=128,
    text_max_chars=160,
    fingerprint_quantum_px=4,
    weight_iou=0.22,
    weight_containment=0.18,
    weight_center=0.14,
    weight_text=0.22,
    weight_role=0.14,
    weight_source_ref=0.10,
    source_reliability={
        "dom": 1.0,
        "uia": 0.96,
        "atspi": 0.93,
        "ax": 0.93,
        "ocr": 0.55,
        "template": 0.60,
        "unknown": 0.40,
    },
)


def _box(
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    space: CoordinateSpace = CoordinateSpace.SCREEN_PHYSICAL_PX,
) -> BoundingBox:
    return BoundingBox(
        space=space,
        x=x,
        y=y,
        width=width,
        height=height,
    )


def _element(
    element_id: str,
    source_type: str,
    *,
    bbox: BoundingBox,
    role: str = "unknown",
    name: str | None = None,
    text: str | None = None,
    ref: str | None = None,
    confidence: float = 1.0,
    relations: ElementRelations | None = None,
    semantic_tags: tuple[str, ...] = (),
) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=3,
        role=role,
        name=name,
        text=text,
        bbox=bbox,
        relations=relations or ElementRelations(),
        semantic_tags=semantic_tags,
        sources=(
            ElementSource(
                type=source_type,
                ref=ref,
                confidence=confidence,
                text=text,
            ),
        ),
    )


def test_ocr_and_atspi_same_object_fuse_to_structure_identity():
    engine = FusionEngine(POLICY)
    structure = _element(
        "atspi_save",
        "atspi",
        bbox=_box(100, 100, 100, 40),
        role="button",
        name="Save",
        ref="node-save",
    )
    ocr = _element(
        "ocr_save",
        "ocr",
        bbox=_box(120, 110, 50, 20),
        role="text",
        text="Save",
        confidence=0.85,
    )

    result = engine.fuse((ocr, structure))

    assert result.output_count == 1
    assert result.merged_count == 1
    merged = result.elements[0]
    assert merged.id == "atspi_save"
    assert merged.role == "button"
    assert {source.type for source in merged.sources} == {"atspi", "ocr"}
    assert merged.fingerprint.startswith("fusion:")


def test_two_same_text_buttons_remain_distinct_by_geometry():
    engine = FusionEngine(POLICY)
    elements = (
        _element(
            "button_left",
            "atspi",
            bbox=_box(20, 20, 100, 40),
            role="button",
            name="Save",
            ref="left",
        ),
        _element(
            "button_right",
            "atspi",
            bbox=_box(220, 20, 100, 40),
            role="button",
            name="Save",
            ref="right",
        ),
        _element(
            "ocr_left",
            "ocr",
            bbox=_box(40, 30, 50, 20),
            role="text",
            text="Save",
        ),
        _element(
            "ocr_right",
            "ocr",
            bbox=_box(240, 30, 50, 20),
            role="text",
            text="Save",
        ),
    )

    result = engine.fuse(elements)

    assert result.output_count == 2
    assert {element.id for element in result.elements} == {
        "button_left",
        "button_right",
    }
    assert all(
        {source.type for source in element.sources} == {"atspi", "ocr"}
        for element in result.elements
    )


def test_ambiguous_equal_candidates_are_not_forced_to_merge():
    engine = FusionEngine(POLICY)
    same_box = _box(100, 100, 100, 40)
    elements = (
        _element(
            "button_a",
            "atspi",
            bbox=same_box,
            role="button",
            name="Save",
            ref="a",
        ),
        _element(
            "button_b",
            "atspi",
            bbox=same_box,
            role="button",
            name="Save",
            ref="b",
        ),
        _element(
            "ocr",
            "ocr",
            bbox=_box(120, 110, 50, 20),
            role="text",
            text="Save",
        ),
    )

    result = engine.fuse(elements)

    assert result.output_count == 3
    assert result.accepted_pairs == 0


def test_coordinate_space_mismatch_never_fuses():
    engine = FusionEngine(POLICY)
    structure = _element(
        "structure",
        "uia",
        bbox=_box(10, 10, 100, 40),
        role="button",
        name="OK",
    )
    ocr = _element(
        "ocr",
        "ocr",
        bbox=_box(
            10,
            10,
            100,
            40,
            space=CoordinateSpace.CLIENT_AREA_PX,
        ),
        role="text",
        text="OK",
    )

    result = engine.fuse((structure, ocr))

    assert result.output_count == 2


def test_conflicting_structured_roles_do_not_fuse():
    engine = FusionEngine(POLICY)
    first = _element(
        "dom_button",
        "dom",
        bbox=_box(10, 10, 100, 40),
        role="button",
        name="Open",
    )
    second = _element(
        "ax_link",
        "ax",
        bbox=_box(10, 10, 100, 40),
        role="link",
        name="Open",
    )

    result = engine.fuse((first, second))

    assert result.output_count == 2
    assert engine.pair_score(first, second) == 0.0


def test_existing_tags_are_preserved_but_fusion_adds_no_semantic_tag():
    engine = FusionEngine(POLICY)
    structure = _element(
        "structure",
        "dom",
        bbox=_box(0, 0, 80, 30),
        role="button",
        name="Apply",
        semantic_tags=("provided-by-dom",),
    )
    ocr = _element(
        "ocr",
        "ocr",
        bbox=_box(10, 5, 50, 20),
        role="text",
        text="Apply",
    )

    merged = engine.fuse((structure, ocr)).elements[0]

    assert merged.semantic_tags == ("provided-by-dom",)


def test_relations_are_remapped_to_canonical_fused_ids():
    engine = FusionEngine(POLICY)
    parent = _element(
        "dom_parent",
        "dom",
        bbox=_box(0, 0, 200, 100),
        role="group",
        name="Panel",
    )
    child = _element(
        "atspi_child",
        "atspi",
        bbox=_box(20, 20, 100, 40),
        role="button",
        name="Go",
        relations=ElementRelations(parent="ocr_parent"),
    )
    ocr_parent = _element(
        "ocr_parent",
        "ocr",
        bbox=_box(10, 10, 180, 80),
        role="text",
        text="Panel",
    )

    result = engine.fuse((parent, child, ocr_parent))
    by_id = {element.id: element for element in result.elements}

    assert "ocr_parent" not in by_id
    assert by_id["atspi_child"].relations.parent == "dom_parent"


def test_policy_rejects_invalid_threshold():
    with pytest.raises(ValueError, match="min_score"):
        FusionPolicy(
            min_score=1.1,
            min_geometry_overlap=0.5,
            min_assignment_margin=0.08,
            max_center_distance_px=96,
            spatial_cell_size_px=128,
            text_max_chars=160,
            fingerprint_quantum_px=4,
            weight_iou=1,
            weight_containment=0,
            weight_center=0,
            weight_text=0,
            weight_role=0,
            weight_source_ref=0,
            source_reliability={"ocr": 0.5},
        )
