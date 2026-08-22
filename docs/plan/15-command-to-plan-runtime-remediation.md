---
title: "HPCU Runtime 개발 계획 15 — 사용자 명령→PlanIR→단일 실행 런타임 개선"
version: "1.0"
date: "2026-08-22"
parent: "AGENTS.md, docs/plan/00-overview-and-goals.md, docs/plan/06-grounding-confidence.md, docs/plan/07-action-dsl-runtime.md, docs/plan/09-workflow-compiler.md, docs/plan/11-quality-benchmark.md, docs/plan/13-common-pipeline-remediation.md, docs/plan/14-goal-compiled-targeting.md"
language: "ko-KR"
---

# 15. 사용자 명령→PlanIR→단일 실행 런타임 개선

현재 `main`의 코드·문서·저장된 실행 아티팩트를 대조해 발견한 **제품 경로 부채**를
해소하는 구현 순서다. 이 문서는 새 스펙을 중복해서 소유하지 않는다.

- 명령 해석·전략 선택·PlanIR의 새 경계와 마이그레이션 순서는 이 문서가 소유한다.
- Action DSL·실행·검증 계약은 `07-action-dsl-runtime.md`가 소유한다.
- Grounding·confidence·모델 승격은 `06-grounding-confidence.md`가 소유한다.
- 경험 승격·workflow qualification은 `09-workflow-compiler.md`가 소유한다.
- 벤치마크 지표와 SLO는 `11-quality-benchmark.md`가 소유한다.
- `TargetingPack` 어휘 계약은 `14-goal-compiled-targeting.md`가 소유하되,
  본 계획 완료 후 **PlanIR node의 grounding hint**로 위치를 낮춘다.

진행 체크는 `docs/plan/tasklist.md`의 **U-*** 항목에서만 한다.

---

## 1. 검수 기준점

검수 기준은 `main` commit `da147dbc87d1ecbe1a364907b9a61e107e0c8a36`
(`feat: add provider-neutral screen runtime and retry evidence`)이다.

저장된 최신 재시도 결과
`artifacts/retries/failed-cases-loop-15/stats.json`은 5/5 성공을 기록했지만,
그 수치를 제품 일반화의 증거로 사용하지 않는다.

- 5개 케이스, `model_call_count=9`, `model_tokens=8477`
- `compile_call_count=5`, `grounding_call_count=4`
- 케이스 elapsed 합 79,659ms
- 저장된 모델 latency 합 58,736ms — 순차 합 기준 전체의 약 73.7%
- `action_count=67`에는 capture·settle·model·evidence 진단 이벤트가 포함됨
- 5개 케이스 모두 완성된 `start_url`을 제공하고 실제 인페이지 선택은 검증하지 않음
- `430 passed, 4 deselected` 실행은 live provider를 포함하지 않음
- `tests/integration/`, `tests/replay/`는 현재 실질 테스트가 없음

따라서 현재 결과가 증명하는 것은 브라우저 focus·주소창 입력·OCR 문자열 관찰의
smoke path다. 사용자 명령의 유연한 방향 설정, 다단계 계획, 독립 검증,
0-model-call replay는 아직 제품 경로로 증명되지 않았다.

---

## 2. 한 줄 진단

> `TargetingPack`은 사이트 사전을 지웠지만 **계획을 만들지는 않는다**.
> 현재 제품 경로는 사실상 “완성 URL 입력 → 0~1회 클릭 → 문자열 확인”이며,
> `CaseRunner`와 `ControlLoop`가 서로 다른 정책·예산·검증 규칙을 가진다.

하위 컴포넌트는 재사용할 수 있다. 문제는 부품 수가 아니라 **상위 방향 결정과
단일 실행 경로가 부재**한 것이다.

---

## 3. 현재 결함 스냅샷

