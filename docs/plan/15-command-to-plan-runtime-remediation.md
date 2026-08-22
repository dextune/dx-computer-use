---
title: "HPCU Runtime 개발 계획 15 — 사용자 명령→PlanIR→단일 실행 런타임 개선"
version: "1.1"
date: "2026-08-23"
parent: "AGENTS.md, docs/plan/00-overview-and-goals.md, docs/plan/06-grounding-confidence.md, docs/plan/07-action-dsl-runtime.md, docs/plan/08-model-gateway.md, docs/plan/09-workflow-compiler.md, docs/plan/11-quality-benchmark.md, docs/plan/13-common-pipeline-remediation.md, docs/plan/14-goal-compiled-targeting.md, docs/plan/16-model-response-reliability.md"
language: "ko-KR"
---

# 15. 사용자 명령→PlanIR→단일 실행 런타임 개선

이 문서는 HPCU의 **제품 실행 경로를 하나로 수렴시키는 구현 순서**를 소유한다.
새 스펙을 여기저기 복제하지 않는다.

- 명령 해석·전략 선택·PlanIR·replan·단일 task runtime의 마이그레이션 순서는 이 문서가 소유한다.
- Action DSL·실행·검증 계약은 `07-action-dsl-runtime.md`가 소유한다.
- Grounding·confidence·모델 승격은 `06-grounding-confidence.md`가 소유한다.
- provider 응답/identity 계약은 `08-model-gateway.md`와 `16-model-response-reliability.md`가 소유한다.
- 경험 승격·workflow qualification은 `09-workflow-compiler.md`가 소유한다.
- 벤치마크 지표와 SLO는 `11-quality-benchmark.md`가 소유한다.
- `TargetingPack` 어휘 계약은 `14-goal-compiled-targeting.md`가 소유하되 최종 위치는 PlanIR node의 grounding hint다.
- 진행 체크는 `docs/plan/tasklist.md`의 **U1~U9**에서만 한다. 코드가 존재해도 통합 gate가 없으면 `[x]`가 아니다.

---

## 1. 2026-08-23 기준점

현재 기준은 `main` merge commit
`1a9c7616fff9eed56f3bf6a0d4404bfe49bbaa52`다.

이 기준점에는 다음 기반 코드가 이미 존재한다.

- `GoalInterpreter`와 `GoalEnvelope`: navigate/search/select/compare/edit/submit intent 분류, CPU-first entity/risk/constraint 추출
- `SemanticSlotFiller`: unresolved slot만 semantic provider에 요청하는 경계
- `StrategyPlanner`: capability-aware local strategy 선택
- `PlanIR`, `PlanNode`, `PlanCompiler.compile_search()`
- `TaskRuntime`: PlanIR node/edge 실행 상태기계
- `TaskBudgetLedger`, `BudgetedGateway`: task-global semantic budget 기반
- `ControlLoop`: observe → ground → policy → execute → fresh observe → verify
- `PreparedAction` scene/frame/fingerprint freshness binding
- unknown physical command fail-closed
- bounded `FrameStore`
- schema-directed JSON response framing, transport retry와 logical-call accounting 분리

그러나 **제품 경로 완성으로 간주하지 않는다.** 현재 구현은 핵심 부품과 unit regression을
만든 상태이고, 실제 case/product 실행 경로에는 아직 legacy path가 남아 있다.

### 1.1 현재 구현 상태

| 영역 | 현재 상태 | 제품 위험 |
|---|---|---|
| Goal 해석 | 기반 구현 있음 | canonical context/cache와 실제 product entry 연결 미완료 |
| Strategy 선택 | screen/tool 전략의 최소 구현 | 후보 전략 비교·workflow cache·surface 전환이 얕음 |
| PlanIR | search 4-node 예제 구현 | navigate/select/compare/edit/submit compiler 부재 |
| TaskRuntime | 고정 success/failure edge 실행 | 동적 local repair/replan 없음 |
| ControlLoop | fresh observe + stale binding 구현 | temporal wait와 실제 loop history 연결 미완료 |
| TaskBudget | 공통 ledger 구현 | `CaseRunner`의 attempt별 `_case_model_calls`가 별도로 존재 |
| Semantic response | 공통 JSON framing 구현 | `SemanticSlotFiller`와 `ModelRouter`가 별도 parser/budget을 가짐 |
| CaseRunner | 자체 gateway/input/retry/verification loop 유지 | 런타임이 사실상 두 벌 |
| Evidence | Verifier 기반 경로 존재 | legacy runner는 TargetingPack token에 과도하게 의존 |
| Recovery | LoopBreaker 구현 존재 | `ControlLoop`가 실제 history 대신 빈 history로 호출하는 경로 존재 |
| CPU delta | tile hash/ring buffer 부품 존재 | 실제 observer hot path 연결·stable OCR identity 미완료 |
| Workflow | compiler/replay 부품 존재 | verified transition 기반 qualification 미완료 |

