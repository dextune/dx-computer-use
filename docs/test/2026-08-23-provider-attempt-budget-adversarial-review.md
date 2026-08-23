# 2026-08-23 공급자 시도 예산 적대적 리뷰 및 보강

## 1. 기준선

- 저장소: `dextune/dx-computer-use`
- 검토 기준: `main`
- 기준 커밋: `15b2b44db65da463fa4d0ed247a16361b256fc02`
- 작업 PR: `#4`
- 검토 축: CPU/로컬 우선, 실제 AI 사용량 제한, 실패 폐쇄, 최소 변경

이번 작업은 새로운 agent, scheduler, provider 또는 perception 기능을 추가하는 작업이 아니다. 최신 `main`의 명령 → 계획 → 실행 경로가 이미 갖춘 구조를 유지하면서, 재시도 계층이 전역 모델 예산을 우회하는 한 경계를 닫는 작업이다.

## 2. 적대적 결론

최신 `main`은 이전 버전보다 분명히 좋아졌다. `CommandRuntime`이 goal interpretation, strategy selection, context compilation, deterministic plan, task runtime을 한 composition root에 모으고 있고, 이전 보강으로 semantic targeting 실패와 CPU grounding 실패 원인도 상당 부분 실패 폐쇄됐다.

하지만 모델 예산은 실제 사용량을 통제하지 못했다.

```text
BudgetedGateway
  └─ CountingGateway              # 논리 호출 1회
       └─ RetryableGateway
            └─ provider           # 실제 요청 최대 3회
```

기존 `TaskBudgetLedger`는 가장 바깥의 논리 호출만 셌다. 따라서 `max_model_calls=1`이어도 transient error가 발생하면 provider에는 최대 세 번 요청할 수 있었다. 이는 단순 통계 오차가 아니다.

- 비용 제한을 우회한다.
- 모델 지연 제한을 우회한다.
- 실패한 요청과 backoff 시간이 ledger에서 사라진다.
- 호출 목적별 회계가 실제 provider 트래픽과 어긋난다.
- “model-last” 정책이 문서상 구호로 남고 transport 계층에서는 무력화된다.

모델을 적게 쓰겠다고 해놓고 재시도를 계산서 밖에 둔 상태였다. 구조가 정돈돼 보인다는 이유로 안전한 구조라고 평가할 수 없었다.

## 3. 발견한 핵심 결함

### P0 — transport retry가 작업 전역 호출 예산을 우회

`CommandRuntime._semantic_gateway()`는 retry wrapper를 만든 뒤 그 바깥을 `BudgetedGateway`로 감싼다. case harness는 logical telemetry를 보존하기 위해 `CountingGateway`도 retry 바깥에 둔다.

이 조립 자체는 logical call telemetry 관점에서는 맞지만, task budget 관점에서는 틀렸다. 한 번의 `BudgetedGateway.call()` 내부에서 몇 번의 provider request가 발생했는지 ledger가 관찰할 수 없었다.

영향:

- `max_model_calls`가 실제 provider request 상한이 아니었다.
- transient failure가 반복될수록 로컬 우선 정책에서 더 멀어졌다.
- recovery 단계에 남겨야 할 모델 예산을 transport retry가 은닉 소비했다.
- case 통계의 logical call과 task budget의 call 의미가 불명확해졌다.

### P0 — 실패 및 backoff의 실제 경과시간이 latency budget에서 누락

기존 ledger는 성공한 `GatewayResponse.latency_ms`만 더했다. provider가 0을 반환하거나 호출이 예외로 끝나면 로컬에서 실제로 소비된 시간은 0으로 기록됐다.

더 나쁜 점은 첫 transient failure와 backoff로 latency budget을 이미 소진했어도, 다음 retry 직전 ledger가 그 시간을 아직 보지 못한다는 것이었다.

영향:

- 실패한 요청은 latency가 없는 것처럼 기록된다.
- backoff가 task latency ceiling을 넘어도 추가 provider request가 가능하다.
- provider가 보고한 latency 값의 품질에 전역 예산 정확성이 종속된다.

## 4. 요소 관계성 평가

### 유지할 가치가 있는 관계

```text
CommandRuntime
  ├─ GoalInterpreter             # 로컬 규칙 우선
  ├─ StrategyPlanner             # capability 기반
  ├─ PlanningContextProvider
  │    └─ TargetingCompiler      # 필요한 경우에만 semantic interrupt
  ├─ PlanCompiler                # 결정적 DAG
  └─ TaskRuntime
       └─ ControlLoop
            ├─ Observer / Scene
            ├─ Grounder          # CPU 우선 후보 계산
            ├─ Executor          # stale/policy gate
            └─ Verifier          # 관찰 기반 사후 검증
```

좋은 점:

- 계획과 실행 경계가 분리돼 있다.
- stale decision은 input injector 전에 차단된다.
- configured semantic provider/model identity를 composition root에서 검증한다.
- task마다 ledger를 새로 만들기 때문에 예산 소유권이 비교적 명확하다.
- semantic targeting 및 grounding 실패 원인이 이전보다 덜 손실된다.
- 정상 경로를 로컬 결정론으로 유지할 수 있는 토대가 있다.

### 여전히 취약한 관계

#### P1 — 목적별 retry 설정이 실제 조립에서 하나로 축약

설정에는 `plan_compile_retry_attempts`, `action_decision_retry_attempts`, `reanalysis_retry_attempts`가 따로 있지만, `CommandRuntime`은 모든 목적에 `action_decision_retry_attempts` 하나를 사용한다.

현재 값이 모두 같아 즉시 동작 차이는 없지만, 설정과 런타임 관계는 거짓이다. 목적별 값을 조정하는 순간 운영자는 설정이 적용됐다고 착각하게 된다.

#### P1 — 작업당 모델 예산의 단일 출처가 없음

`config/runtime-config.yaml`은 `tier_budget.max_model_calls_per_task: 8`을 선언하지만, `CommandRequest`는 `TaskBudgetSpec()` 기본값인 2회를 사용한다. case 실행은 다시 case spec의 값을 주입한다.

즉 현재 예산은 한 곳에서 관리되지 않는다. 이번 패치가 이 값을 임의로 통합하지 않은 이유는 기존 case contract와 운영 기본값을 동시에 바꾸는 별도 migration이 필요하기 때문이다.

#### P1 — 동기 provider call과 `time.sleep`가 async runtime을 막음

gateway contract는 동기식이고 retry backoff도 `time.sleep`이다. task runtime이 async여도 모델 호출 동안 event loop 관점의 동시성은 막힌다.

이는 실제 병렬 workload가 생기면 CPU/local pipeline 활용률을 떨어뜨릴 수 있다. 다만 async gateway 전환은 adapter, timeout, cancellation, tests까지 건드리는 큰 변경이므로 이번 결함 수정에 섞지 않았다.

#### P2 — wrapper 탐색이 private `_inner` 관례에 의존

`CommandRuntime`과 budget layer는 투명 wrapper를 `_inner` 속성으로 순회한다. 현재 wrapper들과는 맞지만 명시적 wrapper protocol은 아니다.

이번에는 기존 관례를 재사용했다. 별도 interface를 추가하면 더 깨끗해지지만 wrapper 수가 적은 현재 단계에서는 오버엔지니어링 가능성이 더 크다.

#### P2 — CPU 최대 활용을 주장할 profiling evidence가 부족

로컬 관찰, targeting, grounding, verification 구조는 존재하지만 병목별 profile, CPU utilization, candidate scoring cost, capture/structure overlap 수치가 아직 없다.

worker pool이나 speculative parallelism을 먼저 넣는 것은 금지해야 한다. 측정 없이 병렬화하면 복잡도와 stale-state 위험만 늘어난다.

## 5. 실행한 최소 개선 플랜

1. 기존 wrapper 순서와 logical telemetry는 유지한다.
2. retry가 실제 provider request를 보내기 직전에 task-local budget hook을 호출한다.
3. hook은 공유 gateway 객체에 ledger를 저장하지 않고 `ContextVar`로 task/thread 격리를 유지한다.
4. 성공·실패 모두 로컬 monotonic 경과시간을 회계한다.
5. retry 직전 누적 경과시간을 projected latency로 검사해 이미 소진된 작업은 provider를 다시 호출하지 않는다.
6. 직접 호출, 투명 wrapper, composition root, 동시 실행을 회귀 테스트한다.
7. 기존 5계층 runtime workflow와 model reliability workflow에 새 계약을 연결한다.

명시적 비목표:

- 새 agent hierarchy
- provider/model 추가
- 목적별 phase reservation
- async gateway 전환
- CPU worker pool 자동 튜닝
- 사이트별 targeting 사전
- wrapper protocol 전면 재설계

## 6. 구현 요약

### `hpcu/gateway/gateway.py`

- retry attempt 직전 실행되는 task-local hook 추가
- `ContextVar` token을 `finally`에서 복원해 누수 방지
- 기존 retry 조건, backoff, provider identity 계약은 유지

### `hpcu/runtime_core/task_budget.py`

- retry wrapper가 내부에 있는지 cycle-safe하게 탐색
- retry가 없으면 기존처럼 logical call 진입 시 차감
- retry가 있으면 실제 provider attempt마다 차감
- 성공 시 provider 보고 latency와 로컬 관찰 latency 중 큰 값을 사용
- 실패 시에도 로컬 경과시간 기록
- 다음 retry 전에 projected latency ceiling 검사

### 테스트 및 workflow

