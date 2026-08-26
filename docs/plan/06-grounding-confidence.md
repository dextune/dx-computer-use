---
title: "HPCU Runtime 개발 계획 06 — Grounding·Confidence·AI 승격 정책"
version: "1.1"
date: "2026-08-26"
parent: "docs/dev-init-001.md (§11~13, §16, §12)"
language: "ko-KR"
---

# 06. Grounding·Confidence·AI 승격 정책

## 1. Grounding

자연어/구조화 target query를 실제 `UIElement.id`로 연결한다.

| 모듈 | 파일 | 설명 |
|---|---|---|
| Grounder | `hpcu/grounder/grounder.py` | resolve. 점수는 scoring 모듈만 호출 |
| Scoring | `hpcu/router/candidate_scoring.py` | **유일한** 후보 점수식. 임계값은 `config/runtime-config.yaml` |

## 2. Confidence 정책

| 조건 | 처리 |
|---|---|
| top-1 ≥ 0.88, margin ≥ 0.20 | 로컬 실행 |
| top-1 ≥ 0.72, margin 낮음 | 텍스트 LLM |
| OCR·role 충돌, 시각 의미 | 소형 VLM |
| 후보 없음 | 대형 VLM |
| 고위험 | confidence와 무관하게 승인 정책 |

## 3. AI 승격 Tiers

| Tier | 설명 | 모델 호출 |
|---|---|---|
| 0 | 결정론적 실행 | 0 |
| 1 | Scene Graph Resolver | 0 |
| 2 | 텍스트 LLM (MiniMax M3) | 1 |
| 3 | 소형 VLM + SoM | 1 |
| 4 | 대형 VLM + Coarse-to-Fine | 1 |
| 5 | 사람 승인 | 0 |

## 4. 모델 출력 규칙

- AI는 좌표가 아닌 `element_id`를 반환한다.
- 응답은 schema validation + stale check 통과 후에만 사용.
- `thinking` 태그는 strip state machine으로 제거.

## 5. Screen Understanding Engine 계약

- `Grounder.resolve()`는 `SceneIndex`로 후보를 줄이되 `resolve_reference()`와 executable
  결과가 동일해야 한다.
- relation/temporal stability는 점수 feature이며 confidence와 top1/top2 margin을 함께
  통과한 경우에만 local execute한다.
- 모호성은 structured semantic interrupt로 승격하며 AI 장애를 arbitrary click으로
  바꾸지 않는다.
- verified experience는 `hpcu/trace/experience.py`의 구조적 `GroundingHint`만 저장한다.
  absolute coordinate, bbox, 과거 `element_id`는 저장 금지다.
- qualified experience도 현재 Scene을 filter/rank하는 힌트일 뿐이다. 실행 전에는 반드시
  normal Grounder로 fresh re-ground한다.
- hint drift나 stale resolution은 즉시 downgrade하고 full fresh grounding 또는 halt로
  돌아간다. fixed-coordinate replay는 허용하지 않는다.
