---
title: "HPCU Runtime 개발 계획 02 — 데이터 스키마 및 좌표 시스템"
version: "1.0"
date: "2026-08-21"
parent: "docs/dev-init-001.md (§9, §11, §14~15, §29)"
language: "ko-KR"
---

# 02. 데이터 스키마 및 좌표 시스템

## 1. 스키마 소유

모든 스키마는 `hpcu/schemas/` 아래 Python dataclass로 정의된다.
JSON Schema 산출물은 `schemas/`. 코드가 권위 문서다.

## 2. 스키마 목록

| 모듈 | 핵심 타입 | 설명 |
|---|---|---|
| `failure_codes.py` | `FailureCode` | 기계 판독 가능 실패 코드 Enum |
| `capability.py` | `Capability` | `supported`/`degraded`/`unsupported` |
| `coordinates.py` | `BoundingBox`, `CoordinateSpace`, `ScreenPoint` | 좌표 공간과 bbox |
| `ui_element.py` | `UIElement`, `ElementState`, `ElementRelations`, `ElementSource` | Scene Graph 노드 |
| `scene.py` | `Scene`, `SceneDelta`, `FrameHandle` | 화면 스냅샷과 delta |
| `action.py` | `Action`, `ActionOp`, `Precondition`, `Postcondition`, `RetryPolicy` | Action DSL |
| `evidence.py` | `EvidenceContract`, `EvidenceCondition`, `EvidenceKind` | 완료 증명 |
| `trace.py` | `TraceRecord`, `TraceEventType` | 실행 추적 |

## 3. 좌표 시스템

모든 `BoundingBox`는 `space` 필드로 공간을 명시한다.

| 공간 | 설명 | 변환 |
|---|---|---|
| `screen_physical_px` | 모니터 물리 픽셀 | 기준 |
| `screen_logical_px` | DPI 독립 픽셀 | `physical_to_logical(dpi_percent)` |
| `monitor_local_px` | 모니터 기준 | `virtual_to_monitor_local(origin)` |
| `window_frame_px` | 창 테두리 포함 | OS별 |
| `client_area_px` | 창 내부 | OS별 |
| `viewport_px` | 브라우저 뷰포트 | `css_to_viewport(dpr)` |
| `css_px` | CSS 픽셀 | `viewport_to_css(dpr)` |
| `crop_local_px` | 크롭 영역 기준 | `to_crop_local(origin)` / `from_crop_local(origin)` |
| `normalized` | [0,1] 정규화 | bbox / 화면 크기 |

좌표 변환은 `hpcu/coordinates/transform.py` (3×3 행렬)와
`hpcu/coordinates/spaces.py` (공간 레지스트리)가 담당한다.

## 4. 불변식

- bbox는 `space`를 반드시 명시한다.
- scene_version은 단조 증가한다.
- FrameHandle에 bytes를 포함하지 않는다.
- Action은 좌표가 아닌 element_id를 참조한다.
- EvidenceContract는 `all` 또는 `any` 최소 하나의 조건을 가진다.