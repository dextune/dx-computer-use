---
title: "HPCU Runtime 개발 계획 04 — CPU 영상처리·OCR 파이프라인"
version: "1.0"
date: "2026-08-21"
parent: "docs/dev-init-001.md (§8, §20.3)"
language: "ko-KR"
---

# 04. CPU 영상처리·OCR 파이프라인

## 1. 개요

Perception은 OS 무관. `hpcu/vision/` 아래에 구현된다.
실제 PaddleOCR/Tesseract/OpenCV는 Phase 3에서 통합.
현재는 fake/stub 구현으로 ABC 계약을 검증한다.

## 2. 모듈

| 모듈 | 파일 | 설명 |
|---|---|---|
| OCR | `hpcu/vision/ocr.py` | 텍스트 영역 검출 + 인식 |
| Shape | `hpcu/vision/shape_detector.py` | 사각형, 원, 선 등 형태 후보 |
| Template | `hpcu/vision/template_matcher.py` | 템플릿/특징점 매칭 |
| Association | `hpcu/vision/text_component_association.py` | 텍스트-컴포넌트 결합 |
| VLM stubs | `hpcu/vision/vlm_stubs.py` | SoM, crop, zoom, VLM selector (Phase 5) |

## 3. 처리 순서

```text
FrameHandle → ROI → OCR + Shape + Template → Association → Scene Graph
```

## 4. 최적화

- 전체 화면 OCR 금지. 변경 ROI만.
- 동일 ROI hash는 캐시.
- 저해상도 탐지 후 원본 crop 인식.
- 고정 sleep 금지. settle detector로 대기.