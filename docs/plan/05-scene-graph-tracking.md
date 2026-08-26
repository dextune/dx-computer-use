---
title: "HPCU Runtime 개발 계획 05 — Scene Graph 및 객체 추적"
version: "1.1"
date: "2026-08-26"
parent: "docs/dev-init-001.md (§9~10)"
language: "ko-KR"
---

# 05. Scene Graph 및 객체 추적

## 1. 개요

DOM, 접근성 트리, OCR, 영상처리 결과를 하나의 `Scene`으로 통합.
요소는 `UIElement`로 표현되며 `id`로 추적된다.

## 2. 모듈

| 모듈 | 파일 | 설명 |
|---|---|---|
| Builder | `hpcu/scene_graph/builder.py` | ObservationDelta → Scene 갱신 |
| Tracker | `hpcu/scene_graph/tracker.py` | 프레임 간 요소 매칭 |

## 3. 추적 매칭 우선순위

```text
Native stable ID exact match
→ structural fingerprint match
→ text + role + parent match
→ visual fingerprint match
→ IoU + neighborhood match
→ new object
```

## 4. 불변식

- scene_version은 단조 증가.
- 요소 id는 좌표가 아니라 fingerprint/relations로 유지.
- delta는 변경분만 전파.

## 5. Screen Understanding Engine 계약

SUE는 기존 immutable Scene fold 책임을 유지하면서 index와 temporal identity를 강화한다.

- `SceneIndex`의 indexed query와 brute-force reference query는 같은 executable 결과를
  내야 한다.
- source ref/fingerprint exact match를 먼저 사용하고, composite temporal cost는 impacted
  dirty ROI에서만 계산한다.
- source provenance와 relation을 보존하며 false merge보다 duplicate retention을 선호한다.
- 현재 `Scene.version`과 다른 element는 Grounder의 executable candidate가 될 수 없다.
- removed/stale object는 Scene과 index에서 함께 제거한다.
- 36-case held-out matrix는 indexed/reference parity와 stale candidate 배제를 release gate로
  사용한다.
