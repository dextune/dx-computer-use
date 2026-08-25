---
title: "HPCU Runtime 개발 계획 20 — Screen Understanding Engine 고도화 인덱스"
version: "1.0"
date: "2026-08-25"
parent: "AGENTS.md, docs/plan/19-next-development-plan.md, docs/plan/03-observation-layer.md, docs/plan/04-vision-ocr-pipeline.md, docs/plan/05-scene-graph-tracking.md, docs/plan/06-grounding-confidence.md"
baseline_commit: "5cc33bd3e61966dae65d92ffebc518a08720a85b"
language: "ko-KR"
scope: "CPU-first screen structuring, scene graph, local grounding, model-last escalation"
---

# 20. Screen Understanding Engine 고도화

## 1. 문서 목적

이 문서군은 HPCU Runtime의 CPU/로컬 처리 계층을 하나의 명확한 제품 자산으로
고도화하기 위한 **실행 계획 인덱스**다.

핵심 목표는 스크린샷을 매번 AI에게 보내는 방식이 아니라, 가능한 구조 소스를 먼저
사용하고 픽셀이 필요한 구간에서는 CPU가 화면을 증분 구조화하여 공통 Scene Graph를
유지하는 것이다. 이후 Grounder는 PlanIR/TargetingPack이 이미 제공한 의미 목표를
Scene Graph의 실제 객체에 결정론적으로 연결하고, 모호성이 남을 때만 설정된 semantic
provider를 호출한다.

> **Structure-first → CPU structure → local grounding → verified execution → model-last.**

이 계획은 `19-next-development-plan.md`를 대체하지 않는다. 19번이
`CommandRuntime → VERIFIED_SUCCESS` 제품 실행 경로를 닫는 계획이라면, 20번은 그
경로에 공급되는 **고속 화면 구조화·추적·검색·로컬 매칭 엔진**을 강화한다.

## 2. 이 문서군의 소유 범위

이 문서군이 소유하는 것은 **고도화 목표, 구현 순서, 신규 모듈 경계, 성능/자격 게이트**다.
최종 계약이 확정되면 기존 소유 문서에 반영한다.

- Capture/Structure/Perception 최종 계약 → `03-observation-layer.md`
- OCR 최종 계약 → `04-vision-ocr-pipeline.md`
- Scene Graph/Tracking 최종 계약 → `05-scene-graph-tracking.md`
- Grounding/Confidence 최종 계약 → `06-grounding-confidence.md`
- 테스트 표준 → `docs/rules/testing-standards.md`
- 진행 체크 → `docs/plan/tasklist.md`

따라서 이 문서군에서 기존 스펙을 몰래 바꾸지 않는다. 구현이 검증된 뒤 소유 문서를
동기화한다.

## 3. 상세 문서

| 문서 | 소유 주제 |
|---|---|
| `01-product-scope-and-architecture.md` | 현재 코드 진단, 목표 아키텍처, 모듈 경계 |
| `02-scene-model-and-indexes.md` | Region/Scene 데이터 모델, 관계, 공간·텍스트 인덱스 |
| `03-segmentation-and-layout-pipeline.md` | 픽셀 구역 분할, 레이아웃 분석, dirty ROI 처리 |
| `04-fusion-and-temporal-tracking.md` | OCR/OS/DOM 객체 fusion, stable identity, temporal update |
| `05-local-grounding-and-ai-escalation.md` | 후보 생성·점수화·confidence/margin·AI 승격 계약 |
| `06-cpu-resource-and-performance.md` | CPU worker, queue, scheduling, profiling, SLO |
| `07-experience-cache-and-replay.md` | 검증된 경험 재사용, drift, coordinate-free replay |
| `08-testing-benchmark-qualification.md` | ground truth, benchmark, 회귀/E2E/held-out 자격 게이트 |
| `09-implementation-roadmap.md` | 구현 순서, 파일별 변경 지도, 완료 조건 |

## 4. 목표 아키텍처