| ID | 증상 | 직접 영향 |
|---|---|---|
| D1 | 자연어 명령을 intent·제약·terminal state·작업 DAG로 변환하는 경계가 없음 | 새 케이스를 `start_url`과 어휘 팩으로 축소하게 됨 |
| D2 | `TargetingPack`이 goal compiler의 결과처럼 사용됨 | 어휘 힌트가 전략·계획·증거를 대신함 |
| D3 | `CaseRunner`가 gateway·input·retry·verification을 직접 소유하고 `ControlLoop`를 우회 | 정책·stale·예산 규칙이 경로마다 달라짐 |
| D4 | `ControlLoop.step()`이 action 후 fresh scene을 관찰하지 않고 이전 scene으로 postcondition 평가 | no-op action도 성공 가능 |
| D5 | 모델이 evidence token을 만들고 같은 token으로 성공을 판정 | 독립 oracle 부재, false-success 가능 |
| D6 | `_case_model_calls`가 attempt마다 초기화되고 compile/reanalysis와 분리 | task 전체 모델 예산이 보장되지 않음 |
| D7 | 모델 schema는 여러 Action을 허용하지만 `CaseRunner`는 실질적으로 click/none만 처리 | 다단계 type·select·scroll 작업 표현 불가 |
| D8 | unknown physical command가 `CLICK`으로 기본 변환 | malformed command가 side effect로 바뀌는 fail-open |
| D9 | tile hash·ring buffer·IoU tracker가 실제 `CompositeObserver` hot path에 연결되지 않음 | CPU-first가 증분 처리보다 반복 OCR에 가까움 |
| D10 | OCR element의 `scene_version`까지 equality 비교하고 안정 ID가 약함 | unchanged screen도 modified로 보일 수 있음 |
| D11 | OCR source의 이론상 최대 score가 기본 local threshold보다 낮은 구간이 있음 | local direct path가 구조상 막히고 runner가 별도 우회 |
| D12 | workflow compiler가 verified transition보다 action record 복사에 가까움 | 0-call replay qualification을 증명하지 못함 |
| D13 | `action_count`가 실제 input과 trace event를 섞음 | 성능·효율 지표가 해석 불가능 |
| D14 | integration/replay/held-out gate가 비어 있음 | 단위 테스트 통과가 제품 능력으로 오인됨 |

---

## 4. 먼저 고정할 제품 경계

현재 문서에는 다음 두 문장이 동시에 존재한다.

1. 허가된 API/CLI/DOM/접근성 조작이 가능하면 가장 빠른 로컬 경로를 우선한다.
2. 화면 제어가 제품 기본 경계이며 DOM/CDP/API가 화면 제어를 대신하지 않는다.

구현자가 임의로 해석하지 않도록 U1에서 실행 모드를 명시한다.

### 4.1 `ExecutionMode`

| 모드 | 관찰 | side effect | 용도 |
|---|---|---|---|
| `screen_strict` | Structure/OCR/geometry 사용 가능 | `InputInjector.physical`만 | 물리 화면 제어 자체를 검증하는 환경 |
| `local_semantic` | API/CLI/DOM/AX/UIA/AT-SPI/OCR | policy가 허용한 semantic/tool action 후 화면 재검증 | 제품 성능·CPU/local-first 기본 모드 |

공통 불변식:

- 어느 모드든 side effect는 `Action` → `PolicyGate` → `Executor` 경로만 통과한다.
- 직접 API/CLI 호출도 `ActionOp.CALL_TOOL`과 capability·policy·trace를 가져야 한다.
- 화면 외부 효과를 사용해도 성공은 `EvidenceContract`로 독립 검증한다.
- runner·recovery·plugin이 raw injector를 직접 호출하지 않는다.
- 모드 결정은 사용자 명령, 정책, capability에 따라 plan-time에 고정한다.

이 경계는 ADR로 이유를 기록하고, 실제 계약은 01·03·07·10의 소유 문서에 반영한다.

---

## 5. 목표 아키텍처

