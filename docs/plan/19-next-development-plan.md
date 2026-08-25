---
title: "HPCU Runtime — Next Development Plan: Bootstrap-to-Verified Local-First Execution"
version: "1.0"
date: "2026-08-25"
parent: "docs/plan/17-remaining-work-plan.md, docs/plan/18-code-review-improvement-plan.md"
baseline_commit: "2e5867405f1324222b4f34d728db7485c3befd30"
language: "ko-KR"
---

# 19. 다음 개발 실행 플랜

## 1. 목적

현재 `main`은 application candidate와 UI locator의 의미를 분리하고,
`application_candidate_id`를 PlanIR identity에 포함했으며, Linux sandbox에서
브라우저를 실제로 실행해 visible page까지 도달하는 bootstrap E2E를 확보했다.

다음 개발의 목표는 이 bootstrap 성공을 독립 기능으로 남겨두지 않고,
**사용자 명령 → 계획 → application resolution → launch/navigation → observation → grounding → local action → verification → terminal evidence**를
하나의 제품 실행 경로로 연결하는 것이다.

최종 제품 원칙은 다음과 같다.

> **OS-first / local-first / model-last.**
> 로컬 구조·픽셀·실행 결과로 결정 가능한 단계에는 모델을 호출하지 않고,
> 모델은 ambiguity 또는 bounded recovery가 필요한 경우에만 task-global budget 안에서 사용한다.

---

## 2. 현재 기준선

기준 커밋: `2e5867405f1324222b4f34d728db7485c3befd30`

이미 완료된 경계:

- `Action.application_candidate_id`가 UI target locator와 분리됨.
- `ApplicationPlanResolver`가 OS application candidate를 명시적으로 PlanIR에 바인딩함.
- executor가 launch 시 `application_candidate_id`를 사용함.
- application candidate 변경이 PlanIR/patch identity 변경으로 반영됨.
- Linux sandbox에서 browser launch → navigate → visible page → screenshot을 실제 검증함.
- `ControlLoop.step()`은 모델을 직접 호출하지 않는 deterministic cycle임.
- `TaskRuntime`은 local repair를 semantic replan보다 먼저 시도함.
- `TaskBudgetLedger`는 task-global model call/token/latency budget, planning ceiling,
  recovery reserve, purpose별 call accounting을 보유함.
- `CommandRunResult`가 purpose별 모델 호출 수를 외부에 노출함.

현재 남은 핵심 gap:

1. live browser E2E가 `Executor` 수준에서 끝나며 `CommandRuntime`의 전체 경로를 관통하지 않음.
2. `CommandRuntime` integration test는 application candidate binding을 검증하지만 실제 OS side effect를 수행하지 않음.
3. launch 후 확보한 observed surface를 이후 local grounding/action/verification과 연결하는 제품 수준 회귀 게이트가 없음.
4. local-first/model-last가 코드 구조상 존재하지만 실제 성공 태스크에서 모델 호출을 최소화한다는 정량 qualification이 부족함.
5. GitHub-hosted workflow는 최근 실행에서 checkout 이전 runner infrastructure failure가 관측되어 hosted CI 신뢰성이 떨어진 상태임.

---

## 3. 이번 단계의 Definition of Done

이번 플랜은 아래 조건이 모두 충족되어야 완료로 본다.

1. Linux sandbox에서 하나의 `CommandRuntime.run()`이 실제 브라우저를 bootstrap하고,
   최소 1회의 target interaction을 수행한 뒤 `VERIFIED_SUCCESS`로 종료한다.
2. launch 직후 획득한 application/window evidence가 fresh observation에서 재획득되어
   후속 target grounding과 충돌하지 않는다.
3. deterministic/local evidence가 충분한 경로에서는 모델 호출이 0회이거나,
   application ambiguity가 실제 존재할 때 필요한 `APPLICATION_SELECTION` 호출만 발생한다.
4. local grounding 실패 또는 verification 실패 시 local repair가 먼저 실행되고,
   semantic replan은 recovery reserve와 task-global budget 안에서만 실행된다.
5. 성공/실패 결과에서 model calls by purpose, observation latency/error/timeout,
   recovery 횟수와 terminal evidence를 검증 가능한 형태로 남긴다.