### 1.2 한 줄 진단

> 좋은 신형 부품은 생겼지만, **제품 실행 경로가 아직 신형 아키텍처를 강제하지 않는다.**
> 다음 단계의 목표는 기능 추가가 아니라 모든 실행·semantic·budget·verification을
> 하나의 task lifecycle로 수렴시키는 것이다.

---

## 2. 절대 목표와 비목표

### 2.1 절대 목표

```text
UserCommand
  → GoalEnvelope
  → CapabilitySnapshot + CurrentContext + WorkflowIndex
  → StrategyPlan
  → PlanIR
  → TaskRuntime
      → ControlLoop.step()
          observe delta
          → local ground
          → policy + stale gate
          → execute
          → fresh observe
          → verify transition
          → local repair / failure edge
      → bounded replan only when unresolved
  → verified result
  → Trace
  → qualified Workflow
```

정상 경로의 의미는 명확하다.

- CPU/local path가 충분하면 runtime model call은 0회다.
- 모델은 **계획의 빈 slot, ambiguous grounding, local recovery 소진**에서만 interrupt된다.
- 모델은 좌표·현재 element ID·성공 여부를 임의로 확정하지 않는다.
- side effect는 항상 Action DSL → Policy → Executor를 통과한다.
- 성공은 fresh evidence로만 commit된다.

### 2.2 비목표

- 사이트/브랜드/CTA/광고/CAPTCHA 문자열 사전 추가
- 모델에게 매 action을 다시 계획시키기
- plan-time 모델이 좌표 또는 현재 scene element ID를 반환하게 하기
- 실패 시 임의 lexical click
- `CaseRunner`와 `TaskRuntime`을 장기간 dual-path로 유지
- unit test 개수를 제품 성공률로 간주
- 검증되지 않은 trajectory 자동 workflow 승격
- CAPTCHA·로그인·보안 확인 우회

---

## 3. 제품 불변식

다음은 구현 편의를 위해 완화하지 않는다.

1. **One task, one runtime.** 제품·case·replay는 동일 `TaskRuntime/ControlLoop`를 사용한다.
2. **One task, one semantic ledger.** intent/plan/ground/reanalysis/recovery는 한 `TaskBudgetLedger`를 공유한다.
3. **One semantic response boundary.** provider text는 공통 framing → schema parser를 통과한다.
4. **No action from stale state.** ground 이후 scene/frame/target fingerprint가 변하면 재탐색 또는 halt한다.
5. **Verification before commit.** low-level input 성공이나 모델의 success 문장만으로 완료하지 않는다.
6. **Local repair before semantic replan.** 재캡처·재ground·alternate local strategy를 먼저 사용한다.
7. **Irreversible progress is monotonic.** 이미 검증된 비가역 node를 replan이 되돌리거나 재실행하지 않는다.
8. **No hidden fallback.** capability/model/budget 부재를 click이나 임의 intent로 숨기지 않는다.

---

## 4. P0 — 제품 경로 단일화

최우선 작업이다. P0가 닫히기 전에는 새 사이트 케이스나 새 모델 기능을 늘리지 않는다.

### 4.1 `CaseRunner`를 test adapter로 축소

현재 `CaseRunner`가 직접 소유하는 다음 책임을 제거한다.

- gateway 호출
- `_case_model_calls`와 attempt별 budget reset
- target semantic decision
- raw physical navigation/input
- 별도 retry state machine
- 별도 success/failure state machine
- 별도 reanalysis verification

