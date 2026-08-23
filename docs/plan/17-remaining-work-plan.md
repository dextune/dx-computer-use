---
title: "HPCU Runtime — 종합 개발 계획 (Remaining Work)"
version: "1.0"
date: "2026-08-23"
parent: "AGENTS.md, docs/plan/tasklist.md, docs/plan/15-command-to-plan-runtime-remediation.md"
language: "ko-KR"
---

# 종합 개발 계획 — 남은 작업

## 1. 현재 상태 진단 (2026-08-23 기준)

### 1.1 한 줄 진단

> **신형 부품은 갖춰졌지만, 제품 실행 경로는 아직 신형 아키텍처를 강제하지 않는다.**
> `CaseRunner`(legacy)와 `TaskRuntime`(신형)이 서로 다른 budget·verification·recovery
> 규칙으로 공존하는 dual-path 상태다. 이걸 하나로 수렴시키는 것이 최우선 과제다.

### 1.2 정량 지표

| 지표 | 현재 값 | 목표 |
|---|---|---|
| 테스트 통과율 | 544/554 (98.2%) | 554/554 (100%) |
| 라인 커버리지 | 86.43% | ≥85% (달성) |
| Live 10-case 성공률 | 9/10 (S1+S4) | 10/10 |
| Live 신규 케이스 성공률 | 0/3 | 3/3 |
| 순환 import | 1건 (grounder↔router↔runtime_core↔control_loop) | 0건 |
| 실행 경로 수 | 2 (CaseRunner + TaskRuntime) | 1 (TaskRuntime) |

### 1.3 즉시 수정이 필요한 문제

1. **순환 import**: `grounder → router → runtime_core → control_loop → grounder` — 5개 테스트 실패 원인
2. **`CompositeObserver.__init__` signature mismatch**: `config` 키워드 누락 — 1개 테스트 실패
3. **신규 live 케이스 실패**: `schema_error:no_unique_targeting_object` (duckduckgo, google) — TargetingPack 컴파일이 새 케이스에 대응하지 못함
4. **fail-closed 테스트 2건**: U1-U9 마이그레이션 중 발생한 regression

---

## 2. 작업 단계별 로드맵

### Phase A — 🔴 Immediate Fixes (예상: 1~2일)

**목표**: 모든 테스트를 통과시키고, 신규 live 케이스 blocking issue를 해결한다.

#### A1. 순환 import 해소

- **원인**: `grounder.grounder` → `router.candidate_scoring` → `router.router` → `runtime_core.task_budget` → `runtime_core.control_loop` → `grounder.grounder`
- **해결 방안**:
  - `grounder.grounder`에서 `router.candidate_scoring` import를 지연 import(lazy)로 변경
  - 또는 `TargetQuery`를 `hpcu.schemas`로 이동하여 순환 고리를 끊음
  - `Grounder`/`GroundingCandidate`/`GroundingResult`를 `router`가 아닌 `schemas`에서 import하도록 변경
- **영향 테스트**: `test_grounder.py` 2건, `test_control_loop.py` 1건

#### A2. `CompositeObserver` signature 복구

- **원인**: U1-U9 마이그레이션 중 `CompositeObserver.__init__`에서 `config` 파라미터가 제거됨
- **해결 방안**: `CompositeObserver`에 `config` 파라미터 복원 또는 테스트를 새 signature에 맞게 수정
- **영향 테스트**: `test_observer_concurrency.py` 2건

#### A3. fail-closed regression 수정

- **원인**: U1-U9 마이그레이션으로 `fail_closed` 로직 변경
- **영향 테스트**: `test_model_failure_never_uses_content_fallback_click`, `test_blocked_pack_ends_in_human_handoff_without_retry`
- **접근**: 테스트 expectation을 현재 구현에 맞게 업데이트하거나, 구현이 invariant를 위반했다면 구현 수정

#### A4. product runtime boundary / task boundary reset 수정

- **영향 테스트**: `test_command_runtime_rejects_provider_or_model_drift`, `test_begin_task_clears_histories_and_loop_attempt_counters`
- **접근**: U1-U9 마이그레이션으로 변경된 인터페이스에 맞춰 테스트 수정