- provider attempt 예산 회귀 테스트 추가
- transparent logical telemetry wrapper 안쪽의 retry도 검증
- `CommandRuntime` 자동 조립 경계 회귀 추가
- runtime-remediation의 정적/집중/결정성 gate에 포함
- model-reliability의 lint/focused gate에 포함

## 7. 5회 검증 결과

서로 다른 결함 계층을 검증했다. 동일 테스트를 이름만 바꿔 다섯 번 실행한 것이 아니다.

| 회차 | 검증 관점 | 결과 |
|---|---|---|
| 1 | 영향 범위 단위·composition 경계: 신규 retry 예산 6개, composition 시나리오 4개 | PASS |
| 2 | 조합 검증: call budget 0~4 × transient failure 0~4 × retry 0~3, 총 100개 | PASS |
| 3 | 동시성 격리: budgeted 64개 + direct 64개, 실제 provider attempt 256개 | PASS |
| 4 | 정적 계약: AST, compileall, 88자 제한, workflow YAML/trigger/test wiring | PASS |
| 5 | 결정성: 신규 pytest 5회 반복, 64개 시나리오 snapshot 10회 동일 hash | PASS |

상세 수치:

```text
1-focused-boundary
  retry_budget_tests=6 passed
  composition_boundary_scenarios=4 passed

2-combination-matrix
  cases=100
  successes=30
  budget_stops=40
  transport_failures=30
  invariant=ledger.model_calls == actual_provider_attempts

3-context-isolation
  tasks=128
  budgeted=64
  direct=64
  provider_attempts=256
  leaked_ledgers=0

4-static-contracts
  parsed_python_files=3
  max_line_length=87
  compileall=pass
  workflow_yaml_files=2
  triggers=main+codex/**+pull_request

5-deterministic-repeat
  pytest_repeats=5
  scenario_repeats=10
  fingerprint=c99308ddcdff9816831f900d53366e37fe3a0148a74b82f914bd5624abfd96cb
```

## 8. GitHub Actions 상태

PR에서 `model-reliability`와 `runtime-remediation`이 실제로 트리거되는 것은 확인했다. 그러나 run은 약 5초 안에 종료됐고 모든 job의 step 목록이 비어 있었다.

확인한 예:

- runtime-remediation run `32614518122`
- model-reliability run `32614518163`
- runtime-remediation run `32614761964`
- model-reliability run `32614761925`

이는 pytest 또는 lint 실패가 아니다. checkout/install 단계조차 생성되지 않은 runner 인프라 장애다. 따라서 원격 전체 portable suite를 통과했다고 기록하지 않는다.

저장소 Actions 설정, 과금, hosted runner 가용성이 복구되면 PR 또는 `main`에서 두 workflow를 다시 실행해야 한다.

## 9. 보류 리스크 우선순위

### 다음 P1 — 예산 설정 단일화

`tier_budget`, `TaskBudgetSpec` 기본값, case spec의 우선순위를 명시하고 composition root에서 하나의 effective budget을 생성해야 한다. 먼저 기존 case 계약과 운영 기대값을 고정해야 한다.

### 다음 P1 — 목적별 retry 정책 연결

`ModelCallPurpose`별 retry ceiling을 `RetryableGateway`에 전달하되, 동일 logical call의 telemetry와 physical attempt 예산을 계속 구분해야 한다.

### 다음 P1 — async cancellation 가능한 provider boundary

동기 call과 blocking backoff를 executor/thread 또는 async adapter로 이동하고, task latency deadline이 실제 요청 취소로 이어지게 해야 한다. 이는 별도 migration으로 처리해야 한다.

### 다음 P1 — phase starvation 계측

intent fill, plan compile, grounding, recovery가 하나의 global budget을 공유한다. 실제 호출 분포를 먼저 기록한 뒤 phase ceiling 또는 최소 예약량을 도입해야 한다.

### 다음 P2 — CPU 병목 측정

capture, structure merge, OCR, candidate scoring, verification의 wall time과 CPU utilization을 수집한 후에만 병렬화를 결정한다.

## 10. 최종 판단

이번 보강 전의 모델 예산은 논리 호출 횟수만 셌고 실제 provider 사용량은 제한하지 못했다. 로컬 우선 Computer Use의 핵심 계약으로 보기에는 허술했다.

이번 변경 후에는 다음이 성립한다.

- provider retry attempt가 task call budget을 우회하지 못한다.
- 실패와 backoff의 실제 경과시간이 task latency에 반영된다.
- latency ceiling을 넘긴 뒤 다음 provider attempt가 시작되지 않는다.
- shared gateway에 task ledger를 저장하지 않아 동시 실행 격리가 유지된다.
- planner, grounder, executor, verifier의 기존 관계는 건드리지 않았다.

기능을 늘린 것이 아니라 이미 선언된 모델 최소화 정책을 실제 transport 경계까지 강제했다. 현재 범위에서 필요한 수정은 여기까지다.