최종 `CaseRunner` 책임은 다음뿐이다.

```text
CaseSpec
→ UserCommand / entry context 변환
→ product runtime 호출
→ TaskRunResult/Trace 수집
→ CaseStats/manifest/artifact 직렬화
```

**금지 CI rule:** `hpcu/cases/runner.py`가 `Gateway`, provider parser,
`InputInjector` 또는 raw `inject_physical`을 import/호출하면 실패한다.

### 4.2 task 시작 시 ledger를 한 번 만든다

semantic gateway 조립 순서는 logical call accounting과 transport retry를 분리해야 한다.

```text
BudgetedGateway(
    CountingGateway(
        RetryableGateway(provider)
    ),
    task_ledger,
)
```

- `TaskBudgetSpec`은 intent fill 이전에 request/config에서 생성한다.
- 같은 ledger를 `SemanticSlotFiller`, plan compile, runtime interrupt, recovery가 공유한다.
- attempt/node/replan 전환으로 ledger를 초기화하지 않는다.
- transport retry는 새 semantic call로 계산하지 않는다.
- budget 소진은 `MODEL_BUDGET_EXHAUSTED`로 종료/edge 전환한다.

### 4.3 제품 composition root 하나

제품 경로에서 다음 객체가 한 곳에서 조립되어야 한다.

```text
Observer
Grounder
Verifier
RiskEngine / ApprovalGate
Executor
TraceRecorder
Budgeted semantic gateway
GoalInterpreter
StrategyPlanner
PlanCompiler
ControlLoop
TaskRuntime
```

플랫폼 adapter와 case adapter는 이 조립을 복제하지 않는다.

### P0 완료 gate

- browser fixture와 terminal fixture가 같은 `TaskRuntime`을 사용
- `CaseRunner` raw gateway/input 직접 호출 0
- task 전체 semantic call이 한 ledger에 기록
- attempt가 5회여도 task budget이 증가하지 않음
- HIGH/CRITICAL policy deny 시 모든 adapter에서 side effect 0
- unknown command → `ACTION_UNSUPPORTED`
- 5+ node fixture를 runtime model call 0으로 완료

---

## 5. P1 — semantic boundary 완전 단일화

### 5.1 모든 provider 응답은 공통 framing을 사용

`hpcu/gateway/json_response.py`를 공통 transport framing 계층으로 사용한다.

다음 직접 파싱을 제거한다.

- `SemanticSlotFiller`의 직접 `json.loads(response.content)`
- `ModelRouter`의 regex `_parse_target`
- runner 내부 provider-response parser
- 새 모듈에서 임의 JSON brace regex 추가

원칙:

```text
provider adapter normalization
→ JSON candidate extraction
→ schema-directed unique object selection
→ typed DTO validation
→ scene/budget/policy validation
```

transport presentation noise만 흡수하고 action-bearing field를 semantic repair하지 않는다.

### 5.2 `ModelRouter`의 독립 budget/parser 제거

`ModelRouter`가 유지된다면 역할을 **승격 정책 계산**으로 제한한다.

- private `_model_calls_remaining` 제거
- gateway 직접 호출 제거 또는 `SemanticInterrupt`로 위임
- target regex fallback 제거
- task-global ledger 공유
- tier 선택 결과는 `SemanticRequest`/`ReplanReason`만 반환

가능하면 최종적으로 `ModelRouter`를 다음 두 책임으로 분해한다.

```text
EscalationPolicy: failure + confidence + risk → semantic interrupt 필요 여부
SemanticInterrupt: typed request → configured provider → typed response
```

### P1 완료 gate

- semantic call site에서 raw `json.loads(response.content)` 0
- semantic call site에서 provider-specific regex 0
- 모든 model call에 purpose + triggering failure/unresolved slot 기록
- schema ambiguity는 fail closed
- model failure가 physical fallback click으로 바뀌는 경로 0

---

## 6. P2 — PlanIR을 SEARCH 예제에서 범용 계획기로 확장

`GoalInterpreter`가 여러 intent를 이해해도 `PlanCompiler`가 search만 만들면 상위 방향
설정은 실질적으로 완성되지 않는다.

### 6.1 compiler public API