#### A5. 신규 live 케이스 `no_unique_targeting_object` 해결

- **원인**: `TargetingCompiler`가 새 케이스(duckduckgo-linux, google-search-notebook)의 MiniMax 응답에서 unique targeting object를 찾지 못함
- **해결 방안**:
  - `_extract_json_object`의 schema-directed unique object selection 로직 디버깅
  - MiniMax가 반환하는 JSON 구조를 로깅하여 실제 응답과 기대 schema 간 차이 확인
  - 필요시 `TargetingPack` 스키마를 더 관대하게 수정 (S2에서 했던 `_extract_json_object` 개선을 targeting compile에도 적용)

---

### Phase B — 🔴 제품 경로 단일화 (P0) (예상: 3~5일)

**목표**: `CaseRunner`와 `TaskRuntime` dual-path를 제거하고 모든 실행을 단일 runtime으로 통합한다.

> **중요**: 이 Phase가 닫히기 전에는 새 사이트 케이스나 새 기능을 추가하지 않는다.
> 상세 설계는 `docs/plan/15-command-to-plan-runtime-remediation.md` §4 참조.

#### B1. semantic/budget 단일 경계 (PR-A)

- [ ] `SemanticSlotFiller`가 공통 `json_response.py` parser를 사용하도록 변경
- [ ] `ModelRouter`의 private parser (`_parse_target`) 및 budget (`_model_calls_remaining`) 제거
- [ ] 모든 semantic call이 하나의 `TaskBudgetLedger`를 공유
- [ ] `BudgetedGateway(CountingGateway(RetryableGateway(provider)), task_ledger)` 조립
- [ ] transport retry는 새 semantic call로 계산하지 않음

#### B2. CaseRunner → TaskRuntime 수렴 (PR-B)

- [ ] `CaseRunner`에서 gateway 호출 제거
- [ ] `CaseRunner`에서 `_case_model_calls` attempt별 budget reset 제거
- [ ] `CaseRunner`에서 raw physical navigation/input 제거
- [ ] `CaseRunner`에서 별도 retry state machine 제거
- [ ] `CaseRunner`에서 별도 reanalysis verification 제거
- [ ] 최종 `CaseRunner` 책임: `CaseSpec → UserCommand 변환 → product runtime 호출 → 결과 수집 → 직렬화`
- [ ] CI rule: `hpcu/cases/runner.py`가 `Gateway`, provider parser, `InputInjector`를 import하면 실패

#### B3. 제품 composition root 하나 (PR-B 계속)

- [ ] `Observer`, `Grounder`, `Verifier`, `RiskEngine`, `Executor`, `TraceRecorder`, `BudgetedGateway`, `GoalInterpreter`, `StrategyPlanner`, `PlanCompiler`, `ControlLoop`, `TaskRuntime` — 한 곳에서 조립
- [ ] 플랫폼 adapter와 case adapter는 이 조립을 복제하지 않음

#### B4. P0 완료 gate 검증

- [ ] browser fixture와 terminal fixture가 같은 `TaskRuntime` 사용
- [ ] `CaseRunner` raw gateway/input 직접 호출 0
- [ ] task 전체 semantic call이 한 ledger에 기록
- [ ] attempt가 5회여도 task budget이 증가하지 않음
- [ ] HIGH/CRITICAL policy deny 시 모든 adapter에서 side effect 0
- [ ] unknown command → `ACTION_UNSUPPORTED`
- [ ] 5+ node fixture를 runtime model call 0으로 완료

---

### Phase C — 🟡 PlanIR 확장 + Dynamic Replan (예상: 3~4일)

**목표**: 6개 intent(navigate/search/select/compare/edit/submit)를 PlanIR로 컴파일하고, bounded dynamic replan을 구현한다.

> 상세 설계는 `docs/plan/15-command-to-plan-runtime-remediation.md` §6~§7 참조.

#### C1. 범용 PlanCompiler (PR-C)

