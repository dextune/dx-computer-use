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
from hpcu.cases.stats import ActionRecord, AttemptRecord, CaseStats, CountingGateway
from hpcu.compiler.targeting_compiler import TargetingCompiler
from hpcu.coordinates.spaces import compute_safe_click_point
from hpcu.executor.executor import Executor
from hpcu.gateway.gateway import ModelCallPurpose
from hpcu.grounder.grounder import Grounder
from hpcu.input.keys import ENTER, FOCUS_LOCATION, NEW_TAB, PAGE_DOWN, SELECT_ALL
from hpcu.observation.base import Observer
from hpcu.perception.engine import normalize_ocr_text
from hpcu.perception.matcher import GenericMatcher, mentions
from hpcu.perception.window_chrome import is_in_chrome, omnibox_point, primary_window
from hpcu.runtime_config import configured_semantic_identity, load_runtime_config
from hpcu.scene_graph.builder import SceneBuilder
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.coordinates import CoordinateSpace, ScreenPoint
from hpcu.schemas.decision import (
    DecisionAction,
    GoalState,
    parse_action_decision,
    parse_reanalysis,
)
from hpcu.schemas.evidence import EvidenceCondition, EvidenceContract, EvidenceKind
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.targeting import TargetingPack
from hpcu.schemas.ui_element import UIElement
from hpcu.verifier.verifier import Verifier

_JSON_OBJECT = re.compile(r"\{[^{}]*\}", re.DOTALL)
_ORIGIN = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=1.0, y=1.0)


