---
title: "HPCU Runtime — 개발 태스크리스트"
version: "1.0"
date: "2026-08-21"
parent: "docs/plan/00-overview-and-goals.md, docs/dev-init-001.md §25"
language: "ko-KR"
scope: "project-wide, progress tracking"
---

# 개발 태스크리스트

진행 관리 기준 문서. 각 태스크는 `docs/dev-init-001.md §25`의 Phase 정의와
`docs/plan/01-system-architecture.md`의 모듈 구조를 따른다.

체크 규칙:

- `[ ]` 미착수 / `[~]` 진행 중 / `[x]` 완료 (완료 조건 + 테스트 통과 시에만)
- 완료 조건이 테스트로 증명되지 않은 태스크는 `[x]` 처리 금지
- 모든 코드는 `docs/rules/naming-conventions.md`, `docs/rules/testing-standards.md` 준수

---

## Phase 0 — 측정 기반과 데이터 계약

목표: 모든 캡처·scene update·action·verification·model call을 재현 가능하게 기록하고,
stale action을 식별하며, 동일 trace를 offline replay 할 수 있다.

### P0-1 프로젝트 스캐폴드

- [x] `pyproject.toml` (Python 3.12+, pytest 마커, coverage 게이트)
- [x] `hpcu/` 패키지 골격 및 모듈 디렉터리
- [x] `tests/` 계층 구조 (unit/integration/replay/failure_injection/e2e)
- [x] `.gitignore` (`.env`, `__pycache__`, `htmlcov`, `.venv`)
- [x] `config/runtime-config.yaml` 기본 임계값 (하드코딩 금지 원칙)

### P0-2 데이터 계약 (schemas)

- [x] `hpcu/schemas/failure_codes.py` — §29 실패 코드 Enum
- [x] `hpcu/schemas/coordinates.py` — 좌표 공간 Enum, `BoundingBox`
- [x] `hpcu/schemas/ui_element.py` — `UIElement`, `ElementState`, `ElementRelations`, `ElementSource`
- [x] `hpcu/schemas/scene.py` — `Scene`, `SceneDelta`, `FrameHandle`, scene_version 단조성
- [x] `hpcu/schemas/action.py` — Action DSL (§14.2 19개 op), pre/postcondition, retry
- [x] `hpcu/schemas/evidence.py` — Evidence contract, `all`/`any` 조합
- [x] `hpcu/schemas/trace.py` — trace 레코드 타입 (capture/scene/action/verify/model_call)
- [x] JSON Schema 산출물 `schemas/*.schema.json` 자동 생성 및 일치 검증

### P0-3 좌표 시스템

- [x] `hpcu/coordinates/transform.py` — 3×3 homogeneous transform, 합성/역변환
- [x] `hpcu/coordinates/spaces.py` — 좌표 공간 간 변환 레지스트리
- [x] DPI 스케일(125%/150%), devicePixelRatio, 다중 모니터 음수 origin 처리
- [x] crop-local ↔ 원본 해상도 좌표 복원
- [x] 안전 클릭점 계산 (§13.3 — bbox 중앙 무조건 사용 금지)

### P0-4 Trace Recorder

- [x] `hpcu/trace/recorder.py` — 단조 증가 seq, 이벤트 append
- [x] `hpcu/trace/storage.py` — SQLite 백엔드 (경험 저장소 겸용)
- [x] `hpcu/trace/replay.py` — offline replay, 결정론 검증
- [x] stale action 식별 (scene_version 불일치 탐지)
- [x] 이미지 미포함 원칙 (frame은 handle 참조만 기록)

### P0-5 벤치마크 하니스

- [x] `benchmarks/harness.py` — 지표 수집(지연/모델 호출 수/성공률)
- [x] reference hardware 프로파일 기록
- [x] baseline screenshot-every-step 에이전트 측정 스텁

### Phase 0 완료 조건

- [x] 한 작업의 모든 캡처·scene update·action·verification·model call 시간 기록
- [x] stale action 식별 가능
- [x] 동일 trace offline replay 가능
- [x] 라인 커버리지 ≥ 85%, 브랜치 ≥ 80%

---

## Phase 1 — 브라우저 결정론적 Vertical Slice

