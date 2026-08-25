"""Immutable lookup indexes for one fresh UI Scene."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement

_HANGUL_SPACE = re.compile(r"(?<=[가-힣])\s+(?=[가-힣])")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_SUPPORTED_RELATIONS = frozenset(
    {
        "inside",
        "parent",
        "same_row",
        "same_column",
        "above",
        "below",
        "left_of",
        "right_of",
        "contains",
    }
)


class StaleSceneIndexError(RuntimeError):
    """A SceneIndex was queried against a different Scene version."""


def _normalize_text(value: str) -> str:
    compact_hangul = _HANGUL_SPACE.sub("", value or "")
    return " ".join(compact_hangul.casefold().split())


def _tokens(value: str) -> tuple[str, ...]:
    normalized = _normalize_text(value)
    if not normalized:
        return ()
    tokens = set(_TOKEN_RE.findall(normalized))
    compact = normalized.replace(" ", "")
    if compact:
        tokens.add(compact)
    return tuple(sorted(tokens))


def _character_grams(value: str, size: int) -> tuple[str, ...]:
    compact = _normalize_text(value).replace(" ", "")
    if not compact or size <= 0 or len(compact) < size:
        return ()
    return tuple(
        sorted(
            {
                compact[index : index + size]
                for index in range(len(compact) - size + 1)
            }
        )
    )


def _valid_box(box: BoundingBox | None) -> bool:
    return box is not None and box.width > 0 and box.height > 0


def _contains(outer: BoundingBox, inner: BoundingBox) -> bool:
    if outer.space != inner.space:
        return False
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.x + inner.width <= outer.x + outer.width
        and inner.y + inner.height <= outer.y + outer.height
    )


def _intersects(first: BoundingBox, second: BoundingBox) -> bool:
    if first.space != second.space:
        return False
    return not (
        first.x + first.width <= second.x
        or second.x + second.width <= first.x
        or first.y + first.height <= second.y
        or second.y + second.height <= first.y
    )


def _box_distance(first: BoundingBox, second: BoundingBox) -> float:
    if first.space != second.space:
        return math.inf
    dx = max(
        first.x - (second.x + second.width),
        second.x - (first.x + first.width),
        0.0,
    )
    dy = max(
        first.y - (second.y + second.height),
        second.y - (first.y + first.height),
        0.0,
    )
    return math.hypot(dx, dy)


class SceneIndex:
    """Role, text, relation and uniform-grid indexes for exactly one Scene."""

    def __init__(self, scene: Scene, *, cell_size_px: int) -> None:
        if cell_size_px <= 0:
            raise ValueError("cell_size_px must be > 0")
        self._scene_version = scene.version
        self._cell_size_px = int(cell_size_px)
        self._elements = scene.elements
        self._role: dict[str, set[str]] = {}
        self._text: dict[str, set[str]] = {}
        self._text_grams: dict[tuple[int, str], set[str]] = {}
        self._grid: dict[tuple[CoordinateSpace, int, int], set[str]] = {}
        self._by_space: dict[CoordinateSpace, set[str]] = {}
        for element in scene.elements.values():
            self._index_element(element)

    @classmethod
    def from_config(cls, scene: Scene, config: dict) -> "SceneIndex":
        graph = config["scene_graph"]
        return cls(scene, cell_size_px=int(graph["index_cell_size_px"]))

    @property
    def scene_version(self) -> int:
        return self._scene_version

    @property
    def cell_size_px(self) -> int:
        return self._cell_size_px

    def assert_scene_version(self, expected: int) -> None:
        if expected != self._scene_version:
            raise StaleSceneIndexError(
                f"scene index version {self._scene_version} != expected {expected}"
            )

    def get(self, element_id: str) -> UIElement | None:
        return self._elements.get(element_id)

    def by_role(self, role: str) -> tuple[str, ...]:
        normalized = role.strip().casefold()
        if not normalized:
            return ()
        return self._ordered(self._role.get(normalized, ()))

    def by_text_token(self, token: str) -> tuple[str, ...]:
        query_tokens = _tokens(token)
        if not query_tokens:
            return ()
        buckets = [self._text.get(part, set()) for part in query_tokens]
        if not buckets or any(not bucket for bucket in buckets):
            return ()
        matched = set(buckets[0])
        for bucket in buckets[1:]:
            matched.intersection_update(bucket)
        return self._ordered(matched)

    def by_text_candidates(self, value: str) -> tuple[str, ...]:
        """Return a safe superset of elements with possible non-zero text match."""
        matched: set[str] = set()
        for token in _tokens(value):
            matched.update(self._text.get(token, ()))

        compact = _normalize_text(value).replace(" ", "")
        if compact:
            size = min(3, len(compact))
            grams = _character_grams(compact, size)
            if grams:
                buckets = [
                    self._text_grams.get((size, gram), set()) for gram in grams
                ]
                if buckets and all(buckets):
                    substring_ids = set(buckets[0])
                    for bucket in buckets[1:]:
                        substring_ids.intersection_update(bucket)
                    matched.update(substring_ids)
        return self._ordered(matched)

    def by_relation(self, relation: str, anchor_element_id: str) -> tuple[str, ...]:
        """Return elements satisfying one explicit precompiled relation constraint."""
        normalized = relation.strip().casefold()
        anchor = anchor_element_id.strip()
        if normalized not in _SUPPORTED_RELATIONS:
            raise ValueError(f"unsupported scene relation: {relation!r}")
        if not anchor or anchor not in self._elements:
            return ()
        matched: list[str] = []
        for element in self._elements.values():
            relations = element.relations
            if normalized in ("inside", "parent"):
                holds = relations.parent == anchor
            else:
                holds = anchor in getattr(relations, normalized, ())
            if holds:
                matched.append(element.id)
        return self._ordered(matched)

    def inside(self, bbox: BoundingBox) -> tuple[str, ...]:
        candidate_ids = self._candidate_ids(bbox)
        matched = (
            element_id
            for element_id in candidate_ids
            if _valid_box(self._elements[element_id].bbox)
            and _contains(bbox, self._elements[element_id].bbox)
        )
        return self._ordered(matched)

    def intersects(self, bbox: BoundingBox) -> tuple[str, ...]:
        candidate_ids = self._candidate_ids(bbox)
        matched = (
            element_id
            for element_id in candidate_ids
            if _valid_box(self._elements[element_id].bbox)
            and _intersects(bbox, self._elements[element_id].bbox)
        )
        return self._ordered(matched)

    def near(self, bbox: BoundingBox, radius_px: float) -> tuple[str, ...]:
        if radius_px < 0:
            raise ValueError("radius_px must be >= 0")
        expanded = BoundingBox(
            space=bbox.space,
            x=bbox.x - radius_px,
            y=bbox.y - radius_px,
            width=bbox.width + radius_px * 2,
            height=bbox.height + radius_px * 2,
        )
        candidate_ids = self._candidate_ids(expanded)
        matched = [
            element_id
            for element_id in candidate_ids
            if _valid_box(self._elements[element_id].bbox)
            and _box_distance(bbox, self._elements[element_id].bbox) <= radius_px
        ]
        return tuple(
            sorted(
                matched,
                key=lambda element_id: (
                    _box_distance(bbox, self._elements[element_id].bbox),
                    *self._spatial_key(element_id),
                ),
            )
        )

    def right_of(self, element_id: str) -> tuple[str, ...]:
        anchor = self._elements.get(element_id)
        if anchor is None or not _valid_box(anchor.bbox):
            return ()
        anchor_box = anchor.bbox
        anchor_right = anchor_box.x + anchor_box.width
        matched = []
        for candidate_id in self._by_space.get(anchor_box.space, ()):
            if candidate_id == element_id:
                continue
            candidate_box = self._elements[candidate_id].bbox
            if _valid_box(candidate_box) and candidate_box.x >= anchor_right:
                matched.append(candidate_id)
        return tuple(
            sorted(
                matched,
                key=lambda candidate_id: (
                    self._elements[candidate_id].bbox.x - anchor_right,
                    abs(
                        self._elements[candidate_id].bbox.center[1]
                        - anchor_box.center[1]
                    ),
                    *self._spatial_key(candidate_id),
                ),
            )
        )

    def _index_element(self, element: UIElement) -> None:
        role = element.role.strip().casefold()
        if role:
            self._role.setdefault(role, set()).add(element.id)
        values = tuple(
            dict.fromkeys(value for value in (element.name, element.text) if value)
        )
        text = " ".join(values)
        for token in _tokens(text):
            self._text.setdefault(token, set()).add(element.id)
        for size in (1, 2, 3):
            for gram in _character_grams(text, size):
                self._text_grams.setdefault((size, gram), set()).add(element.id)
        if not _valid_box(element.bbox):
            return
        self._by_space.setdefault(element.bbox.space, set()).add(element.id)
        for cell_x, cell_y in self._cells_for_bbox(element.bbox):
            self._grid.setdefault(
                (element.bbox.space, cell_x, cell_y), set()
            ).add(element.id)

    def _candidate_ids(self, bbox: BoundingBox) -> set[str]:
        if not _valid_box(bbox):
            return set()
        result: set[str] = set()
        for cell_x, cell_y in self._cells_for_bbox(bbox):
            result.update(self._grid.get((bbox.space, cell_x, cell_y), ()))
        return result

    def _cells_for_bbox(self, bbox: BoundingBox) -> Iterable[tuple[int, int]]:
        if not _valid_box(bbox):
            return ()
        right = math.nextafter(bbox.x + bbox.width, -math.inf)
        bottom = math.nextafter(bbox.y + bbox.height, -math.inf)
        first_x = math.floor(bbox.x / self._cell_size_px)
        last_x = math.floor(right / self._cell_size_px)
        first_y = math.floor(bbox.y / self._cell_size_px)
        last_y = math.floor(bottom / self._cell_size_px)
        return (
            (cell_x, cell_y)
            for cell_x in range(first_x, last_x + 1)
            for cell_y in range(first_y, last_y + 1)
        )

    def _ordered(self, element_ids: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted(set(element_ids), key=self._spatial_key))

    def _spatial_key(self, element_id: str) -> tuple[str, float, float, str]:
        box = self._elements[element_id].bbox
        if box is None:
            return ("~", math.inf, math.inf, element_id)
        return (box.space.value, box.y, box.x, element_id)


@dataclass(frozen=True)
class IndexedScene:
    """A Scene bundled with an index that is valid only for that version."""

    scene: Scene
    index: SceneIndex

    def __post_init__(self) -> None:
        self.index.assert_scene_version(self.scene.version)

    @classmethod
    def build(cls, scene: Scene, *, cell_size_px: int) -> "IndexedScene":
        return cls(scene=scene, index=SceneIndex(scene, cell_size_px=cell_size_px))

    @classmethod
    def from_config(cls, scene: Scene, config: dict) -> "IndexedScene":
        return cls(scene=scene, index=SceneIndex.from_config(scene, config))

    def assert_scene_version(self, expected: int) -> None:
        self.index.assert_scene_version(expected)
