---
title: "HPCU Runtime — 코드 리뷰 개선 플랜 (Phase A Post-Review)"
version: "1.0"
date: "2026-08-23"
parent: "docs/plan/17-remaining-work-plan.md, commit a9b70f3"
language: "ko-KR"
---

# 코드 리뷰 개선 플랜

커밋 `a9b70f3` (`fix: harden unified runtime phase A`) 의 17개 변경 파일에 대한
5개 리뷰 서브에이전트의 병렬 분석 결과를 종합한 개선 플랜이다.

---

## 1. 리뷰 요약

| 지표 | 값 |
|---|---|
| 총 발견 이슈 | 42건 |
| 🔴 HIGH | 7건 |
| 🟡 MEDIUM | 18건 |
| 🟢 LOW | 17건 |
| 현재 테스트 통과 | 554/554 ✅ |

---

## 2. 🔴 HIGH 우선순위 (즉시 수정)

### H1. `blocked_tokens`가 `CasePlanCompiler`에서 누락됨

**파일**: `hpcu/cases/planning.py:108-112`  
**심각도**: 🔴 HIGH  
**영향**: case 기반 태스크(주 실행 경로)에서 `blocked_tokens`가 `()`로 초기화되어
차단 토큰 검사가 완전히 무력화됨. 접근 제어 화면 감지 기능이 dead code.

**수정**:
```python
return PlanIR(
    ...
    blocked_tokens=plan.blocked_tokens,  # 추가
)
```

---

### H2. 매 노드마다 추가 `_observe_scene()` 호출

**파일**: `hpcu/runtime_core/task_runtime.py:177`  
**심각도**: 🔴 HIGH  
**영향**: blocked_tokens가 있는 모든 태스크에서 노드당 캡처가 3회로 증가 (50% 성능 저하).
각 observe는 50~500ms 소요.

**수정**: 이전 step의 scene을 재사용. 첫 iteration에서만 fresh capture:
```python
if current_plan.blocked_tokens:
    if last is not None and last.scene.version > 0:
        scene = last.scene
    else:
        scene = await self.control_loop._observe_scene()
```

---

### H3. `_observe_scene()` 예외 처리 누락

**파일**: `hpcu/runtime_core/task_runtime.py:177`  
**심각도**: 🔴 HIGH  
**영향**: observer 실패 시 예외가 TaskRuntime 전체를 crash. clean terminal commit 없음.

**수정**: try/except로 감싸고 실패 시 HUMAN_HANDOFF 또는 continue:
```python
try:
    scene = await self.control_loop._observe_scene()
except Exception:
    return self._commit_terminal(
        status=TaskStatus.FAILED,
        failure_code=FailureCode.CAPTURE_BACKEND_UNAVAILABLE.value,
        ...
    )
```

---

### H4. `allowed_keys` 제거로 nested-object ambiguity regression

**파일**: `hpcu/compiler/targeting_compiler.py:150`  
**심각도**: 🔴 HIGH  
**영향**: MiniMax가 wrapper 객체 안에 targeting payload를 nesting하면, 두 객체 모두
required keys를 만족하여 ambiguous error 발생. `select_json_object`가 `ValueError`를
raise.

**수정**: `allowed_keys`를 preference hint로 복원하거나, `prefer_outermost` 옵션 추가:
```python
payload = select_json_object(
    response.content,
    required_keys=_TARGETING_KEYS,
    allowed_keys=_TARGETING_KEYS,  # 복원
    schema_name="targeting plan",
)
```

---

### H5. `asyncio.gather()` 예외 시 dangling task

**파일**: `hpcu/observation/facade.py:98-100`  
**심각도**: 🔴 HIGH  
**영향**: `_grab_frame()` 또는 `_read_structure()` 중 하나가 예외를 던지면
다른 task가 취소되지 않고 background에서 계속 실행. 공유 상태 오염 가능.

**수정**:
```python
frame_task = asyncio.create_task(self._grab_frame())
structure_task = asyncio.create_task(self._read_structure())
try:
    frame, current = await asyncio.gather(frame_task, structure_task)
except BaseException:
    frame_task.cancel()
    structure_task.cancel()
    raise
```

---

### H6. `call_gateway_async`가 과도하게 넓은 `TimeoutError`를 catch

**파일**: `hpcu/gateway/async_gateway.py:58`  
**심각도**: 🔴 HIGH  
**영향**: `builtins.TimeoutError`(OSError 하위)와 `asyncio.TimeoutError`를 구분하지
않고 동일하게 처리. provider의 임의 `TimeoutError`가 deadline timeout으로 오분류.

**수정**: `except asyncio.TimeoutError`로 좁히고, 다른 exception은 RetryableGateway로 전파.

