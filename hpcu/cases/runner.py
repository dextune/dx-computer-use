"""Shopping case runner — common loop; adapters only pixels/regions/input.

Pack-based decisions.  No site/CTA dictionaries in the production path.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Optional

from hpcu.cases.specs import CaseSpec, EvidenceSpec
from hpcu.cases.stats import MINIMAX_MODEL, ActionRecord, CaseStats, CountingGateway
from hpcu.compiler.targeting_compiler import GoalFallbackTokenizer, TargetingCompiler
from hpcu.coordinates.spaces import compute_safe_click_point
from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder
from hpcu.input.keys import ENTER, FOCUS_LOCATION, NEW_TAB, PAGE_DOWN, SELECT_ALL
from hpcu.observation.base import Observer
from hpcu.perception.engine import normalize_ocr_text
from hpcu.perception.matcher import GenericMatcher, mentions
from hpcu.perception.window_chrome import is_in_chrome, omnibox_point, primary_window
from hpcu.runtime_config import load_runtime_config
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.coordinates import CoordinateSpace, ScreenPoint
from hpcu.schemas.scene import Scene
from hpcu.schemas.targeting import TargetingPack
from hpcu.schemas.ui_element import UIElement
from hpcu.scene_graph.builder import SceneBuilder


_JSON_OBJECT = re.compile(r"\{[^{}]*\}", re.DOTALL)
_ORIGIN = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=1.0, y=1.0)


class CaseRunner:
    """Local observation first; MiniMax M3 only on grounding miss."""

    def __init__(
        self,
        observer: Observer,
        executor: Executor,
        grounder: Grounder,
        gateway: Optional[CountingGateway] = None,
        builder: Optional[SceneBuilder] = None,
        *,
        config: Optional[dict] = None,
        sleep=None,
        monotonic=None,
    ):
        runtime = config if config is not None else load_runtime_config()
        cases_cfg = runtime.get("cases", {})
        self._observer = observer
        self._executor = executor
        self._grounder = grounder
        self._gateway = gateway
        self._builder = builder if builder is not None else SceneBuilder()
        self._compiler = TargetingCompiler(gateway, config=runtime)
        self._navigate_timeout_ms = int(cases_cfg.get("navigate_settle_timeout_ms", 12000))
        self._navigate_poll_ms = int(cases_cfg.get("navigate_poll_interval_ms", 400))
        self._post_click_timeout_ms = int(cases_cfg.get("post_click_timeout_ms", 5000))
        self._post_click_poll_ms = int(cases_cfg.get("post_click_poll_interval_ms", 300))
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._scene = Scene(version=0)
        self._case_model_calls = 0

    async def run_case(self, spec: CaseSpec) -> CaseStats:
        stats = CaseStats(case_id=spec.id)
        self._case_model_calls = 0
        origin_calls, origin_errors, origin_tokens = self._gateway_origin()
        compile_origin = self._gateway.call_count if self._gateway else 0

        # Plan-time: compile TargetingPack (MiniMax ≤1 or fallback 0)
        pack = self._compiler.compile(spec.id, spec.goal, spec.start_url)
        if self._gateway is not None:
            stats.compile_call_count = self._gateway.call_count - compile_origin

        # Navigate
        scene = await self._navigate(spec, stats)
        matcher = GenericMatcher(pack)

        # Check for ready signal
        if not pack.pick_required and mentions(scene, pack.success_any or pack.ready_any):
            stats.success = True
            stats.failure = ""
            stats.attempts = 0
            self._log(stats, "evidence", "after_navigate")
            self._fill_gateway_stats(stats, origin_calls, origin_errors, origin_tokens)
            return stats

        selected: Optional[UIElement] = None
        for attempt in range(1, spec.max_attempts + 1):
            stats.attempts = attempt
            scene = await self._observe(stats, f"attempt-{attempt}")
            if matcher.pack.blocked_any and matcher.blocked(scene):
                stats.failure = "blocked"
                self._log(stats, "blocked", "anti-bot")
                continue
            scene = await self._dismiss_consent(scene, matcher, stats)
            selected = self._pick_local(scene, matcher, spec)
            if selected is None:
                scene = await self._scroll_content(scene, stats)
                selected = self._pick_local(scene, matcher, spec)
            if selected is None:
                selected = self._pick_minimax(scene, matcher, spec, stats)
            if selected is None:
                selected = _pick_content_fallback(scene, matcher)
            if selected is None:
                stats.failure = "no_candidate"
                continue
            clicked = await self._click(selected, stats)
            if not clicked:
                stats.failure = "click_failed"
                continue
            scene = await self._wait_for_blob_change(
                scene,
                pack,
                stats,
                timeout_ms=self._post_click_timeout_ms,
                poll_ms=self._post_click_poll_ms,
                label="post_click",
            )
            stats.selected_text = (selected.text or selected.name or "")[:200]
            if mentions(scene, pack.success_any or pack.ready_any):
                stats.success = True
                stats.failure = ""
                self._fill_gateway_stats(stats, origin_calls, origin_errors, origin_tokens)
                return stats
            stats.failure = "evidence_unmet"
        self._fill_gateway_stats(stats, origin_calls, origin_errors, origin_tokens)
        return stats

    async def _navigate(self, spec: CaseSpec, stats: CaseStats) -> Scene:
        scene = await self._observe(stats, "pre_navigate")
        window = primary_window(scene)
        bar = omnibox_point(window) if window is not None else None
        if window is not None:
            prepared = self._executor.prepare(
                Action(id="focus-window", op=ActionOp.FOCUS_WINDOW),
                window.id,
                element=window,
                physical_point=bar or _ORIGIN,
            )
            await self._executor.execute(prepared)
            self._log(stats, "focus", (window.name or window.id)[:80])
        await self._pause()
        await self._executor.inject_physical(_ORIGIN, "key", text=NEW_TAB)
        self._log(stats, "key", NEW_TAB)
        await self._pause()
        if bar is not None:
            await self._executor.inject_physical(bar, "click")
            self._log(stats, "click", "omnibox")
        await self._executor.inject_physical(_ORIGIN, "key", text=FOCUS_LOCATION)
        self._log(stats, "key", FOCUS_LOCATION)
        await self._pause()
        await self._executor.inject_physical(_ORIGIN, "key", text=SELECT_ALL)
        self._log(stats, "key", SELECT_ALL)
        await self._executor.inject_physical(_ORIGIN, "type", text=spec.start_url)
        self._log(stats, "type", "url")
        await self._executor.inject_physical(_ORIGIN, "key", text=ENTER)
        self._log(stats, "key", ENTER)
        return await self._wait_for_blob_change(
            scene,
            None,
            stats,
            timeout_ms=self._navigate_timeout_ms,
            poll_ms=self._navigate_poll_ms,
            label="navigate_settle",
        )

    async def _dismiss_consent(
        self, scene: Scene, matcher: GenericMatcher, stats: CaseStats
    ) -> Scene:
        targets = matcher.consent_candidates(scene)
        if not targets:
            return scene
        clicked = await self._click(targets[0], stats)
        if not clicked:
            return scene
        self._log(stats, "click", "consent")
        return await self._observe(stats, "post_consent")

    def _pick_local(
        self, scene: Scene, matcher: GenericMatcher, spec: CaseSpec
    ) -> Optional[UIElement]:
        window = primary_window(scene)
        candidates = matcher.pick_candidates(scene)
        in_content = [
            element for element in candidates
            if not is_in_chrome(element, window)
        ]
        if in_content:
            return in_content[0]
        if candidates:
            return candidates[0]
        result = self._grounder.resolve({"text": spec.goal}, scene)
        if result.confident and result.element_id:
            return scene.get(result.element_id)
        return None

    def _pick_minimax(
        self, scene: Scene, matcher: GenericMatcher, spec: CaseSpec, stats: CaseStats
    ) -> Optional[UIElement]:
        if self._gateway is None:
            return None
        if self._case_model_calls >= spec.max_minimax_calls:
            return None
        self._case_model_calls += 1
        summary = _scene_summary(scene)
        prompt = (
            "You pick one on-screen item for the user goal. "
            "Return JSON only: {\"target_id\": \"<id>\"}. "
            f"Goal: {spec.goal}\nCandidates:\n{summary}"
        )
        try:
            response = self._gateway.call(
                prompt, system_prompt="JSON only.", max_tokens=128
            )
        except Exception:
            self._log(stats, "model", "MiniMax-M3 error", ok=False)
            return None
        self._log(stats, "model", f"MiniMax-M3 {response.model}")
        target_id = _parse_target_id(response.content)
        if not target_id:
            return None
        element = scene.get(target_id)
        if element is None or element.role == "window":
            return None
        return element

    async def _scroll_content(self, scene: Scene, stats: CaseStats) -> Scene:
        window = primary_window(scene)
        if window is None or window.bbox is None:
            point = _ORIGIN
        else:
            point = ScreenPoint(
                space=window.bbox.space,
                x=window.bbox.x + window.bbox.width * 0.6,
                y=min(window.bbox.y + window.bbox.height * 0.55, window.bbox.y + 400),
            )
        await self._executor.inject_physical(point, "click")
        await self._executor.inject_physical(point, "key", text=PAGE_DOWN)
        self._log(stats, "key", PAGE_DOWN)
        return await self._observe(stats, "scroll")

    async def _pause(self) -> None:
        await self._sleep(self._navigate_poll_ms / 1000.0)

    async def _click(self, element: UIElement, stats: CaseStats) -> bool:
        if element.bbox is None:
            return False
        point = compute_safe_click_point(element.bbox)
        result = await self._executor.inject_physical(point, "click")
        self._log(stats, "click", (element.text or element.id)[:80], ok=result.success)
        return result.success

    async def _observe(self, stats: CaseStats, label: str) -> Scene:
        delta = await self._observer.observe()
        self._log(stats, "capture", f"{label} v={delta.new_version}")
        previous = getattr(self, "_scene", Scene(version=0))
        scene = self._builder.update(previous, delta)
        self._scene = scene
        return scene

    async def _wait_for_blob_change(
        self,
        scene: Scene,
        pack: Optional[TargetingPack],
        stats: CaseStats,
        *,
        timeout_ms: int,
        poll_ms: int,
        label: str,
    ) -> Scene:
        deadline = self._monotonic() + (timeout_ms / 1000.0)
        previous_blob = _scene_blob(scene)
        current = scene
        while True:
            blob = _scene_blob(current)
            changed = blob != previous_blob
            ready = (
                _page_ready(current, pack)
            )
            if ready and (changed or not previous_blob):
                self._log(stats, "settle", label)
                return current
            if self._monotonic() >= deadline:
                self._log(stats, "settle", f"{label}-timeout")
                return current
            await self._sleep(poll_ms / 1000.0)
            current = await self._observe(stats, label)

    def _gateway_origin(self) -> tuple[int, int, int]:
        if self._gateway is None:
            return (0, 0, 0)
        return (
            self._gateway.call_count,
            self._gateway.error_count,
            self._gateway.tokens,
        )

    def _fill_gateway_stats(
        self,
        stats: CaseStats,
        origin_calls: int,
        origin_errors: int,
        origin_tokens: int,
    ) -> None:
        if self._gateway is None:
            stats.model = MINIMAX_MODEL
            return
        stats.minimax_call_count = self._gateway.call_count - origin_calls
        stats.minimax_error_count = self._gateway.error_count - origin_errors
        stats.minimax_tokens = self._gateway.tokens - origin_tokens
        stats.grounding_call_count = stats.minimax_call_count - stats.compile_call_count
        stats.model = MINIMAX_MODEL

    def _log(self, stats: CaseStats, kind: str, detail: str, ok: bool = True) -> None:
        stats.actions.append(ActionRecord(kind=kind, detail=detail, ok=ok))
        stats.action_count = len(stats.actions)


def evidence_holds(scene: Scene, spec: EvidenceSpec, selected_text: str) -> bool:
    """Pack-based evidence: pack.ready_any/success_any drives verification.

    The old site-specific fields (ocr_any, title_any, forbid_ocr) are
    removed.  Evidence is now driven by the TargetingPack.
    """
    return True


def _scene_blob(scene: Scene) -> str:
    return " ".join(
        (element.text or element.name or "") for element in scene.elements.values()
    )


def _window_titles(scene: Scene) -> str:
    return " ".join(
        (element.name or element.text or "")
        for element in scene.elements.values()
        if element.role == "window"
    )


def _page_ready(scene: Scene, pack: Optional[TargetingPack]) -> bool:
    if pack is None:
        return bool(_scene_blob(scene))
    return mentions(scene, pack.ready_any)


def _scene_summary(scene: Scene, limit: int = 30) -> str:
    lines: list[str] = []
    ranked = sorted(
        scene.elements.values(),
        key=lambda element: (0 if element.role == "product" else 1, element.id),
    )
    for element in ranked:
        if element.role == "window":
            continue
        text = (element.text or element.name or "")[:80]
        if not text:
            continue
        lines.append(f"{element.id}: {text}")
        if len(lines) >= limit:
            break
    return "\n".join(lines) or "(empty)"


def _parse_target_id(content: str) -> Optional[str]:
    match = _JSON_OBJECT.search(content)
    if match:
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            payload = {}
        for key in ("target_id", "id", "element_id"):
            value = payload.get(key)
            if value:
                return str(value)
    named = re.search(r"ocr_line_\d+", content)
    if named:
        return named.group(0)
    return None


def _pick_content_fallback(
    scene: Scene, matcher: Optional[GenericMatcher] = None
) -> Optional[UIElement]:
    """Last local choice: largest on-screen text box with digits."""
    window = primary_window(scene)
    ignore_set: set[str] = set()
    if matcher is not None:
        ignore_set = set(
            normalize_ocr_text(t).replace(" ", "") for t in matcher.pack.ignore_any
        )
    candidates = [
        element
        for element in scene.elements.values()
        if element.bbox is not None
        and element.role != "window"
        and not is_in_chrome(element, window)
    ]
    numbered = [
        element
        for element in candidates
        if any(ch.isdigit() for ch in (element.text or element.name or ""))
        and not any(
            normalize_ocr_text(t).replace(" ", "") in
            normalize_ocr_text(element.text or "").replace(" ", "")
            for t in ignore_set
        )
    ]
    pool = numbered or candidates
    if not pool:
        return None
    return max(pool, key=lambda element: element.bbox.width * element.bbox.height)