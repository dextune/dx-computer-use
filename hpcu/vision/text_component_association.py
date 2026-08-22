"""Associate text regions with shapes into semantic component groups.

This module is pure logic — it performs no OS or vision calls.  It
groups the outputs of OCR and shape detection into semantically related
components (text inside a shape, a label beside a field, a price below
a name).
"""

from dataclasses import dataclass
from typing import Optional

from hpcu.schemas.coordinates import BoundingBox
from hpcu.vision.ocr import TextRegion
from hpcu.vision.shape_detector import ShapeCandidate


@dataclass(frozen=True)
class ComponentGroup:
    """A set of related text and shape components."""

    text_inside_shape: Optional[TextRegion] = None
    label: Optional[TextRegion] = None
    name: Optional[TextRegion] = None
    price: Optional[TextRegion] = None


def _bbox_contains(container: BoundingBox, box: BoundingBox) -> bool:
    """True if `box` lies entirely within `container`."""
    return (
        container.x <= box.x
        and container.y <= box.y
        and box.x + box.width <= container.x + container.width
        and box.y + box.height <= container.y + container.height
    )


def _horizontal_overlap(first: BoundingBox, second: BoundingBox) -> bool:
    return not (
        first.x + first.width < second.x or second.x + second.width < first.x
    )


def _is_label_left_of_field(label_box: BoundingBox, field_box: BoundingBox) -> bool:
    """True if `label_box` sits immediately to the left of `field_box`.

    The label's right edge must be at or before the field's left edge,
    and the two must share vertical overlap.
    """
    if label_box.x + label_box.width > field_box.x:
        return False
    return (
        max(label_box.y, field_box.y)
        < min(label_box.y + label_box.height, field_box.y + field_box.height)
    )


def _looks_like_price(text: str) -> bool:
    return any(char.isdigit() for char in text)


def _is_price_below_name(name_region: TextRegion, price_region: TextRegion) -> bool:
    """True if `price_region` is a price directly below `name_region`."""
    if not _horizontal_overlap(name_region.bbox, price_region.bbox):
        return False
    if not _looks_like_price(price_region.text):
        return False
    return price_region.bbox.y >= name_region.bbox.y + name_region.bbox.height


def associate(
    text_regions: list[TextRegion], shapes: list[ShapeCandidate]
) -> list[ComponentGroup]:
    """Group `text_regions` and `shapes` into `ComponentGroup` associations.

    For every shape, a component group is produced when either a text
    region lies entirely inside the shape, or a label sits to the left
    of the shape.  Separately, any name/price pair where a numeric price
    appears directly below a name is also grouped.
    """
    regions = list(text_regions)
    groups: list[ComponentGroup] = []

    for shape in shapes:
        inner = next(
            (region for region in regions if _bbox_contains(shape.bbox, region.bbox)),
            None,
        )
        label = next(
            (
                region
                for region in regions
                if _is_label_left_of_field(region.bbox, shape.bbox)
            ),
            None,
        )
        if inner is not None or label is not None:
            groups.append(ComponentGroup(text_inside_shape=inner, label=label))

    unmatched = list(regions)
    for name_region in regions:
        for candidate in unmatched:
            if candidate is name_region:
                continue
            if _is_price_below_name(name_region, candidate):
                groups.append(ComponentGroup(name=name_region, price=candidate))
                unmatched = [region for region in unmatched if region is not candidate]
                break

    return groups