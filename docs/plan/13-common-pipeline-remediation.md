---
title: "HPCU Runtime 개발 계획 13 — 공통 파이프라인 개선"
version: "1.0"
date: "2026-08-21"
parent: "docs/plan/01-system-architecture.md, docs/plan/03-observation-layer.md, AGENTS.md"
language: "ko-KR"
---

# 13. 공통 파이프라인 개선

코드–문서 대조 검수에서 나온 **공통 계층 부채**를 한곳에 모은 구현 계획이다.
스펙 본문(ABC, matrix, DSL)은 01·03·07이 소유한다. 이 문서는 **무엇을 어떤 순서로
고칠지**만 적는다. 표를 복제하지 않는다.

진행 체크는 `docs/plan/tasklist.md`의 **R-*** 항목.

---

## 1. 한 줄 진단

> 공통 **경계**(ABC, `FrameHandle`, session_id, platform import 금지)는 맞다.
> 공통 **의미**(delta Scene, 단일 Grounder, observe→…→verify 루프)는 조립되지 않았다.
> 모듈은 나란히 있고, 문서의 한 파이프는 아니다.

OS 플러그인(Phase 2/8 stub)은 이 계획의 주 대상이 아니다.
먼저 공통 경로를 이은 뒤에야 stub을 “실행된다”고 말할 수 있다.

---

## 2. 범위

### 한다

1. `ControlLoop.step()`을 문서 루프대로 조립 (모델 없이 fixture Scene으로 증명).
2. 관찰 산출물을 `SceneDelta`(added/removed/modified)로 통일하고 tracker를 builder에 연결.
3. Grounder 점수식을 `router.score_candidates` + `config/runtime-config.yaml`로 합친다.
4. 스키마 불변식: 좌표 space 타입, Scene 불변, compiler가 Enum/실제 op를 쓰게.
5. 네이밍·테스트 마커·tasklist `[x]` 과장 교정.

### 하지 않는다

- Playwright / DXGI / X11 / ScreenCaptureKit / 실 VLM 연동 (기존 stub 유지).
- 새 OS ABC. 계약은 03이 이미 소유.
- capability matrix를 이 문서에 다시 적기.
- tasklist에서 설계를 바꾸기.

---

## 3. 현재 상태 (검수 스냅샷)

| ID | 증상 | 위반 문서 |
|---|---|---|
| G1 | `ControlLoop.step`/`run`이 `NotImplementedError`. Policy·Recovery·Builder 미연결 | 01 §5, AGENTS 실행 불변식 |
| G2 | `ObservationDelta.structure`는 스냅샷. `SceneDelta` 미사용. tracker 미연결 | 01 §2.3 Delta-Only, 05 |
| G3 | Grounder 자체 점수 + Router 점수 두 벌. Grounder 임계 기본 0.0 | 06, AGENTS 하드코딩 금지 |
| G4 | `pydantic` 의존만 있고 스키마 미검증. `FrameHandle.space`가 `str` | 02, 계획 P0-2 |
| G5 | frozen `Scene.elements`가 가변 dict | AGENTS 불변 전달 |
| G6 | Compiler가 모든 ACTION을 `CLICK`으로. `Precondition.kind` 문자열 + `# type: ignore` | 09, 스키마 경계 |
| G7 | `ApprovalManager`, `WorkflowVersionManager` — 금지 suffix | naming-conventions §5 |
| G8 | Phase 0 테스트에 `@pytest.mark.unit` 없음 | testing-standards §4.2 |
| G9 | `wait_until`이 5ms `asyncio.sleep` 폴링 | 07 settle, AGENTS sleep 금지 취지 |
| G10 | tasklist `[x]`가 stub/미연결을 완료로 표시 | tasklist 체크 규칙, AGENTS |

공통이 **잘 된 것**(이 계획에서 깨지 말 것): platform import 0, Capture/Structure/Input ABC,
`FrameHandle`에 bytes 없음, session_id 불일치 거부, stub `UNSUPPORTED`,
모듈 자기 등록 없음, Grounder가 좌표가 아니라 `element_id`.

---

## 4. 작업 묶음 (순서 강제)

한 묶음이 테스트로 닫히기 전에 다음 묶음으로 넘어가지 않는다.
파일 충돌을 피하려고 묶음은 모듈 경계로 나눈다.

### R1 — 제어 루프 조립 (G1)