```text
UserCommand
    ↓
GoalInterpreter
    ├─ CPU: 숫자·날짜·가격·개수·부정·명시 앱/사이트·위험 동사 파싱
    └─ Semantic interrupt: CPU가 채우지 못한 slot만 보완
    ↓
GoalEnvelope
    ↓
CapabilitySnapshot + CurrentContext + WorkflowIndex
    ↓
StrategyPlanner
    ↓
StrategyPlan
    ↓
PlanCompiler
    ↓
PlanIR (typed DAG / state machine)
    ↓
TaskRuntime
    └─ ControlLoop.step(PlanNode)
         observe delta
         → ground
         → policy + stale gate
         → execute
         → fresh observe
         → verify transition
         → recovery edge 또는 commit
               ↓ unresolved only
         SemanticInterrupt
    ↓
Trace → WorkflowCompiler → qualified replay
```

`CaseRunner`는 이 경로를 조립하는 제품 런타임이 아니다. 마이그레이션 후에는
case spec을 `GoalEnvelope` 입력으로 변환하고 결과·아티팩트를 수집하는 **test adapter**만 남긴다.

---

## 6. 새 경계 DTO

경계 DTO는 `frozen dataclass + __post_init__` 검증을 기본으로 한다.
모델 응답 DTO와 런타임 DTO를 같은 타입으로 쓰지 않는다.

### 6.1 `GoalEnvelope`

권장 위치: `hpcu/schemas/goal.py`

```python
@dataclass(frozen=True)
class GoalEnvelope:
    raw_instruction: str
    intent: IntentKind
    terminal_state: TerminalState
    entities: tuple[GoalEntity, ...]
    constraints: tuple[GoalConstraint, ...]
    preferred_surface: SurfaceKind | None
    forbidden_actions: tuple[ActionOp, ...]
    risk_class: RiskClass
    reversibility: Reversibility
    ambiguity_slots: tuple[str, ...]
    evidence_requirements: tuple[EvidenceRequirement, ...]
    latency_budget_ms: int
    model_call_budget: int
```

규칙:

- 원문 문자열을 버리지 않는다.
- 숫자·날짜·가격·개수·명시 앱/사이트·부정 조건은 CPU parser가 먼저 추출한다.
- 모델은 전체 계획을 자유문으로 반환하지 않고 unresolved slot만 schema로 채운다.
- 위험·금지 action은 모델이 완화할 수 없다.
- 단순 navigate/search는 parser 결과만으로 plan을 만들 수 있어야 한다.

### 6.2 `CapabilitySnapshot`

권장 위치: `hpcu/schemas/capability_snapshot.py`

```python
@dataclass(frozen=True)
class CapabilitySnapshot:
    active_surface: SurfaceKind
    execution_mode: ExecutionMode
    capture: Capability
    structure: Capability
    semantic_input: Capability
    physical_input: Capability
    ocr: Capability
    dirty_rects: Capability
    tool_actions: tuple[str, ...]
    reusable_workflows: tuple[str, ...]
    measured_latency_ms: Mapping[str, float]
    observed_reliability: Mapping[str, float]
```

계획은 존재하지 않는 capability를 가정하지 않는다.
`UNSUPPORTED`는 recovery가 아니라 plan 후보 제거 조건이다.

### 6.3 `StrategyPlan`

권장 위치: `hpcu/schemas/strategy.py`

각 후보는 실행 가능성·예상 단계·증거 강도·위험·모델 비용을 가진다.

```text
utility =
    feasibility
  × expected_reliability
  × evidence_strength
  - expected_latency
  - model_cost
  - risk_cost
  - recovery_cost
```

초기 구현은 학습 모델이 아니라 설정 기반 결정론 점수식으로 시작한다.
동점 또는 필수 slot 미해결 때만 configured semantic provider를 호출한다.

### 6.4 `PlanIR`

권장 위치: `hpcu/schemas/plan.py`

```python
@dataclass(frozen=True)
class PlanNode:
    id: str
    op: ActionOp
    surface: SurfaceKind
    target_query: TargetQuery | None
    grounding_hints: GroundingHints | None
    value: str | None
    preconditions: tuple[Precondition, ...]
    postconditions: tuple[Postcondition, ...]
    evidence: EvidenceContract
    risk: RiskClass
    idempotency: Idempotency
    timeout_ms: int
    retry: RetryPolicy
    model_gate: ModelGate
    success_edge: str | None
    failure_edges: Mapping[str, str]
```

