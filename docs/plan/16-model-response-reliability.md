---
title: "HPCU Runtime 개발 계획 16 — 모델 응답 신뢰성 개선"
version: "1.0"
date: "2026-08-22"
parent: "docs/plan/14-goal-compiled-targeting.md, docs/plan/13-common-pipeline-remediation.md, docs/plan/15-command-to-plan-runtime-remediation.md"
language: "ko-KR"
---

# 16. 모델 응답 신뢰성 개선

현재 `main`의 `CaseRunner` + `TargetingPack` 경로에서 2026-08-22 10개 케이스
실행 결과 7/10 성공, 3/10 실패가 발생했다. 실패 3건의 근본 원인은 모두
**configured semantic provider(MiniMax-M3)의 응답이 strict schema를 통과하지
못한 것**이다. 이 문서는 그 원인을 분석하고, U1~U9 마이그레이션과 충돌하지
않는 선에서 **단기 신뢰성 개선**을 설계한다.

진행 체크는 `docs/plan/tasklist.md`의 **S-*** 항목에서만 한다.

---

## 1. 한 줄 진단

> `CaseRunner`의 schema 검증은 `_load_exact_object`의 `set(payload) == set(keys)`
> 하나로 모든 응답 변형을 거부한다. MiniMax는 때때로 extra key를 추가하거나
> optional field를 다른 타입으로 반환한다. 이 차이를 파싱 계층이 흡수하지
> 못하고 `MODEL_SCHEMA_INVALID`로 즉시 실패 처리한다. 또한 transient timeout에
> 대한 재시도가 compile 경로에만 있고 action/reanalysis 경로에는 없다.

**이 계획이 하는 것:**

- `_load_exact_object`의 exact key match를 완화하고 JSON 추출을 강화한다.
- action decision과 reanalysis에 transient error 재시도를 추가한다.
- reanalysis schema 실패 시 evidence-based verification으로 fallback한다.
- 실패 원인 진단을 위한 raw response 로깅을 추가한다.

**이 계획이 하지 않는 것 (U1~U9가 담당):**

- `CaseRunner`를 `TaskRuntime` + `ControlLoop`로 교체하지 않는다.
- `TargetingPack`을 `PlanIR` node grounding hint로 축소하지 않는다.
- model이 evidence token과 성공 판정을 동시에 하는 구조(D5)를 해소하지 않는다.
- task-global model budget(D6)을 구현하지 않는다.
- multi-step action(D7)을 지원하지 않는다.

---

## 2. 실패 스냅샷 (2026-08-22 10-case run)

| ID | 케이스 | 실패 코드 | 직접 원인 | 발생 위치 |
|---|---|---|---|---|
| S1 | `coupang-fresh-popular` | `model_schema_invalid` | action decision: `response must be one JSON object` / reanalysis: `response must be one JSON object` | `decision.py:_load_exact_object` |
| S2 | `google-search-notebook` | `model_failed` | plan compile: 1차 응답 → dict 아님, 2차 `ReadTimeout` | `targeting_compiler.py:compile` |
| S3 | `example-org` | `model_schema_invalid` | action decision: decision 없음 / reanalysis: `reason_code must be a string` | `decision.py:ReanalysisDecision.__post_init__` |

### S1 상세 (`coupang-fresh-popular`)

```
attempt 1: action decision → halt → "no_model_decision"
attempt 2: action decision → strict_action_rejected (JSON object 아님)
attempt 3: click 성공 → post_click settle → reanalysis → strict_reanalysis_rejected (JSON object 아님)
```

진단: `action scene=7: ValueError: response must be one JSON object`
진단: `reanalysis scene=9: ValueError: response must be one JSON object`

원인 가능성: MiniMax가 응답 JSON에 schema에 없는 필드를 포함했거나,
` thinking` block이 완전히 strip되지 않은 잔여 텍스트가 JSON 앞에 붙음.

### S2 상세 (`google-search-notebook`)

```
compile attempt 1: 응답 수신, _load_json_object → payload가 dict가 아님 → continue
compile attempt 2: httpx.ReadTimeout → continue
→ retry_attempts 소진 → GoalFallbackTokenizer → pack.source="goal_tokens"
→ runner: pack.source != "model" → plan_compile_failed → 중단 (action 0)
```