```text
Structure sources (DOM/UIA/AT-SPI/AX) ───────────────┐
                                                     │
Capture/FrameHandle → Change Detector → Pixel Layout ├→ Object Fusion
                                  │                  │
                                  └→ ROI OCR ────────┘
                                                         ↓
                                                  Temporal Scene Graph
                                                  ├─ role/text index
                                                  ├─ spatial index
                                                  ├─ relations
                                                  └─ source provenance
                                                         ↓
                                             deterministic local Grounder
                                                  ├─ confident → execute
                                                  └─ ambiguous → AI interrupt
                                                         ↓
                                                 Policy → Executor
                                                         ↓
                                                   fresh observation
                                                         ↓
                                                    Verification
```

중요: Structure source가 충분한 환경에서는 픽셀 분석을 억지로 수행하지 않는다.
반대로 VNC/remote처럼 구조 소스가 없는 환경에서는 픽셀 파이프라인이 동일한 Scene
계약을 채운다.

## 5. 절대 불변식

1. CPU Perception은 중립 관찰을 만든다. raw 화면에서 임의의 사용자 의도나 성공을
   결정하지 않는다.
2. 의미 목표는 PlanIR/TargetingPack/설정된 semantic provider에서 온다. 로컬 엔진은
   그 목표를 관찰 객체에 매칭할 뿐 새 의미를 발명하지 않는다.
3. 전체 스크린샷은 기본 AI 입력이 아니다. 구조화 top-k로 해결되지 않을 때만 별도
   이미지 경로를 검토한다.
4. 좌표를 경험으로 저장하지 않는다. fresh Scene의 object identity/selector/관계로
   매번 re-ground한다.
5. unchanged frame은 OCR과 레이아웃 재계산을 하지 않는 것이 목표다.
6. stale object는 실행하지 않는다. 모든 action은 현재 Scene version에 바인딩된다.
7. pipeline queue는 bounded이며 latest-state 우선이다.
8. CPU 사용률을 높이는 것 자체가 목표가 아니다. **actionable latency와 local resolution
   rate를 개선하는 것**이 목표다.

## 6. 제품 성공 지표

최종 성능 수치는 reference hardware baseline을 먼저 측정한 뒤 고정한다. 방향성
게이트는 다음과 같다.

| 지표 | 목표 |
|---|---|
| unchanged frame full OCR | 0회 |
| unchanged region rebuild | 0 |
| deterministic normal action model call | 0회 |
| repeated qualified workflow model call | 0회 |
| stale action execution | 0 |
| false completion | 0 |
| processed pixel ratio | 전체 화면 대비 지속 감소 |
| stable element identity retention | 지속 상승 |
| local grounding precision | semantic 승격 감소와 함께 유지/상승 |
| first actionable latency | baseline 대비 지속 감소 |

## 7. 구현 마일스톤

```text
SUE-0 Baseline/Profile
  ↓
SUE-1 Region + Index contracts
  ↓
SUE-2 Incremental Layout Segmentation
  ↓
SUE-3 Cross-source Object Fusion
  ↓
SUE-4 Temporal Identity/Delta Update
  ↓
SUE-5 Indexed Local Grounding + Confidence
  ↓
SUE-6 Structured AI Interrupt
  ↓
SUE-7 Verified Experience Cache
  ↓
SUE-8 Held-out Qualification + spec consolidation
```

`SUE-0~SUE-1`은 19번 플랜과 병행 가능하다. 실제 제품 E2E에 새 Scene 경로를 연결하는
작업은 19번의 fresh-observation handoff 계약과 충돌하지 않도록 integration test를 먼저
고정한 뒤 진행한다.

## 8. 첫 번째 개발 목표

첫 번째 가시적 결과는 거대한 범용 비전 엔진이 아니다. 아래 fixture를 CPU로 빠르게
구조화하는 vertical slice가 먼저다.

- 브라우저 창 1개
- content region
- textbox 1개
- button 1개
- result region 1개
- 구조 소스 있음/없음 두 버전
- 한 영역만 변하는 후속 frame

이 fixture에서 **전체 OCR → 구조화 → local grounding** baseline과
**dirty ROI → region update → indexed grounding**을 비교한다. 두 번째 실행에서 처리량이
실제로 줄어드는 것을 artifact로 증명해야 다음 단계로 간다.
