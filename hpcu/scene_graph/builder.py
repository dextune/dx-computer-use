"""SceneBuilder — fold a SceneDelta into an immutable Scene."""

from dataclasses import replace
from typing import Optional

from hpcu.schemas.scene import Scene, SceneDelta
from hpcu.schemas.ui_element import ElementSource, UIElement


class SceneBuilder:
    """Immutable Scene updater driven by SceneDelta."""

    def update(self, scene: Scene, delta: SceneDelta) -> Scene:
        """Apply added/removed/modified. Never mutates `scene`."""
        new_version = (
            delta.new_version
            if delta.new_version > scene.version
            else scene.version + 1
        )
        elements: dict[str, UIElement] = dict(scene.elements)
        for element_id in delta.removed:
            elements.pop(element_id, None)
        for element in delta.added:
            existing = elements.get(element.id)
            elements[element.id] = (
                self._merge_element(existing, element) if existing else element
            )
        for element in delta.modified:
            existing = elements.get(element.id)
            elements[element.id] = self._merge_element(existing, element)
        # A Scene is the freshness boundary. Capture adapters may reuse an
        # element object from an earlier observation, but retaining its old
        # version would make every otherwise-current target fail the stale
        # decision check. Normalize retained elements to this Scene version.
        elements = {
            element_id: replace(element, scene_version=new_version)
            for element_id, element in elements.items()
        }
        frame = delta.frame if delta.frame is not None else scene.frame
        return Scene(
            version=new_version,
            window_id=scene.window_id,
            window_title=scene.window_title,
            elements=elements,
            frame=frame,
        )

    @classmethod
    def _merge_element(
        cls,
        existing: Optional[UIElement],
        incoming: UIElement,
    ) -> UIElement:
        if existing is None:
            return incoming
        merged_sources = cls._union_sources(incoming.sources, existing.sources)
        fresher = incoming.scene_version >= existing.scene_version
        base = incoming if fresher else existing
        last_seen = max(base.last_seen_at, incoming.last_seen_at)
        return replace(
            base,
            sources=merged_sources,
            last_seen_at=last_seen,
        )

    @staticmethod
    def _union_sources(
        primary: tuple[ElementSource, ...],
        secondary: tuple[ElementSource, ...],
    ) -> tuple[ElementSource, ...]:
        seen: set[str] = set()
        merged: list[ElementSource] = []
        for source in (*primary, *secondary):
            if source.type in seen:
                continue
            seen.add(source.type)
            merged.append(source)
        return tuple(merged)