- [ ] `_compile_navigate`: resolve entry → focus surface → navigate → verify surface identity
- [ ] `_compile_search`: resolve search field → focus → replace text → submit → verify query echo
- [ ] `_compile_select`: resolve candidate set → deterministic filter/rank → select → verify selected state
- [ ] `_compile_compare`: resolve candidate A/B → read structured attributes → normalize → verify both evidence sets
- [ ] `_compile_edit`: resolve editable target → capture old value → replace/toggle → verify value_changed
- [ ] `_compile_submit`: resolve form control → precondition validation → risk/policy gate → submit → verify confirmation
- [ ] `PlanningContext`/`CapabilitySnapshot` 연결
- [ ] deterministic plan hash (동일 goal/context/capability → 동일 plan)

#### C2. bounded dynamic replan (PR-D)

- [ ] `ReplanRequest` / `PlanPatch` DTO 구현
- [ ] local repair hierarchy: L0(retry guard) → L1(re-ground) → L2(alternate query) → L3(alternate strategy) → L4(semantic interrupt) → L5(halt)
- [ ] 같은 action+same scene 3회 반복 방지
- [ ] irreversible node 보존 (verified 완료 node 재실행 금지)
- [ ] `max_replans_per_task` config 제한
- [ ] patch lineage trace 기록

#### C3. temporal wait + LoopBreaker 연결 (PR-E)

- [ ] `WAIT_UNTIL`을 단일 scene assert가 아닌 bounded poll로 구현
- [ ] poll interval / stable polls / timeout은 config 기반
- [ ] `ControlLoop`/`TaskRuntime`이 bounded history 유지 (action fingerprint, pre/post scene hash, failure code, node id, replan id)
- [ ] `LoopBreaker.detect()`에 빈 history 호출 금지
- [ ] LoopBreaker 결과: `REEXPLORE` / `SWITCH_MODE` / `ESCALATE` / `HALT`

#### C4. typed evidence + terminal commit (PR-F)

- [ ] intent별 최소 EvidenceContract:
  - navigate: surface identity 또는 URL/title/content typed signal
  - search: query echo + result candidate
  - select: selected/toggled/active state 변화
  - compare: 두 candidate의 독립 attribute evidence
  - edit: old value ≠ new value + requested value observable
  - submit: submission confirmation 또는 독립 외부 효과
- [ ] success state 단일화: `TaskRuntime`만 `VERIFIED_SUCCESS`/`HUMAN_HANDOFF`/`FAILED` 결정
- [ ] no-op injector false success 0, previous-scene-only evidence false success 0, 모델 `goal_state=success` 단독 성공 0

#### C5. 완료 gate

- [ ] 6 intent 모두 deterministic fixture PlanIR 생성
- [ ] duplicate label, modal, slow loading, stale target fixture에서 bounded repair 성공/정지
- [ ] same action+same scene 3회 반복 0
- [ ] replan 횟수 config 초과 0
- [ ] irreversible node 중복 실행 0
- [ ] wait-until slow-loading fixture 통과
- [ ] loop recovery가 raw injector를 호출하지 않음
- [ ] no-op injector false success 0

---

### Phase D — 🟢 성능 최적화 (U7) (예상: 2~3일)

**목표**: CPU-first hot path를 실제 observer에 연결하여 unchanged frame의 불필요한 OCR을 제거한다.

> **선행 조건**: Phase B, C 완료 (correctness가 먼저다)
> 상세 설계는 `docs/plan/15-command-to-plan-runtime-remediation.md` §10 참조.

#### D1. dirty ROI hot path 연결

- [ ] ring buffer/tile hash를 `CompositeObserver` 실제 path에 연결
- [ ] unchanged frame full OCR 0
- [ ] decoded frame / crop cache
- [ ] ROI dense OCR → 필요 ROI sparse OCR
- [ ] full-screen OCR는 budgeted fallback
- [ ] equality diff에서 transient field(`scene_version`/`first_seen`/`last_seen`) 제외

#### D2. stable OCR identity

