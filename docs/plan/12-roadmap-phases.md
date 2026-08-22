---
title: "HPCU Runtime 개발 계획 12 — 개발 로드맵 (Phase 0~8)"
version: "1.0"
date: "2026-08-21"
parent: "docs/dev-init-001.md (§25, §32)"
language: "ko-KR"
---

# 12. 개발 로드맵 (Phase 0~8)

## Phase 0 — 측정 기반과 데이터 계약 ✅
- 스캐폴드, 스키마, 좌표, trace, benchmark, ABC 계약
- 완료: 87 tests, 97% coverage

## Phase 1 — 브라우저 결정론적 Vertical Slice
- Executor, Verifier, Grounder, Scene Graph, Browser stub
- Observer facade, CompositeObserver

## Phase 2 — Windows Structured UI
- DXGI capture, UIA observer, SendInput injector (stubs)

## Phase 3 — CPU Screen Perception
- OCR, shape, template, ring buffer, tile hash, tracker

## Phase 4 — Confidence Router와 텍스트 LLM
- Router, candidate scoring, Gateway ABC, MiniMax M3 adapter

## Phase 5 — Small VLM, SoM, Coarse-to-Fine
- SoM overlay, crop transform, VLM selectors (stubs)

## Phase 6 — Recovery와 Evidence Verifier
- Loop breaker, popup recovery, risk engine, approval

## Phase 7 — Workflow Compiler
- Trajectory parameterization, selector ensemble, versioning, drift

## Phase 8 — Linux/macOS 및 원격 화면
- Platform stubs (X11, PipeWire, AT-SPI, ScreenCaptureKit, AX, CGEvent, VNC)

## 진행 관리
`docs/plan/tasklist.md`에서 체크박스로 추적.

공통 계층이 모듈만 있고 루프가 비면 `docs/plan/13-common-pipeline-remediation.md` (R1~R6).
스펙은 01·03이 소유한다. 13은 고치는 순서만 적는다.