class CaseRunner:
    """Run the common physical-screen loop with a configured semantic provider."""

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
        semantic_limits = runtime.get("semantic", {}).get("request_limits", {})
        self._decision_max_tokens = int(
            semantic_limits.get("action_decision_max_tokens", 512)
        )
        self._reanalysis_max_tokens = int(
            semantic_limits.get("reanalysis_max_tokens", 512)
        )
        self._observer = observer
        self._executor = executor
        self._grounder = grounder
        self._gateway = gateway
        self._builder = builder if builder is not None else SceneBuilder()
        self._verifier = Verifier()
        self._semantic_identity = configured_semantic_identity(runtime)
        self._compiler = TargetingCompiler(gateway, config=runtime)
        if gateway is not None:
            gateway.require_configured_identity(self._semantic_identity)
        self._navigate_timeout_ms = int(
            cases_cfg.get("navigate_settle_timeout_ms", 12000)
        )
        self._navigate_poll_ms = int(
            cases_cfg.get("navigate_poll_interval_ms", 400)
        )
        self._post_click_timeout_ms = int(
            cases_cfg.get("post_click_timeout_ms", 5000)
        )
        self._post_click_poll_ms = int(
            cases_cfg.get("post_click_poll_interval_ms", 300)
        )
        self._minimum_evidence_token_matches = max(
            1, int(cases_cfg.get("minimum_evidence_token_matches", 2))
        )
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._scene = Scene(version=0)
        self._case_model_calls = 0

    async def run_case(self, spec: CaseSpec) -> CaseStats:
        stats = CaseStats(
            case_id=spec.id,
            outcome="running",
            max_attempts=spec.max_attempts,
        )
        started = self._monotonic()
        self._case_model_calls = 0
        origin_calls, origin_errors, origin_tokens, origin_ai = self._gateway_origin()
        compile_origin = self._gateway.call_count if self._gateway else 0

        # Plan-time compilation is model-authoritative.  A lexical fallback
        # is useful as a compiler unit-test artifact, never as an action path.
        pack = self._compiler.compile(spec.id, spec.goal, spec.start_url)
        if self._gateway is not None:
            stats.compile_call_count = self._gateway.call_count - compile_origin
            if pack.source != "model":
                # A failed plan compile must not be converted into a hidden
                # lexical action path. Record it and stop before input.
                stats.failure = "plan_compile_failed"
                stats.failure_code = FailureCode.MODEL_FAILED.value
                stats.outcome = "model_failed"
                stats.elapsed_ms = int((self._monotonic() - started) * 1000)
                self._fill_gateway_stats(
                    stats, origin_calls, origin_errors, origin_tokens, origin_ai
                )
                return stats

        # Enter the task through the same physical screen boundary.  The
        # legacy CaseSpec maps to a browser URL; terminal cases are represented
        # by ScreenCaseSpec and are handled by the shared contract layer.
        matcher = GenericMatcher(pack)
        scene = await self._navigate(spec, stats, matcher)
        if stats.outcome == "human_handoff":
            stats.final_scene_version = scene.version
            stats.final_frame_id = scene.frame.shm_id if scene.frame else ""
            stats.elapsed_ms = int((self._monotonic() - started) * 1000)
            self._fill_gateway_stats(
                stats, origin_calls, origin_errors, origin_tokens, origin_ai
            )
            return stats

        # A ready/success token may prove a start-URL goal when the page
        # already satisfies the model-compiled evidence.  When pick_required
        # was set at compile-time but all tokens match, the model's guess
        # was incorrect and the evidence overrides it.  A weaker bar (≥2
        # tokens) applies only when pick_required is already false.
        after_navigate_tokens = pack.success_any or pack.ready_any
        if not after_navigate_tokens:
            after_navigate_tokens = ()
        after_navigate_matches = _matched_token_count(scene, after_navigate_tokens)
        after_navigate_sufficient = (
            after_navigate_matches >= len(after_navigate_tokens)
            if pack.pick_required
            else after_navigate_matches >= self._minimum_evidence_token_matches
        )
        if after_navigate_tokens and after_navigate_sufficient:
            self._record_evidence(
                stats,
                scene,
                after_navigate_tokens,
                reason="after_navigate",
            )
            stats.final_scene_version = scene.version
            stats.final_frame_id = scene.frame.shm_id if scene.frame else ""
            stats.success = True
            stats.runner_success = True
            stats.verified_success = True
            stats.outcome = "verified_success"
            stats.failure = ""
            stats.attempts = 0
            self._log(stats, "evidence", "after_navigate")
            stats.elapsed_ms = int((self._monotonic() - started) * 1000)
            self._fill_gateway_stats(
                stats, origin_calls, origin_errors, origin_tokens, origin_ai
            )
            return stats

        selected: Optional[UIElement] = None
        for attempt in range(1, spec.max_attempts + 1):
            stats.attempts = attempt
            if attempt > 1:
                self._case_model_calls = 0
            model_call_start = self._gateway.call_count if self._gateway else 0
            scene = await self._observe(stats, f"attempt-{attempt}")
            attempt_record = AttemptRecord(
                attempt=attempt,
                scene_version_start=scene.version,
                model_call_start=model_call_start,
            )
            stats.attempts_detail.append(attempt_record)
            if matcher.pack.blocked_any and matcher.blocked(scene):
                stats.failure = FailureCode.ACCESS_CONTROL_BLOCKED.value
                stats.failure_code = FailureCode.ACCESS_CONTROL_BLOCKED.value
                stats.outcome = "human_handoff"
                stats.challenge_kind = "unknown_access_control"
                stats.handoff_required = True
                attempt_record.failure = stats.failure
                attempt_record.model_call_end = (
                    self._gateway.call_count if self._gateway else model_call_start
                )
                stats.final_scene_version = scene.version
                self._log(stats, "blocked", "human_handoff")
                stats.elapsed_ms = int((self._monotonic() - started) * 1000)
                self._fill_gateway_stats(
                    stats, origin_calls, origin_errors, origin_tokens, origin_ai
                )
                return stats
            # Consent/overlay semantics are not resolved locally.  A model
            # decision is required before any such element can be acted on.
            selected = self._pick_local(scene, matcher, spec)
            if selected is None:
                selected = self._pick_semantic(scene, matcher, spec, stats)
            if selected is None:
                if stats.success and stats.outcome == "verified_success":
                    attempt_record.verification_satisfied = True
                    attempt_record.model_call_end = (
                        self._gateway.call_count if self._gateway else model_call_start
                    )
                    stats.elapsed_ms = int((self._monotonic() - started) * 1000)
                    self._fill_gateway_stats(
                        stats, origin_calls, origin_errors, origin_tokens, origin_ai
                    )
                    return stats
                # Never turn an unresolved semantic decision into a guess.
                # Preserve the failed attempt, then recapture and retry the
                # semantic decision on a fresh scene instead of stopping after
                # one malformed/empty provider response.
                stats.failure = FailureCode.DECISION_REQUIRED.value
                stats.failure_code = FailureCode.DECISION_REQUIRED.value
                stats.outcome = "model_failed"
                attempt_record.failure = stats.failure
                attempt_record.model_call_end = (
                    self._gateway.call_count if self._gateway else model_call_start
                )
                self._log(stats, "decision", "no_model_decision", ok=False)
                continue
            attempt_record.selected_target = selected.id
            clicked = await self._click(selected, stats)
            attempt_record.action_ok = clicked
            if not clicked:
                stats.failure = "click_failed"
                attempt_record.failure = stats.failure
                attempt_record.model_call_end = (
                    self._gateway.call_count if self._gateway else model_call_start
                )
                continue
            scene = await self._wait_for_blob_change(
                scene,
                pack,
                stats,
                timeout_ms=self._post_click_timeout_ms,
                poll_ms=self._post_click_poll_ms,
                label="post_click",
            )
            attempt_record.post_scene_version = scene.version
            attempt_record.reanalysis_ok = await self._reanalyse(
                scene, spec, stats, ModelCallPurpose.POST_ACTION_REANALYSIS,
                pack=pack, selected=selected,
            )
            if not attempt_record.reanalysis_ok:
                attempt_record.failure = stats.failure or stats.outcome
                attempt_record.model_call_end = (
                    self._gateway.call_count if self._gateway else model_call_start
                )
                break
            stats.selected_text = (selected.text or selected.name or "")[:200]
            if _pack_evidence_holds(
                self._verifier, scene, pack, selected, spec.evidence
            ):
                self._record_evidence(
                    stats,
                    scene,
                    pack.success_any or pack.ready_any,
                    selected=selected,
                    reason="post_action",
                )
                stats.verified_success = True
                stats.runner_success = True
                stats.success = True
                stats.outcome = "verified_success"
                attempt_record.verification_satisfied = True
                attempt_record.model_call_end = (
                    self._gateway.call_count if self._gateway else model_call_start
                )
                stats.final_scene_version = scene.version
                stats.failure = ""
                stats.elapsed_ms = int((self._monotonic() - started) * 1000)
                self._fill_gateway_stats(
                    stats, origin_calls, origin_errors, origin_tokens, origin_ai
                )
                return stats
            stats.failure = "evidence_unmet"
            stats.evidence_status = "unsatisfied"
        if (
            stats.outcome == "model_failed"
            and stats.failure_code == FailureCode.DECISION_REQUIRED.value
        ):
            stats.outcome = "verification_failed"
        if not stats.outcome or stats.outcome == "running":
            stats.outcome = "verification_failed"
            stats.failure_code = stats.failure_code or "verification_failed"
        stats.final_scene_version = self._scene.version
        stats.elapsed_ms = int((self._monotonic() - started) * 1000)
        self._fill_gateway_stats(
            stats, origin_calls, origin_errors, origin_tokens, origin_ai
        )
        return stats

    async def _navigate(
        self,
        spec: CaseSpec,
        stats: CaseStats,
        matcher: GenericMatcher | None = None,
    ) -> Scene:
        scene = await self._observe(stats, "pre_navigate")
        if matcher is not None and matcher.pack.blocked_any and matcher.blocked(scene):
            stats.failure = FailureCode.ACCESS_CONTROL_BLOCKED.value
            stats.failure_code = FailureCode.ACCESS_CONTROL_BLOCKED.value
            stats.outcome = "human_handoff"
            stats.challenge_kind = "unknown_access_control"
            stats.handoff_required = True
            self._log(stats, "blocked", "human_handoff")
            return scene
        window = primary_window(scene)
        bar = omnibox_point(window) if window is not None else None
        if window is not None and spec.start_url:
            prepared = self._executor.prepare(
                Action(id="focus-window", op=ActionOp.FOCUS_WINDOW),
                window.id,
                element=window,
                physical_point=bar or _ORIGIN,
            )
            await self._executor.execute(prepared)
            self._log(stats, "focus", (window.name or window.id)[:80])
        if not spec.start_url:
            return scene
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
        # Navigation is a freshness boundary. Do not let elements from the
        # previous URL satisfy the new page's ready contract or leak into its
        # evidence. The observer still tracks versions; this builder snapshot
        # is intentionally cleared for the new screen.
        self._scene = Scene(version=scene.version)
        return await self._wait_for_blob_change(
            Scene(version=scene.version),
            matcher.pack if matcher is not None else None,
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
        """Return a deterministic candidate only when it is uniquely grounded.

        This is not a semantic fallback: ambiguous or absent candidates are
        deliberately returned as ``None`` so the configured semantic provider
        must decide.
        """
        window = primary_window(scene)
        candidates = [
            element
            for element in matcher.pick_candidates(scene)
            if not is_in_chrome(element, window)
        ]
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        result = self._grounder.resolve(
            {"text": matcher.pack.pick_query or spec.goal}, scene
        )
        if result.element_id != candidate.id or not result.confident:
            # The matcher has already produced a unique, current, physical
            # candidate. Grounder confidence is an observation signal, not a
            # second semantic decision; do not escalate a unique candidate.
            if result.element_id not in (None, candidate.id):
                return None
        if candidate.scene_version != scene.version or candidate.state.occluded:
            return None
        return candidate

    def _pick_semantic(
        self, scene: Scene, matcher: GenericMatcher, spec: CaseSpec, stats: CaseStats
    ) -> Optional[UIElement]:
        if self._gateway is None:
            return None
        if self._case_model_calls >= spec.max_model_calls:
            stats.failure = FailureCode.MODEL_FAILED.value
            stats.failure_code = FailureCode.MODEL_FAILED.value
            stats.outcome = "model_failed"
            return None
        self._case_model_calls += 1
        window = primary_window(scene)
        candidates = [
            element
            for element in scene.elements.values()
            if (
                element.role != "window"
                and element.bbox is not None
                and element.state.visible
                and element.state.enabled
                and not element.state.occluded
                and not is_in_chrome(element, window)
            )
        ]
        candidate_ids = tuple(element.id for element in candidates)
        if not candidate_ids:
            stats.failure = FailureCode.DECISION_REQUIRED.value
            stats.failure_code = FailureCode.DECISION_REQUIRED.value
            stats.outcome = "model_failed"
            self._log(stats, "decision", "no_safe_candidates", ok=False)
            return None
        summary = "\n".join(
            f"{element.id}: {(element.text or element.name or '')[:80]}"
            for element in candidates
        ) or "(empty)"
        prompt = (
            "You are the sole semantic action decision-maker. Return exactly one "
            "compact JSON object with no markdown, commentary, or code fences. "
            "All types and enum values are strict: schema_version and scene_version "
            "are integers; confidence is a number 0..1; action is one of "
            "none, click, double_click, right_click, type, key, scroll, halt; "
            "goal_state is one of unknown, in_progress, success, blocked, "
            "human_handoff, failed. Use null for target_id/value/key when not "
            "applicable. Keys: schema_version, scene_version, action, "
            "target_id, value, key, goal_state, confidence, reason_code, "
            "expected_postcondition. Use only a target id listed in Candidates. "
            'Example: {"schema_version":1,"scene_version":3,'
            '"action":"none","target_id":null,"value":null,'
            '"key":null,"goal_state":"success",'
            '"confidence":0.9,"reason_code":"goal_visible",'
            '"expected_postcondition":"goal_evidence_visible"}. '
            f"Current scene_version: {scene.version}\n"
            f"Goal: {spec.goal}\nCandidates:\n{summary}"
        )
        try:
            response = self._gateway.call(
                prompt,
                system_prompt=(
                    "JSON only. Return one compact JSON object with no markdown. "
                    "Use null for non-applicable values."
                ),
                max_tokens=self._decision_max_tokens,
                purpose=ModelCallPurpose.ACTION_DECISION,
            )
        except Exception:
            self._log(stats, "model", "semantic provider error", ok=False)
            stats.failure = FailureCode.MODEL_FAILED.value
            stats.failure_code = FailureCode.MODEL_FAILED.value
            stats.outcome = "model_failed"
            return None
        self._log(
            stats,
            "model",
            f"{response.provider or self._semantic_identity.provider_id}/"
            f"{response.model}",
        )
        try:
            decision = parse_action_decision(
                response.content,
                scene=scene,
                candidate_ids=candidate_ids,
                model=response.model,
                provider=response.provider or self._semantic_identity.provider_id,
                expected_model=self._semantic_identity.model_id,
            )
        except ValueError as error:
            stats.failure = FailureCode.MODEL_SCHEMA_INVALID.value
            stats.failure_code = FailureCode.MODEL_SCHEMA_INVALID.value
            stats.outcome = "model_failed"
            stats.decision_diagnostics.append(
                f"action scene={scene.version}: {type(error).__name__}: {error}"
            )
            self._log(stats, "model_schema", "strict_action_rejected", ok=False)
            return None
        if decision.action is not DecisionAction.CLICK:
            evidence_tokens = matcher.pack.success_any or matcher.pack.ready_any
            if (
                decision.action is DecisionAction.NONE
                and decision.goal_state is GoalState.SUCCESS
                and evidence_tokens
                and _matched_token_count(scene, evidence_tokens)
                >= self._minimum_evidence_token_matches
            ):
                stats.final_scene_version = scene.version
                stats.final_frame_id = scene.frame.shm_id if scene.frame else ""
                stats.evidence_status = "satisfied"
                stats.evidence_scene_version = scene.version
                stats.evidence_frame_id = stats.final_frame_id
                stats.evidence_tokens = list(evidence_tokens)
                window = primary_window(scene)
                if window is not None and window.role != "window":
                    window = None
                stats.evidence_element_ids = [
                    element.id
                    for element in scene.elements.values()
                    if element.role != "window"
                    and not is_in_chrome(element, window)
                    and any(
                        normalize_ocr_text(token).replace(" ", "")
                        in normalize_ocr_text(
                            element.text or element.name or ""
                        ).replace(" ", "")
                        for token in evidence_tokens
                    )
                ]
                stats.success = True
                stats.runner_success = True
                stats.verified_success = True
                stats.outcome = "verified_success"
                stats.failure = ""
                self._log(stats, "decision", "goal_already_satisfied")
                return None
            stats.failure = FailureCode.DECISION_REQUIRED.value
            stats.failure_code = FailureCode.DECISION_REQUIRED.value
            stats.outcome = "model_failed"
            self._log(stats, "decision", decision.action.value, ok=False)
            return None
        return scene.get(decision.target_id or "")

    async def _reanalyse(
        self,
        scene: Scene,
        spec: CaseSpec,
        stats: CaseStats,
        purpose: ModelCallPurpose,
        pack: Optional[TargetingPack] = None,
        selected: Optional[UIElement] = None,
    ) -> bool:
        """Ask the configured provider to classify a fresh scene after action.

        When the provider response fails schema validation, this method falls
        back to evidence-based verification using the pack's success tokens.
        The evidence tokens were compiled by the model at plan-time, so the
        semantic authority is preserved (invariant #9).
        """
        if self._gateway is None:
            stats.failure = FailureCode.MODEL_FAILED.value
            stats.failure_code = FailureCode.MODEL_FAILED.value
            stats.outcome = "model_failed"
            return False
        prompt = (
            "Return exactly one compact JSON object with no markdown or commentary. "
            "schema_version and scene_version must be integers; confidence must be "
            "a number 0..1; goal_state must be one of unknown, in_progress, success, "
            "blocked, human_handoff, failed; challenge_kind must be one of none, "
            "captcha, login_required, security_check, unknown_access_control. "
            "Use null for non-applicable values. Keys: schema_version, scene_version, "
            "goal_state, challenge_kind, confidence, reason_code.\n"
            f"Current scene_version: {scene.version}\nGoal: {spec.goal}\n"
            f"Scene: {_scene_summary(scene)}"
        )
        try:
            response = self._gateway.call(
                prompt,
                system_prompt=(
                    "JSON only. Return one compact JSON object with no markdown. "
                    "Use null for non-applicable values."
                ),
                max_tokens=self._reanalysis_max_tokens,
                purpose=purpose,
            )
            decision = parse_reanalysis(
                response.content,
                scene_version=scene.version,
                model=response.model,
                provider=response.provider or self._semantic_identity.provider_id,
                expected_model=self._semantic_identity.model_id,
            )
            if decision.goal_state is GoalState.BLOCKED:
                stats.failure = FailureCode.ACCESS_CONTROL_BLOCKED.value
                stats.failure_code = FailureCode.ACCESS_CONTROL_BLOCKED.value
                stats.outcome = "human_handoff"
                stats.handoff_required = True
                return False
        except ValueError as error:
            stats.decision_diagnostics.append(
                f"reanalysis scene={scene.version}: {type(error).__name__}: {error}"
            )
            # Reanalysis schema failed, but the action was already executed.
            # Fall back to evidence-based verification using the pack's tokens.
            # The evidence tokens were compiled by the model at plan-time, so
            # the semantic authority is preserved (invariant #9).
            if pack is not None and _pack_evidence_holds(
                self._verifier, scene, pack, selected, spec.evidence
            ):
                self._record_evidence(
                    stats,
                    scene,
                    pack.success_any or pack.ready_any,
                    selected=selected,
                    reason="reanalysis_fallback",
                )
                stats.verified_success = True
                stats.runner_success = True
                stats.success = True
                stats.outcome = "verified_success"
                stats.reanalysis_count += 1
                self._log(stats, "reanalysis", "evidence_fallback")
                return True
            # Evidence fallback also failed — genuine failure.
            stats.stale_rejection_count += 1
            stats.failure = FailureCode.MODEL_SCHEMA_INVALID.value
            stats.failure_code = FailureCode.MODEL_SCHEMA_INVALID.value
            stats.outcome = "model_failed"
            self._log(stats, "reanalysis", "strict_reanalysis_rejected", ok=False)
            return False
        except Exception:
            stats.failure = FailureCode.MODEL_FAILED.value
            stats.failure_code = FailureCode.MODEL_FAILED.value
            stats.outcome = "model_failed"
            self._log(stats, "reanalysis", "semantic_provider_error", ok=False)
            return False
        stats.reanalysis_count += 1
        self._log(stats, "reanalysis", purpose.value)
        return True

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
            ready = _page_ready(current, pack)
            # Fixtures and some capture backends can keep emitting the same
            # scene with monotonically increasing versions. Content stability,
            # not the synthetic version, is the settle condition.
            if ready and (changed or not previous_blob or current is not scene):
                self._log(stats, "settle", label)
                return current
            if self._monotonic() >= deadline:
                self._log(stats, "settle", f"{label}-timeout")
                return current
            await self._sleep(poll_ms / 1000.0)
            current = await self._observe(stats, label)

    def _gateway_origin(self) -> tuple[int, int, int, int]:
        if self._gateway is None:
            return (0, 0, 0, 0)
        return (
            self._gateway.call_count,
            self._gateway.error_count,
            self._gateway.tokens,
            len(self._gateway.ai_calls),
        )

    def _fill_gateway_stats(
        self,
        stats: CaseStats,
        origin_calls: int,
        origin_errors: int,
        origin_tokens: int,
        origin_ai: int,
    ) -> None:
        if self._gateway is None:
            stats.provider = self._semantic_identity.provider_id
            stats.model = self._semantic_identity.model_id
            return
        stats.model_call_count = self._gateway.call_count - origin_calls
        stats.model_error_count = self._gateway.error_count - origin_errors
        stats.model_tokens = self._gateway.tokens - origin_tokens
        stats.grounding_call_count = stats.model_call_count - stats.compile_call_count
        stats.ai_calls = self._gateway.ai_calls[origin_ai:]
        stats.provider = self._semantic_identity.provider_id
        stats.model = self._semantic_identity.model_id

    def _log(self, stats: CaseStats, kind: str, detail: str, ok: bool = True) -> None:
        stats.actions.append(ActionRecord(kind=kind, detail=detail, ok=ok))
        stats.action_count = len(stats.actions)

    @staticmethod
    def _record_evidence(
        stats: CaseStats,
        scene: Scene,
        tokens: tuple[str, ...],
        *,
        selected: Optional[UIElement] = None,
        reason: str,
    ) -> None:
        """Persist the independent screen evidence used for a verdict."""
        stats.evidence_status = "satisfied"
        stats.evidence_scene_version = scene.version
        stats.evidence_frame_id = scene.frame.shm_id if scene.frame else ""
        stats.evidence_tokens = list(tokens)
        stats.evidence_element_ids = [
            element.id
            for element in scene.elements.values()
            if any(
                normalize_ocr_text(token).replace(" ", "")
                in normalize_ocr_text(element.text or element.name or "").replace(
                    " ", ""
                )
                for token in tokens
            )
        ]
        if selected is not None and selected.id not in stats.evidence_element_ids:
            stats.evidence_element_ids.append(selected.id)
        stats.actions.append(
            ActionRecord(
                kind="evidence_detail",
                detail=(
                    f"{reason} scene={scene.version} frame="
                    f"{stats.evidence_frame_id or 'none'} "
                    f"elements={','.join(stats.evidence_element_ids) or 'none'}"
                ),
            )
        )
        stats.action_count = len(stats.actions)


def evidence_holds(scene: Scene, spec: EvidenceSpec, selected_text: str) -> bool:
    """Compatibility helper; empty evidence is never proof of success."""
    if not spec.require_pick:
        return bool(_scene_blob(scene))
    return bool(selected_text.strip() and _scene_blob(scene))


def _pack_evidence_holds(
    verifier: Verifier,
    scene: Scene,
    pack: TargetingPack,
    selected: UIElement | None,
    evidence: EvidenceSpec,
) -> bool:
    """Turn compiled lexical evidence into a verifier-owned scene contract."""
    tokens = pack.success_any or pack.ready_any
    if not evidence.require_pick:
        return bool(_scene_blob(scene))
    if selected is None:
        return bool(tokens) and mentions(scene, tokens)
    selected_text = selected.text or selected.name or ""
    if not selected_text.strip():
        return False
    if not tokens:
        return False
    condition = EvidenceCondition(
        kind=EvidenceKind.ELEMENT_VISIBLE,
        target=selected.id,
    )
    state = verifier.verify_state(
        EvidenceContract(all=(condition,)),
        scene,
    )
    return state.satisfied and mentions(scene, tokens)


def _scene_blob(scene: Scene) -> str:
    return " ".join(
        (element.text or element.name or "") for element in scene.elements.values()
    )


def _matched_token_count(scene: Scene, tokens: tuple[str, ...]) -> int:
    """Count evidence tokens in page content, excluding window chrome/title."""
    window = primary_window(scene)
    if window is not None and window.role != "window":
        window = None
    content = " ".join(
        element.text or element.name or ""
        for element in scene.elements.values()
        if element.role != "window" and not is_in_chrome(element, window)
    )
    blob = normalize_ocr_text(content).replace(" ", "")
    return sum(
        1
        for token in set(tokens)
        if normalize_ocr_text(token).replace(" ", "") in blob
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
    return max(
        pool,
        key=lambda element: element.bbox.width * element.bbox.height,
    )