- [ ] fingerprint → IoU + text + role + source ensemble
- [ ] `ocr_line_N` 순번을 persistent identity로 사용하지 않음
- [ ] source-aware confidence calibration

#### D3. 성능 게이트

- [ ] unchanged 1080p frame p95 ≤ 10ms (reference hardware 기록)
- [ ] delta scene update p95 ≤ 150ms
- [ ] unchanged frame OCR pass 0
- [ ] 100-frame stable ID 유지율 artifact
- [ ] processed pixel / OCR pass / CPU time / peak RSS 기록

---

### Phase E — 🟢 Workflow Qualification + 제품 게이트 (U8-U9) (예상: 3~4일)

**목표**: 검증된 state transition을 workflow로 승격하고, held-out 30+ task로 제품 품질을 검증한다.

> 상세 설계는 `docs/plan/15-command-to-plan-runtime-remediation.md` §11~§12 참조.

#### E1. verified workflow compile (U8)

- [ ] verified trace candidate → repeated success → experimental workflow → multi-session offline shadow replay → qualified → online fresh verification replay → active
- [ ] 같은 `TaskRuntime`, `Grounder`, `Verifier`를 offline scene stream에서도 사용
- [ ] 첫 verified run → 동일 fixture replay model 0
- [ ] viewport/locale/text drift에서 re-ground 또는 halt
- [ ] fixed coordinate replay 0
- [ ] failed trajectory 승격 0
- [ ] drift/false completion 발생 시 즉시 downgrade

#### E2. held-out 30+ qualification gate (U9)

- [ ] dataset: 30개 이상 held-out task, intent family 6개, browser/terminal/desktop 최소 3 surface, 절반 이상 3 node 이상, duplicate label/modal/slow loading/stale frame 포함, locale/viewport/theme 변화 포함
- [ ] case spec에 최종 URL, 정답 element ID, 성공 문자열 직접 제공 금지
- [ ] 합격 기준:
  - false completion 0
  - stale action execution 0
  - policy bypass 0
  - access-control bypass 0
  - simple navigate/search runtime model call 0
  - uncached semantic plan compile ≤1 logical call
  - compiled replay model call 0
  - task budget 초과 semantic call 0
  - bounded failure stop ≤5 repair/replan cycles
  - 모든 실패에 final scene + failure reason + trace artifact 존재

---

### Phase F — 🔵 플랫폼 실기 구현 (예상: 5~7일)

**목표**: 현재 stub으로 존재하는 플랫폼 adapter를 실 구현으로 교체한다.

> **선행 조건**: Phase B 완료 (단일 runtime이 확립된 후에야 stub을 real로 교체하는 의미가 있다)

#### F1. 브라우저 (P1-2b, P1-8)

- [ ] 실 CDP/Playwright capture (`Page.screenshot` 기반)
- [ ] 실 CDP/Playwright structure (`Accessibility.getFullAXTree` / DOM tree)
- [ ] 실 CDP/Playwright input (`Input.dispatchMouseEvent`, `Input.dispatchKeyEvent`)
- [ ] fixture 사이트: iframe, modal, dynamic list

#### F2. Windows (P2-4, P2-5)

- [ ] UIA event 구독 (FocusChanged, StructureChanged, WindowOpened 등)
- [ ] per-monitor coordinate normalization (DPI 125%/150%, 다중 모니터 음수 origin)
- [ ] Win32/WPF/WinUI 샘플에서 공통 DSL 동작 검증

#### F3. OCR (P3-3b)

- [ ] PaddleOCR 또는 Tesseract 실 연동
- [ ] ROI OCR 지연/정확도 비교 (vs fake fixture)
- [ ] 언어팩 설정 (한국어 + 영어)

#### F4. MiniMax 실 API (P4-8)

- [ ] MiniMax tool calling 검증
- [ ] MiniMax multimodal (이미지 입력) 실 API 검증
- [ ] provider/network failure와 code failure 분리 검증

#### F5. 권한 가이드 (P8-5b)

- [ ] 각 OS별 권한 요구사항 문서화
- [ ] capability matrix 실측값으로 갱신
- [ ] `probe()`가 실제 capability를 반환하도록 구현

