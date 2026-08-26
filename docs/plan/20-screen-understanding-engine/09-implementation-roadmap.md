---
title: "Screen Understanding Engine 09 — 상세 구현 로드맵"
version: "1.1"
date: "2026-08-26"
parent: "docs/plan/20-screen-understanding-engine/index.md"
language: "ko-KR"
---

# 09. 상세 구현 로드맵

## 1. 순서 원칙

한 단계가 fixture와 benchmark로 닫히기 전 다음 단계의 최적화를 무리하게 넣지 않는다.
19번 플랜의 CommandRuntime E2E를 깨뜨리지 않는 작은 main 커밋 단위로 진행한다.

## 2. SUE-0 — Baseline/Profile 고정

### 변경 후보

- `hpcu/runtime_core/performance.py`
- `hpcu/perception/engine.py`
- `hpcu/observation/facade.py`
- `benchmarks/harness.py`
- 신규 benchmark fixture

### 작업

- [ ] stage taxonomy 확정
- [ ] processed pixel/full OCR/ROI OCR counter
- [ ] first actionable latency 측정 경계 정의
- [ ] reference hardware artifact
- [ ] current baseline 30-run 결과 저장

### 완료

새 알고리즘 없이 현재 코드의 비용 구조를 숫자로 설명할 수 있다.

## 3. SUE-1 — Region/Index 계약

### 신규

- `hpcu/schemas/region.py`
- `hpcu/scene_graph/index.py`
- `hpcu/scene_graph/relations.py`

### 작업

- [ ] neutral `RegionNode`
- [ ] uniform spatial grid
- [ ] role/text inverted index
- [ ] contains/same-row/left-right 등 기존 relation 계산
- [ ] IndexedScene version guard

### 테스트

- [ ] brute force == index result
- [ ] coordinate-space mismatch safe reject
- [ ] deterministic relation ordering

## 4. SUE-2 — Incremental Layout Segmentation

### 신규

- `hpcu/perception/segmentation.py`
- `hpcu/perception/regions.py`

### 작업

- [ ] dirty ROI selection 재사용
- [ ] projection profile
- [ ] XY-cut
- [ ] connected components
- [ ] DSU region merge
- [ ] containment hierarchy
- [ ] repeated layout neutral grouping
- [ ] bounded max depth/regions

### 테스트

- [ ] 8개 deterministic image fixture
- [ ] unchanged segmentation 0
- [ ] dirty ROI 밖 recompute 0

## 5. SUE-3 — Cross-source Fusion

### 신규

- `hpcu/perception/fusion.py`

### 수정

- `hpcu/observation/facade.py`
- `hpcu/scene_graph/builder.py`는 fold 책임 유지 확인

### 작업

- [ ] spatial gated pair generation
- [ ] fusion feature/score
- [ ] source-pair Hungarian assignment
- [ ] DSU source ensemble
- [ ] conservative conflict handling
- [ ] merged source provenance

### gate

false merge가 executable wrong target을 만들지 않는다.

## 6. SUE-4 — Temporal Identity V2

### 수정

- `hpcu/scene_graph/tracker.py`
- `hpcu/observation/facade.py`

### 작업

- [ ] source ref exact match
- [ ] fingerprint exact match
- [ ] composite temporal cost
- [ ] impacted ROI only assignment
- [ ] stability hysteresis
- [ ] removed/stale cleanup

### gate

100-frame fixture에서 stable identity artifact를 남기고 false carry-over 0을 우선한다.

## 7. SUE-5 — Indexed Grounding V2

### 수정

- `hpcu/grounder/grounder.py`
- `hpcu/router/candidate_scoring.py`

### 작업

- [ ] SceneIndex candidate generation
- [ ] relation feature
- [ ] temporal stability feature
- [ ] confidence + top1/top2 margin
- [ ] replay 기반 threshold calibration
- [ ] brute-force reference mode 유지

### gate

unique target local model call 0, ambiguous target wrong action 0.

## 8. SUE-6 — Structured AI Interrupt

### 수정 후보

- `hpcu/planning/semantic_interrupt.py`
- `hpcu/runtime_core/task_runtime.py`
- semantic replanner 경계

### 작업

- [ ] top-k structured candidate payload
- [ ] scene version 포함
- [ ] model purpose accounting
- [ ] stale response reject
- [ ] crop/image capability는 optional/UNSUPPORTED 명시

### gate

AI 장애 시 arbitrary fallback click 0.

## 9. SUE-7 — Verified Experience Cache

### 수정 후보

- `hpcu/trace/storage.py`
- `hpcu/trace/replay.py`
- `hpcu/workflow` 계층이 추가될 경우 기존 `09-workflow-compiler.md` 계약 우선

