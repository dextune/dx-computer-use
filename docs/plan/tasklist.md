---
title: "HPCU Runtime — 개발 태스크리스트"
version: "1.2"
date: "2026-08-22"
parent: "docs/plan/00-overview-and-goals.md, docs/dev-init-001.md §25, docs/plan/17-remaining-work-plan.md"
language: "ko-KR"
scope: "project-wide, progress tracking"
---

# 개발 태스크리스트

진행 관리만 한다. 설계를 이 파일에서 바꾸지 않는다.
Phase 이름·범위는 `docs/dev-init-001.md` §25 / 향후 `12-roadmap-phases.md`.
모듈 위치는 `01-system-architecture.md`. 관찰 계약은 `03-observation-layer.md`.

체크 규칙:

- `[ ]` 미착수 / `[~]` 진행 중 / `[x]` 완료 (완료 조건 + 테스트 통과 시에만)
- 완료 조건이 테스트로 증명되지 않은 태스크는 `[x]` 처리 금지
- 기존 `[x]`는 component 존재와 product qualification을 혼동할 수 있으므로 U1에서 재분류
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

### P0-6 크로스 OS 계약 (Capture / Input / Capability)

ABC는 OS 구현보다 먼저 고정한다. 구현체는 fake로 테스트한다.

- [x] `hpcu/capture/backend.py` — `CaptureBackend` ABC, `CaptureCapabilities`, `FrameHandle`
- [x] `hpcu/input/injector.py` — `InputInjector` ABC, `InputCapabilities`
- [x] `hpcu/schemas/capability.py` — `Capability` enum (`supported`/`degraded`/`unsupported`)
- [x] 실패 코드 추가: `CAPTURE_PERMISSION_DENIED`, `CAPTURE_BACKEND_UNAVAILABLE`,
      `STRUCTURE_TREE_EMPTY`, `INPUT_PERMISSION_DENIED`, `INPUT_SEMANTIC_UNSUPPORTED`,
      `INPUT_PHYSICAL_UNSUPPORTED`, `CAPABILITY_MISSING`
- [x] `hpcu/platform/` 패키지 골격 (`windows/`, `linux/`, `macos/`, `browser/`, `remote/`)
      — 각 `__init__`만, native import 없음
- [x] fake capture/input으로 ABC 계약 테스트 (bytes 복사 금지, session_id 결합)
- [x] `vision`이 `hpcu.platform`을 import하면 실패하는 lint/테스트

---

## Phase 1 — 브라우저 결정론적 Vertical Slice

- [x] P1-1 `Observer` ABC + `PluginRegistry` (lazy load, 생성자 주입)
- [x] P1-1b Observer facade = CaptureBackend + StructureObserver 합성, session_id 묶음
- [x] P1-2 Playwright adapter 계약 stub (`probe()=False`, capability UNSUPPORTED)
- [ ] P1-2b 실 CDP/Playwright capture·structure·input
- [x] P1-3 role/name/label locator + selector ensemble
- [x] P1-4 DOM/AX → Scene Graph 변환
- [x] P1-5 Action DSL 실행기: click/type/select/wait_until/assert
- [x] P1-6 settle detector (고정 sleep 제거, §7.3)
- [x] P1-7 구조화된 상품 카드 추출 예제 (grounder scoring)
- [ ] P1-8 fixture 사이트 (iframe / modal / dynamic list)
- [x] 완료 조건(공통): ControlLoop.step fixture 1사이클, 모델 0회 (실 브라우저 e2e는 P1-2b)

---

## Phase 2 — Windows Structured UI

`hpcu.platform.windows`가 Capture + Structure + Input 세 ABC를 구현한다.

- [x] P2-1 UIA tree observer (`StructureObserver`) — stub
- [x] P2-1b DXGI Desktop Duplication capture (`CaptureBackend`, dirty rect) — stub
- [x] P2-2 Invoke/Value/Selection/Toggle 패턴 (`InputInjector.semantic`) — stub
- [x] P2-3 window focus 및 app lifecycle 관리 — probe()
- [ ] P2-4 UIA event 구독 (실기)
- [ ] P2-5 per-monitor coordinate normalization (실기)
- [x] P2-6 semantic 실패 시 physical fallback 계약 (stub UNSUPPORTED)
- [ ] 완료 조건: Win32/WPF/WinUI 샘플에서 공통 DSL 동작, 125%/150% DPI 통과

---

## Phase 3 — CPU Screen Perception

Perception은 OS 무관. 캡처 스레드/ring buffer는 공통, grab은 fake 또는 등록된 backend.

