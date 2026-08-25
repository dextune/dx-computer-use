---
title: "Screen Understanding Engine 01 — 제품 범위와 목표 아키텍처"
version: "1.0"
date: "2026-08-25"
parent: "docs/plan/20-screen-understanding-engine/index.md"
language: "ko-KR"
---

# 01. 제품 범위와 목표 아키텍처

## 1. 현재 코드 진단

현재 저장소는 완전히 빈 상태가 아니다. 다음 자산이 이미 있다.

### 이미 재사용할 것

- `hpcu/perception/engine.py`
  - `FrameStore` 기반 PNG 처리
  - dirty rect union/intersection
  - unchanged frame 재사용
  - ROI OCR
  - dense/sparse OCR fallback
  - OCR cache
  - bounded semaphore/queue
  - stable OCR fingerprint
- `hpcu/observation/facade.py`
  - capture + structure 병렬 관찰
  - stateful `SceneDelta`
  - async OCR offload
  - observation performance/error/timeout metric
- `hpcu/schemas/ui_element.py`
  - source provenance
  - bbox/state/fingerprint
  - parent/left/right/above/below/contains 등의 relation 필드
- `hpcu/scene_graph/builder.py`
  - immutable `SceneDelta → Scene`
  - source union
  - scene-version freshness normalization
- `hpcu/scene_graph/tracker.py`
  - fingerprint matching
  - IoU
  - Hungarian minimum-cost assignment
- `hpcu/router/candidate_scoring.py`
  - text/role/source/structure 기반 deterministic scoring
- `hpcu/runtime_core/performance.py`
  - wall/CPU time stage metric
- `hpcu/runtime_core/resource_policy.py`
  - CPU count/reserve/perception worker/queue 계산

즉 핵심 문제는 알고리즘이 하나도 없다는 것이 아니라, 이 기능들이 아직
**하나의 명시적 Screen Understanding Engine 계약과 제품 benchmark로 묶이지 않았다는 것**이다.

## 2. 현재 gap

### G1. 명시적 Region 모델이 없다

OCR line과 structure element는 존재하지만, 화면의 큰 구역(content/header/sidebar/card
cluster 등)을 중립적으로 표현하는 계층이 없다. 그 결과 관계 계산과 ROI 재분석의 단위가
개별 element 또는 단순 content ROI에 머문다.

### G2. cross-source object fusion이 약하다

`SceneBuilder`는 같은 element id의 source를 합칠 수 있지만, OCR bbox와 AT-SPI/DOM bbox가
서로 다른 id로 들어왔을 때 “같은 실제 객체”라는 것을 일반적으로 결합하는 단계가 없다.

### G3. tracker의 최적 assignment가 제품 hot path에 충분히 연결되지 않았다

`ElementTracker`는 Hungarian IoU를 갖고 있지만 observation facade의 ID remap은 주로
fingerprint 기반이다. fingerprint가 달라진 객체의 continuity를 보강할 여지가 있다.

### G4. Scene 검색이 전수 스캔 중심이다

현재 candidate scoring과 matcher는 scene elements를 순회한다. 일반 화면에서는 충분할 수
있지만 반복 질의, 관계 질의, 큰 OCR scene에서는 text/role/spatial index가 필요하다.

### G5. CPU 최적화가 product-level KPI로 연결되지 않았다

OCR cache, dirty ROI, worker 제한은 존재하지만 “이 최적화 때문에 model call/processed pixels/
first action latency가 얼마나 줄었는가”를 qualification gate로 강제하지 않는다.

## 3. 목표 모듈 구조

기존 패키지를 유지하면서 아래 파일을 추가하는 것을 기본안으로 한다.

```text
hpcu/
  perception/
    engine.py                 # 유지: OCR orchestration
    segmentation.py           # 신규: pixel region segmentation
    regions.py                # 신규: region construction/merge helpers
    fusion.py                 # 신규: cross-source observation fusion
  scene_graph/
    builder.py                # 확장: fusion 결과/region relation 반영
    tracker.py                # 확장: composite temporal cost
    relations.py              # 신규: spatial relation derivation
    index.py                  # 신규: text/role/spatial lookup
  schemas/
    region.py                 # 신규: immutable neutral region DTO
  grounder/
    grounder.py               # 확장: indexed candidate generation
  runtime_core/
    performance.py            # 확장: stage taxonomy
    resource_policy.py        # 확장: measured resource policy
```

처음부터 `screen_understanding/`이라는 새 mega-package를 만들지 않는다. 현재 아키텍처의
Perception → Scene Graph → Grounder 경계를 유지하고, 20번 문서군이 이 전체 pipeline을
제품 개념으로 묶는다.

## 4. 처리 경로

### 구조 소스가 충분한 경우

```text
Capture metadata + DOM/AX/UIA/AT-SPI
 → structure elements
 → optional dirty ROI OCR for missing text/visual gaps
 → fusion
 → indexed Scene
```

### 구조 소스가 희박한 경우

```text
FrameHandle
 → dirty detection
 → region segmentation
 → ROI OCR
 → neutral visual candidates
 → fusion
 → indexed Scene
```

### remote/VNC처럼 구조가 없는 경우

```text
FrameHandle
 → segmentation + OCR
 → temporal tracking
 → indexed Scene
```

모든 경우 출력 계약은 동일한 `Scene`이다.

## 5. 책임 분리

### Perception

허용:
- OCR text/confidence
- bbox
- edge/connected component
- whitespace/layout partition
- source overlap
- neutral region/control candidate
- frame/region hash

금지:
- “이 버튼을 눌러야 한다” 결정
- 사용자 목표 의미 생성
- CAPTCHA/로그인 의미 판정 단독 수행
- 성공 판정

### Scene Graph

허용:
- 객체/region identity
- parent/child/spatial relation
- source provenance
- temporal continuity
- index

### Grounder

허용:
- 이미 제공된 `TargetQuerySpec`을 Scene object에 매칭
- deterministic rank/confidence/margin

금지:
- raw 사용자 문장을 임의로 새로운 target semantics로 바꾸기

### Semantic provider

담당:
- unresolved intent/slot
- semantic target compilation
- local evidence만으로 후보가 동률/모호한 상황의 bounded 해소
- bounded recovery replan

## 6. 설계 원칙

1. **reuse before rewrite**: 현재 incremental OCR/Hungarian/scoring을 폐기하지 않는다.
2. **measure before parallelize**: 병렬 worker를 늘리기 전에 stage profile을 남긴다.
3. **index after correctness**: 인덱스 결과는 brute-force reference와 동일해야 한다.
4. **freshness over speed**: stale object를 빨리 찾는 최적화는 실패다.
5. **neutral pixels**: 픽셀 파이프는 중립 구조까지만 만든다.
6. **source-aware**: DOM/UIA/AT-SPI/AX/OCR의 신뢰도를 동일 취급하지 않는다.
7. **incremental by default**: 전체 재계산은 fallback이어야 한다.

## 7. 첫 통합 경계

최초 vertical slice에서는 `CompositeObserver`가 다음 순서를 보장한다.

```text
capture + structure
 → changed region selection
 → optional pixel segmentation/OCR
 → fusion
 → temporal remap
 → SceneDelta
 → SceneBuilder
```

Grounder는 이 결과를 받아 target을 찾는다. 이 단계에서 AI 호출은 없어야 한다.