최종 진입점은 intent별 private helper를 감싸는 하나의 compile 계약으로 수렴한다.

```python
PlanCompiler.compile(
    goal: GoalEnvelope,
    strategy: StrategyPlan,
    context: PlanningContext,
    task_budget: TaskBudgetSpec,
) -> PlanIR
```

내부 template:

- `_compile_navigate`
- `_compile_search`
- `_compile_select`
- `_compile_compare`
- `_compile_edit`
- `_compile_submit`

### 6.2 최소 node pattern

#### Navigate

```text
resolve entry
→ focus/open surface
→ navigate
→ verify surface identity
```

#### Search

```text
resolve search field
→ focus
→ replace text
→ submit
→ verify query echo + result candidate
```

#### Select

```text
resolve candidate set
→ deterministic filter/rank
→ select/click
→ verify selected state
```

#### Compare

```text
resolve candidate A/B
→ read structured attributes locally
→ normalize comparable facts
→ verify both evidence sets available
→ return comparison state
```

비교 자체가 GUI side effect를 필요로 하지 않으면 모델 호출 없이 local read로 끝낼 수 있어야 한다.

#### Edit

```text
resolve editable target
→ capture old value
→ replace/toggle/select
→ verify value_changed + requested value
```

#### Submit

```text
resolve form/submit control
→ precondition validation
→ risk/policy approval gate
→ submit
→ verify independent confirmation
```

### 6.3 plan-time 모델의 제한

모델이 필요한 경우에도 반환 가능한 것은 다음에 한정한다.

- unresolved goal slot
- abstract target query
- strategy preference 후보
- evidence requirement 보완

반환 금지:

- screen coordinate
- current `element_id`
- 임의 success claim
- policy/risk 완화
- raw OS command

### P2 완료 gate

- 6 intent 모두 deterministic fixture PlanIR 생성
- 동일 goal/context/capability → 동일 plan hash
- capability 없는 op가 plan에 등장하지 않음
- uncached semantic plan compile ≤1 logical call
- simple navigate/search는 plan compile 없이도 가능한 fixture 존재
- 3개 intent 이상에서 4+ node end-to-end integration 통과

---

## 7. P3 — bounded dynamic replan

현재 `TaskRuntime`은 미리 만들어진 edge만 따라간다. 범용 GUI에서는 화면 상태가
예상과 달라질 수 있으므로 **무제한 모델 재계획이 아니라 bounded plan repair**가 필요하다.

### 7.1 replan hierarchy

실패 시 항상 다음 순서를 지킨다.

```text
L0 current node retry 금지 조건 검사
↓
L1 fresh recapture + same query local re-ground
↓
L2 alternate local query / local interaction mode
↓
L3 alternate feasible StrategyPlan
↓
L4 unresolved facts만 semantic interrupt
↓
L5 human handoff / bounded halt
```

같은 입력을 그대로 반복하는 것은 repair가 아니다.

### 7.2 신규 경계

권장 DTO:

```python
@dataclass(frozen=True)
class ReplanRequest:
    reason: FailureCode
    failed_node_id: str
    scene_version: int
    unresolved_slots: tuple[str, ...]
    completed_nodes: tuple[str, ...]
    remaining_budget: TaskBudgetSnapshot

@dataclass(frozen=True)
class PlanPatch:
    parent_plan_hash: str
    replaced_node_ids: tuple[str, ...]
    nodes: Mapping[str, PlanNode]
    resume_node_id: str
    reason: FailureCode
```

규칙:

- patch는 node boundary에서만 적용한다.
- verified 완료 node를 삭제/재실행하지 않는다.
- irreversible node 이후에는 그 효과를 전제로만 재계획한다.
- `max_replans_per_task`는 config에서 제한한다.
- 모든 patch는 parent plan hash와 reason을 trace에 남긴다.

### 7.3 semantic replan의 범위

모델에게 전체 화면과 전체 계획을 다시 맡기지 않는다.

예:

```text
실패: search textbox 미발견
local facts: textbox candidate 0, button 3, page role=dialog 존재
질문: "현재 계획에서 해결되지 않은 target role / next abstract step만 반환"
```

모델 응답이 액션 좌표나 임의 element ID를 반환하면 거부한다.

