# 2026-08-23 `main` 적대적 리뷰 및 로컬 우선 런타임 보강

## 1. 기준선

- 저장소: `dextune/dx-computer-use`
- 검토 기준 브랜치: `main`
- 기준 커밋: `3bfbaaa93bf077eef4203d2a90cc79f030dc724d`
- 검토 관점: CPU/로컬 자원 우선, 모델 호출 최소화, 실패 폐쇄, 검증 우선

이번 변경은 새 기능 묶음을 추가하는 작업이 아니다. 이미 존재하는 명령 해석 → 목표 컴파일 → 계획 → grounding → 실행 → 검증 경로에서, 로컬 판정이 사라지거나 AI 오류가 정상 입력처럼 위장되는 경계를 바로잡는 작업이다.

## 2. 적대적 결론

전체 구조는 이전보다 분명히 나아졌다. `CommandRuntime`이 단일 작업 예산을 만들고, `GoalInterpreter`, `StrategyPlanner`, `PlanningContextProvider`, `PlanCompiler`, `TaskRuntime`, `ControlLoop`을 연결하는 관계는 대체로 일관된다. 그러나 그 구조가 안전하다는 인상을 주는 것과 실제 실패 경계가 닫혀 있는 것은 별개였다.

가장 심각한 문제는 두 가지였다.

### P0 — 설정된 AI 실패를 로컬 성공처럼 위장

`TargetingCompiler`는 모델이 설정된 상태에서도 gateway 예외, JSON 파싱 실패, 스키마 충돌이 발생하면 사용자 목표 문자열의 토큰을 targeting 힌트로 다시 사용했다. 이는 “AI를 적게 쓴다”가 아니라 “AI가 실패했음을 숨기고 실행 가능한 추측으로 바꾼다”에 가깝다.

영향:

- 의미 해석 실패가 명시적 실패로 드러나지 않는다.
- 목표 문자열에 포함된 일반 단어가 성공 조건 또는 클릭 대상처럼 재사용될 수 있다.
- 공급자 장애와 모델 스키마 장애가 실제 UI 입력으로 이어질 여지가 생긴다.
- 모델 호출 회계는 남아도 의사결정 품질 실패는 통계에서 사라진다.

조치:

- gateway가 아예 없는 명시적 로컬 모드에서만 `goal_tokens` fallback을 허용한다.
- gateway가 설정된 경우 예외, JSON framing 실패, 타입/스키마 오류를 `TargetingCompilationError`로 종료한다.
- 예산 초과처럼 이미 타입화된 실패 코드는 보존한다.
- 한 번의 논리적 컴파일에서 모델 호출은 계속 최대 1회다.

### P0 — CPU grounding 판정을 런타임이 뭉개버림

`Grounder`는 후보 없음, 저신뢰, 상위 후보 동률을 서로 다르게 계산하고 있었지만, `ControlLoop`은 실행 직전 이 결과를 사실상 `grounding_no_candidates` 하나로 축약했다. CPU가 이미 계산한 정보를 버리고 복구 계층에 더 빈약한 입력을 넘긴 셈이다.

영향:

- 로컬 재관찰, semantic re-plan, 사용자 확인 중 어떤 복구가 적절한지 구분하기 어렵다.
- 실패 통계가 실제 원인을 반영하지 않는다.
- 동률 모호성이 단순 미탐처럼 기록되어 잘못된 튜닝을 유도한다.
- verification grounding 실패도 일반 검증 실패로 퇴색된다.

조치:

- `GroundingResult`에 타입화된 `failure_code`를 추가한다.
- `grounding_no_candidates`, `grounding_confidence_low`, `grounding_ambiguous`를 끝까지 보존한다.
- 해결되지 않은 targeting은 executor 호출 전에 차단한다.
- verification query의 grounding 실패도 동일하게 원인을 보존한다.

### P1 — CI가 존재하지만 `main`의 실제 검증 증거가 아님

기존 workflow의 push 대상이 일회성 과거 브랜치에 고정되어 있었다. 더구나 최근 실패한 Actions run은 job step이 하나도 시작되지 않은 runner 인프라 실패였다. 따라서 “workflow 파일이 있다”와 “최신 main이 원격에서 검증되었다”를 동일시할 수 없다.

조치:

- 두 신뢰성 workflow를 `main`과 `codex/**` push에서 실행하도록 교정한다.
- runtime remediation gate에 grounder 및 관련 회귀 테스트를 포함한다.
- PR과 merge 뒤 Actions 상태를 별도 확인하며, runner가 시작하지 못하면 코드 실패로 위장하지 않는다.

## 3. 요소 관계성 평가

현재 핵심 관계는 다음처럼 정리된다.

```text
CommandRuntime
  ├─ GoalInterpreter             # 로컬 규칙 우선 명령 정규화
  ├─ StrategyPlanner             # capability 기반 실행 전략
  ├─ PlanningContextProvider
  │    └─ TargetingCompiler      # 필요한 경우에만 semantic interrupt
  ├─ PlanCompiler                # 결정적 DAG 컴파일
  └─ TaskRuntime
       └─ ControlLoop
            ├─ Observer/Scene
            ├─ Grounder          # CPU 우선 후보 점수화
            ├─ Executor          # stale/policy/coordinate gate 뒤 입력
            └─ Verifier          # 관찰 기반 사후 검증
```

