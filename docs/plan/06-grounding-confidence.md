---
title: "HPCU Runtime 개발 계획 06 — Grounding·Confidence·AI 승격 정책"
version: "1.0"
date: "2026-08-21"
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