**목표:** 모델 호출 0회로, fixture Scene에서 한 사이클이 돈다.

`hpcu/runtime_core/control_loop.py`가 생성자 주입으로 다음을 받는다:

```text
Observer, SceneBuilder, Grounder, RiskEngine, Approval (optional),
Executor, Verifier, TraceRecorder, LoopBreaker (optional)
```

한 `step()`:

```text
delta = observer.observe()
scene = builder.update(scene, delta)
result = grounder.resolve(query, scene)
if not result.confident: return unresolved (모델 호출 없음 — 이 묶음 범위)
if not policy.allow(action, scene): pause_for_approval / skip
prepared = executor.prepare(...)
execution = executor.execute(prepared)
ok = verifier.verify_postconditions(...)
if ok: recorder.append(ACTION); commit scene
else: recovery.handle(...)  # 있으면. 없으면 failure_code로 반환
```

완료 조건:

- `tests/unit/test_control_loop.py`: fake observer + fake injector로
  click 1회 → postcondition 참 → seq 증가, model_call_count == 0.
- policy HIGH면 execute가 호출되지 않음.
- 루프가 `hpcu.platform`을 import하지 않음 (기존 grep 유지).

### R2 — Scene delta + tracker (G2)

**목표:** 관찰→Scene이 증분이다. 사라진 노드가 남기지 않는다.

1. `ObservationDelta`를 폐기하거나, `SceneDelta`의 별칭으로 축소한다.
   권위 타입은 `hpcu.schemas.scene.SceneDelta` 하나.
2. `CompositeObserver.observe()`가 **이전 스냅샷 대비** added/removed/modified를 채운다.
   첫 호출만 전체 added.
3. `SceneBuilder.update(scene, delta: SceneDelta)`:
   - added 삽입, removed 삭제, modified 교체
   - `ElementTracker`로 id 재식별 (fingerprint → IoU). 새 id는 new object.
4. `Scene.elements`는 매 업데이트마다 **새 mapping** (R3과 함께 불변화).

완료 조건:

- 요소가 트리에서 빠지면 다음 Scene에 없다.
- 같은 fingerprint면 id가 유지된다.
- builder가 `ObservationDelta.structure` 전체 머지를 하지 않는다.

### R3 — Grounder 단일화 + config 임계값 (G3)

**목표:** 점수식은 하나. 숫자는 yaml.

1. `Grounder.resolve`는 `hpcu.router.candidate_scoring.score_candidates`만 호출.
   `_match_score` 삭제.
2. `confidence.local_execute_threshold`, `local_margin_min`을
   `config/runtime-config.yaml`에서 로드. Grounder/Router 생성자가 기본값을
   코드에 박지 않는다 (yaml 실패 시에만 문서와 같은 기본을 한곳 —
   `hpcu` 설정 로더 한 파일).
3. `is_resolved`는 top-1 ≥ threshold **그리고** margin ≥ min.

완료 조건:

- Grounder와 Router가 같은 fixture에서 같은 순위.
- yaml 임계를 바꾸면 테스트가 그 값을 읽는다 (하드코딩 어서션 금지:
  테스트가 yaml을 monkeypatch하거나 생성자에 주입).

### R4 — 스키마 불변식 (G4, G5, G6)

**목표:** 경계 DTO가 문서 스키마와 같다.

1. `FrameHandle.space: CoordinateSpace` (str 폐기). 호출부 일괄 수정.
2. `Scene.elements`를 `MappingProxyType` 또는 `tuple` 기반으로 만들어
   생성 후 삽입이 불가능하게. builder는 새 `Scene`을 만든다.
3. Compiler:
   - trace payload에서 `op`를 읽어 `ActionOp`로 복원. 없으면 그 레코드 skip
     또는 실패 — 기본 `CLICK` 금지.
   - `Precondition`/`Postcondition`은 Enum 멤버만. `# type: ignore` 삭제.
4. Pydantic: ** entirely 쓰거나 빼기.**
   - 권장: 경계 DTO는 frozen dataclass 유지 + `__post_init__` 검증
     (이미 `SceneDelta`/`EvidenceContract` 방식). `pydantic`을
     `pyproject.toml`에서 제거하거나, 스키마를 pydantic v2로 옮기고
     JSON Schema를 거기서 생성. 둘 다 어중간하게 두지 않음.
   - 선택은 구현 PR에서 하나로. 기본안은 dataclass+post_init
     (의존성 축소, 기존 코드와 일치).