6. 같은 계약을 Windows/macOS로 확장할 수 있도록 application bootstrap/handoff의
   OS-specific 부분과 runtime 공통 부분의 경계가 테스트로 고정된다.
7. docs-only가 아닌 runtime 변경을 merge하기 전에 focused unit/integration suite와
   Linux sandbox E2E가 모두 통과한다.

---

## 4. P0 — Hosted CI runner 신뢰성 복구

### 문제

최근 runtime 커밋들의 GitHub-hosted workflow가 repository checkout 전에 실패하는
`steps=null` 형태의 runner infrastructure 문제를 보였다. 이 상태에서는 코드 회귀와
CI 인프라 장애를 구분하기 어렵다.

### 작업

- `.github/workflows/model-reliability.yml`
- `.github/workflows/runtime-remediation.yml`

확인 항목:

1. workflow dispatch 자체가 runner를 정상 할당하는지 확인한다.
2. checkout 전 실패이면 application 코드 수정으로 우회하지 않는다.
3. workflow/run/job identifier와 실패 phase를 진단 로그에 남긴다.
4. runner가 복구되면 baseline commit과 최신 main을 같은 workflow로 재검증한다.
5. CI green을 제품 correctness의 유일한 증거로 쓰지 않고 local focused suite 결과와 함께 기록한다.

### 완료 조건

- 최소 한 번의 hosted run이 checkout → dependency setup → test phase까지 진입한다.
- 인프라 장애와 test failure를 구분할 수 있는 기록이 남는다.

---

## 5. P1 — Bootstrap → observed surface handoff 고정

### 목표

`LAUNCH_APPLICATION` 성공 evidence를 후속 UI locator로 재사용하지 않고,
**실행된 application을 fresh scene에서 다시 관찰한 뒤 후속 target을 별도로 grounding**한다.

### 후보 파일

- `hpcu/runtime_core/control_loop.py`
- `hpcu/observation/facade.py`
- `hpcu/lifecycle/resolver.py`
- `hpcu/executor/executor.py`
- `hpcu/schemas/action.py`
- `hpcu/schemas/plan.py`
- `tests/integration/test_browser_bootstrap_runtime.py`
- `tests/e2e/test_grok_linux_sandbox.py`

### 구현 규칙

- `application_candidate_id`: OS application 선택 identity 전용.
- `ActionTarget.element_id` / locator: 현재 scene의 UI element identity 전용.
- launch evidence element id는 실행 성공 증거이며 다음 node의 target을 강제하는 locator가 아님.
- launch/navigation 이후 반드시 fresh observation에서 scene version이 전진해야 함.
- application이 실행됐으나 observation에서 surface를 찾을 수 없으면
  `APPLICATION_NOT_OBSERVED` 또는 동등한 명시적 failure contract로 fail closed한다.
- stale scene이나 이전 window element를 성공 evidence로 인정하지 않는다.

### 테스트

- candidate id와 UI locator가 다시 alias되지 않는 regression.
- launch 성공 후 fresh scene에서 동일 application/window가 관찰됨.
- launch evidence가 사라진 경우 fail closed.
- navigation 후 page evidence가 이전 scene이 아니라 새 scene/frame에 속함.

---

## 6. P2 — CommandRuntime 전체 Linux local-first E2E

### 목표

현재의 두 테스트 경계를 합친다.

- integration: `CommandRuntime` → candidate binding까지
- live E2E: launcher/executor → visible page까지

새 E2E는 **제품 entry point인 `CommandRuntime`에서 terminal result까지** 관통해야 한다.

### 첫 qualification scenario

결정적이고 외부 네트워크에 의존하지 않는 local HTML/data URL fixture를 사용한다.

1. 사용자 명령 입력
2. browser application resolve
3. browser launch
4. fixture page navigate
5. textbox/button 등 구조적으로 관찰 가능한 target 탐색
6. click 또는 replace-text/type 수행
7. fresh observation
8. postcondition verification
9. terminal evidence binding
10. `TaskStatus.VERIFIED_SUCCESS`

### 권장 fixture

단일 HTML page에 다음을 둔다.

- 고유 title
- textbox 1개
- submit/action button 1개
- 입력 값을 echo하는 result element 1개

외부 사이트, 로그인, CAPTCHA, network timing을 qualification의 필수조건으로 두지 않는다.

### 모델 호출 기대값

