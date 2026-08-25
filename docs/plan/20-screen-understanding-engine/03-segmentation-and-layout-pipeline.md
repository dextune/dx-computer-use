---
title: "Screen Understanding Engine 03 — CPU Segmentation·Layout 파이프라인"
version: "1.0"
date: "2026-08-25"
parent: "docs/plan/20-screen-understanding-engine/index.md"
language: "ko-KR"
---

# 03. CPU Segmentation·Layout 파이프라인

## 1. 목적

스크린샷 전체를 AI가 이해하게 하기 전에 CPU가 **변경 영역과 공간 구조**를 계산한다.
이 단계의 출력은 의미 판단이 아니라 Region/bbox/geometry다.

## 2. 실행 조건

픽셀 segmentation은 항상 실행하지 않는다.

우선순위:

```text
DOM/UIA/AT-SPI/AX 충분
 → pixel segmentation skip 또는 gap ROI만

structure 일부 부족
 → missing/dirty ROI만 segmentation

structure 없음(remote/VNC)
 → pixel segmentation이 primary
```

## 3. 입력

- `FrameHandle`
- `FrameHandle.dirty_rects`
- optional content/window ROI
- previous Region set
- previous Scene version

이미지 bytes를 모듈 사이로 복사하지 않는다. 실제 decode는 `FrameStore`를 소유한 perception
경계에서만 수행한다.

## 4. 알고리즘 단계

### Step 1 — changed area 결정

현재 `ScreenPerception._select_roi()`의 dirty ratio 정책을 재사용한다.

- dirty rect 없음 + previous 있음 → OCR/segmentation skip 가능
- dirty rect가 작은 비율 → dirty ROI
- dirty rect가 너무 큼 → requested content ROI
- previous 없음 → requested ROI/full initial scan

### Step 2 — grayscale/edge preparation

초기 구현은 NumPy/Pillow 기반으로 시작한다.

- grayscale
- local contrast normalization 필요 여부 측정
- horizontal/vertical gradient
- threshold

OpenCV를 필수 dependency로 바로 넣지 않는다. baseline 대비 이득이 명확할 때만 검토한다.

### Step 3 — projection profile

행/열별 edge/ink density를 계산해 큰 whitespace cut 후보를 찾는다.

```text
horizontal projection → header/content/footer 분리 후보
vertical projection   → sidebar/main column 분리 후보
```

### Step 4 — XY-cut

큰 whitespace gap을 기준으로 ROI를 재귀 분할한다.

종료 조건:
- 최소 region width/height
- whitespace score 임계값 미달
- max depth
- 최소 component 수

### Step 5 — connected components

텍스트/경계 후보 binary mask에서 component bbox를 생성한다.

용도:
- OCR line 주변 UI block 경계 후보
- 작은 독립 control candidate
- repeated layout 후보

### Step 6 — region merge

과분할 방지를 위해 union-find(DSU) 기반 merge를 사용한다.

merge feature:
- bbox gap
- alignment
- shared baseline
- overlap
- size ratio
- common parent ROI

의미 문자열은 merge 조건에 사용하지 않는다. OCR text는 text-cluster grouping에만 제한적으로
사용할 수 있다.

### Step 7 — region hierarchy

containment 기반 parent-child tree를 만든다.

- 가장 작은 valid containing parent 선택
- cycle 금지
- same bbox duplicate 제거
- depth upper bound

## 5. 반복 구조 탐지

상품 카드, 리스트 행처럼 반복되는 레이아웃은 의미를 붙이지 않고 `repeated_block`으로 기록할
수 있다.

중립 feature:
- 유사 width/height
- 일정한 x/y interval
- 동일 column/row alignment
- 내부 child count 유사

출력:

```text
repeated_group_id = layout_group_...
```

“상품”, “검색 결과” 같은 의미는 설정된 semantic target 또는 structure source에서만 온다.

## 6. incremental update

전체 Region tree를 매번 버리지 않는다.

```text
previous regions
 + dirty ROI
 → dirty ROI와 겹치는 region subtree invalidate
 → 해당 ROI만 segmentation
 → unchanged subtree 유지
 → temporal matcher로 id 보존
```

Region fingerprint 후보:

```text
quantized bbox geometry
+ child geometry signature
+ optional neutral text-shape signature
```

텍스트 원문 전체를 fingerprint의 유일한 identity로 사용하지 않는다.

## 7. 비용 관리

worst-case recursive segmentation을 막는다.

config-driven bounds:
- max segmentation depth
- max regions/frame
- min region area
- max components/ROI
- dirty ROI ratio threshold

bound 초과 시 더 공격적으로 분할하지 않고 coarse region으로 반환한다. 모델 호출로 자동 전환하지
않는다.

## 8. 신규 파일 후보

```text
hpcu/perception/segmentation.py
  LayoutSegmenter
  SegmentationResult

hpcu/perception/regions.py
  projection helpers
  connected components
  region merge
  hierarchy construction
```

ABC가 필요한지 먼저 증명하지 않는다. 우선 공통 deterministic 모듈로 구현하고 교체 가능성이
실제로 생길 때 protocol/ABC를 추가한다.

## 9. 테스트 fixture

반드시 synthetic/controlled fixture를 만든다.

1. header + content + footer
2. sidebar + main
3. form row(textbox + button)
4. repeated 10-row list
5. modal overlay
6. 3% dirty region only
7. full-screen change
8. structure source가 region을 이미 제공하는 경우 pixel skip

검증:
- region count bounded
- key boundary IoU
- hierarchy correctness
- unchanged subtree identity 유지
- dirty ROI 밖 recompute 0
- 동일 이미지 결과 deterministic
