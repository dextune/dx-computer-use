---
title: "HPCU Runtime 개발 계획 08 — 모델 게이트웨이 및 MiniMax M3 연동"
version: "1.0"
date: "2026-08-21"
parent: "docs/dev-init-001.md (§16~17), docs/minimax-m3-api-spec.md"
language: "ko-KR"
---

# 08. 모델 게이트웨이 및 MiniMax M3 연동

## 1. Gateway ABC

`hpcu/gateway/gateway.py` — provider-independent adapter.
`call(prompt, system_prompt) -> GatewayResponse`.

## 2. MiniMax M3 Adapter

`hpcu/gateway/minimax_adapter.py` — OpenAI Chat Completions 호환.

핵심 제약:
- `reasoning_effort: "none"`이 무시됨 → strip state machine으로 대응.
- 응답 `content`에 ` thinking... response`가 직렬화됨.
- tool calling / multimodal은 미검증 → Phase 4 검증 태스크.

## 3. Strip State Machine

`strip_thinking(content: str) -> str`
- ` thinking` 태그 발견 시 시작
- ` response` 태그 발견 시 종료
- 그 사이의 모든 텍스트 제거
- ` response` 이후의 텍스트만 반환

## 4. Model Router

`hpcu/router/router.py` — Tier 선택, budget 관리, stale detection.
`hpcu/router/scene_summarizer.py` — LLM에 넘길 scene 요약.