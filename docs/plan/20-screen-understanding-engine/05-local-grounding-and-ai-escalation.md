---
title: "Screen Understanding Engine 05 — Local Grounding·AI Escalation"
version: "1.0"
date: "2026-08-25"
parent: "docs/plan/20-screen-understanding-engine/index.md"
language: "ko-KR"
---

# 05. Local Grounding·AI Escalation

## 1. 핵심 경계

이 문서에서 말하는 local grounding은 **사용자 문장을 CPU가 의미 해석한다는 뜻이 아니다.**

입력은 이미 의미가 정리된 다음 중 하나다.

- `TargetQuerySpec`
- PlanIR node target query
- TargetingPack의 compiled hint

CPU는 이 query를 현재 Scene의 객체에 매칭하고 신뢰도를 계산한다.

## 2. candidate generation

현재처럼 모든 element를 곧바로 scoring하지 않고 index로 후보를 축소한다.

순서:

```text
role constraint 있음 → role index
text token 있음      → text inverted index
region relation 있음 → spatial/relation index
intersection/union
 → executable visibility/state filter
 → scoring
```

후보가 너무 적으면 단계적으로 constraint를 완화하되, query에 없던 의미 token을 로컬에서
발명하지 않는다.

## 3. scoring V2

현재 text/role/source/structure weighted sum을 유지하고 relation/temporal feature를 추가한다.

```text
score =
  text_match
  role_match
  source_reliability
  structure_quality
  relation_match
  temporal_stability
```

weights는 config 기반이다.

### relation_match 예

Target query가 구조적으로 `inside`, `right_of`, `same_row` 같은 constraint를 명시한 경우만
사용한다. 자연어에서 CPU가 임의로 relation을 추출하지 않는다.

### temporal_stability

동일 target이 직전 fresh scene에서 안정적으로 존재했고 현재 scene에서도 source/geometry가
이어지면 작은 가점을 줄 수 있다. 이전 coordinate를 그대로 실행하는 것은 금지한다.

## 4. confidence와 margin

단순 top1 score만으로 실행 여부를 정하지 않는다.

```text
confidence = calibrated(top1_score, source mix, feature coverage)
margin = top1_score - top2_score
```

local execution gate 예:

```text
confidence >= local_threshold
AND margin >= ambiguity_margin
AND element scene_version == current scene
AND policy preconditions satisfied
```

실제 수치는 replay/fixture calibration으로 정한다.

## 5. calibration

처음부터 ML calibration model을 넣지 않는다.

1. fixture 결과에서 score bucket별 precision 수집
2. threshold/margin curve 작성
3. config 값 결정
4. 충분한 데이터가 쌓인 뒤 isotonic/logistic calibration 필요성을 검토

모델 추가보다 **잘못된 local click 0에 가까운 보수적 threshold**가 우선이다.

## 6. AI escalation 조건

설정된 semantic provider를 호출할 수 있는 경우:

- candidate 0이고 query semantics 자체가 더 필요함
- top candidates가 threshold 위지만 margin이 너무 작음
- 구조 source 간 의미 충돌이 있음
- local repair 후에도 동일 ambiguity가 남음

호출하지 않는 경우:

- 단순 OCR timeout/capture error → typed failure/retry
- stale Scene → fresh observe
- policy deny → approval/handoff
- repeated same action/same scene → loop breaker

## 7. AI 입력 최소화

기본 payload는 구조화 데이터다.

```json
{
  "goal_node": "search-submit",
  "target_query": {"text": "검색", "role": "button"},
  "scene_version": 42,
  "candidates": [
    {
      "element_id": "e1",
      "role": "button",
      "text": "검색",
      "confidence": 0.78,
      "relations": {"parent": "r7"},
      "sources": ["atspi", "ocr"]
    }
  ]
}
```

원본 전체 Scene을 덤프하지 않는다. top-k와 필요한 관계만 보낸다.

## 8. crop/image fallback

이미지가 반드시 필요한 semantic provider가 실제로 연결되는 경우에도 순서는 다음이다.

```text
structured top-k
 → candidate/region crop
 → wider context crop
 → full screenshot (last resort)
```

이미지 fallback은 별도 model-call purpose/telemetry로 구분하는 방향을 검토한다. 현재 provider
capability가 없으면 없는 기능을 숨기지 않고 `UNSUPPORTED`로 둔다.

## 9. stale response

AI가 candidate `e1`을 골라도 실행 전 다음을 확인한다.

- response가 기준으로 삼은 scene_version
- current scene_version
- `e1`이 current scene에 존재하는지
- current fingerprint/source continuity

불일치면 재실행하지 않고 re-ground/reanalysis한다.

## 10. 테스트

- unique DOM/AX target → model call 0
- unique OCR target → model call 0
- top1/top2 close → executor 0, escalation
- stale AI selection → executor 0
- no candidate + no semantic provider → typed failure
- AI provider timeout/schema error → arbitrary fallback click 0
- local repair success → semantic replan 0
- indexed candidate 결과 == brute-force scoring 결과