### P3 완료 gate

- duplicate label, modal, slow loading, stale target fixture에서 bounded repair 성공/정지
- 같은 action+same scene 3회 반복 0
- replan 횟수 config 초과 0
- semantic replan 후에도 task budget이 유지
- irreversible node 중복 실행 0
- patch lineage가 trace artifact에 남음

---

## 8. P4 — temporal execution semantics와 실제 LoopBreaker 연결

### 8.1 `WAIT_UNTIL`은 단일 scene assert가 아니다

현재 local verify op로 즉시 평가하는 형태를 제거한다.

정상 계약:

```text
deadline 시작
→ observe
→ predicate/evidence 평가
→ stable poll count 충족? success
→ 아니면 bounded wait
→ fresh observe
→ timeout 시 TIMEOUT/POSTCONDITION_UNMET
```

- 고정 `time.sleep` 금지
- poll interval / stable polls / timeout은 config
- scene version만 증가하고 content가 동일한 경우 안정 상태로 취급 가능
- wait 중에도 access-control/policy relevant state가 나타나면 즉시 중단 가능

### 8.2 LoopBreaker에 실제 history 제공

`ControlLoop`/`TaskRuntime`은 bounded history를 유지한다.

```text
action fingerprint
pre/post scene hash
failure code
node id
replan id
```

`detect((), ())` 같은 빈 history 호출은 금지한다.

LoopBreaker 결과는 raw input을 직접 수행하지 않고 다음 중 하나로 변환한다.

- `REEXPLORE` → local re-ground
- `SWITCH_MODE` → alternate local StrategyPlan 후보
- `ESCALATE` → semantic interrupt request
- `HALT` → `LOOP_DETECTED` / `RECOVERY_EXHAUSTED`

### P4 완료 gate

- repeated action, A-B-A, popup reappear, scroll-no-change를 실제 TaskRuntime fixture에서 탐지
- wait-until slow-loading fixture 통과
- timeout은 bounded stop
- loop recovery가 raw injector를 호출하지 않음

---

## 9. P5 — 독립 Evidence와 단일 상태 commit

### 9.1 모델 편찬 token은 evidence oracle이 아니다

`TargetingPack.success_any`/`ready_any`는 grounding 및 관찰 힌트로 사용할 수 있지만,
그 token을 만든 모델이 같은 token 존재 여부로 성공을 단독 확정하게 하지 않는다.

intent별 최소 evidence:

| intent | 최소 evidence |
|---|---|
| navigate | surface identity 또는 URL/title/content의 강한 typed signal |
| search | query echo + result candidate |
| select | selected/toggled/active state 변화 |
| compare | 서로 다른 두 candidate의 독립 attribute evidence |
| edit | old value ≠ new value + requested value observable |
| submit | submission confirmation 또는 독립 외부 효과 |

### 9.2 task terminal state는 한 곳에서만 commit

`TaskRuntime` 또는 별도 순수 state transition 함수만 다음을 결정한다.

```text
RUNNING
VERIFIED_SUCCESS
HUMAN_HANDOFF
FAILED
```

runner/model/verifier가 각자 `success=True`를 직접 세팅하지 않는다.

성공 commit 시:

- failure/failure_code clear
- final scene/frame/evidence binding 저장
- terminal evidence ids 저장
- model claim과 independent evidence 구분 기록

### P5 완료 gate

- no-op injector false success 0
- previous-scene-only evidence false success 0
- 모델 `goal_state=success` 단독 성공 0
- empty scene/blob truthiness 성공 0
- success row에 failure code 잔류 0

---

## 10. P6 — CPU-first hot path를 실제 observer에 연결

Correctness(P0~P5)가 닫힌 뒤 최적화한다.

### 10.1 target hot path

```text
capture
→ backend dirty rect 또는 tile hash
→ unchanged: previous Scene reuse
→ changed ROI만 perception
→ structure delta fusion
→ stable identity remap
→ SceneDelta
→ grounding
```

### 10.2 구현 항목