```python
@dataclass(frozen=True)
class PlanIR:
    goal: GoalEnvelope
    strategy_id: str
    entry_node_id: str
    nodes: Mapping[str, PlanNode]
    task_budget: TaskBudgetSpec
    compiler_version: str
```

불변식:

- plan-time 모델은 좌표·현재 `element_id`를 반환하지 않는다.
- 모든 side effect node는 precondition·postcondition·risk를 가진다.
- retry는 node 재실행이 아니라 명시된 failure edge를 따른다.
- 한 PlanIR은 여러 click/type/select/scroll/tool node를 표현할 수 있다.
- `TargetingPack`은 전역 goal 결과가 아니라 node별 `GroundingHints`로 이동한다.

### 6.5 `TaskBudgetSpec` / `TaskBudgetLedger`

권장 위치: `hpcu/schemas/budget.py`, `hpcu/runtime_core/task_budget.py`

```python
@dataclass(frozen=True)
class TaskBudgetSpec:
    max_model_calls: int
    max_model_tokens: int
    max_model_latency_ms: int
```

```python
@dataclass
class TaskBudgetLedger:
    spec: TaskBudgetSpec
    calls_by_purpose: dict[ModelCallPurpose, int]
    tokens_used: int = 0
    latency_ms: int = 0
```

- plan compile, intent fill, grounding, reanalysis, recovery를 모두 합산한다.
- immutable limit와 mutable runtime counter를 분리한다.
- attempt가 바뀌어도 ledger를 초기화하지 않는다.
- gateway 외부에서 counter를 따로 만들지 않는다.
- budget 소진은 클릭 fallback이 아니라 명시적 `MODEL_BUDGET_EXHAUSTED`다.

---

## 7. 단일 실행 경로 계약

### 7.1 역할 분리

| 모듈 | 최종 역할 |
|---|---|
| `GoalInterpreter` | 자연어 → `GoalEnvelope` |
| `StrategyPlanner` | capability/context/workflow를 보고 전략 선택 |
| `PlanCompiler` | 전략 → typed `PlanIR` |
| `TaskRuntime` | PlanIR node 상태·edge·budget 관리 |
| `ControlLoop.step()` | 단일 node의 observe→ground→policy→execute→fresh observe→verify |
| `CaseRunner` | fixture 입력·통계·아티팩트 adapter |
| `ModelRouter` | `SemanticInterrupt` 한 경계로 흡수 또는 제거 |
| `TargetingCompiler` | node-level `GroundingHints` 편찬기로 축소 |

다음은 금지한다.

- `CaseRunner` 내부에서 gateway 직접 호출
- runner/recovery/plugin에서 raw injector 직접 호출
- 경로별 별도 success state machine
- 경로별 별도 모델 budget
- unknown action을 click으로 변환
- action 전 scene으로 postcondition 평가

### 7.2 action freshness

`PreparedAction`에는 최소 다음 binding을 넣는다.

```python
@dataclass(frozen=True)
class PreparedAction:
    action: Action
    element_id: str | None
    source_scene_version: int
    source_frame_id: str | None
    target_fingerprint: str | None
    target_bbox: BoundingBox | None
```

실행 직전 현재 scene version 또는 target fingerprint가 달라지면 실행하지 않는다.
고정 좌표는 binding의 결과일 뿐 재사용 가능한 selector가 아니다.

### 7.3 transition verification

```text
pre_scene
→ policy/stale gate
→ execute
→ post_scene = fresh observe()
→ SceneDelta(pre, post)
→ verifier.verify_transition(pre_scene, action, post_scene)
→ success state commit
```

성공 조건:

- action의 low-level `ExecutionResult.success`만으로 완료하지 않는다.
- 모델 `goal_state=success`만으로 완료하지 않는다.
- `bool(scene_blob)`를 증거로 사용하지 않는다.
- 성공 시 이전 attempt의 `failure_code`·`failure`를 명시적으로 clear한다.
- reanalysis가 `unknown`, `in_progress`, `failed`, `human_handoff`이면 성공 경로로 통과하지 않는다.
- navigation/no-op 목표도 URL·title·content·state 중 최소 두 독립 신호 또는
  하나의 강한 typed signal을 요구한다.

---

## 8. CPU-first hot path

CPU-first는 “매 frame 전체 OCR”이 아니라 **변화량을 최소 비용으로 좁히는 것**이다.

### 8.1 capture와 frame lifetime

- `FrameStore`에 bounded capacity와 eviction을 추가한다.
- 실제 shared memory를 쓰지 않는 구현이면 `shm_id`라는 이름으로 무복사를 주장하지 않는다.
- capture backend가 PNG를 제공해도 perception 내부에서 반복 decode→crop→encode하지 않도록
  decoded frame/cache 또는 raw buffer 계약을 추가한다.
- frame handle·store lifetime을 trace와 분리한다.

### 8.2 dirty ROI 연결

실제 hot path:

```text
capture
→ backend dirty rect 또는 tile hash
→ unchanged: 이전 Scene 재사용
→ changed ROI만 perception
→ structure delta와 fusion
→ stable id remap
→ SceneDelta
```

- `hpcu/capture/tile_hash.py`와 ring buffer를 `CompositeObserver`에 실제 연결한다.
- ROI dense OCR → 필요 ROI에만 sparse OCR; full-screen 재시도는 budgeted fallback이다.
- 처리 pixel 수와 OCR pass 수를 benchmark에 기록한다.

### 8.3 stable element identity

- equality diff에서 `scene_version`, first/last seen 같은 transient field를 제외한다.
- fingerprint가 없으면 IoU + text/role/source ensemble로 remap한다.
- `ocr_line_N` 순번을 안정 ID로 간주하지 않는다.
- 동일 화면에서 unchanged element가 매 frame modified가 되는 회귀 테스트를 추가한다.

### 8.4 generic perception

가격·통화 패턴은 중립적인 `numeric_amount` feature로 추출할 수 있으나,
공통 perception이 임의로 `role="product"`를 확정하지 않는다.

- OCR는 text·bbox·confidence·generic feature를 만든다.
- “상품”, “성공”, “결제 버튼” 같은 goal 의미는 node grounding hint 또는
  structure role과 결합해 결정한다.
- source별 confidence ceiling을 실측하고 threshold를 source-aware하게 보정한다.
- OCR 후보가 이론상 local threshold를 넘을 수 없는 설정을 금지하는 테스트를 둔다.

---

## 9. Workflow compiler의 승격 기준

workflow는 action log 복사가 아니라 **검증된 state transition의 재사용 프로그램**이다.

승격 입력:

- `GoalEnvelope`
- 선택된 `StrategyPlan`
- 실행된 `PlanIR`
- 각 node의 pre/post scene fingerprint
- 실제 selector ensemble
- independent evidence result
- recovery edge
- capability·execution mode·environment fingerprint

승격 단계:

1. 한 번 성공: trace candidate
2. 동일 goal family 반복 성공: experimental workflow
3. 다른 세션·viewport·theme·locale에서 shadow replay 통과: qualified
4. 실제 replay에서 fresh verification 통과: active
5. drift 또는 false completion: 즉시 downgrade

`ShadowReplayEngine`은 element ID 문자열만 비교하지 않는다.
같은 `TaskRuntime`·Grounder·Verifier를 offline scene stream에 실행한다.

---

## 10. 작업 묶음 — 순서 강제

한 묶음의 완료 조건이 자동 테스트와 아티팩트로 닫히기 전에 다음 묶음으로 가지 않는다.
새 feature·새 사이트 케이스 추가보다 U1~U6을 우선한다.

### U1 — 경계 결정·기준선·태스크 정직화

**목표:** 무엇을 제품 성공이라 부를지 먼저 고정한다.

변경:

- `screen_strict` / `local_semantic` 실행 모드 ADR 작성
- 현재 `main`의 false-success·모델 latency·physical input 기준선 저장
- 기존 tasklist `[x]`를 component existence와 product qualification으로 분리
- `action_count`를 input/event/observe/model/verify로 분리할 스키마 확정
- held-out spec에서 완성 `start_url`을 정답으로 제공하지 않는 규칙 추가

완료 조건:

- 같은 commit/config에서 재현 가능한 baseline JSON
- success인데 `failure_code`가 남는 fixture가 실패
- `tests/integration`, `tests/replay`가 빈 디렉터리가 아님
- 문서 소유 관계가 00·01·06·07·09·11·14와 충돌하지 않음

### U2 — `GoalEnvelope`와 최소 semantic fill

**목표:** 사용자 명령에서 방향 설정에 필요한 구조를 먼저 만든다.

변경:

- `hpcu/schemas/goal.py`
- `hpcu/planning/goal_interpreter.py`
- CPU parser: 숫자·날짜·가격·개수·명시 surface·부정·위험 표현
- unresolved slot만 configured semantic provider가 채우는 strict schema
- 같은 goal의 canonical hash와 cache

완료 조건:

- navigate/search/select/compare/edit/submit 최소 6 intent fixture
- 모델 없이 구조화 가능한 명령은 model call 0
- provider 오류 시 임의 intent로 추측하지 않고 unresolved 반환
- 위험·forbidden field를 모델 응답이 완화하지 못함

### U3 — Capability-aware `StrategyPlan`과 typed `PlanIR`

**목표:** 하나의 `pick_query`가 아니라 다단계 실행 프로그램을 만든다.

변경:

- `CapabilitySnapshot`, `StrategyCandidate`, `StrategyPlan`
- `PlanNode`, `PlanIR`, JSON schema
- workflow cache 우선, deterministic strategy score
- `TargetingPack` compatibility adapter → node `GroundingHints`
- plan-time 모델 응답에서 좌표·현재 element ID 거부

완료 조건:

- search box focus → type → submit → result verify의 4-node PlanIR
- existing-screen, URL entry, terminal command 전략을 동일 PlanIR로 표현
- capability가 없는 전략은 선택되지 않음
- 동일 입력·동일 context에서 PlanIR hash가 결정론적
- plan compile 호출은 uncached task당 최대 1회

### U4 — `TaskRuntime` + 단일 `ControlLoop`

**목표:** 실제 제품 경로를 하나로 만든다.

변경:

- `TaskRuntime`은 PlanIR node/edge/status만 관리
- `ControlLoop.step(node)`가 유일한 action cycle
- `CaseRunner`의 gateway/input/policy/verification/retry 로직 제거
- `ModelRouter`와 runner semantic decision을 `SemanticInterrupt`로 통합
- 모든 navigation·recovery·tool action도 Action DSL 경유

완료 조건:

- `CaseRunner`가 raw gateway·injector를 import하지 않음
- browser·terminal fixture가 같은 `TaskRuntime`을 사용
- 5개 이상 node 작업이 model runtime call 0으로 실행
- policy HIGH면 어떤 adapter 경로에서도 side effect 0
- unknown command가 `ACTION_UNSUPPORTED`로 fail closed

### U5 — fresh verification·stale binding·상태 정규화

**목표:** false-success 경로를 구조적으로 제거한다.

변경:

- `PreparedAction` scene/frame/fingerprint binding
- action 후 fresh observe 강제
- `Verifier.verify_transition(pre, action, post)`
- typed navigation/search/select/edit evidence
- success/failure state transition 한 곳
- lexical token은 evidence 보조 신호로만 사용

완료 조건:

- no-op injector는 성공하지 못함
- 이전 scene에만 존재하는 postcondition은 실패
- action 사이 화면 변화 시 stale rejection 100%
- `bool(scene_blob)`·모델 success 문장만으로 완료 0건
- success row의 failure code는 항상 empty
- false-success failure injection suite 통과

### U6 — 전역 `TaskBudget`과 model-last 정책