원인: compile retry는 있지만 backoff 없음. timeout이 연속 발생할 때
간격 없이 재시도하여 두 번째도 timeout.

### S3 상세 (`example-org`)

```
attempt 1: action decision → none → "no_model_decision"
attempt 2: "Learn more" 클릭 성공 → post_click settle → reanalysis
→ ValueError: reason_code must be a string
```

원인: `ReanalysisDecision.__post_init__`에서 `reason_code` 필드가
문자열이 아닌 타입(예: `null` 또는 숫자)으로 반환됨.

---

## 3. 현재 코드 경로 분석

### 3.1 JSON 파싱의 두 단계

**step 1 — adapter strip (`minimax_adapter.py:strip_thinking`)**

```
raw content → regex로 <think>...</think> 제거 → ```json``` fence 제거
→ 첫 `{` 이전 텍스트 제거 → ` thinking... response` state machine strip
```

이 단계는 MiniMax 전용. 다른 provider는 자체 adapter가 담당.

**step 2 — common parser (`decision.py:_load_exact_object`)**

```python
def _load_exact_object(content: str, keys: frozenset[str]) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as error:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match is None:
            raise ValueError("response must be one JSON object")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as nested_error:
            raise ValueError("response must be one JSON object")
    if not isinstance(payload, dict) or set(payload) != set(keys):
        raise ValueError("response does not match the strict decision schema")
    return payload
```

**문제점:**

1. `set(payload) != set(keys)` — exact equality. MiniMax가 `_comment` 같은
   필드를 추가하면 거부된다. `schema_version` 같은 필드가 누락돼도 거부된다.
   JSON Schema의 `additionalProperties: false`와 동일한 동작이지만, 실제
   provider 응답은 사양을 벗어나는 일이 빈번하다.

2. `re.search(r"\{.*\}", content, re.DOTALL)` — greedy match. 응답에
   JSON 객체가 두 개 이상 있으면 마지막 `}`까지 하나로 묶어 파싱 실패.

3. `strip_thinking` 이후에도 prose prefix가 남을 수 있다. `content.find("{")`
   로 첫 `{`를 찾지만, `find`는 `{`를 포함한 모든 위치를 반환하므로
   JSON 내부의 문자열 `{`가 아니라 실제 객체 시작을 찾는 보장이 없다.

### 3.2 재시도 경로의 비대칭

| 경로 | retry 있음 | backoff | 최대 횟수 |
|---|---|---|---|
| `plan_compile` | 있음 | 없음 | `retry_attempts` (2) |
| `action_decision` | 없음 | 해당 없음 | 1 |
| `post_action_reanalysis` | 없음 | 해당 없음 | 1 |

`targeting_compiler.py:98-115`의 retry loop는 `except Exception: continue`로
모든 예외를 잡지만, `continue` 직후에 지연 없이 즉시 재시도한다.

### 3.3 reanalysis 실패와 evidence fallback의 부재

`runner.py:596-603`는 reanalysis `ValueError`를 잡아서 즉시 실패 처리한다.
하지만 action은 이미 실행됐고, `runner.py:260-289`의 `_pack_evidence_holds`
로직은 reanalysis 실패 시 호출되지 않는다. 즉, **클릭이 성공했고 증거도
화면에 존재하는데** reanalysis 응답 형식 문제만으로 실패하는 상황이 발생한다.

---

## 4. 개선 설계

모든 개선은 불변식 #9(의미 결정은 configured provider만), #12(모델 장애 시
꼼수 금지), #8(사이트 사전 금지)을 준수한다.

### 4.1 S-A: JSON 파싱 강건화

**대상:** S1, S3
**파일:** `hpcu/schemas/decision.py`

**변경:**

`_load_exact_object`의 exact key match를 required-key check로 완화:

```python
def _load_exact_object(content: str, keys: frozenset[str]) -> dict[str, Any]:
    payload = _extract_json_object(content)  # 새 헬퍼
    if not isinstance(payload, dict):
        raise ValueError("response must be one JSON object")
    missing = keys - set(payload)
    if missing:
        raise ValueError(
            f"response missing required keys: {sorted(missing)}"
        )
    return payload
```