- ring buffer/tile hash를 `CompositeObserver` 실제 path에 연결
- unchanged frame full OCR 0
- decoded frame / crop cache
- ROI dense OCR → 필요 ROI sparse OCR
- full-screen OCR는 budgeted fallback
- equality diff에서 transient scene_version/first_seen/last_seen 제외
- OCR stable ID: fingerprint → IoU + text + role + source ensemble
- `ocr_line_N` 순번을 persistent identity로 사용하지 않음
- source-aware confidence calibration
- generic numeric/text/geometry feature만 perception에서 생성

### P6 완료 gate

- unchanged 1080p frame p95 ≤ 10ms (reference hardware 기록)
- delta scene update p95 ≤ 150ms
- unchanged frame OCR pass 0
- 100-frame stable ID 유지율 artifact
- processed pixel / OCR pass / CPU time / peak RSS 기록

---

## 11. P7 — verified Workflow와 0-call replay

workflow는 action log 복사가 아니라 **검증된 state transition의 재사용 프로그램**이다.

승격 입력:

- GoalEnvelope
- StrategyPlan
- PlanIR + plan patch lineage
- node별 pre/post scene fingerprint
- selector ensemble
- independent EvidenceState
- recovery edge
- capability/execution mode/environment fingerprint

qualification:

```text
verified trace candidate
→ repeated success
→ experimental workflow
→ multi-session offline shadow replay
→ qualified
→ online fresh verification replay
→ active
```

같은 `TaskRuntime`, `Grounder`, `Verifier`를 offline scene stream에서도 사용한다.

### P7 완료 gate

- 첫 verified run → 동일 fixture replay model 0
- viewport/locale/text drift에서 re-ground 또는 halt
- fixed coordinate replay 0
- failed trajectory 승격 0
- drift/false completion 발생 시 즉시 downgrade

---

## 12. P8 — held-out 제품 자격 게이트

unit count가 아니라 일반화·안전·성능을 릴리스 기준으로 사용한다.

초기 dataset:

- 30개 이상 held-out task
- intent family 6개
- browser/terminal/desktop 최소 3 surface
- 절반 이상 3 node 이상
- duplicate label, modal, slow loading, stale frame 포함
- locale/viewport/theme 변화 포함
- case spec에 최종 URL, 정답 element ID, 성공 문자열 직접 제공 금지

합격 기준:

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

## 13. 권장 PR 순서

각 PR은 이전 PR의 자동 gate가 통과하기 전 merge하지 않는다.

### PR-A — semantic/budget 단일 경계

- `SemanticSlotFiller` 공통 JSON parser 사용
- `ModelRouter` private parser/budget 제거
- one task / one `TaskBudgetLedger`
- logical vs transport retry regression

### PR-B — CaseRunner → TaskRuntime 수렴

- CaseRunner를 test adapter로 축소
- navigation 포함 모든 input을 Action DSL로 이동
- 제품 composition root 하나
- dual success/retry state machine 제거

**이 PR은 장기간 분할하지 않는다. dual-path 기간을 최소화한다.**

### PR-C — 범용 PlanCompiler

- navigate/select/compare/edit/submit compiler
- PlanningContext/CapabilitySnapshot 연결
- deterministic plan hash

### PR-D — bounded local repair + replan

- ReplanRequest/PlanPatch
- local repair hierarchy
- irreversible progress 보존
- replan lineage trace

### PR-E — temporal wait + LoopBreaker

- real history
- wait_until/settle temporal semantics
- recovery → replan/edge 연결

### PR-F — typed evidence + terminal commit

- intent별 EvidenceContract
- success state 단일화
- legacy token-only success 제거

### PR-G — dirty ROI hot path

- tile hash/ring buffer/ROI OCR/stable IDs
- CPU performance metrics

### PR-H — verified workflow + held-out qualification

- offline same-runtime replay
- multi-session qualification
- 30+ held-out gate

---

## 14. 테스트 전략

### Unit

- GoalEnvelope invariant / CPU parser
- semantic schema-directed parser
- budget monotonicity
- strategy feasibility/score
- PlanIR validation/hash
- PlanPatch invariant
- irreversible node protection
- temporal wait predicate
- state transition normalization

### Integration

- `UserCommand → GoalEnvelope → StrategyPlan → PlanIR → TaskRuntime → ControlLoop → Verifier`
- browser search 4+ node
- terminal multi-step
- select/edit/submit
- policy deny
- stale after ground
- semantic timeout/schema failure
- dynamic modal + replan