---

### H7. nested-object ambiguity 테스트 커버리지 없음

**파일**: `hpcu/compiler/targeting_compiler.py` (테스트 부재)  
**심각도**: 🔴 HIGH  
**영향**: H4의 regression을 감지할 수 있는 테스트가 없음.

**수정**: wrapper 객체 내부의 targeting payload를 시뮬레이션하는 테스트 추가.

---

## 3. 🟡 MEDIUM 우선순위 (다음 이터레이션)

### M1. blocked_tokens substring matching false positive

**파일**: `hpcu/runtime_core/task_runtime.py:179-186`  
**영향**: `"bot"`이 `"about"`에 매칭되는 등 의도치 않은 HUMAN_HANDOFF 발생.

**수정**: `.replace(" ", "")` 후 substring 검사 대신 word-boundary 기반 매칭 사용.

---

### M2. blocked_tokens empty string 검증 누락

**파일**: `hpcu/schemas/plan.py:59,149`  
**영향**: `("",)` 토큰이 모든 scene에 매칭되어 전체 태스크 중단.

**수정**: `__post_init__`에서 empty string 검증 추가.

---

### M3. `blocked_tokens`가 `plan_hash`에 미포함

**파일**: `hpcu/schemas/plan.py:187-201`  
**영향**: blocked_tokens만 다른 두 PlanIR이 동일 hash → 잘못된 patch 적용 가능.

**수정**: hash payload에 `"blocked": list(self.blocked_tokens)` 추가.

---

### M4. `compile_search` public API가 blocked_tokens 누락

**파일**: `hpcu/planning/plan_compiler.py:81-85`  
**영향**: 이 API 경로로 생성된 모든 plan이 차단 토큰 보호를 받지 못함.

**수정**: 파라미터 추가 또는 문서화.

---

### M5. whitespace-only text가 weight redistribution bypass

**파일**: `hpcu/router/candidate_scoring.py:129`  
**영향**: `TargetQuery(text="   ", role="button")`가 text-only 경로로 처리되어
max score가 0.55로 제한됨.

**수정**: `if not query.text:` → `if not _normalize(query.text):`

---

### M6. `_bracket_count_extract` — string 내부 `{` 컨텍스트 미인지

**파일**: `hpcu/gateway/json_response.py:30`  
**영향**: string 내부의 `{`에 대해 전체 content를 scan. 성능 저하 (graceful).

**수정**: 주석으로 제한사항 문서화. 실패 시 다음 `{`로 넘어가므로 정확성 문제 없음.

---

### M7. `allowed_keys`가 soft tie-breaker (API 계약 misleading)

**파일**: `hpcu/gateway/json_response.py:127`  
**영향**: `allowed_keys`를 hard filter로 기대하는 호출자가 의도치 않은 동작 가능.

**수정**: docstring 명확화 또는 `strict_allowed` 파라미터 추가.

---

### M8. bracket-counting fallback이 non-JSON span 추출 가능

**파일**: `hpcu/gateway/json_response.py:83-90`  
**영향**: trailing comma, unquoted key 등 유효하지 않은 JSON span을 추출 → `json.loads` 실패 → catch. 불필요한 연산.

**수정**: fast pre-check regex 추가 (낮은 우선순위).

---

### M9. 여러 `{` 문자에서 O(n×m) 성능

**파일**: `hpcu/gateway/json_response.py:77-90`  
**영향**: prose에 `{`가 많은 경우 bracket-counting fallback이 content 길이만큼 반복 스캔.

**수정**: 최대 시도 횟수 제한 또는 short-circuit.

---

### M10. `PerformanceSnapshot`에 latency/error 필드 부재

**파일**: `hpcu/observation/facade.py:28-41`  
**영향**: count만 추적. latency, error, timeout 정보 없음.

**수정**: `_Metric`에 `total_latency_ms`, `error_count`, `timeout_count` 추가.

---

### M11. config key type unsafety

**파일**: `hpcu/observation/facade.py:79-82`  
**영향**: `config["performance"]`가 dict가 아니면 `AttributeError`.

**수정**: `isinstance(performance, dict)` 가드 추가.

---

### M12. `TimeoutError` vs `asyncio.TimeoutError` mismatch

**파일**: `hpcu/observation/facade.py:90-91`  
**영향**: `asyncio.TimeoutError`를 catch하는 caller가 `builtins.TimeoutError`를 놓침.

**수정**: `asyncio.TimeoutError`를 raise하거나 문서화.

---

### M13. sync `elements_from_frame()`가 event loop를 block

**파일**: `hpcu/observation/facade.py:109-117`  
**영향**: Tesseract OCR이 event loop를 blocking.