완료 조건:

- `FrameHandle(space="oops")` 타입 체크 또는 생성 실패.
- compiler 테스트: TYPE 이벤트 → `ActionOp.TYPE` step. CLICK으로 안 바뀜.
- `pyproject.toml`과 실제 스키마 구현이 일치.

### R5 — 이름·테스트·settle (G7, G8, G9)

1. `ApprovalManager` → `ApprovalGate` (또는 `ApprovalBook`).
   `WorkflowVersionManager` → `WorkflowVersions`.
2. `tests/unit/` 전 파일 `@pytest.mark.unit`. CI `--strict-markers` 유지.
3. settle: 연속 N회 predicate 참 **또는** 관찰 이벤트. 고정 5ms 폴링만으로
   “안정”이라 부르지 않음. `poll_interval_ms`는 config.
   테스트는 fake clock / 즉시 참 predicate — `asyncio.sleep` 경로를
   실시간으로 기다리지 않음.

### R6 — tasklist 정직화 (G10)

`[x]`는 **그 줄의 완료 조건이 테스트로 증명된 것**만.

| 현재 `[x]` | 조치 |
|---|---|
| P1-2 Playwright adapter | `[ ]` 또는 `[~]` + “stub: probe False”. 실 CDP는 별도 항목 |
| P1-8 fixture 사이트 | `[ ]` 유지. 없으면 완료 아님 |
| P1-5/P1-6 | R1 통과 후에만 실행기·settle을 루프 연결 완료로 인정 |
| P2/P8 native | “계약 stub”과 “실기”를 두 줄로 분리. stub `[x]`, 실기 `[ ]` |
| P3-3 OCR | “fake fixture OCR `[x]` / Paddle 연동 `[ ]`” |

이 묶음은 코드 변경 없이 tasklist 편집만으로 시작 가능. R1~R5 머지 시점에
해당 줄을 다시 `[x]`.

---

## 5. 검증 (이 계획의 완료)

기존 게이트에 더해:

```text
python -m pytest tests/unit/ -v --strict-markers
python -m pytest tests/ --cov=hpcu --cov-fail-under=85
grep -R 'from hpcu.platform' hpcu/vision hpcu/scene_graph hpcu/runtime_core \
     hpcu/schemas hpcu/coordinates  → 0
grep -R 'time.sleep' tests/  → 0
ControlLoop.step 이 NotImplementedError 가 아님 (unit이 fake로 1사이클)
```

커버리지 하한은 유지. 새 루프 분기는 테스트로 채운다.

---

## 6. 문서 동기화 (구현 PR에 포함)

한 주제 한 곳. 숫자·표를 여기 말고 소유 문서에만 고친다.

| 변경 | 소유 |
|---|---|
| 루프가 돈다 | 01 §5 코드 예시와 구현이 같아지게 01은 **링크만** 유지. 코드가 권위 |
| SceneDelta가 관찰 산출 | 03 — ObservationDelta 폐기/축소 반영 |
| Grounder = scoring 모듈 | 06 한 줄: 구현은 `hpcu.router.candidate_scoring` |
| stub vs 실기 | tasklist만. 03 matrix는 설계 기본값 유지 |
| 이 계획의 체크 | tasklist `R1`~`R6` |

`dev-init-001.md`는 원안. 루프 의사코드가 구현과 달라지면 plan 01/07만 고친다.

---

## 7. 리스크

- Observer를 SceneDelta로 바꾸면 facade·builder·테스트가 동시에 움직인다. R2는
  한 PR. 중간 커밋에서 두 타입을 동시에 받으면 안 된다.
- Grounder 임계를 0.0 → yaml(0.88)로 올리면 기존 Grounder 테스트가 깨진다.
  테스트가 “약한 매칭도 resolve”를 가정하면 고친다. 동작이 문서에 맞다.
- ControlLoop에 Recovery를 넣으면 Phase 6 트리거까지 한 테스트가 커진다.
  R1은 recovery **optional**. 실패 시 failure_code 반환이면 통과.

---

## 8. 비목표 재확인

실 브라우저 vertical slice, Windows UIA, PaddleOCR, MiniMax 실호출은
이 문서가 끝나도 `[ ]`일 수 있다. 그것은 정직함이지 후퇴가 아니다.
공통 파이프가 붙은 뒤에야 플러그인을 의미 있게 꽂는다.