- preferred/단일 browser candidate가 있으면: **0 calls** 목표.
- 실제 application ambiguity가 있으면: `APPLICATION_SELECTION` **1 call 이내**.
- target grounding과 normal execution에는 모델 호출 금지.
- semantic recovery fixture에서만 별도 목적의 모델 호출을 허용한다.

### 완료 조건

E2E 결과가 아래를 동시에 검증한다.

- `result.success is True`
- `result.task.status == VERIFIED_SUCCESS`
- required evidence가 모두 fresh scene에 bind됨.
- 성공 경로에서 불필요한 semantic purpose가 기록되지 않음.
- 실제 physical/local execution이 발생했음을 executor/visible-state evidence로 확인함.

---

## 7. P3 — Local-first / model-last 정책을 회귀 게이트로 승격

### 목표

현재 구조적 원칙을 테스트 가능한 제품 invariant로 만든다.

### invariant

1. `ControlLoop`는 semantic gateway를 직접 소유하거나 호출하지 않는다.
2. local target이 confident하면 action decision을 위해 모델을 호출하지 않는다.
3. recovery edge가 있으면 semantic replan보다 먼저 사용한다.
4. local repairer가 사용 가능하면 semantic replan보다 먼저 사용한다.
5. semantic replan은 지정된 failure class에서만 발생한다.
6. model budget exhaustion은 추가 호출 없이 명시적 terminal 결과로 수렴한다.

### 테스트 후보

- confident local target → model calls unchanged.
- ambiguous local target → action 미실행, recovery path 진입.
- local repair success → semantic replanner 0회.
- repeated same failure → bounded stop/loop detection.
- semantic escalation → purpose와 remaining budget 검증.

### 성능 기준

정확성 저하 없이 다음 지표를 지속 추적한다.

- successful task당 model calls
- local resolution rate
- observation transaction latency
- first actionable step latency
- recovery count
- verification success rate

`CPU utilization` 자체를 억지로 높이는 것이 목표가 아니라,
**CPU/로컬 구조·픽셀 처리로 해결 가능한 일을 원격 모델 호출보다 먼저 처리하는 비율**을 높이는 것을 목표로 한다.

---

## 8. P4 — Model budget / recovery qualification 강화

`TaskBudgetLedger`의 기반 기능은 이미 구현되어 있으므로 새 ledger를 만들지 않는다.
대신 실제 product flow에서 정책이 깨지지 않는지 검증한다.

### 검증 항목

- `INTENT_FILL`, `PLAN_COMPILE`, `APPLICATION_SELECTION`이 planning ceiling을 공유함.
- non-recovery call이 `recovery_call_reserve`를 침범하지 못함.
- retryable provider의 실제 attempt가 budget accounting을 우회하지 못함.
- token/latency 초과 후 추가 provider call이 발생하지 않음.
- `POST_ACTION_REANALYSIS`, `RECOVERY_REANALYSIS`가 recovery reserve를 사용할 수 있음.
- `CommandRunResult.model_calls_by_purpose` 합계와 ledger total이 일치함.

### 추가 telemetry

필요 시 기존 result/trace에 다음을 추가한다.

- model call purpose
- local vs semantic recovery 선택 이유
- semantic escalation 직전 remaining calls/tokens/latency
- terminal 시 사용된 recovery count

새 telemetry는 hot path에 별도 모델 호출이나 blocking I/O를 추가하면 안 된다.

---

## 9. P5 — Cross-platform application bootstrap parity

Linux sandbox를 reference contract로 삼고 Windows/macOS에 같은 의미를 적용한다.

### 공통 계약

- discovery → explicit candidate identity
- launch → observed application evidence
- candidate identity와 UI locator 분리
- fresh observation after launch
- deterministic failure code
- no semantic application selection when OS/local preference resolves uniquely

### 플랫폼별 진행 순서

1. Windows application discovery/launch capability 점검
2. macOS application discovery/launch capability 점검
3. 공통 contract test를 platform adapter에 재사용
4. live environment가 없는 CI에서는 fixture/adapter contract까지 검증
5. 실제 플랫폼 qualification은 별도 marker로 분리

플랫폼별 구현 차이를 공통 PlanIR 의미론으로 누출시키지 않는다.

---

## 10. P6 — Qualification matrix