- [x] P3-1 고속 capture 파이프라인 + ring buffer (`hpcu/capture`, backend 교체 가능)
- [x] P3-2 dirty ROI / tile hash (백엔드 dirty_rects가 없으면 이 경로)
- [x] P3-3 OCR fake fixture (`hpcu/vision/ocr.py`)
- [ ] P3-3b PaddleOCR / Tesseract 연동
- [x] P3-4 shape/component detector — stub
- [x] P3-5 template matcher — stub
- [x] P3-6 text-component association
- [x] P3-7 visual Scene Graph + temporal tracking (§10.2 매칭 우선순위)
- [x] 완료 조건(공통 fake): fixture에서 후보 생성
- [ ] 완료 조건(실기): ROI OCR 지연/정확도 비교

---

## Phase 4 — Confidence Router와 텍스트 LLM

- [x] P4-1 candidate scoring + confidence calibration
- [x] P4-2 top-k scene summarizer
- [x] P4-3 `Gateway` ABC + MiniMax M3 adapter
- [x] P4-4 `<think>` strip state machine (docs/minimax-m3-api-spec.md)
- [x] P4-5 schema-constrained 응답 검증
- [x] P4-6 stale response rejection
- [x] P4-7 model call budget
- [ ] P4-8 MiniMax tool calling / multimodal 실 API 검증
- [x] 완료 조건: 고신뢰 후보 모델 미호출, 모델 오류/timeout에도 executor 안정

---

## Phase 5 — Small VLM, SoM, Coarse-to-Fine

- [x] P5-1 object mark overlay (SoM)
- [x] P5-2 crop transform + 좌표 복원
- [x] P5-3 zoom request protocol
- [x] P5-4 소형 VLM candidate selector — stub
- [x] P5-5 대형 VLM fallback (coarse-to-fine) — stub
- [x] P5-6 visual verification — stub
- [x] 완료 조건: 모델이 절대 좌표를 반환하지 않아도 클릭 가능 (contract)

---

## Phase 6 — Recovery와 Evidence Verifier

- [x] P6-1 completion verifier (evidence contract 평가)
- [x] P6-2 loop breaker (§15.4 5개 트리거)
- [x] P6-3 popup/overlay recovery
- [x] P6-4 alternate interaction mode 전환
- [x] P6-5 risk policy engine
- [x] P6-6 human approval 경로
- [x] 완료 조건: 무한 반복 방지, 증거 없이 성공 보고 금지

---

## Phase 7 — Workflow Compiler

- [x] P7-1 trajectory parameterization
- [x] P7-2 selector ensemble 저장
- [x] P7-3 pre/postcondition 추론
- [x] P7-4 shadow replay
- [x] P7-5 workflow versioning + rollback
- [x] P7-6 drift detection
- [x] P7-7 qualification report
- [x] 완료 조건: 반복 작업 정상 경로 모델 호출 0회

---

## Phase 8 — Linux/macOS 및 원격 화면

Phase 0-6의 ABC를 구현만 한다. 새 제어 루프를 만들지 않는다.

- [x] P8-1 Linux X11 Capture + AT-SPI Structure + XTest Input — stub
      (호스트와 Docker Xvfb 동일 플러그인, DISPLAY만 다름)
- [x] P8-2 macOS ScreenCaptureKit Capture + AXUIElement Structure + CGEvent Input — stub
- [x] P8-3 Wayland: PipeWire capture + input portal. 거부 시 우회 금지 — stub
- [x] P8-4 remote VNC/RDP Capture + RFB Input, Structure는 empty가 정상 — stub
- [x] P8-5 각 패키지 `probe()` (False). permissions.md / matrix 실측은 실기 이후
- [ ] P8-5b 권한 가이드 + capability 실측
- [x] P8-6 capture와 input이 다른 화면을 가리키면 부팅 실패 (session_id)
- [x] P8-7 Linux/macOS 패키지 제거 후 `tests/unit` + vision 테스트 통과
- [x] 완료 조건: 동일 Action DSL이 플랫폼별 adapter에서 실행,
      unsupported capability는 명시 거절

---

## 문서 태스크 (병행)