- [x] P1-1 `Observer` ABC + `PluginRegistry` (lazy load, 생성자 주입)
- [x] P1-2 Playwright adapter — accessibility snapshot
- [ ] P1-3 role/name/label locator + selector ensemble
- [ ] P1-4 DOM/AX → Scene Graph 변환
- [x] P1-5 Action DSL 실행기: click/type/select/wait_until/assert
- [x] P1-6 settle detector (고정 sleep 제거, §7.3)
- [ ] P1-7 구조화된 상품 카드 추출 예제
- [ ] P1-8 fixture 사이트 (iframe / modal / dynamic list)
- [ ] 완료 조건: 모델 없이 fixture 핵심 태스크 수행, re-render 후 locator 재탐색

---

## Phase 2 — Windows Structured UI

- [ ] P2-1 UIA tree observer
- [ ] P2-2 Invoke/Value/Selection/Toggle 패턴
- [ ] P2-3 window focus 및 app lifecycle 관리
- [ ] P2-4 UIA event 구독
- [ ] P2-5 per-monitor coordinate normalization
- [ ] P2-6 semantic action 실패 시 input fallback
- [ ] 완료 조건: Win32/WPF/WinUI 샘플에서 공통 DSL 동작, 125%/150% DPI 통과

---

## Phase 3 — CPU Screen Perception

- [ ] P3-1 고속 capture + ring buffer
- [ ] P3-2 dirty ROI / tile hash
- [ ] P3-3 OCR (PaddleOCR 주, Tesseract fallback)
- [ ] P3-4 shape/component detector
- [ ] P3-5 template matcher
- [ ] P3-6 text-component association
- [ ] P3-7 visual Scene Graph + temporal tracking (§10.2 매칭 우선순위)
- [ ] 완료 조건: 접근성 없는 fixture에서 후보 생성, ROI OCR 지연/정확도 비교

---

## Phase 4 — Confidence Router와 텍스트 LLM

- [ ] P4-1 candidate scoring + confidence calibration
- [ ] P4-2 top-k scene summarizer
- [ ] P4-3 `Gateway` ABC + MiniMax M3 adapter
- [ ] P4-4 `<think>` strip state machine (docs/minimax-m3-api-spec.md)
- [ ] P4-5 schema-constrained 응답 검증
- [ ] P4-6 stale response rejection
- [ ] P4-7 model call budget
- [ ] P4-8 MiniMax tool calling / multimodal 검증 태스크
- [ ] 완료 조건: 고신뢰 후보 모델 미호출, 모델 오류/timeout에도 executor 안정

---

## Phase 5 — Small VLM, SoM, Coarse-to-Fine

- [ ] P5-1 object mark overlay (SoM)
- [ ] P5-2 crop transform + 좌표 복원
- [ ] P5-3 zoom request protocol
- [ ] P5-4 소형 VLM candidate selector
- [ ] P5-5 대형 VLM fallback (coarse-to-fine)
- [ ] P5-6 visual verification
- [ ] 완료 조건: 모델이 절대 좌표를 반환하지 않아도 클릭 가능

---

## Phase 6 — Recovery와 Evidence Verifier

- [ ] P6-1 completion verifier (evidence contract 평가)
- [ ] P6-2 loop breaker (§15.4 5개 트리거)
- [ ] P6-3 popup/overlay recovery
- [ ] P6-4 alternate interaction mode 전환
- [ ] P6-5 risk policy engine
- [ ] P6-6 human approval 경로
- [ ] 완료 조건: 무한 반복 방지, 증거 없이 성공 보고 금지

---

## Phase 7 — Workflow Compiler

- [ ] P7-1 trajectory parameterization
- [ ] P7-2 selector ensemble 저장
- [ ] P7-3 pre/postcondition 추론
- [ ] P7-4 shadow replay
- [ ] P7-5 workflow versioning + rollback
- [ ] P7-6 drift detection
- [ ] P7-7 qualification report
- [ ] 완료 조건: 반복 작업 정상 경로 모델 호출 0회

---

## Phase 8 — Linux/macOS 및 원격 화면

- [ ] P8-1 AT-SPI adapter
- [ ] P8-2 AXUIElement adapter
- [ ] P8-3 Wayland/X11 정책
- [ ] P8-4 remote desktop capture/input adapter
- [ ] P8-5 권한 설치 가이드 + capability matrix
- [ ] 완료 조건: 동일 Action DSL이 플랫폼별 adapter에서 실행

---

## 문서 태스크 (병행)

- [ ] `docs/plan/02-schemas-and-coordinates.md` ~ `12-roadmap-phases.md` 작성
- [ ] `docs/decisions/` ADR 기록 시작
- [ ] Phase 종료 시 가정·수치를 실측값으로 교체