**목표:** 모델 호출을 task 전체에서 통제하고 호출 이유를 설명 가능하게 만든다.

변경:

- compile/intent/ground/reanalysis/recovery 공통 budget
- attempt별 counter reset 제거
- purpose별 token·latency·error 기록
- 동일 scene에서 schema 오류만 이유로 무의미하게 재질문하지 않음
- plan/cache/local grounding/semantic interrupt 순서 고정

완료 조건:

- budget 초과 호출 0
- simple navigate/search runtime grounding call 0
- compiled replay total model call 0
- 모델 실패 시 임의 lexical click 0
- 모든 model call에 triggering failure code와 unresolved slot 기록

### U7 — 실제 증분 CPU perception

**목표:** 전체 OCR 반복을 dirty ROI pipeline으로 교체한다.

변경:

- bounded `FrameStore`
- ring buffer·tile hash·dirty rect 연결
- decoded frame/crop cache
- stable OCR ID와 semantic diff
- source-aware confidence calibration
- generic feature extraction; shopping role 제거

완료 조건:

- unchanged 1080p frame 처리 p95 ≤ 10ms — reference hardware 기록
- delta scene update p95 ≤ 150ms
- unchanged frame OCR pass 0
- 변경 pixel 비율·OCR pass·CPU time·peak RSS 기록
- 동일 화면 100 frame에서 stable element ID 유지율 측정
- OCR-only exact target의 local path가 설정상 도달 가능

### U8 — verified workflow compile과 실제 replay

**목표:** 성공 경험을 0-call 재사용 프로그램으로 승격한다.

변경:

- verified transition 기반 parameterization
- selector ensemble·node evidence·recovery edge 저장
- offline scene stream에 `TaskRuntime` shadow replay
- multi-session qualification, drift downgrade
- workflow artifact에 compiler/config/schema version 기록

완료 조건:

- 첫 실행 verified success 후 동일 fixture replay model 0
- viewport/text drift에서 재탐색 또는 halt, 고정 좌표 재생 0
- 실패 trajectory 승격 0
- shadow replay가 element ID 문자열 비교만 하지 않음
- workflow qualification 결과가 독립 artifact로 남음

### U9 — held-out 제품 자격 게이트

**목표:** unit count가 아닌 실제 일반화·성능·안전을 릴리스 기준으로 만든다.

초기 benchmark 구성:

- 30개 이상 held-out task
- intent family 5개 이상
- surface 3개 이상
- 절반 이상 3 node 이상
- 중복 label, popup, 느린 로딩, stale frame, 다른 locale/viewport 포함
- test spec에 최종 URL·정답 element ID·성공 문자열 직접 제공 금지

초기 합격 기준:

- false completion 0
- stale action 실행 0
- policy bypass 0
- access-control 우회 0
- compiled replay model call 0
- simple navigate/search의 runtime model call 0
- uncached task plan compile ≤1
- 모델 latency / total latency를 cached·uncached로 분리 보고
- physical/semantic/tool input과 trace event를 분리 보고
- 실패 케이스는 5회 이내 bounded stop + 증거 artifact

---

## 11. 테스트 구조

### Unit

- DTO invariant
- local goal parser
- strategy score
- PlanIR validation
- budget monotonicity
- source-aware confidence
- unknown action rejection
- state transition normalization

### Integration

- `GoalEnvelope → PlanIR → TaskRuntime → ControlLoop → Verifier`
- browser fixture multi-step
- terminal fixture multi-step
- policy gate와 adapter 경계
- model timeout/schema error
- action 후 fresh scene

### Replay

- stored scene stream에 같은 runtime 실행
- selector drift
- locale/viewport 변화
- workflow qualification/downgrade
- model call 0 확인

### Failure injection

- stale after ground
- no-op input
- frame unchanged
- malformed command
- false success token
- budget exhaustion
- partial action failure
- popup loop
- access-control screen

### E2E

- 실 provider 검증은 unit pass로 대체하지 않는다.
- provider/network 실패는 환경 실패 또는 model failure로 기록한다.
- 실제 X11/Browser/Windows 결과는 commit/config/provider identity를 포함한다.

