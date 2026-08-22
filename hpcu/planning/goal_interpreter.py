"""CPU-first goal interpretation with constrained semantic slot filling."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import replace

from hpcu.schemas.action import ActionOp
from hpcu.schemas.goal import (
    GoalConstraint,
    GoalEntity,
    GoalEnvelope,
    GoalRisk,
    IntentKind,
    Reversibility,
)
from hpcu.schemas.surface import SurfaceKind

SemanticSlotFiller = Callable[[GoalEnvelope], Mapping[str, str]]

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_NUMBER_RE = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?)\s*"
    r"(개|원|만원|달러|usd|krw|%|개 이하|개 이상)?",
    re.IGNORECASE,
)
_KO_SEARCH_RE = re.compile(r"에서\s+(.+?)(?:을|를)?\s*(?:검색|찾아|찾아줘|찾아 줘|찾)")
_EN_SEARCH_RE = re.compile(r"(?:search|find)\s+(?:for\s+)?(.+?)(?:\.|$)", re.IGNORECASE)


def _contains(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(word.casefold() in lowered for word in words)


class GoalInterpreter:
    """Interpret cheap facts locally; optionally fill only unresolved slots."""

    def interpret(
        self,
        instruction: str,
        *,
        semantic_fill: SemanticSlotFiller | None = None,
        latency_budget_ms: int = 30_000,
        model_call_budget: int = 2,
    ) -> GoalEnvelope:
        raw = instruction.strip()
        if not raw:
            raise ValueError("instruction must be non-empty")

        intent = self._intent(raw)
        surface = self._surface(raw)
        risk, reversibility = self._risk(raw)
        entities = list(self._entities(raw))
        constraints = list(self._constraints(raw))
        forbidden = list(self._forbidden_actions(raw))

        ambiguity: list[str] = []
        if intent is IntentKind.UNKNOWN:
            ambiguity.append("intent")
        if intent is IntentKind.SEARCH and not any(
            entity.kind == "search_query" for entity in entities
        ):
            ambiguity.append("search_query")

        envelope = GoalEnvelope(
            raw_instruction=raw,
            intent=intent,
            terminal_state=self._terminal_state(intent),
            entities=tuple(entities),
            constraints=tuple(constraints),
            preferred_surface=surface,
            forbidden_actions=tuple(dict.fromkeys(forbidden)),
            risk_class=risk,
            reversibility=reversibility,
            ambiguity_slots=tuple(ambiguity),
            evidence_requirements=self._evidence_requirements(intent),
            latency_budget_ms=latency_budget_ms,
            model_call_budget=model_call_budget,
        )
        if not envelope.ambiguity_slots or semantic_fill is None:
            return envelope
        return self._apply_semantic_fill(envelope, semantic_fill(envelope))

    @staticmethod
    def _intent(text: str) -> IntentKind:
        ordered = (
            (IntentKind.COMPARE, ("비교", "compare")),
            (IntentKind.EDIT, ("수정", "바꿔", "변경", "rename", "edit")),
            (IntentKind.SUBMIT, ("제출", "전송", "보내", "submit", "send")),
            (IntentKind.SEARCH, ("검색", "찾아", "find", "search")),
            (IntentKind.SELECT, ("선택", "골라", "choose", "select")),
            (IntentKind.NAVIGATE, ("열어", "접속", "이동", "open", "go to", "navigate")),
        )
        for intent, words in ordered:
            if _contains(text, words):
                return intent
        if _URL_RE.search(text):
            return IntentKind.NAVIGATE
        return IntentKind.UNKNOWN

    @staticmethod
    def _surface(text: str) -> SurfaceKind | None:
        if _contains(text, ("터미널", "쉘", "명령어", "terminal", "shell", "cli")):
            return SurfaceKind.TERMINAL
        if _contains(
            text,
            ("브라우저", "사이트", "웹", "browser", "website", "http://", "https://"),
        ):
            return SurfaceKind.BROWSER
        if _contains(text, ("데스크톱", "앱", "desktop", "application")):
            return SurfaceKind.DESKTOP
        return None

    @staticmethod
    def _risk(text: str) -> tuple[GoalRisk, Reversibility]:
        if _contains(
            text, ("결제", "구매", "송금", "pay", "purchase", "checkout", "transfer")
        ):
            return GoalRisk.CRITICAL, Reversibility.IRREVERSIBLE
        if _contains(
            text, ("삭제", "탈퇴", "제출", "delete", "remove", "deactivate", "submit")
        ):
            return GoalRisk.HIGH, Reversibility.IRREVERSIBLE
        if _contains(text, ("수정", "변경", "edit", "rename", "toggle", "select")):
            return GoalRisk.MEDIUM, Reversibility.REVERSIBLE
        return GoalRisk.LOW, Reversibility.REVERSIBLE

    @staticmethod
    def _entities(text: str) -> tuple[GoalEntity, ...]:
        entities: list[GoalEntity] = []
        for match in _URL_RE.finditer(text):
            entities.append(GoalEntity("url", match.group(0).rstrip(".,)")))
        ko = _KO_SEARCH_RE.search(text)
        if ko:
            query = re.sub(r"(?:을|를)$", "", ko.group(1).strip())
            if query:
                entities.append(GoalEntity("search_query", query))
        else:
            en = _EN_SEARCH_RE.search(text)
            if en:
                query = en.group(1).strip()
                if query:
                    entities.append(GoalEntity("search_query", query))
        for number, unit in _NUMBER_RE.findall(text):
            suffix = unit.strip() if unit else ""
            entities.append(GoalEntity("number", f"{number}{suffix}"))
        return tuple(entities)

    @staticmethod
    def _constraints(text: str) -> tuple[GoalConstraint, ...]:
        result: list[GoalConstraint] = []
        if _contains(text, ("하지 마", "하지마", "금지", "don't", "do not", "without")):
            result.append(GoalConstraint("negative_instruction", text, negated=True))
        for token in ("이하", "이상", "미만", "초과", "under", "over", "at least", "at most"):
            if token.casefold() in text.casefold():
                result.append(GoalConstraint("bound", token))
        return tuple(result)

    @staticmethod
    def _forbidden_actions(text: str) -> tuple[ActionOp, ...]:
        forbidden: list[ActionOp] = []
        lowered = text.casefold()
        if (
            "api 사용하지" in text
            or "cli 사용하지" in text
            or "도구 사용하지" in text
            or "don't use api" in lowered
            or "do not use api" in lowered
        ):
            forbidden.append(ActionOp.CALL_TOOL)
        if "입력하지" in text or "don't type" in lowered or "do not type" in lowered:
            forbidden.extend((ActionOp.TYPE, ActionOp.REPLACE_TEXT))
        return tuple(forbidden)

    @staticmethod
    def _terminal_state(intent: IntentKind) -> str:
        return {
            IntentKind.NAVIGATE: "requested surface is visibly open",
            IntentKind.SEARCH: (
                "search results matching the requested query are visible"
            ),
            IntentKind.SELECT: (
                "the requested item is selected and independently observable"
            ),
            IntentKind.COMPARE: (
                "comparison evidence for the requested entities is available"
            ),
            IntentKind.EDIT: "the requested value is changed and observable",
            IntentKind.SUBMIT: "submission result is independently observable",
            IntentKind.UNKNOWN: (
                "requested user-visible state is independently observable"
            ),
        }[intent]

    @staticmethod
    def _evidence_requirements(intent: IntentKind) -> tuple[str, ...]:
        return {
            IntentKind.NAVIGATE: ("surface_identity",),
            IntentKind.SEARCH: ("query_echo", "result_candidate"),
            IntentKind.SELECT: ("selected_state",),
            IntentKind.COMPARE: ("multiple_candidates",),
            IntentKind.EDIT: ("value_changed",),
            IntentKind.SUBMIT: ("submission_confirmation",),
            IntentKind.UNKNOWN: ("independent_terminal_evidence",),
        }[intent]

    @staticmethod
    def _apply_semantic_fill(
        envelope: GoalEnvelope, values: Mapping[str, str]
    ) -> GoalEnvelope:
        allowed = set(envelope.ambiguity_slots)
        if any(key not in allowed for key in values):
            raise ValueError(
                "semantic filler attempted to modify a resolved/protected field"
            )
        intent = envelope.intent
        surface = envelope.preferred_surface
        entities = list(envelope.entities)
        remaining = list(envelope.ambiguity_slots)
        if "intent" in values:
            intent = IntentKind(values["intent"])
            remaining.remove("intent")
        if "preferred_surface" in values:
            surface = SurfaceKind(values["preferred_surface"])
            remaining.remove("preferred_surface")
        if "search_query" in values:
            query = values["search_query"].strip()
            if not query:
                raise ValueError("semantic search_query must be non-empty")
            entities.append(GoalEntity("search_query", query))
            remaining.remove("search_query")
        # Risk, reversibility and forbidden actions are intentionally copied
        # unchanged: semantic assistance cannot relax local safety facts.
        return replace(
            envelope,
            intent=intent,
            terminal_state=GoalInterpreter._terminal_state(intent),
            entities=tuple(entities),
            preferred_surface=surface,
            ambiguity_slots=tuple(remaining),
            evidence_requirements=GoalInterpreter._evidence_requirements(intent),
        )
