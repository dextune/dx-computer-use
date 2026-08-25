---
title: "Screen Understanding Engine 04 — Object Fusion·Temporal Tracking"
version: "1.0"
date: "2026-08-25"
parent: "docs/plan/20-screen-understanding-engine/index.md"
language: "ko-KR"
---

# 04. Object Fusion·Temporal Tracking

## 1. 목표

DOM/Accessibility/OCR/pixel layout이 같은 실제 UI 객체를 서로 다른 id로 보고하는 문제를
해결하고, frame이 바뀌어도 stable object identity를 가능한 한 유지한다.

## 2. Fusion 입력

- structure elements: DOM/UIA/AT-SPI/AX
- OCR elements
- neutral visual/region candidates
- previous Scene elements

Fusion은 source provenance를 잃지 않는다.

## 3. pair candidate generation

모든 source pair를 O(N²) 비교하지 않는다.

1. bbox가 있는 객체는 spatial grid로 nearby 후보 생성
2. exact source ref/fingerprint가 있으면 우선 후보
3. bbox가 없는 structure element는 source-native relation/name을 통해 제한된 후보만 생성

## 4. Fusion score

동일 객체 가능성을 아래 feature로 계산한다.

```text
fusion_score =
  w_iou        * bbox_iou
+ w_contain    * containment
+ w_center     * center_proximity
+ w_text       * normalized_text_similarity
+ w_role       * role_compatibility
+ w_source_ref * source_reference_match
```

각 feature는 [0,1]. weight/threshold는 config로 이동한다.

### text similarity

초기에는 비용이 낮은 순서:

1. normalized exact
2. substring ratio
3. token overlap
4. 필요하면 normalized edit similarity

긴 OCR 문장 전체에 고비용 문자열 알고리즘을 무조건 적용하지 않는다.

### role compatibility

구조 source가 명시 role을 제공한 경우만 강하게 사용한다.
OCR/pixel source가 추측한 role은 source confidence가 낮게 반영되거나 neutral candidate로 유지한다.

## 5. one-to-one assignment

source A와 source B 사이에서 threshold를 넘는 후보가 여러 개면 현재 tracker의 Hungarian
구현을 재사용한다.

비용:

```text
cost = 1 - fusion_score
```

bbox/role gate에서 불가능한 pair는 sentinel cost로 제외한다.

3개 이상 source 결합은 pair assignment 결과를 DSU로 묶되, 서로 모순되는 one-to-one
결과가 생기면 보수적으로 분리한다.

## 6. merged UIElement

merge 시 선택 규칙:

- role: 구조 source 우선
- name/text: source reliability + confidence 기반
- bbox: 가장 신뢰도 높은 구조 source 우선, 없으면 fusion consensus
- state: 구조 source 우선
- sources: 전부 보존하되 source type 중복은 ref/confidence가 더 강한 항목 선택 검토
- semantic_tags: raw pixel heuristic로 새 의미 tag를 만들지 않음
- fingerprint: source ensemble 기반 재계산

## 7. Temporal matching hierarchy

현재 fingerprint + IoU Hungarian을 다음 계층으로 확장한다.

```text
T0 exact source ref
T1 exact stable fingerprint
T2 same fusion/source ensemble fingerprint
T3 composite temporal assignment
T4 unmatched → new id
```

### composite temporal score

```text
temporal_score =
  a * IoU
+ b * center proximity
+ c * text similarity
+ d * role compatibility
+ e * source continuity
+ f * region-parent continuity
```

Hungarian assignment을 사용하되 gate를 먼저 적용한다.

## 8. identity hysteresis

작은 text 변화 때문에 id가 흔들리지 않도록 한다.

예:
- 버튼 count badge 변화
- 검색 결과 수 변화
- timer/clock

stable identity는 geometry/role/source가 충분히 유지되면 text 일부 변화에도 보존한다.
반대로 geometry와 source가 크게 바뀌면 같은 text라도 새 객체로 본다.

## 9. dirty subtree update

Temporal Scene은 dirty ROI와 관계 없는 객체를 재매칭하지 않는 것이 목표다.

```text
dirty ROI
 → impacted element ids via spatial index
 → impacted + boundary-neighbor만 rematch
 → untouched ids 그대로 유지
```

전체 Hungarian matrix는 initial/full-change fallback이다.

## 10. SceneBuilder 변경 원칙

`SceneBuilder`는 fusion/tracking 알고리즘을 직접 품지 않는다.

권장 순서:

```text
raw observations
 → FusionEngine
 → TemporalTracker
 → SceneDelta
 → SceneBuilder.update()
```

Builder는 immutable fold 책임만 유지한다.

## 11. 테스트

- OCR + AT-SPI 동일 bbox/text → 1 UIElement
- 동일 text 두 버튼 → geometry/source로 별개 유지
- text만 약간 변경 → id 유지
- 객체 이동 → threshold 범위에서 id 유지
- 완전히 교체 → id 변경
- dirty ROI 밖 id 100% 유지
- source 하나가 사라져도 ensemble evidence로 bounded continuity
- ambiguous fusion은 억지 merge하지 않음
- 100/500/1000 element synthetic tracking benchmark