- [x] `docs/plan/03-observation-layer.md` 작성 (Capture/Structure/Perception/Input)
- [x] `docs/plan/02-schemas-and-coordinates.md`, `04`~`12` 작성
- [x] `docs/plan/13-common-pipeline-remediation.md` (공통 파이프 개선 계획)
- [x] `docs/plan/14-goal-compiled-targeting.md` (목표 편찬 Targeting Pack)
- [x] `docs/plan/15-command-to-plan-runtime-remediation.md` (명령→PlanIR→단일 런타임 개선 계획)
- [x] `docs/decisions/001-three-layer-platform.md`
- [x] `AGENTS.md` (방향·불변식)
- [ ] Phase 종료 시 가정·수치를 실측값으로 교체
- [ ] capability matrix를 실측 후 03 §8 갱신

---

## Remediation — 공통 파이프라인 (R1~R6)

설계는 `docs/plan/13-common-pipeline-remediation.md`. 여기서 순서를 바꾸지 않는다.
한 묶음이 테스트로 닫히기 전에 다음으로 가지 않는다.

- [x] R1 `ControlLoop.step()` 조립 (observe → build → ground → policy → execute → verify). 모델 0회 fixture 테스트
- [x] R2 `SceneDelta`로 관찰 산출 통일. tracker를 builder에 연결. 제거된 요소가 Scene에 안 남음
- [x] R3 Grounder가 `router.score_candidates` + yaml 임계값만 사용. 점수식 한 벌
- [x] R4 `FrameHandle.space: CoordinateSpace`, Scene 불변 mapping, compiler가 실제 `ActionOp`/Enum
- [x] R5 `ApprovalGate`/`WorkflowVersions` 개명, 전 unit 마커, settle이 config 기반
- [x] R6 tasklist `[x]` 정직화 (Playwright/OCR/native stub vs 실기 분리)

---

## Targeting Pack (T1~T5)

설계는 `docs/plan/14-goal-compiled-targeting.md`. 여기서 설계를 바꾸지 않는다.

- [x] T1 TargetingPack + GenericMatcher + GoalFallbackTokenizer (모델 0, unit)
- [x] T2 TargetingCompiler + CountingGateway (fake MiniMax, MiniMax-M3)
- [x] T3 Runner가 pack만 사용. 프로덕션 사이트/CTA 사전 삭제
- [x] T4 케이스 YAML은 goal+url+예산만
- [x] T5 compile/grounding 횟수 분리, 시크릿 미누출

---

## Remediation — 사용자 명령·PlanIR·단일 런타임 (U1~U9)

설계는 `docs/plan/15-command-to-plan-runtime-remediation.md`.
새 사이트 케이스와 OS 기능을 늘리기 전에 U1~U6을 순서대로 닫는다.
기존 Phase·R·T의 `[x]`는 이 제품 자격 게이트를 대신하지 않는다.

- [ ] U1 execution mode ADR, 재현 가능한 baseline, 지표 분리, 기존 `[x]` product qualification 재분류
- [ ] U2 `GoalEnvelope` + CPU parser + unresolved slot만 semantic fill
- [ ] U3 capability-aware `StrategyPlan` + typed multi-step `PlanIR`; TargetingPack은 node grounding hint로 축소
- [ ] U4 `TaskRuntime` + 유일한 `ControlLoop`; CaseRunner는 test adapter로 축소
- [ ] U5 action 후 fresh scene verification, stale binding, 독립 evidence, 상태 정규화
- [ ] U6 task-global `TaskBudget`; compile/ground/reanalysis/recovery 합산, attempt reset 제거
- [ ] U7 tile hash·ring buffer·dirty ROI·stable OCR ID를 실제 observer hot path에 연결
- [ ] U8 verified transition 기반 workflow compile + 동일 runtime offline/online replay
- [ ] U9 held-out 30+ task 제품 게이트: false completion/stale action/policy bypass 0

---

## Remediation — 모델 응답 신뢰성 (S1~S5)

설계는 `docs/plan/16-model-response-reliability.md`.
이 작업들은 U1~U9 마이그레이션과 충돌하지 않으며, `CaseRunner` 테스트
어댑터의 단기 신뢰성을 개선한다. 완료 후에도 10/10 성공이 보장되지는
않는다 — 이 계획은 MiniMax 응답 품질 자체를 바꾸지 않는다.

- [x] S1 실패 진단 로깅: `_load_exact_object` 실패 시 raw response prefix를 `decision_diagnostics`에 기록
- [x] S2 JSON 파싱 강건화: `_extract_json_object` — markdown fence, bracket counting, extra key 허용
- [x] S3 transient error 재시도 + backoff: `RetryableGateway`, action/reanalysis에도 retry 적용
- [x] S4 reanalysis evidence fallback: schema 실패 시 `_pack_evidence_holds`로 2차 검증
- [x] S5 10개 케이스 재실행 및 성공률 비교
