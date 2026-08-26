from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from hpcu.trace.experience import (
    ExperienceCache,
    ExperienceState,
    GroundingHint,
    validate_coordinate_free,
)
from hpcu.trace.storage import TraceStorage

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Source:
    type: str


@dataclass(frozen=True)
class _Element:
    id: str
    scene_version: int
    role: str = "button"
    sources: tuple[_Source, ...] = (_Source("dom"),)


@dataclass(frozen=True)
class _Scene:
    version: int
    elements: dict[str, _Element] = field(default_factory=dict)
    window_id: str | None = None
    window_title: str | None = None
    frame: object | None = None


class _Grounder:
    def __init__(self):
        self.calls = []

    def resolve(self, target_query, scene):
        self.calls.append((target_query, scene.version, tuple(scene.elements)))
        element_id = next(iter(scene.elements), None)
        return SimpleNamespace(element_id=element_id)


def _qualified(cache: ExperienceCache) -> GroundingHint:
    hint = GroundingHint(
        surface_fingerprint="surface-v1",
        target_signature="submit-button",
        preferred_roles=("button",),
        source_types=("dom",),
        selector_ensemble=("button[type=submit]",),
    )
    first = cache.record_verified_success(hint, scene_version=1, latency_us=100)
    assert first.state is ExperienceState.EXPERIMENTAL
    second = cache.record_verified_success(hint, scene_version=2, latency_us=80)
    qualified = cache.qualify(second.cache_key, offline_replay_passed=True)
    assert qualified.state is ExperienceState.QUALIFIED
    return hint


def test_repeated_success_and_replay_required_before_qualification():
    cache = ExperienceCache(min_successes=2)
    hint = GroundingHint("surface-v1", "submit")
    first = cache.record_verified_success(hint, scene_version=1)
    first_qualified = cache.qualify(first.cache_key, offline_replay_passed=True)
    assert first_qualified.state is ExperienceState.EXPERIMENTAL
    second = cache.record_verified_success(hint, scene_version=2)
    replay_failed = cache.qualify(second.cache_key, offline_replay_passed=False)
    assert replay_failed.state is ExperienceState.EXPERIMENTAL
    qualified = cache.qualify(second.cache_key, offline_replay_passed=True)
    assert qualified.state is ExperienceState.QUALIFIED


def test_experience_payload_rejects_coordinates_and_cached_element_ids():
    for payload in (
        {"x": 10},
        {"nested": {"bbox": [0, 0, 10, 10]}},
        {"element_id": "old-button"},
    ):
        with pytest.raises(ValueError, match="forbidden field"):
            validate_coordinate_free(payload)


def test_storage_roundtrip_and_bounded_retention():
    storage = TraceStorage(":memory:")
    cache = ExperienceCache(storage=storage, min_successes=2, max_entries=2)
    for index in range(3):
        hint = GroundingHint(f"surface-{index}", f"target-{index}")
        entry = cache.record_verified_success(hint, scene_version=index + 1)
        cache.record_verified_success(hint, scene_version=index + 2)
        cache.qualify(entry.cache_key, offline_replay_passed=True)
    assert storage.experience_count() == 2
    reloaded = ExperienceCache(storage=storage, min_successes=2, max_entries=2)
    assert len(reloaded._entries) == 2


def test_corrupt_experience_row_does_not_block_hydration():
    storage = TraceStorage(":memory:")
    storage._conn.execute(
        "INSERT INTO experiences(cache_key, schema_version, payload, updated_ns) "
        "VALUES (?, ?, ?, ?)",
        ("bad", 1, "{not-json", 1),
    )
    storage._conn.commit()
    cache = ExperienceCache(storage=storage)
    assert cache.get("bad") is None


def test_qualified_replay_freshly_regrounds_current_scene_and_activates():
    cache = ExperienceCache(min_successes=2)
    hint = _qualified(cache)
    grounder = _Grounder()
    scene = _Scene(version=9, elements={"fresh-button": _Element("fresh-button", 9)})
    result = cache.replay(
        grounder=grounder,
        target_query={"text": "Submit"},
        scene=scene,
        surface_fingerprint=hint.surface_fingerprint,
        target_signature=hint.target_signature,
    )
    assert result.used_hint is True
    assert result.grounding.element_id == "fresh-button"
    assert result.fresh_scene_version == 9
    assert result.coordinate_replay_executions == 0
    assert grounder.calls == [({"text": "Submit"}, 9, ("fresh-button",))]
    assert cache.get(result.cache_key).state is ExperienceState.ACTIVE


def test_hint_drift_downgrades_and_falls_back_to_full_fresh_scene():
    cache = ExperienceCache(min_successes=2)
    hint = _qualified(cache)
    grounder = _Grounder()
    scene = _Scene(
        version=10,
        elements={"new-link": _Element("new-link", 10, role="link")},
    )
    result = cache.replay(
        grounder=grounder,
        target_query={"text": "Submit"},
        scene=scene,
        surface_fingerprint=hint.surface_fingerprint,
        target_signature=hint.target_signature,
    )
    assert result.used_hint is False
    assert result.fallback_used is True
    assert result.drifted is True
    assert result.grounding.element_id == "new-link"
    assert cache.get(result.cache_key).state is ExperienceState.DOWNGRADED


def test_surface_drift_is_cache_miss_not_coordinate_replay():
    cache = ExperienceCache(min_successes=2)
    hint = _qualified(cache)
    grounder = _Grounder()
    scene = _Scene(version=11, elements={"button": _Element("button", 11)})
    result = cache.replay(
        grounder=grounder,
        target_query={"text": "Submit"},
        scene=scene,
        surface_fingerprint="surface-v2",
        target_signature=hint.target_signature,
    )
    assert result.cache_key is None
    assert result.used_hint is False
    assert result.coordinate_replay_executions == 0


def test_offline_replay_qualification_uses_fresh_grounding_parity():
    cache = ExperienceCache(min_successes=2)
    hint = GroundingHint(
        surface_fingerprint="surface-v1",
        target_signature="submit",
        preferred_roles=("button",),
        source_types=("dom",),
    )
    cache.record_verified_success(hint, scene_version=1)
    second = cache.record_verified_success(hint, scene_version=2)
    grounder = _Grounder()
    scene = _Scene(
        version=3,
        elements={"fresh-button": _Element("fresh-button", 3)},
    )

    qualified = cache.qualify_with_replay(
        second.cache_key,
        grounder=grounder,
        target_query={"text": "Submit"},
        scene=scene,
    )

    assert qualified.state is ExperienceState.QUALIFIED
    assert qualified.offline_replay_passed is True
    assert grounder.calls == [
        ({"text": "Submit"}, 3, ("fresh-button",)),
        ({"text": "Submit"}, 3, ("fresh-button",)),
    ]