### 작업

- [x] coordinate-free GroundingHint
- [x] repeated success qualification
- [x] offline replay
- [x] drift/downgrade
- [x] fresh re-ground mandatory

### gate

qualified repeated fixture model call 0, fixed coordinate replay 0.

## 10. SUE-8 — Product Qualification

### 작업

- [x] 30+ held-out task에 SUE metrics 추가
- [x] structure-rich/poor/pixels-only 포함
- [x] Linux CommandRuntime E2E 연결
- [~] benchmark A/B report — artifact writer/gate 구현 완료, reference-hardware 실측 run 필요
- [x] final thresholds/SLO 고정
- [x] 소유 문서 03/04/05/06/11 동기화 (`03`은 기존 SUE observation 계약 재검토, 변경 불필요)
- [~] tasklist에 테스트로 증명된 항목만 `[x]` — 이 문서의 아래 상태표를 SUE authoritative status로 사용

## 11. 커밋 단위 권장

```text
Commit A  perf: baseline screen-understanding stages
Commit B  scene: add neutral regions and deterministic indexes
Commit C  perception: add incremental layout segmentation
Commit D  perception: fuse cross-source observations
Commit E  scene: strengthen temporal identity on dirty regions
Commit F  grounding: use indexed candidates and ambiguity margin
Commit G  runtime: send structured ambiguity context to semantic interrupt
Commit H  workflow: qualify verified grounding hints
Commit I  test: add SUE held-out qualification artifacts
Commit J  docs: consolidate verified SUE contracts into owner specs
```

각 커밋은 `main`에서 직접 수행한다.

## 12. 파일별 변경 지도

| 현재 파일 | 계획 |
|---|---|
| `hpcu/perception/engine.py` | OCR orchestration 유지, segmentation/fusion 호출 연결 |
| `hpcu/perception/matcher.py` | TargetingPack matcher 유지, SceneIndex 활용 여부 후속 검토 |
| `hpcu/scene_graph/builder.py` | immutable fold 유지, 알고리즘 과적재 금지 |
| `hpcu/scene_graph/tracker.py` | composite temporal assignment 확장 |
| `hpcu/router/candidate_scoring.py` | relation/temporal feature와 calibration 확장 |
| `hpcu/grounder/grounder.py` | index 기반 candidate generation |
| `hpcu/observation/facade.py` | raw observation → segmentation/fusion/tracking orchestration |
| `hpcu/runtime_core/resource_policy.py` | baseline 이후 stage resource fields 필요 시 확장 |
| `hpcu/runtime_core/performance.py` | stable stage metrics 확장 |

## 13. 개발 중 금지

- 새 site/CTA dictionary로 benchmark 맞추기
- semantic class를 pixel rule로 하드코딩
- full OCR가 쉬우니 dirty path 우회
- threshold를 테스트 파일에서만 특별 취급
- 좌표를 cache해서 재생
- benchmark 없이 worker 수 확대
- false merge/false completion을 속도 개선으로 무시

## 14. 최종 Definition of Done

20번 계획이 완료되었다고 말하려면 다음이 동시에 필요하다.

1. 화면 변경량에 비례해 perception 계산량이 감소한다.
2. structure/OCR/pixel source가 하나의 stable Scene object로 보수적으로 fusion된다.
3. Scene query가 index를 사용해도 reference brute-force 결과와 동일하다.
4. stable UI object id가 frame 사이에서 유지되고 stale object는 제거된다.
5. deterministic target은 AI 없이 실행된다.
6. 모호한 target은 잘못 클릭하지 않고 bounded semantic interrupt로 승격된다.
7. 반복 검증된 경로는 model call 0으로 fresh re-ground된다.
8. Linux CommandRuntime E2E에서 terminal evidence까지 검증된다.
9. false completion/stale action/policy bypass는 0이다.
10. 성능 개선이 benchmark artifact로 증명되고 기존 owner spec이 동기화된다.

## 15. 현재 완료 판정 — 2026-08-26

SUE-7 구현/portable test는 완료다. SUE-8의 qualification 코드, 36-case held-out matrix,
필수 artifact writer, CI gate 연결, Linux terminal E2E test path도 구현 완료다.

최종 **product qualification**은 코드 존재와 구분한다. 다음 두 실측 증거가 없는 상태에서는
20번 전체 Definition of Done을 `[x]`로 선언하지 않는다.

1. reference hardware에서 생성된 baseline-vs-SUE A/B artifact 5종
2. 실제 Linux X11 sandbox에서 terminal evidence E2E가 pass한 run

따라서 남은 것은 신규 기능 개발이 아니라 위 qualification run/evidence 확보다.
