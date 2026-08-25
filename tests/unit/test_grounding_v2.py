"""SUE-5 qualification coverage for indexed local grounding V2."""

import pytest

from hpcu.grounder.grounder import Grounder
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.plan import TargetQuerySpec
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import (
    ElementRelations,
    ElementSource,
    UIElement,
)

pytestmark = pytest.mark.unit
_SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


def _element(
    element_id: str,
    text: str,
    *,
    role: str = "button",
    source: str = "dom",
    x: float = 0,
    relations: ElementRelations | None = None,
) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=1,
        role=role,
        name=text,
        text=text,
        bbox=BoundingBox(_SPACE, x, 0, 40, 20),
        relations=relations or ElementRelations(),
        sources=(ElementSource(type=source, confidence=1.0),),
        stable_frames=1,
    )


def _scene(*elements: UIElement) -> Scene:
    return Scene(version=1, elements={element.id: element for element in elements})


@pytest.mark.parametrize("source", ["dom", "ocr"])
def test_unique_exact_target_clears_local_gate_without_model(source: str):
    target = _element(source, "Search", source=source)

    result = Grounder().resolve({"text": "Search"}, _scene(target))

    assert result.element_id == source
    assert result.confidence >= 0.88
    assert result.failure_code is None
    assert result.indexed is True
    assert result.scanned_candidates == 1


def test_target_query_spec_carries_explicit_relation_into_grounding():
    query = TargetQuerySpec(
        text="Email",
        role="textbox",
        relation="right_of",
        anchor_element_id="label",
    )

    assert query.as_dict()["relation"] == "right_of"
    assert query.as_dict()["anchor_element_id"] == "label"


def test_explicit_relation_constraint_is_indexed_and_scored():
    label = _element("label", "Email", role="text", x=0)
    field = _element(
        "field",
        "Email",
        role="textbox",
        x=80,
        relations=ElementRelations(right_of=("label",)),
    )
    unrelated = _element("other", "Email", role="textbox", x=160)

    result = Grounder(confidence_threshold=0.3, min_margin=0.0).resolve(
        {
            "text": "Email",
            "role": "textbox",
            "relation": "right_of",
            "anchor_element_id": "label",
        },
        _scene(label, field, unrelated),
    )

    assert result.element_id == "field"
    assert result.candidates[0].feature_coverage == 1.0


def test_indexed_decision_matches_bruteforce_reference_and_scans_less():
    elements = [
        _element("target", "Launch", x=0),
        _element("settings", "Launch settings", x=60),
    ]
    elements.extend(
        _element(f"noise-{index}", f"Other {index}", x=120 + index * 50)
        for index in range(40)
    )
    scene = _scene(*elements)
    grounder = Grounder(confidence_threshold=0.3, min_margin=0.0)

    indexed = grounder.resolve({"text": "Launch"}, scene)
    reference = grounder.resolve_reference({"text": "Launch"}, scene)

    assert indexed.element_id == reference.element_id == "target"
    assert indexed.confidence == reference.confidence
    assert indexed.failure_code == reference.failure_code
    assert indexed.indexed is True
    assert indexed.scanned_candidates < reference.scanned_candidates


def test_text_index_keeps_substring_competitor_and_matches_reference():
    scene = _scene(
        _element("exact", "Search", source="ocr", x=0),
        _element("substring", "Research", source="dom", x=60),
        _element("noise", "Other", source="dom", x=120),
    )
    grounder = Grounder(confidence_threshold=0.2, min_margin=0.0)

    indexed = grounder.resolve({"text": "Search"}, scene)
    reference = grounder.resolve_reference({"text": "Search"}, scene)

    assert indexed.element_id == reference.element_id == "exact"
    assert {candidate.element_id for candidate in indexed.candidates} == {
        "exact",
        "substring",
    }
    assert indexed.scanned_candidates == 2
    assert reference.scanned_candidates == 3


def test_ambiguous_top_two_never_resolve_to_arbitrary_target():
    scene = _scene(
        _element("pay-a", "Pay", x=0),
        _element("pay-b", "Pay", x=60),
    )

    result = Grounder(confidence_threshold=0.5, min_margin=0.2).resolve(
        {"text": "Pay"},
        scene,
    )

    assert result.element_id is None
    assert result.failure_code.value == "grounding_ambiguous"
    assert result.margin == pytest.approx(0.0)