| Gate | Unit | Integration | Live E2E |
|---|---:|---:|---:|
| application candidate / locator 분리 | 필수 | 필수 | 필수 |
| PlanIR identity | 필수 | 필수 | 간접 |
| bootstrap → fresh observation | 필수 | 필수 | 필수 |
| local target grounding | 필수 | 필수 | 필수 |
| local action → verification | 필수 | 필수 | 필수 |
| local repair before semantic | 필수 | 필수 | 선택 |
| recovery reserve / planning ceiling | 필수 | 필수 | 선택 |
| verified terminal evidence | 필수 | 필수 | 필수 |
| Windows/macOS adapter parity | 필수 | 필수 | 플랫폼별 |

### 최소 regression suite

관련 변경마다 최소한 다음 영역을 함께 실행한다.

- application resolver/executor tests
- PlanIR/schema tests
- control loop tests
- task runtime/recovery tests
- task budget/retry tests
- browser bootstrap integration tests
- Linux sandbox bootstrap/interaction E2E

---

## 11. 권장 커밋 순서

### Commit A — handoff contract

- launch evidence와 next UI target의 의미 분리 고정
- fresh observation regression 추가

### Commit B — full CommandRuntime Linux E2E

- local fixture
- real launch/navigation/interaction/verification
- terminal evidence assertion

### Commit C — local-first/model-last qualification

- no-unnecessary-model-call assertions
- local repair → semantic fallback 순서 assertion
- purpose/budget accounting assertions

### Commit D — telemetry and benchmark gate

- local/model decision metrics
- observation/recovery/result metrics
- stable qualification report

### Commit E — Windows/macOS parity

- adapter contract
- platform-marked qualification tests

각 커밋은 가능한 한 독립적으로 green이어야 하며, semantic 기능을 추가하기 전에
local deterministic 경로의 회귀 테스트를 먼저 잠근다.

---

## 12. Non-goals

이번 단계에서 하지 않는다.

- 모델이 모든 action을 결정하는 agent loop로 회귀
- application candidate를 UI locator에 다시 저장
- CI runner 장애를 application runtime 코드로 우회
- 외부 웹사이트만으로 E2E 성공을 정의
- unlimited replan/retry
- terminal evidence 없는 'action executed'를 task success로 간주
- CPU 사용률을 높이기 위한 무의미한 busy work

---

## 13. 리스크와 대응

### R1. launch 성공과 관찰 성공의 race

**대응:** bounded polling + fresh scene version + explicit timeout/failure code.

### R2. 구조 observer와 capture가 서로 다른 window 상태를 볼 수 있음

**대응:** `CompositeObserver` transaction을 사용하고 timeout/cancellation/error metrics를 유지한다.

### R3. local-first가 low-confidence click으로 변질될 수 있음

**대응:** confidence gate를 낮추지 않는다. ambiguous/no-candidate는 실행보다 recovery를 우선한다.

### R4. semantic fallback이 planning budget을 소진

**대응:** 기존 planning ceiling + recovery reserve를 product E2E에서 검증한다.

### R5. live sandbox flakiness

**대응:** 외부 network 제거, deterministic fixture, bounded polling, 명시적 platform marker 사용.

---

## 14. 완료 후 문서 정리

구현이 완료되면 다음 문서의 상태를 실제 코드 기준으로 동기화한다.

- `docs/plan/17-remaining-work-plan.md`
- `docs/plan/18-code-review-improvement-plan.md`
- `docs/plan/tasklist.md`
- `docs/test/2026-08-23-browser-bootstrap-gap-live-test.md`

과거 gap 문서는 삭제하지 않고 당시 검증 기록으로 유지하되,
상단에 해결 커밋과 superseded 상태를 명시한다.

---

## 15. 다음 착수점

**첫 구현 작업은 P1 + P2를 하나의 vertical slice로 진행한다.**

구체적으로는 Linux sandbox에서 `CommandRuntime.run()`이 browser bootstrap 후
fresh observation을 통해 textbox/button을 local grounding하고 실제 interaction을 수행한 뒤
terminal evidence로 `VERIFIED_SUCCESS`를 반환하는 E2E를 먼저 만든다.

이 테스트가 green이 되기 위해 필요한 최소 runtime 변경만 수행한다.
그 뒤 model budget qualification과 cross-platform parity를 확장한다.