**수정**: `await self._perception.elements_from_frame_async()` 사용.

---

### M14. test config가 runtime defaults에 의존

**파일**: `tests/failure_injection/test_fail_closed.py:195-203`  
**영향**: runtime config defaults 변경 시 테스트가 silently break.

**수정**: 명시적 `targeting`, `semantic` 섹션 추가.

---

### M15. action execution 중 model failure 테스트 부재

**파일**: `tests/failure_injection/test_fail_closed.py`  
**영향**: plan compile 실패만 테스트. action decision 실패 시나리오 미커버.

**수정**: action decision 중 모델 실패 테스트 추가.

---

### M16. NAVIGATE ambiguity fallback 테스트 부재

**파일**: `tests/unit/test_control_loop.py`  
**영향**: 새로운 fallback 분기 테스트 없음.

**수정**: `test_navigate_falls_back_to_top_ambiguous_candidate` 추가.

---

### M17. `_requires_target` FOCUS_WINDOW exclusion 테스트 부재

**파일**: `tests/unit/test_control_loop.py`  
**영향**: FOCUS_WINDOW가 grounding을 bypass하는지 검증 안 됨.

**수정**: FOCUS_WINDOW + no query + no element_id 테스트 추가.

---

### M18. `begin_task` loop_breaker.reset() 테스트 부재

**파일**: `tests/unit/test_control_loop.py` (기존 `test_task_boundary_reset.py`)  
**영향**: `reset()` 호출 검증이 integration test에만 의존.

**수정**: unit test에서 mock loop_breaker로 `reset()` 호출 검증.

---

## 4. 🟢 LOW 우선순위 (백로그)

| # | 파일 | 이슈 |
|---|---|---|
| L1 | `grounder.py:6-9` | Lazy import가 불필요 (실제 순환 import 없음). harmless |
| L2 | `candidate_scoring.py:85` | `_role_match`가 `""`와 `None`을 동일 처리 |
| L3 | `test_candidate_scoring.py` | role-only query 테스트 없음 |
| L4 | `json_response.py:35` | bracket counting depth limit 없음 |
| L5 | `control_loop.py:160-164` | fallback GroundingResult가 `failure_code` 손실 |
| L6 | `control_loop.py:445-452` | `_requires_target` implicit default for unknown ops |
| L7 | `control_loop.py:341` | NAVIGATE fallback이 `_wait_until`에 미적용 |
| L8 | `facade.py:68` | `performance_snapshot`이 public mutable |
| L9 | `test_control_loop.py:128` | `RiskEngine`이 default config에 의존 |
| L10 | `plan.py:205-250` | `apply_patch`가 blocked_tokens 수정 불가 (의도적) |
| L11 | `task_runtime.py:179-182` | blob construction 비용 (capture 대비 negligible) |

---

## 5. 권장 수정 순서

### Iteration 1 (당일 — blocking fixes)
1. **H1** — `CasePlanCompiler`에 `blocked_tokens` 전달
2. **H2** — `_observe_scene()` 중복 호출 제거
3. **H3** — `_observe_scene()` 예외 처리
4. **H4** — `allowed_keys` 복원
5. **H5** — `asyncio.gather()` task cleanup
6. **H6** — `call_gateway_async` timeout narrowing

### Iteration 2 (1~2일 — correctness hardening)
7. **M1** — substring matching → word-boundary
8. **M2** — empty string blocked_tokens 검증
9. **M5** — whitespace text weight bypass
10. **M10~M13** — facade 성능/안전성 개선
11. **M16~M18** — 테스트 커버리지 보강

### Iteration 3 (백로그)
12. **M3, M4, M6~M9, M14~M15** — plan hash, compile_search, JSON parser, 테스트
13. **L1~L11** — LOW 우선순위 항목

---

## 6. 예상 영향

| 지표 | 현재 | Iter 1 후 | Iter 2 후 |
|---|---|---|---|
| 테스트 통과 | 554/554 | 554/554 | 560+/560+ |
| blocked_tokens 기능 | dead | 정상 작동 | 정상 + false positive 방지 |
| 노드당 observe 호출 | 3회 | 2회 | 2회 |
| observer crash 처리 | unhandled | fail-safe | fail-safe |
| target ambiguity | regression | 해결 | 해결 |
| scoring edge case | whitespace bypass | whitespace bypass | 해결 |

---

## 7. 완료 정의

- Iteration 1의 6개 HIGH 이슈가 모두 수정되고 테스트 통과
- Iteration 2의 주요 MEDIUM 이슈가 수정되고 회귀 없음
- `docs/plan/17-remaining-work-plan.md`의 Phase A live gate가 통과
- CI: `python -m pytest tests/ -q` 0 failure, coverage ≥ 85%