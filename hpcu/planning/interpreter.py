"""CPU-first interpretation of user commands into :class:`GoalEnvelope`."""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urlparse

from hpcu.schemas.planning import (
    ExecutionMode,
    GoalConstraint,
    GoalEnvelope,
    GoalIntent,
)

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_NUMBER_UNIT_RE = re.compile(
    r"(?P<prefix>이하|미만|이상|초과|at\s+most|under|below|"
    r"at\s+least|over|above)?\s*"
    r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>만원|천원|원|개|명|초|분|시간|%|percent|usd|krw|달러)?\s*"
    r"(?P<suffix>이하|미만|이상|초과)?",
    re.IGNORECASE,
)

# Domain-neutral command grammar. These are intent verbs, not site/CTA tokens.
_INTENT_MARKERS: tuple[tuple[GoalIntent, tuple[str, ...]], ...] = (
    (GoalIntent.COMPARE, ("비교", "compare", "차이")),
    (GoalIntent.MONITOR, ("모니터", "감시", "계속 확인", "monitor", "watch")),
    (GoalIntent.SUBMIT, ("제출", "전송", "보내", "submit", "send")),
    (GoalIntent.EDIT, ("수정", "변경", "입력", "작성", "edit", "change", "type")),
    (GoalIntent.SELECT, ("고르", "선택", "pick", "select", "choose")),
    (GoalIntent.SEARCH, ("검색", "찾아", "찾기", "search", "find")),
    (GoalIntent.NAVIGATE, ("열어", "이동", "접속", "navigate", "open", "visit")),
    (GoalIntent.READ, ("읽어", "알려", "확인", "read", "tell", "inspect")),
)

_OPERATOR_MAP = {
    "이하": "lte",
    "미만": "lt",
    "이상": "gte",
    "초과": "gt",
    "at most": "lte",
    "under": "lt",
    "below": "lt",
    "at least": "gte",
    "over": "gt",
    "above": "gt",
}


class GoalInterpreter:
    """Extract stable facts locally; leave only unresolved semantics as slots."""

    def interpret(
        self,
        instruction: str,
        *,
        goal_id: str = "goal",
        execution_mode: ExecutionMode = ExecutionMode.LOCAL_SEMANTIC,
        max_model_calls: int = 2,
        max_model_tokens: int = 0,
    ) -> GoalEnvelope:
        raw = str(instruction or "").strip()
        if not raw:
            raise ValueError("user instruction is required")

        explicit_url = self._extract_url(raw)
        intent = self._intent(raw, explicit_url)
        constraints = tuple(self._constraints(raw, explicit_url))
        entities: dict[str, str] = {}
        if explicit_url:
            entities["url"] = explicit_url
            host = urlparse(explicit_url).hostname
            if host:
                entities["host"] = host

        ambiguity_slots: list[str] = []
        if intent is GoalIntent.UNKNOWN:
            ambiguity_slots.append("intent")
        if intent in {GoalIntent.NAVIGATE, GoalIntent.SEARCH} and not explicit_url:
            ambiguity_slots.append("entry_strategy")

        return GoalEnvelope(
            id=goal_id,
            raw_instruction=raw,
            intent=intent,
            terminal_state=self._terminal_state(raw, intent),
            constraints=constraints,
            entities=entities,
            explicit_url=explicit_url,
            preferred_surface="browser" if explicit_url else "",
            execution_mode=execution_mode,
            ambiguity_slots=tuple(ambiguity_slots),
            evidence_requirements=self._evidence_requirements(intent),
            max_model_calls=max_model_calls,
            max_model_tokens=max_model_tokens,
        )

    @staticmethod
    def merge_semantic_slots(
        envelope: GoalEnvelope,
        *,
        intent: GoalIntent | None = None,
        terminal_state: str | None = None,
        preferred_surface: str | None = None,
    ) -> GoalEnvelope:
        """Fill declared unresolved slots without replacing CPU facts."""
        unresolved = set(envelope.ambiguity_slots)
        updates: dict[str, object] = {}
        if intent is not None:
            if "intent" not in unresolved:
                raise ValueError("intent was not an unresolved semantic slot")
            updates["intent"] = intent
            unresolved.remove("intent")
        if terminal_state is not None:
            normalized = terminal_state.strip()
            if not normalized:
                raise ValueError("terminal_state cannot be empty")
            updates["terminal_state"] = normalized
        if preferred_surface is not None:
            if "entry_strategy" not in unresolved:
                raise ValueError("entry_strategy was not an unresolved semantic slot")
            normalized = preferred_surface.strip()
            if not normalized:
                raise ValueError("preferred_surface cannot be empty")
            updates["preferred_surface"] = normalized
            unresolved.remove("entry_strategy")
        updates["ambiguity_slots"] = tuple(sorted(unresolved))
        return replace(envelope, **updates)

    @staticmethod
    def _extract_url(instruction: str) -> str:
        match = _URL_RE.search(instruction)
        if match is None:
            return ""
        candidate = match.group(0).rstrip(".,;:!?)")
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return candidate

    @staticmethod
    def _intent(instruction: str, explicit_url: str) -> GoalIntent:
        folded = instruction.casefold()
        for intent, markers in _INTENT_MARKERS:
            if any(marker.casefold() in folded for marker in markers):
                return intent
        if explicit_url:
            return GoalIntent.NAVIGATE
        return GoalIntent.UNKNOWN

    @staticmethod
    def _constraints(instruction: str, explicit_url: str) -> list[GoalConstraint]:
        constraints: list[GoalConstraint] = []
        url_span: tuple[int, int] | None = None
        if explicit_url:
            start = instruction.find(explicit_url)
            if start >= 0:
                url_span = (start, start + len(explicit_url))
        for index, match in enumerate(_NUMBER_UNIT_RE.finditer(instruction)):
            number = match.group("number")
            if not number:
                continue
            if (
                url_span
                and match.start() >= url_span[0]
                and match.end() <= url_span[1]
            ):
                continue
            operator_token = match.group("prefix") or match.group("suffix") or ""
            operator_raw = " ".join(operator_token.casefold().split())
            operator = _OPERATOR_MAP.get(operator_raw, "equals")
            unit = (match.group("unit") or "").casefold()
            value = number.replace(",", "") + (f" {unit}" if unit else "")
            constraints.append(
                GoalConstraint(
                    name=f"numeric_{index}",
                    value=value,
                    operator=operator,
                    raw_text=match.group(0).strip(),
                )
            )
        return constraints

    @staticmethod
    def _terminal_state(instruction: str, intent: GoalIntent) -> str:
        if intent is GoalIntent.UNKNOWN:
            return "사용자 명령의 완료 상태를 추가 해석해야 함"
        return f"{intent.value} 목표가 독립 증거로 확인됨: {instruction}"

    @staticmethod
    def _evidence_requirements(intent: GoalIntent) -> tuple[str, ...]:
        if intent is GoalIntent.NAVIGATE:
            return ("fresh_scene", "destination_visible")
        if intent is GoalIntent.SEARCH:
            return ("query_reflected", "result_visible")
        if intent is GoalIntent.SELECT:
            return ("selected_state_or_destination",)
        if intent in {GoalIntent.EDIT, GoalIntent.SUBMIT}:
            return ("state_transition", "result_visible")
        if intent in {GoalIntent.READ, GoalIntent.COMPARE, GoalIntent.MONITOR}:
            return ("observable_content",)
        return ("explicit_evidence_contract",)