---

### Phase G — 📝 문서화 및 정리 (예상: 1~2일)

- [ ] Phase 종료 시 가정·수치를 실측값으로 교체
- [ ] `docs/plan/03-observation-layer.md` §8 capability matrix 실측값 갱신
- [ ] `docs/plan/tasklist.md` 모든 `[x]` 재검증 (unit 존재만으로 `[x]` 처리된 항목 재분류)
- [ ] `config/runtime-config.yaml` 기본 임계값 실측값으로 교체

---

## 3. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| **순환 import 해소 실패** | 5개 테스트 영구 실패, 모듈 구조 변경 필요 | import를 `schemas`로 이동하여 순환 고리 끊기 |
| **CaseRunner 제거 중 regression** | live 케이스 성공률 하락 | PR-B는 분할하지 않고 한 번에 진행, 충분한 integration test |
| **MiniMax 응답 품질 변동** | schema 검증 실패 증가 | S1-S4에서 구현한 fallback/retry로 흡수, 한계 초과 시 `MODEL_FAILED`로 fail closed |
| **PlanIR 6 intent 구현 복잡도** | 일정 지연 | search 외 intent는 최소 node pattern으로 시작, 점진적 확장 |
| **held-out 30+ task 준비** | 데이터셋 부족 | 기존 10-case 확장 + 신규 케이스 추가, 합성 fixture로 보완 |

---

## 4. 테스트 전략

### Phase A 완료 후
- 모든 554개 테스트 통과
- 기존 10-case live 성공률 9/10 유지 또는 개선
- 신규 3개 live 케이스 통과

### Phase B 완료 후
- browser fixture + terminal fixture 동일 `TaskRuntime` 사용 검증
- `CaseRunner` raw gateway/input 직접 호출 0 (CI lint rule)
- task 전체 semantic call이 한 ledger에 기록

### Phase C 완료 후
- 6 intent deterministic fixture PlanIR 생성
- duplicate label, modal, slow loading, stale target fixture에서 bounded repair
- no-op injector false success 0

### Phase D 완료 후
- unchanged 1080p frame p95 ≤ 10ms
- unchanged frame OCR pass 0

### Phase E 완료 후
- held-out 30+ task에서 false completion/stale action/policy bypass 0

---

## 5. 예상 일정

| Phase | 작업 | 예상 기간 | 의존성 |
|---|---|---|---|
| **A** | Immediate Fixes | 1~2일 | 없음 |
| **B** | 제품 경로 단일화 (P0) | 3~5일 | A |
| **C** | PlanIR + Dynamic Replan | 3~4일 | B |
| **D** | 성능 최적화 (U7) | 2~3일 | B, C |
| **E** | Workflow + 제품 게이트 (U8-U9) | 3~4일 | B, C |
| **F** | 플랫폼 실기 구현 | 5~7일 | B |
| **G** | 문서화 및 정리 | 1~2일 | E |

**총 예상**: 18~27일 (Phase A~E는 순차, Phase F는 B 이후 병행 가능)

---

## 6. 완료 정의

본 계획은 아래가 모두 자동 검증될 때 완료다:

1. 모든 제품/case/replay 실행이 동일 `TaskRuntime/ControlLoop`를 사용
2. 사용자 명령이 `GoalEnvelope → StrategyPlan → PlanIR`로 구조화
3. 6 core intent가 동일 runtime에서 다단계로 실행
4. 모든 side effect가 policy + stale gate를 통과
5. 모든 side effect 후 fresh scene 또는 독립 외부 효과로 검증
6. 모든 semantic call이 하나의 task-global ledger와 schema parser를 사용
7. local repair 후에만 bounded semantic replan 발생
8. same action/same scene 무한 반복 자동 차단
9. unchanged frame은 full OCR 없이 처리
10. verified workflow는 다음 실행에서 모델 0회로 replay
11. held-out benchmark에서 false completion·stale execution·policy bypass 0
12. 각 실패는 bounded stop과 재현 가능한 trace/manifest를 남김