**`_extract_json_object` 신규 헬퍼:**

```python
def _extract_json_object(content: str) -> Any:
    """Extract the first JSON object from a provider response.

    Tries in order:
    1. Whole content as JSON.
    2. First `` ```json ``` `` fenced block.
    3. First `` ``` `` fenced block.
    4. Bracket-counted extraction from first ``{`` to matching ``}``.
    """
    # 1. direct parse
    try:
        return json.loads(content)
    except (TypeError, json.JSONDecodeError):
        pass

    # 2. markdown json fence
    match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 3. any markdown fence
    match = re.search(r"```\s*(\{.*?\})\s*```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 4. bracket-counted extraction
    start = content.find("{")
    if start == -1:
        raise ValueError("no JSON object found in response")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(content)):
        ch = content[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(content[start:i + 1])

    raise ValueError("unbalanced braces in response")
```

**설계 결정:**
- `_extract_json_object`는 `decision.py`에 두고, `targeting_compiler.py`의
  `_load_json_object`도 동일 헬퍼를 import하여 사용한다.
- type coercion은 하지 않는다. `reason_code`가 문자열이어야 하는데 숫자면
  여전히 실패한다. 이는 `ReanalysisDecision.__post_init__`의 책임이다.
- extra key는 허용하지만 missing key는 거부한다. missing key 허용은
  downstream `__post_init__`에서 `AttributeError`를 유발하므로 더 위험하다.

### 4.2 S-B: transient error 재시도 + backoff

**대상:** S2, S1(일부)
**파일:** `hpcu/gateway/gateway.py`, `hpcu/gateway/minimax_adapter.py`, `config/runtime-config.yaml`

**변경:**

`config/runtime-config.yaml`에 retry 설정 추가:

```yaml
semantic:
  request_limits:
    plan_compile_max_tokens: 1024
    action_decision_max_tokens: 512
    reanalysis_max_tokens: 512
    plan_compile_retry_attempts: 2
    action_decision_retry_attempts: 2      # 신규
    reanalysis_retry_attempts: 2           # 신규
    retry_base_delay_ms: 500               # 신규
    retry_max_delay_ms: 8000               # 신규
```

`hpcu/gateway/gateway.py`에 `RetryableGateway` wrapper 추가:

```python
class RetryableGateway(Gateway):
    """Transient-error retry wrapper. Retries only on network/timeout/5xx.

    Does NOT retry on 4xx (bad request) — schema errors are permanent.
    """

    def __init__(
        self,
        inner: Gateway,
        *,
        max_retries: int = 2,
        base_delay_ms: int = 500,
        max_delay_ms: int = 8000,
    ):
        ...
```

`hpcu/cases/__main__.py`에서 gateway 생성 시 wrapping:

```python
gateway = RetryableGateway(
    CountingGateway(create_gateway(runtime_config)),
    max_retries=action_decision_retry_attempts,
    ...
)
```

`runner.py`의 `_pick_semantic`과 `_reanalyse`에서 `try/except Exception`
블록이 이미 존재하므로, retry는 gateway 계층에서 투명하게 처리된다.
runner 코드 변경은 불필요.

**재시도 대상 오류:**
- `httpx.ReadTimeout`
- `httpx.ConnectError`
- `httpx.RemoteProtocolError`
- HTTP 429, 502, 503, 504

**재시도하지 않는 오류:**
- HTTP 400, 401, 403 — 요청 자체 오류
- `json.JSONDecodeError` — adapter가 이미 response를 parse했으므로 발생 불가
- `ValueError` from model identity check — 구성 오류

**backoff 공식:**
```
delay_ms = min(base_delay_ms * (2 ** attempt) + random(0, base_delay_ms), max_delay_ms)
```

### 4.3 S-C: reanalysis schema 실패 시 evidence fallback

**대상:** S1, S3
**파일:** `hpcu/cases/runner.py`

**변경:**

`_reanalyse()` 메서드에서 `ValueError` catch 블록 수정:

```python
except ValueError as error:
    stats.decision_diagnostics.append(
        f"reanalysis scene={scene.version}: {type(error).__name__}: {error}"
    )
    # reanalysis schema failed, but the action was already executed.
    # Fall back to evidence-based verification using the pack's tokens.
    # The evidence tokens were compiled by the model at plan-time, so
    # the semantic authority is preserved (invariant #9).
    if pack is not None and _pack_evidence_holds(
        self._verifier, scene, pack, selected, spec.evidence
    ):
        self._record_evidence(stats, scene, pack.success_any or pack.ready_any,
                             selected=selected, reason="reanalysis_fallback")
        stats.verified_success = True
        stats.runner_success = True
        stats.success = True
        stats.outcome = "verified_success"
        stats.reanalysis_count += 1
        self._log(stats, "reanalysis", "evidence_fallback")
        return True
    # evidence fallback also failed — genuine failure
    stats.stale_rejection_count += 1
    stats.failure = FailureCode.MODEL_SCHEMA_INVALID.value
    ...
    return False
```

**설계 결정:**
- evidence token은 plan compile 시점에 모델이 편찬한 것이므로,
  의미 결정권자가 바뀌지 않는다 (불변식 #9 준수).
- `_pack_evidence_holds`는 이미 `runner.py`에 구현되어 있고
  `post_action` 경로에서 사용 중이다. 동일 함수를 재사용한다.
- evidence fallback이 실패하면 기존처럼 `MODEL_SCHEMA_INVALID`로 실패한다.
  evidence가 불충분한데 schema만 통과했다고 성공 처리하지 않는다 (불변식 #13).

### 4.4 S-D: 실패 진단 로깅

**대상:** 전체
**파일:** `hpcu/schemas/decision.py`, `hpcu/cases/runner.py`

**변경:**

`_load_exact_object` 실패 시 raw 응답 앞부분을 예외 메시지에 포함:

```python
except ValueError:
    preview = content[:200].replace("\n", " ").replace("\r", "")
    raise ValueError(f"{error} [raw_preview: {preview}]") from error
```

이렇게 하면 `decision_diagnostics`에 MiniMax가 실제로 반환한 텍스트의
앞부분이 기록되어, 사후 분석이 가능해진다. 현재는 `"response must be one
JSON object"`만 남아서 원인 추적이 불가능하다.

---

## 5. 예상 효과

| 케이스 | 현행 | S-A | S-A+S-B | S-A+S-B+S-C |
|---|---|---|---|---|
| coupang-fresh-popular | 실패 | 실패 가능성↓ | 실패 가능성↓ | **실패 가능성↓↓** |
| google-search-notebook | 실패 | 실패 | 실패 가능성↓ | 실패 가능성↓ |
| example-org | 실패 | 실패 | 실패 가능성↓ | **실패 가능성↓↓** |
| youtube-search-lofi | 성공 | 성공 | 성공 | 성공 |
| 나머지 6개 | 성공 | 성공 | 성공 | 성공 |

**S-C가 가장 큰 효과를 낸다.** S1과 S3 모두 클릭은 성공했고 reanalysis
schema만 실패했으므로, evidence fallback이 작동하면 성공으로 전환될
가능성이 높다.

**S-A의 효과는 제한적이다.** extra key 허용으로 일부 문제는 해결되지만,
`reason_code` 타입 오류(S3)는 `ReanalysisDecision.__post_init__`의
type check에서 여전히 실패한다. type coercion은 하지 않는다.

**S-B는 compile timeout(S2)에 직접 대응한다.** backoff를 적용하면
두 번째 시도가 timeout을 피할 확률이 높아진다.

---

## 6. 구현 순서와 의존성

| 순서 | ID | 작업 | 의존성 | 파일 |
|---|---|---|---|---|
| 1 | S-D | 실패 진단 로깅 | 없음 | `decision.py` |
| 2 | S-A | JSON 파싱 강건화 | 없음 | `decision.py`, `targeting_compiler.py` |
| 3 | S-B | transient error 재시도 | 없음 | `gateway.py`, `minimax_adapter.py`, `runtime-config.yaml` |
| 4 | S-C | reanalysis evidence fallback | S-A (같은 파일, 충돌 방지) | `runner.py` |
| 5 | — | 10개 케이스 재실행 검증 | S-A, S-B, S-C | `python -m hpcu.cases` |

각 단계는 독립적이며, 단위 테스트로 자체 검증 후 다음 단계로 진행한다.
S-D를 먼저 적용하면 S-A~S-C 구현 전에 현재 실패의 raw response를 수집할
수 있어, S-A의 `_extract_json_object` 설계를 데이터 기반으로 보정할 수 있다.

---

## 7. 테스트 계획

### S-A 단위 테스트

- `_extract_json_object`가 markdown fenced JSON, bare JSON, prose prefix
  JSON, nested JSON, 이스케이프된 문자열 내 `{` 를 올바르게 처리하는지 검증
- `_load_exact_object`가 extra key를 허용하고 missing key를 거부하는지 검증
- 기존 `parse_action_decision` / `parse_reanalysis` 테스트가 계속 통과하는지 확인

### S-B 단위 테스트

- `RetryableGateway`가 `ReadTimeout`에 retry하고 `ValueError`에 retry하지 않는지 검증
- backoff delay가 `base_delay_ms * 2^attempt` 범위 내인지 검증 (fake clock)
- `max_retries` 소진 후 원래 예외가 propagation되는지 검증

### S-C 단위 테스트

- `_reanalyse`가 schema 실패 시 `_pack_evidence_holds`를 호출하는지 검증
- evidence가 충분하면 `verified_success`로 전환되는지 검증
- evidence가 불충분하면 기존처럼 `MODEL_SCHEMA_INVALID`로 실패하는지 검증

### 회귀 검증

- `tests/unit/` 전체 통과 (`python -m pytest tests/unit/ -v --strict-markers`)
- `tests/failure_injection/` 통과
- 기존 10-case 재실행으로 성공률 변화 측정

---

## 8. U1~U9 마이그레이션과의 관계

이 계획의 모든 변경은 U1~U9 마이그레이션과 **충돌하지 않도록** 설계한다.

| 이 계획의 변경 | U1~U9에서의 운명 |
|---|---|
| `_extract_json_object` | `PlanIR` node의 model response parsing에도 재사용 가능. 공통 유틸리티로 승격 |
| `RetryableGateway` | `TaskRuntime`에서도 동일 wrapper 사용. Gateway ABC의 데코레이터이므로 교체 불필요 |
| `_reanalyse` evidence fallback | U5에서 `EvidenceContract`가 PlanIR node로 이동하면, fallback은 node의 evidence contract를 참조하도록 변경 |
| `decision_diagnostics` raw preview | U9 held-out 게이트에서 실패 분석에 직접 활용 |

**이 계획이 U1~U9를 지연시키지 않는다.** S-A~S-D는 `CaseRunner` 내부 동작을
개선할 뿐, 새 경계(GoalEnvelope, StrategyPlan, PlanIR, TaskRuntime)를
추가하지 않는다. `CaseRunner`가 U4에서 test adapter로 축소될 때 이 코드는
자연스럽게 adapter 내부로 격리된다.

---

## 9. 리스크

- **S-A: `_extract_json_object`가 MiniMax 특이 응답을 모두 커버하지 못할 수 있다.**
  → S-D로 raw response를 수집한 후 S-A를 보정한다. S-A 단독으로 100%를
  목표하지 않는다.

- **S-C: evidence fallback이 false-success를 유발할 수 있다.**
  → evidence token은 모델이 plan compile 시점에 편찬한 것이므로, 모델이
  "이 토큰이 보이면 성공"이라고 정의한 조건을 그대로 사용한다. 모델과
  다른 oracle이 성공을 판정하는 구조(D5)는 U5에서 해결한다.

- **S-B: retry가 model call budget을 초과할 수 있다.**
  → retry는 `spec.max_model_calls` 내에서만 동작하며, `CountingGateway`가
  모든 call을 집계한다. U6에서 task-global budget이 구현되면 retry도
  그 범위 내로 제한된다.

---

## 10. 비목표 재확인

- 이 계획으로 10/10 성공을 약속하지 않는다. MiniMax 응답 품질 자체를
  개선하는 것이 아니다.
- type coercion(문자열이어야 할 필드에 숫자가 오면 문자열로 변환)은
  구현하지 않는다. provider 응답을 수정하는 것은 불변식 #12 위반이다.
- `plan_compile_failed` 시 lexical fallback token으로 실행을 강행하지
  않는다. 현행 fail-closed 동작이 정확하다.