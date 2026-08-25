---
title: "Screen Understanding Engine 08 — 테스트·벤치마크·Qualification"
version: "1.0"
date: "2026-08-25"
parent: "docs/plan/20-screen-understanding-engine/index.md"
language: "ko-KR"
---

# 08. 테스트·벤치마크·Qualification

## 1. 원칙

“화면이 잘 구조화되는 것 같다”를 완료 조건으로 쓰지 않는다. 각 단계마다 ground truth와
측정 지표가 있어야 한다.

## 2. fixture 계층

### Unit synthetic

직접 생성 가능한 bbox/element/region fixture.

검증:
- relation
- spatial index
- fusion score
- Hungarian assignment
- temporal identity
- candidate scoring

### Image fixture

저장된 deterministic PNG.

종류:
- form
- toolbar/content
- repeated list
- modal
- dense text
- sparse text
- 3% dirty change
- full redraw

각 PNG에는 기대 region/bbox/text를 별도 fixture metadata로 둔다.

### Structure fixture

DOM/UIA/AT-SPI/AX 역할을 하는 fake structure stream과 OCR stream을 함께 제공한다.
동일 객체가 다른 id/source로 들어오는 상황을 재현한다.

### Integration

`CompositeObserver → SceneDelta → SceneBuilder → Grounder`를 한 번에 통과한다.

### E2E

19번의 Linux sandbox CommandRuntime E2E에 새 perception/index path를 연결한다.

## 3. segmentation 지표

- expected major region recall
- region bbox IoU
- hierarchy edge correctness
- over-segmentation count
- under-segmentation count
- dirty ROI 밖 recompute count

초기에는 semantic class accuracy를 측정하지 않는다. segmentation은 의미 분류기가 아니다.

## 4. fusion 지표

- true same-object merge precision
- true same-object merge recall
- false merge count
- source provenance retention

**false merge가 특히 위험**하므로 precision 우선으로 threshold를 잡는다.

## 5. temporal 지표

- stable identity retention rate
- false identity carry-over
- new object detection recall
- removed object stale retention count

100-frame sequence fixture를 만들어 identity artifact를 남긴다.

## 6. grounding 지표

- top1 precision
- confident-local precision
- ambiguity detection rate
- local resolution rate
- unnecessary semantic escalation rate
- false executable target count

local resolution rate를 높이기 위해 precision을 희생하지 않는다.

## 7. performance 지표

- capture/structure/change/layout/OCR/fusion/index/tracking/grounding stage p50/p95
- processed pixels
- full/ROI OCR pass
- cache hit
- queue wait/depth
- wall/cpu time
- first actionable latency
- successful task model calls

## 8. baseline A/B

반드시 같은 fixture에서 비교한다.

### A — current baseline

```text
current CompositeObserver + ScreenPerception + existing Grounder
```

### B — new SUE path

```text
incremental segmentation + fusion + temporal index + indexed Grounder
```

성능만 비교하지 않고 성공/오류 결과도 동일해야 한다.

## 9. 핵심 qualification gate

### Q1 correctness

- stale action 0
- false completion 0
- false merge로 잘못된 executable target 0

### Q2 incremental

- unchanged frame OCR 0
- dirty ROI 밖 segmentation/OCR recompute 0
- removed element가 Scene/Index에서 잔존 0

### Q3 local-first

- unique structured target model call 0
- unique OCR target model call 0
- ambiguous target은 executor 호출 전 halt/escalate

### Q4 replay

- qualified repeated fixture model call 0
- coordinate replay 0
- drift에서 re-ground 또는 halt

### Q5 performance

reference hardware에서 baseline 대비:
- first actionable latency 개선 또는 동일
- processed pixels 감소
- full OCR 감소
- p95 regression 없음

절대 SLO는 SUE-0 artifact 후 문서에 고정한다.

## 10. held-out set

U9 제품 gate와 합쳐 30+ held-out task를 유지하되 SUE 관점의 화면 다양성을 추가한다.

- browser
- terminal
- desktop
- structure-rich
- structure-poor
- remote-like pixels-only
- duplicate labels
- modal
- slow loading
- stale frame
- viewport/scale variation
- Korean/English text

## 11. CI 전략

unit/integration은 hosted CI required를 목표로 한다.
실 Linux sandbox는 runner infrastructure 상태와 별개로 local/self-hosted evidence를 보존한다.

CI가 checkout 전에 실패하면 product test failure로 기록하지 않는다.

## 12. artifact 형식

매 benchmark run에서 최소:

```text
summary.json
stage-metrics.json
qualification.json
fixture-list.txt
reference-hardware.json
```

필요한 경우 시각 비교용 PNG를 남길 수 있으나 이미지 자체가 성공 판정의 유일한 근거가 되면
안 된다.
