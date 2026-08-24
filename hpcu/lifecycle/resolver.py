"""Local-first application candidate binding for launch nodes."""

from __future__ import annotations

import json
from dataclasses import replace

from hpcu.gateway.async_gateway import call_gateway_async
from hpcu.gateway.gateway import Gateway, ModelCallPurpose
from hpcu.gateway.json_response import select_json_object
from hpcu.lifecycle.launcher import ApplicationCandidate, ApplicationLauncher
from hpcu.runtime_config import semantic_call_timeout_ms
from hpcu.schemas.action import ActionOp, ActionTarget
from hpcu.schemas.capability import Capability
from hpcu.schemas.plan import PlanIR


class ApplicationPlanResolver:
    """Bind OS-discovered launch candidates, using AI only for real ambiguity."""

    def __init__(self, *, config: dict) -> None:
        self._config = config

    async def resolve(
        self,
        plan: PlanIR,
        launcher: ApplicationLauncher | None,
        gateway: Gateway | None,
    ) -> PlanIR:
        if launcher is None:
            return plan
        if launcher.capabilities().discovery is Capability.UNSUPPORTED:
            return plan
        nodes = dict(plan.nodes)
        changed = False
        for node_id, node in tuple(nodes.items()):
            action = node.action
            if action.op is not ActionOp.LAUNCH_APPLICATION:
                continue
            if action.target.locator:
                continue
            application = (action.value or "").strip()
            if not application:
                continue
            try:
                discovery = await launcher.discover(application)
            except Exception:
                # Discovery is advisory at plan time. Execution will retry through
                # the typed lifecycle boundary and report an explicit failure.
                continue
            if not discovery.candidates:
                continue
            candidate_id = discovery.preferred_candidate_id
            if candidate_id is None and len(discovery.candidates) == 1:
                candidate_id = discovery.candidates[0].id
            if candidate_id is None and gateway is not None:
                candidate_id = await self._semantic_select(
                    plan,
                    application,
                    discovery.candidates,
                    gateway,
                )
            if candidate_id is None:
                continue
            candidate_ids = {candidate.id for candidate in discovery.candidates}
            if candidate_id not in candidate_ids:
                continue
            nodes[node_id] = replace(
                node,
                action=replace(
                    action,
                    target=ActionTarget(
                        element_id=action.target.element_id,
                        locator=candidate_id,
                    ),
                ),
            )
            changed = True
        if not changed:
            return plan
        return replace(plan, nodes=nodes)

    async def _semantic_select(
        self,
        plan: PlanIR,
        application: str,
        candidates: tuple[ApplicationCandidate, ...],
        gateway: Gateway,
    ) -> str | None:
        limits = self._config.get("semantic", {}).get("request_limits", {})
        max_tokens = int(limits.get("application_selection_max_tokens", 256))
        if max_tokens <= 0:
            return None
        try:
            response = await call_gateway_async(
                gateway,
                self._prompt(plan, application, candidates),
                system_prompt=(
                    "Select one OS-discovered application candidate only when it "
                    "matches the requested logical application. Never invent an ID."
                ),
                max_tokens=max_tokens,
                purpose=ModelCallPurpose.APPLICATION_SELECTION,
                timeout_ms=semantic_call_timeout_ms(self._config),
            )
        except Exception:
            # Semantic selection is optional. Leaving the locator unresolved makes
            # the platform launcher return DECISION_REQUIRED before side effects.
            return None
        try:
            payload = select_json_object(
                response.content,
                required_keys=("candidate_id",),
                allowed_keys=("candidate_id",),
                schema_name="application selection",
            )
        except ValueError:
            return None
        candidate_id = payload.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            return None
        candidate_id = candidate_id.strip()
        if candidate_id not in {candidate.id for candidate in candidates}:
            return None
        return candidate_id

    @staticmethod
    def _prompt(
        plan: PlanIR,
        application: str,
        candidates: tuple[ApplicationCandidate, ...],
    ) -> str:
        return json.dumps(
            {
                "instruction": plan.goal.raw_instruction,
                "logical_application": application,
                "candidates": [
                    {
                        "candidate_id": candidate.id,
                        "label": candidate.label,
                        "window_class": candidate.window_class,
                        "executable": candidate.executable,
                    }
                    for candidate in candidates
                ],
                "contract": (
                    "Return exactly one JSON object with candidate_id equal to "
                    "one candidate_id above. Do not return commands, paths, "
                    "coordinates, selectors, or new application names."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
