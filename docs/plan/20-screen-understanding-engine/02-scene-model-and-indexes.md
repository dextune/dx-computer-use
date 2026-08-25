---
title: "Screen Understanding Engine 02 — Scene 모델·관계·인덱스"
version: "1.0"
date: "2026-08-25"
parent: "docs/plan/20-screen-understanding-engine/index.md"
language: "ko-KR"
---

# 02. Scene 모델·관계·인덱스

## 1. 목표

Scene을 단순 `id → UIElement` 집합에서 **빠른 공간·텍스트·역할 질의를 지원하는
immutable observation model**로 강화한다. 기존 `UIElement`를 폐기하지 않는다.

## 2. Region DTO

픽셀 레이아웃 분할 결과는 UI 의미를 확정하지 않는 별도 DTO로 둔다.

신규 후보: `hpcu/schemas/region.py`

```python
@dataclass(frozen=True)
class RegionNode:
    id: str
    scene_version: int
    bbox: BoundingBox
    parent_id: str | None = None
    child_ids: tuple[str, ...] = ()
    source: str = "pixel_layout"
    kind: str = "unknown"
    fingerprint: str = ""
    stable_frames: int = 0
```

`kind`는 의미 역할이 아니라 중립 레이아웃 분류만 허용한다.

허용 예:
- `unknown`
- `text_cluster`
- `container_candidate`
- `repeated_block`
- `chrome_candidate`

초기에는 `button`, `login_form`, `purchase_card` 같은 의미 라벨을 pixel-only rule로 만들지 않는다.
구조 source가 실제 role을 제공하면 `UIElement.role`에 유지한다.

## 3. UIElement 유지/확장 원칙

기존 `ElementRelations`의 필드를 최대한 재사용한다.

이미 존재:
- parent
- label_for
- same_row/same_column
- above/below
- left_of/right_of
- contains/overlays
- modal_owner
- scroll_container
- repeated_group

따라서 relation enum을 새로 대량 추가하기보다 `relations.py`가 위 필드를 일관된
threshold로 계산하도록 한다.

추가가 실제로 필요한 관계가 발견되면 스키마를 먼저 확장하고 JSON schema 동기화 테스트를
추가한다.

## 4. SceneIndex

인덱스는 우선 runtime helper로 두며 Scene 직렬화 계약에 즉시 넣지 않는다.

신규 후보: `hpcu/scene_graph/index.py`

```python
class SceneIndex:
    def by_role(self, role: str) -> tuple[str, ...]: ...
    def by_text_token(self, token: str) -> tuple[str, ...]: ...
    def inside(self, bbox: BoundingBox) -> tuple[str, ...]: ...
    def near(self, bbox: BoundingBox, radius_px: float) -> tuple[str, ...]: ...
    def right_of(self, element_id: str) -> tuple[str, ...]: ...
```

### 4.1 role index

```text
normalized_role -> tuple[element_id]
```

Scene마다 한 번 구축하며 immutable lookup table로 유지한다.

### 4.2 text index

초기에는 복잡한 검색 엔진을 넣지 않는다.

- Unicode casefold
- whitespace normalization
- OCR Hangul normalization 재사용
- token → element ids inverted index

substring/유사도 scoring은 candidate scoring 단계에서 수행한다.

### 4.3 spatial index

1차 구현은 **uniform grid**를 사용한다.

이유:
- 화면 크기가 제한적임
- 구현/검증이 단순함
- dirty ROI 업데이트가 쉬움
- R-tree보다 dependency와 유지비가 낮음

예:

```text
screen 1920x1080
cell 128x128
bbox가 겹치는 cell에 element id 등록
```

질의 후보 수를 줄인 뒤 정확한 bbox predicate를 수행한다.

R-tree 도입 조건:
- 실측에서 element 수가 충분히 커서 grid cell 후보 폭증
- relation query p95가 목표를 넘음
- benchmark로 개선이 증명됨

## 5. 관계 계산

신규 후보: `hpcu/scene_graph/relations.py`

### 기본 geometry

- containment ratio
- bbox center
- horizontal/vertical gap
- overlap ratio
- normalized distance
- alignment tolerance

### 관계 결정 예

```text
contains:
  child area의 >= configured_containment_ratio가 parent 안에 존재

same_row:
  center_y 차이 <= row_tolerance 또는 vertical overlap >= threshold

right_of:
  candidate.left >= anchor.right - tolerance
  + vertical overlap gate

above/below:
  horizontal overlap gate + vertical order
```

모든 threshold는 `config/runtime-config.yaml`에서 온다.

## 6. 관계 폭발 방지

N개 객체의 모든 pair 관계를 만들면 O(N²)이다. 다음 순서로 제한한다.

1. spatial grid로 근처 후보만 생성
2. contains는 bbox area hierarchy로 큰 영역 후보만 비교
3. left/right/above/below는 같은 row/column 근처 k개만 기록
4. relation tuple에 모든 객체를 넣지 않고 가장 가까운 bounded N개만 저장

config 후보:

```yaml
perception:
  relation_neighbor_limit: 8
  relation_grid_cell_px: 128
  containment_ratio: 0.90
  row_tolerance_px: 10
  column_tolerance_px: 10
```

실제 키 이름은 구현 시 naming/config 검토 후 확정한다.

## 7. index freshness

SceneIndex는 반드시 특정 `scene_version`에 결합된다.

```python
@dataclass(frozen=True)
class IndexedScene:
    scene: Scene
    index: SceneIndex
```

다른 version의 Scene에 index를 재사용하면 즉시 오류로 처리한다. stale index로 빠른 검색을
하는 것보다 재구축이 낫다.

## 8. 검증

- brute-force query 결과 == indexed query 결과
- 동일 Scene에서 index build deterministic
- SceneDelta 후 removed element가 index에서 사라짐
- bbox coordinate space가 다르면 관계 계산하지 않음
- hidden/occluded 객체의 executable candidate 처리 규칙은 Grounder와 동일하게 유지
- 1k synthetic element에서도 relation/index build 시간을 benchmark artifact로 기록
