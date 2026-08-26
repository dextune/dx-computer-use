"""Verified, coordinate-free grounding experience cache.

Experiences only narrow a *fresh* Scene before the normal Grounder runs. They
never contain an executable coordinate, cached element id, or raw frame data.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Protocol

from hpcu.trace.storage import TraceStorage

EXPERIENCE_SCHEMA_VERSION = 1
_COORDINATE_KEYS = frozenset(
    {
        "x",
        "y",
        "left",
        "top",
        "right",
        "bottom",
        "bbox",
        "point",
        "coordinate",
        "coordinates",
        "element_id",
    }
)


class GrounderLike(Protocol):
    def resolve(self, target_query: dict[str, Any], scene: Any) -> Any: ...


class ExperienceState(str, Enum):
    EXPERIMENTAL = "experimental"
    QUALIFIED = "qualified"
    ACTIVE = "active"
    DOWNGRADED = "downgraded"


@dataclass(frozen=True)
class GroundingHint:
    """Structural hint only; never an executable target."""

    surface_fingerprint: str
    target_signature: str
    plan_node_signature: str = ""
    preferred_roles: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    relation_signature: str = ""
    selector_ensemble: tuple[str, ...] = ()
    verification_contract: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.surface_fingerprint.strip():
            raise ValueError("surface_fingerprint must be non-empty")
        if not self.target_signature.strip():
            raise ValueError("target_signature must be non-empty")
        validate_coordinate_free(asdict(self))


@dataclass(frozen=True)
class ExperienceEntry:
    cache_key: str
    hint: GroundingHint
    state: ExperienceState = ExperienceState.EXPERIMENTAL
    success_count: int = 0
    failure_count: int = 0
    last_verified_scene_version: int = 0
    last_verified_ns: int = 0
    mean_latency_us: int = 0
    offline_replay_passed: bool = False


@dataclass(frozen=True)
class ExperienceReplayResult:
    """Result of cache-assisted local replay; execution still uses fresh grounding."""

    grounding: Any
    cache_key: str | None
    used_hint: bool
    fallback_used: bool
    fresh_scene_version: int
    coordinate_replay_executions: int = 0
    drifted: bool = False


class ExperienceCache:
    """Qualify repeated structural hints and replay them by fresh re-grounding."""

    def __init__(
        self,
        storage: TraceStorage | None = None,
        *,
        min_successes: int = 2,
        max_entries: int = 256,
        time_ns=None,
    ) -> None:
        if min_successes < 2:
            raise ValueError("min_successes must be >= 2")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._storage = storage or TraceStorage(":memory:")
        self._min_successes = min_successes
        self._max_entries = max_entries
        self._time_ns = time_ns or time.time_ns
        self._entries: dict[str, ExperienceEntry] = {}
        self._hydrate()

    @staticmethod
    def key_for(hint: GroundingHint) -> str:
        material = "\x1f".join(
            (
                hint.surface_fingerprint,
                hint.plan_node_signature,
                hint.target_signature,
            )
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def record_verified_success(
        self,
        hint: GroundingHint,
        *,
        scene_version: int,
        latency_us: int = 0,
    ) -> ExperienceEntry:
        """Record a verified transition; one success never qualifies a hint."""
        key = self.key_for(hint)
        previous = self._entries.get(key)
        success_count = 1 if previous is None else previous.success_count + 1
        prior_total = (
            0
            if previous is None
            else previous.mean_latency_us * previous.success_count
        )
        mean_latency = (prior_total + max(0, int(latency_us))) // success_count
        state = ExperienceState.EXPERIMENTAL
        replay_passed = False
        failure_count = 0
        if previous is not None:
            if previous.state in (ExperienceState.QUALIFIED, ExperienceState.ACTIVE):
                state = previous.state
            replay_passed = previous.offline_replay_passed
            failure_count = previous.failure_count
        entry = ExperienceEntry(
            cache_key=key,
            hint=hint,
            state=state,
            success_count=success_count,
            failure_count=failure_count,
            last_verified_scene_version=max(0, int(scene_version)),
            last_verified_ns=self._time_ns(),
            mean_latency_us=mean_latency,
            offline_replay_passed=replay_passed,
        )
        self._persist(entry)
        return entry

    def qualify_with_replay(
        self,
        cache_key: str,
        *,
        grounder: GrounderLike,
        target_query: dict[str, Any],
        scene: Any,
    ) -> ExperienceEntry:
        """Qualify by matching hint-narrowed replay to normal fresh grounding."""
        entry = self._require(cache_key)
        if entry.success_count < self._min_successes:
            return self._set_qualification(entry, replay_passed=False)
        narrowed = _narrow_fresh_scene(scene, entry.hint)
        if narrowed is None:
            return self._set_qualification(entry, replay_passed=False)
        baseline = grounder.resolve(target_query, scene)
        hinted = grounder.resolve(target_query, narrowed)
        baseline_id = getattr(baseline, "element_id", None)
        hinted_id = getattr(hinted, "element_id", None)
        replay_passed = (
            baseline_id is not None
            and baseline_id == hinted_id
            and _is_fresh_resolved(scene, hinted_id)
        )
        return self._set_qualification(entry, replay_passed=replay_passed)

    def qualify(
        self, cache_key: str, *, offline_replay_passed: bool
    ) -> ExperienceEntry:
        """Import trusted replay evidence; prefer :meth:`qualify_with_replay`."""
        return self._set_qualification(
            self._require(cache_key), replay_passed=offline_replay_passed
        )

    def _set_qualification(
        self, entry: ExperienceEntry, *, replay_passed: bool
    ) -> ExperienceEntry:
        qualified = replay_passed and entry.success_count >= self._min_successes
        updated = replace(
            entry,
            state=(
                ExperienceState.QUALIFIED
                if qualified
                else ExperienceState.EXPERIMENTAL
            ),
            offline_replay_passed=bool(replay_passed),
        )
        self._persist(updated)
        return updated

    def lookup(
        self,
        surface_fingerprint: str,
        target_signature: str,
        *,
        plan_node_signature: str = "",
    ) -> ExperienceEntry | None:
        probe = GroundingHint(
            surface_fingerprint=surface_fingerprint,
            target_signature=target_signature,
            plan_node_signature=plan_node_signature,
        )
        entry = self._entries.get(self.key_for(probe))
        if entry is None:
            return None
        if entry.state not in (ExperienceState.QUALIFIED, ExperienceState.ACTIVE):
            return None
        return entry

    def mark_drift(self, cache_key: str) -> ExperienceEntry:
        entry = self._require(cache_key)
        updated = replace(
            entry,
            state=ExperienceState.DOWNGRADED,
            failure_count=entry.failure_count + 1,
        )
        self._persist(updated)
        return updated

    def replay(
        self,
        *,
        grounder: GrounderLike,
        target_query: dict[str, Any],
        scene: Any,
        surface_fingerprint: str,
        target_signature: str,
        plan_node_signature: str = "",
    ) -> ExperienceReplayResult:
        """Use a qualified hint only to narrow a fresh Scene, then re-ground."""
        entry = self.lookup(
            surface_fingerprint,
            target_signature,
            plan_node_signature=plan_node_signature,
        )
        if entry is None:
            grounding = grounder.resolve(target_query, scene)
            return ExperienceReplayResult(
                grounding=grounding,
                cache_key=None,
                used_hint=False,
                fallback_used=False,
                fresh_scene_version=int(scene.version),
            )

        narrowed = _narrow_fresh_scene(scene, entry.hint)
        if narrowed is None:
            self.mark_drift(entry.cache_key)
            grounding = grounder.resolve(target_query, scene)
            return ExperienceReplayResult(
                grounding=grounding,
                cache_key=entry.cache_key,
                used_hint=False,
                fallback_used=True,
                fresh_scene_version=int(scene.version),
                drifted=True,
            )

        grounding = grounder.resolve(target_query, narrowed)
        element_id = getattr(grounding, "element_id", None)
        fresh = _is_fresh_resolved(scene, element_id)
        if not fresh:
            self.mark_drift(entry.cache_key)
            fallback = grounder.resolve(target_query, scene)
            return ExperienceReplayResult(
                grounding=fallback,
                cache_key=entry.cache_key,
                used_hint=False,
                fallback_used=True,
                fresh_scene_version=int(scene.version),
                drifted=True,
            )

        if entry.state is ExperienceState.QUALIFIED:
            self._persist(replace(entry, state=ExperienceState.ACTIVE))
        return ExperienceReplayResult(
            grounding=grounding,
            cache_key=entry.cache_key,
            used_hint=True,
            fallback_used=False,
            fresh_scene_version=int(scene.version),
        )

    def get(self, cache_key: str) -> ExperienceEntry | None:
        return self._entries.get(cache_key)

    def _require(self, cache_key: str) -> ExperienceEntry:
        entry = self._entries.get(cache_key)
        if entry is None:
            raise KeyError(cache_key)
        return entry

    def _persist(self, entry: ExperienceEntry) -> None:
        payload = _entry_to_payload(entry)
        validate_coordinate_free(payload)
        self._entries[entry.cache_key] = entry
        self._storage.upsert_experience(
            entry.cache_key,
            payload,
            schema_version=EXPERIENCE_SCHEMA_VERSION,
            updated_ns=self._time_ns(),
            max_entries=self._max_entries,
        )
        if len(self._entries) > self._max_entries:
            live_keys = {
                row["cache_key"] for row in self._storage.load_experiences()
            }
            self._entries = {
                key: value for key, value in self._entries.items() if key in live_keys
            }

    def _hydrate(self) -> None:
        for row in self._storage.load_experiences():
            if row["schema_version"] != EXPERIENCE_SCHEMA_VERSION:
                continue
            try:
                validate_coordinate_free(row["payload"])
                entry = _entry_from_payload(row["cache_key"], row["payload"])
            except (KeyError, TypeError, ValueError):
                continue
            self._entries[entry.cache_key] = entry


def validate_coordinate_free(value: Any) -> None:
    """Reject coordinate-bearing or cached-element-id experience payloads."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().casefold()
            if normalized in _COORDINATE_KEYS:
                raise ValueError(f"experience payload contains forbidden field: {key}")
            validate_coordinate_free(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            validate_coordinate_free(item)


def _narrow_fresh_scene(scene: Any, hint: GroundingHint) -> Any | None:
    """Return a same-version Scene containing only structurally compatible nodes."""
    compatible: dict[str, Any] = {}
    roles = {role.casefold() for role in hint.preferred_roles if role.strip()}
    sources = {source.casefold() for source in hint.source_types if source.strip()}

    for element_id, element in scene.elements.items():
        if getattr(element, "scene_version", None) != scene.version:
            continue
        role = str(getattr(element, "role", "") or "").casefold()
        if roles and role not in roles:
            continue
        element_sources = {
            str(getattr(source, "type", "") or "").casefold()
            for source in getattr(element, "sources", ())
        }
        if sources and not (sources & element_sources):
            continue
        compatible[element_id] = element

    if not compatible:
        return None
    try:
        return type(scene)(
            version=scene.version,
            window_id=getattr(scene, "window_id", None),
            window_title=getattr(scene, "window_title", None),
            elements=compatible,
            frame=getattr(scene, "frame", None),
        )
    except TypeError:
        return replace(scene, elements=compatible)


def _is_fresh_resolved(scene: Any, element_id: str | None) -> bool:
    if not element_id:
        return False
    element = scene.elements.get(element_id)
    return (
        element is not None
        and getattr(element, "scene_version", None) == scene.version
    )


def _entry_to_payload(entry: ExperienceEntry) -> dict[str, Any]:
    return {
        "hint": asdict(entry.hint),
        "state": entry.state.value,
        "success_count": entry.success_count,
        "failure_count": entry.failure_count,
        "last_verified_scene_version": entry.last_verified_scene_version,
        "last_verified_ns": entry.last_verified_ns,
        "mean_latency_us": entry.mean_latency_us,
        "offline_replay_passed": entry.offline_replay_passed,
    }


def _entry_from_payload(cache_key: str, payload: dict[str, Any]) -> ExperienceEntry:
    hint_payload = dict(payload["hint"])
    for tuple_field in (
        "preferred_roles",
        "source_types",
        "selector_ensemble",
        "verification_contract",
    ):
        hint_payload[tuple_field] = tuple(hint_payload.get(tuple_field, ()))
    hint = GroundingHint(**hint_payload)
    if ExperienceCache.key_for(hint) != cache_key:
        raise ValueError("experience cache key does not match payload")
    return ExperienceEntry(
        cache_key=cache_key,
        hint=hint,
        state=ExperienceState(payload.get("state", ExperienceState.EXPERIMENTAL.value)),
        success_count=max(0, int(payload.get("success_count", 0))),
        failure_count=max(0, int(payload.get("failure_count", 0))),
        last_verified_scene_version=max(
            0, int(payload.get("last_verified_scene_version", 0))
        ),
        last_verified_ns=max(0, int(payload.get("last_verified_ns", 0))),
        mean_latency_us=max(0, int(payload.get("mean_latency_us", 0))),
        offline_replay_passed=bool(payload.get("offline_replay_passed", False)),
    )