### Replay

- stored scene delta stream에 같은 runtime 실행
- selector drift
- viewport/locale 변화
- loop/replan lineage
- model call 0 workflow replay

### Failure injection

- no-op input
- stale target
- unchanged frame
- malformed command
- false success token
- budget exhaustion
- partial action failure
- popup loop
- scroll no change
- access-control screen
- conflicting semantic JSON objects

### E2E

- real provider 결과는 unit으로 대체하지 않는다.
- provider/network failure와 code failure를 분리한다.
- commit/config/provider/model/case hash를 manifest에 기록한다.

---

## 15. 관측 지표

`action_count` 하나로 여러 현상을 합치지 않는다.

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
local_repair_count
replan_count
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

모든 benchmark artifact fingerprint:

- commit SHA
- runtime config hash
- Goal/Plan schema version
- compiler version
- provider/model identity
- OS/CPU/RAM/resolution/scaling
- case dataset version
- execution mode

---

## 16. 마이그레이션 규칙

1. 새 runner/control loop를 만들지 않는다.
2. legacy `CaseRunner` 기능은 새 runtime에 복사하지 않고 제거하면서 옮긴다.
3. P0~P5 correctness 전에는 CPU 성능 최적화를 성공으로 홍보하지 않는다.
4. semantic retry와 task replan을 같은 것으로 취급하지 않는다.
5. transport retry는 같은 logical call이다.
6. replan은 verified node를 되돌리지 않는다.
7. 모델이 만든 token은 grounding hint이지 독립 success oracle이 아니다.
8. 모든 threshold/timeout/replan limit는 config에서 읽는다.
9. compatibility alias는 허용하되 새 제품 코드가 legacy path를 import하면 CI를 실패시킨다.
10. tasklist `[x]`는 unit 존재가 아니라 integration/replay/held-out artifact가 증명할 때만 사용한다.

---

## 17. 즉시 실행 우선순위

현재 `main`에서 바로 시작할 순서는 다음과 같다.

### Priority 0

1. `CaseRunner`의 `_case_model_calls` attempt reset 제거가 아니라 **CaseRunner semantic loop 자체 제거**
2. 모든 semantic call을 하나의 `BudgetedGateway`/schema parsing boundary로 통합
3. CaseRunner → TaskRuntime 단일 제품 경로 전환

### Priority 1

4. `PlanCompiler`의 6 intent 지원
5. bounded dynamic replan
6. `WAIT_UNTIL` temporal semantics
7. LoopBreaker real history 연결
8. typed terminal evidence + single state commit

### Priority 2

9. dirty ROI / stable OCR identity hot path
10. verified workflow compile / offline same-runtime replay
11. held-out 30+ qualification gate

우선순위의 핵심은 **새 기능보다 경로 단일화**다.
현재 가장 큰 리스크는 기능 부족 자체가 아니라 동일 프로젝트 안에서 서로 다른
budget·verification·recovery 규칙을 가진 두 실행 경로가 공존하는 것이다.

---

## 18. 완료 정의

본 계획은 아래가 모두 자동 검증될 때만 완료다.

1. 모든 product/case/replay 실행이 동일 `TaskRuntime/ControlLoop`를 사용한다.
2. 사용자 명령이 `GoalEnvelope → StrategyPlan → PlanIR`로 구조화된다.
3. 6 core intent가 동일 runtime에서 다단계로 실행된다.
4. 모든 side effect가 policy + stale gate를 통과한다.
5. 모든 side effect 후 fresh scene 또는 독립 외부 효과로 검증된다.
6. 모든 semantic call이 하나의 task-global ledger와 schema parser를 사용한다.
7. local repair 후에만 bounded semantic replan이 발생한다.
8. same action/same scene 무한 반복이 자동 차단된다.
9. unchanged frame은 full OCR 없이 처리된다.
10. verified workflow는 다음 실행에서 모델 0회로 replay된다.
11. held-out benchmark에서 false completion·stale execution·policy bypass가 0이다.
12. 각 실패는 bounded stop과 재현 가능한 trace/manifest를 남긴다.
