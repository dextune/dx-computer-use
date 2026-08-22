---
title: "HPCU Runtime 개발 계획 05 — Scene Graph 및 객체 추적"
version: "1.0"
date: "2026-08-21"
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