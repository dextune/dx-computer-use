---
title: "HPCU Runtime 개발 계획 07 — Action DSL·실행기·검증기·Recovery"
version: "1.0"
date: "2026-08-21"
parent: "docs/dev-init-001.md (§14~15)"
language: "ko-KR"
---

# 07. Action DSL·실행기·검증기·Recovery

## 1. Action DSL

19개 op: `focus_window`, `navigate`, `invoke`, `click`, `double_click`,
`right_click`, `type`, `replace_text`, `hotkey`, `select`, `toggle`, `scroll`,
`drag`, `wait_until`, `assert`, `read`, `call_tool`, `checkpoint`,
`request_approval`.

각 Action은 `id`, `op`, `target`, `preconditions`, `postconditions`,
`timeout_ms`, `retry`를 가진다.

## 2. 실행기 (Executor)

| 모듈 | 파일 |
|---|---|
| Executor | `hpcu/executor/executor.py` |

실행 우선순위: `InputInjector.semantic()` → hotkey → `InputInjector.physical()`.
고정 sleep 금지. settle detector로 대기.

## 3. 검증기 (Verifier)

| 모듈 | 파일 |
|---|---|
| Verifier | `hpcu/verifier/verifier.py` |

`EvidenceContract`를 `Scene`에 대해 평가. postcondition 검증.

## 4. Recovery

| 모듈 | 파일 |
|---|---|
| Loop Breaker | `hpcu/recovery/loop_breaker.py` |
| Popup Recovery | `hpcu/recovery/popup_recovery.py` |
| Alternate Modes | `hpcu/recovery/alternate_modes.py` |

5개 loop trigger: 같은 hash+action, postcondition 반복, A→B→A,
popup loop, scroll no change.