---

## 12. 지표 스키마

`CaseStats.action_count` 하나로 여러 현상을 합치지 않는다.

```text
trace_event_count
observation_count
full_frame_count
dirty_roi_count
ocr_pass_count
ocr_processed_pixels
semantic_input_count
physical_input_count
tool_action_count
verification_count
recovery_count
model_call_count
model_tokens
model_latency_ms
cpu_time_ms
wall_time_ms
peak_rss_bytes
stale_rejection_count
false_completion_count
```

모든 benchmark artifact에 다음 fingerprint를 넣는다.

- commit SHA
- runtime config hash
- schema/compiler version
- provider/model identity
- OS·CPU·RAM·resolution·scaling
- case dataset version
- execution mode

---

## 13. 마이그레이션 원칙

1. U2~U3은 기존 runner를 건드리기 전에 schema와 fake tests로 닫는다.
2. U4에서 새 loop를 하나 더 만들지 않는다. 기존 `ControlLoop`를 유일 경로로 승격한다.
3. `CaseRunner`는 한 PR에서 test adapter로 축소한다. 장기간 dual-path를 유지하지 않는다.
4. U5가 끝나기 전에는 `verified_success`를 제품 지표로 사용하지 않는다.
5. U6가 끝나기 전에는 calls/task 절감 주장을 하지 않는다.
6. U7 최적화는 U4~U5 correctness gate 뒤에 한다. 빠른 오답을 먼저 만들지 않는다.
7. U8 workflow 승격은 verified transition만 입력으로 받는다.
8. 기존 API는 compatibility alias를 둘 수 있으나 새 코드가 legacy path를 import하면 CI가 실패한다.

---

## 14. 문서 동기화

각 구현 묶음에서 다음 소유 문서를 함께 갱신한다.

| 변경 | 소유 문서 |
|---|---|
| execution mode와 module map | 01·03, 이유는 새 ADR |
| GoalEnvelope·PlanIR 경계 | 02와 본 계획 |
| model tier·global budget | 06·08 |
| single loop·fresh verification·stale binding | 07 |
| workflow qualification | 09 |
| policy gate·forbidden action | 10 |
| 지표·held-out gate | 11 |
| TargetingPack → node GroundingHints | 14 |
| 진행 체크 | tasklist |
| 짧은 불변식 변경이 필요한 경우 | AGENTS.md |

기존 문서에 같은 표를 복사하지 않는다. 본 계획은 **개선 순서**, 각 소유 문서는
**최종 계약**만 가진다.

---

## 15. 비목표

- 사이트별 adapter·CTA·광고·CAPTCHA 문자열 사전 추가
- 모델이 좌표 또는 plan-time element ID를 직접 선택
- 최종 URL을 case spec에 넣어 계획을 우회
- 단위 테스트 수를 제품 성공률로 홍보
- U4 전에 또 다른 runner/control loop 추가
- 검증 없이 workflow 자동 승격
- 보안 확인·로그인·CAPTCHA 우회
- 모든 OS native adapter를 본 계획에서 동시에 완성

---

## 16. 계획 완료 정의

U1~U9 완료는 다음을 모두 만족할 때만 선언한다.

1. 사용자 명령이 `GoalEnvelope → StrategyPlan → PlanIR`로 구조화된다.
2. 모든 제품·case·replay 경로가 하나의 `TaskRuntime/ControlLoop`를 사용한다.
3. 모든 side effect가 policy와 stale gate를 통과한다.
4. 모든 action 성공은 fresh post-scene 또는 독립 외부 효과로 검증된다.
5. 모델 호출은 task-global budget에 포함되고 unresolved 지점에서만 발생한다.
6. unchanged frame은 full OCR 없이 처리된다.
7. verified trajectory가 다음 실행에서 모델 0회로 replay된다.
8. held-out benchmark에서 false completion·stale action·policy bypass가 0이다.
9. tasklist `[x]`는 위 자동 gate와 artifact로 증명된다.