좋은 점:

- 계획과 실행이 분리되어 있으며, 실행 직전 stale decision 검사가 존재한다.
- 작업 단위 global model budget과 purpose별 회계가 한 런타임에 모인다.
- grounding과 verification이 executor 앞뒤에 배치되어 있다.
- 정상 경로를 로컬 결정론으로 유지할 수 있는 구조적 토대가 있다.

부족한 점:

- semantic compilation 실패 계약이 기존 로컬 fallback과 혼재되어 있었다.
- grounding의 세밀한 로컬 판정이 runtime boundary에서 손실되었다.
- phase별 모델 예산 예약은 아직 없다. intent fill이 전체 예산을 먼저 소비할 수 있다.
- compare 계열 목표의 독립 후보 의미 컴파일은 아직 충분히 일반화되지 않았다.
- composition root와 case harness 사이에 조립 책임 중복이 남아 있다.

이번 변경은 앞의 두 경계만 바로잡는다. 나머지는 성능 측정과 사용 사례 증거 없이 동시에 건드리면 오버엔지니어링이 된다.

## 4. 실행한 최소 개선 플랜

1. 설정된 semantic compiler는 실패 폐쇄한다.
2. CPU grounding 실패 원인을 타입화하여 실행·복구·검증 경계까지 보존한다.
3. CaseRunner가 targeting compilation 실패를 입력 0회 상태의 terminal failure로 기록한다.
4. `main`과 작업 브랜치에서 검증 workflow가 실제로 트리거되도록 한다.
5. 위 계약만 대상으로 단위·경계·반복 검증을 추가한다.

명시적 비목표:

- 새 agent hierarchy 또는 scheduler 추가
- 근거 없는 speculative concurrency
- 사이트/CTA별 문자열 사전 추가
- 무제한 재계획 루프
- VLM 호출 확대
- 벤치마크 없이 CPU worker 수 자동 튜닝

## 5. 구현 요약

- `hpcu/compiler/targeting_compiler.py`
  - `TargetingCompilationError` 추가
  - configured gateway 실패 시 fail-closed
  - no-gateway일 때만 deterministic local token fallback
  - gateway의 타입화된 실패 코드 보존

- `hpcu/grounder/grounder.py`
  - `GroundingResult.failure_code` 추가
  - 후보 없음/저신뢰/모호성 분리

- `hpcu/runtime_core/control_loop.py`
  - pre-action 및 verification grounding 실패 코드 보존
  - unresolved target에서 executor 호출 차단

- `hpcu/cases/runner.py`
  - targeting compilation 실패를 action count 0의 terminal result로 매핑

- workflow 2개와 관련 회귀 테스트 보강

## 6. 5회 검증 결과

동일 테스트를 이름만 바꿔 반복하지 않고, 서로 다른 실패 계층을 검증했다.

| 회차 | 검증 관점 | 결과 |
|---|---|---|
| 1 | 정적 계약: Python AST/compile, 88자 제한, workflow YAML, trigger, fail-open 패턴 | PASS |
| 2 | 모델 실패 주입: invalid JSON, 충돌 객체, gateway 예외, 예산 실패, local-only 경로 | PASS |
| 3 | CPU grounding 분류: resolved/no-candidate/low-confidence/ambiguous | PASS |
| 4 | 런타임 차단: pre-action executor 0회, verification 실패 원인 보존 | PASS |
| 5 | Case boundary 및 결정성 반복: terminal code, action 0회, 핵심 경로 3회 동일 fingerprint | PASS |

검증 산출:

```text
PASS 1-static-contracts
PASS 2-model-failure-injection
PASS 3-cpu-grounding-classification
PASS 4-control-loop-propagation
PASS 5-case-boundary-and-repeat
repeat_fingerprint=946ff793f1be026ab7f907e7f9c5577f86cec24dae0adbc623b7001f1f05b89c
```

## 7. 보류된 리스크와 후속 우선순위

### 다음 P1

작업 전역 예산만 두지 말고 `intent_fill`, `plan_compile`, `recovery`에 최소 예약량 또는 phase ceiling을 둔다. 단, 실제 호출 분포를 먼저 계측해야 한다.

### 다음 P1

compare/select 계열에서 후보 A/B가 각각 독립적인 semantic target contract를 갖도록 일반화한다. 사이트별 템플릿으로 해결해서는 안 된다.

### 다음 P2

CPU 병렬화는 observation/candidate scoring profile을 수집한 뒤 적용한다. 현재 단계에서 worker pool을 추가하면 코드 복잡도만 늘고 병목을 옮길 가능성이 높다.

### 운영 리스크

GitHub-hosted runner가 job step을 시작하지 못하는 인프라 문제는 코드 수정으로 해결할 수 없다. PR 및 merge 후 run을 재확인하고, 동일 증상이면 저장소 Actions 설정/과금/runner 가용성을 별도 점검해야 한다.
