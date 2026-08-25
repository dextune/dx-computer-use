---
title: "Screen Understanding Engine 06 — CPU Resource·Performance 계획"
version: "1.0"
date: "2026-08-25"
parent: "docs/plan/20-screen-understanding-engine/index.md"
language: "ko-KR"
---

# 06. CPU Resource·Performance 계획

## 1. 목표 정의

“CPU 성능 극대화”를 CPU 사용률 100%로 정의하지 않는다.

최적화 목표:

```text
minimize(first_actionable_latency)
minimize(processed_pixels)
minimize(full_ocr_passes)
minimize(model_wait_time)
subject to:
  stale_action = 0
  false_completion = 0
  local_precision 유지
```

## 2. 현재 기반

`LocalResourcePolicy`는 이미 다음을 계산한다.

- cpu_count
- reserve_cores
- perception_workers
- perception_queue_depth

`ScreenPerception`은 semaphore와 submitted count로 bounded OCR을 수행하고,
`PerformanceTrace`는 wall/cpu/max/queue depth metric을 기록한다.

첫 단계는 이 기반을 없애는 것이 아니라 **stage별 병목을 보이게 만드는 것**이다.

## 3. stage taxonomy

최소 다음 이름으로 계측한다.

```text
capture
structure_read
change_detect
layout_segment
ocr_queue_wait
ocr_dense
ocr_sparse
object_fusion
relation_build
scene_index_build
temporal_match
grounding_candidate_generation
grounding_score
verification
```

stage 이름은 변경 시 benchmark 비교 가능성이 깨지므로 한 번 확정하면 안정적으로 유지한다.

## 4. worker 전략

### Phase 1 — 현재 bounded worker 유지

- OCR은 현재 semaphore/to_thread 경로 유지
- layout segmentation을 같은 local resource budget 안에 넣음
- capture/input에 reserve core 개념 유지

### Phase 2 — dedicated executor 검토

`asyncio.to_thread`는 process-global default executor를 사용하므로 실제 profile에서 다른 작업과
경합이 보이면 perception 전용 `ThreadPoolExecutor`를 고려한다.

도입 조건:
- queue wait가 병목
- default executor 경합이 artifact로 확인
- 전용 pool이 p95를 개선

### Phase 3 — stage-aware scheduling

OCR와 layout이 동시에 쌓일 때 latest frame/dirty ROI를 우선한다.
오래된 frame의 perception 결과가 current action에 필요 없으면 취소/폐기한다.

무제한 PriorityQueue는 금지. bounded queue + latest-state policy를 유지한다.

## 5. 병렬화 원칙

병렬화 가능한 것:
- capture와 structure read
- 독립 dirty ROI OCR
- 독립 region feature 계산
- source pair fusion candidate feature

직렬화가 필요한 것:
- Scene version commit
- temporal identity allocation
- action freshness check
- terminal verification commit

같은 stateful tracker에 여러 frame을 동시에 commit하지 않는다.

## 6. memory/copy 정책

- pipeline boundary는 `FrameHandle`
- PNG bytes/base64를 DTO에 넣지 않음
- crop cache는 bounded LRU
- OCR result cache는 bounded LRU
- index는 current Scene 기준으로만 유지
- 이전 Scene history는 LoopBreaker/trace가 요구하는 bounded 수만 유지

## 7. dirty ROI 경제성

매 frame 기록:

```text
dirty_pixel_ratio
processed_pixel_ratio
full_ocr_passes
roi_ocr_passes
unchanged_reuse_count
```

목표는 CPU를 더 쓰는 것이 아니라 **화면 변화량에 비례해 계산량이 줄어드는 것**이다.

## 8. first actionable latency

사용자가 체감하는 핵심 metric을 별도로 둔다.

정의:

```text
command accepted
 → 첫 policy-approved executable action이 준비된 시점
```

model call이 필요 없는 fixture에서는 이 시간이 local pipeline 성능의 직접 KPI다.

## 9. baseline 방법

SUE-0에서 reference hardware 정보를 artifact에 기록한다.

- logical CPU count
- RAM
- OS
- screen resolution
- OCR language config
- Python version

각 fixture 30회 이상 실행하고 warm/cold를 분리한다.

기록:
- median
- p95
- max
- CPU time
- wall time
- queue depth

한 번의 우연한 best-case를 성능 수치로 사용하지 않는다.

## 10. 초기 SLO 후보

기존 문서의 `unchanged 1080p p95 <= 10ms`, `delta scene update p95 <= 150ms` 방향을
참고하되, 20번 구현의 absolute gate는 SUE-0 baseline 후 확정한다.

먼저 강제할 invariant:
- unchanged OCR pass = 0
- stale frame result commit = 0
- queue depth configured bound 초과 = 0
- full screen OCR는 initial/large-change/fallback에만 발생

## 11. 성능 회귀 규칙

기능 정확성 테스트 통과 후 benchmark를 비교한다.

다음 중 하나면 regression review 대상:
- processed pixels 증가
- unchanged OCR 발생
- p95 악화
- queue wait 급증
- model call 증가

정확성을 희생해서 수치만 낮춘 변경은 reject한다.
