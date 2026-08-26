---
title: "Screen Understanding Engine 07 — Verified Experience Cache·Replay"
version: "1.1"
date: "2026-08-26"
parent: "docs/plan/20-screen-understanding-engine/index.md"
language: "ko-KR"
---

# 07. Verified Experience Cache·Replay

## 1. 목적

같은 환경에서 같은 종류의 작업을 반복할 때 매번 처음처럼 검색하지 않도록 한다. 단,
좌표 매크로를 만드는 것이 아니라 **검증된 구조 패턴을 재사용하는 것**이 목표다.

## 2. 저장 금지

다음은 experience로 저장하지 않는다.

- absolute click coordinate
- raw password/secret
- 로그인 credential
- 실패 trajectory를 성공 힌트로 승격
- 한 번의 성공만으로 active rule
- 사이트/CTA 전역 사전

## 3. 저장 후보

검증된 transition에서 다음을 추출한다.

```text
surface identity
plan node signature
target query signature
successful source ensemble
role/text fingerprint
parent/region relation fingerprint
selector ensemble
verification evidence contract
observed latency
success count
last verified version/time
```

text 원문이 민감할 수 있는 값은 저장 정책을 별도로 적용하며 trace/storage의 secret 금지 원칙을
따른다.

## 4. GroundingHint

신규 DTO를 바로 스키마에 박기 전에 workflow/trace 경계에서 필요한 최소 필드를 먼저 정의한다.

개념:

```python
GroundingHint(
    surface_fingerprint=...,
    target_signature=...,
    preferred_roles=(...),
    source_types=(...),
    relation_signature=...,
    selector_ensemble=(...),
)
```

이 hint는 실행 target이 아니다. fresh Scene의 candidate rank를 좁히는 입력이다.

## 5. retrieval

순서:

```text
current surface fingerprint
 + plan node target signature
 → qualified hint lookup
 → fresh Scene candidate generation
 → hint-compatible candidates boost/filter
 → normal confidence gate
```

hint가 맞지 않으면 일반 grounding으로 돌아간다. 좌표 fallback은 없다.

## 6. qualification lifecycle

기존 workflow compiler 원칙과 맞춘다.

```text
verified transition
 → repeated success
 → experimental hint
 → offline replay
 → qualified hint
 → online fresh verification
 → active
```

드리프트/false completion/selector failure 발생 시 즉시 downgrade한다.

## 7. drift score

드리프트 feature:
- source type 변화
- role 변화
- parent/region 변화
- text fingerprint 변화
- bbox layout class 변화
- verification failure rate

좌표 이동만으로 drift라고 보지는 않는다. re-ground가 정상적으로 성공하면 structural identity가
유지된 것으로 본다.

## 8. 저장소

새 DB를 만들기 전에 기존 `hpcu/trace/storage.py` SQLite 경계를 재사용할 수 있는지 검토한다.

원칙:
- trace와 experience schema는 논리적으로 분리
- migration/version 명시
- bounded retention
- corrupt entry 하나가 runtime boot를 막지 않음

## 9. replay

offline replay도 online runtime과 같은 Grounder/Verifier를 사용한다.

검증:
- cached hint 사용 시 model call 0
- viewport 이동 → fresh re-ground
- locale/text drift → hint miss 또는 downgrade
- stale selector → 실행 금지
- coordinate-only replay 0

## 10. 성능 목표

Experience Cache가 성공하면 반복 작업에서 줄어야 하는 것은:

- candidate generation 폭
- OCR 영역
- semantic call
- grounding latency

오히려 hint lookup 비용이 일반 grounding보다 큰 경우 active path에서 제거한다.

## 11. 구현 상태 — 2026-08-26

SUE-7 구현은 다음 파일로 닫는다.

- `hpcu/trace/experience.py` — coordinate-free `GroundingHint`, repeated-success lifecycle,
  offline fresh-grounding parity qualification, drift downgrade, mandatory fresh re-ground
- `hpcu/trace/storage.py` — versioned/bounded SQLite experience table, corrupt-row isolation
- `hpcu/trace/replay.py` — `ExperienceReplayEngine`, coordinate replay 없이 cache replay 경계 제공
- `tests/unit/test_experience_cache.py` — 좌표/`element_id` 저장 거부, bounded retention,
  corrupt-row boot isolation, replay parity, fresh-scene activation, drift fallback 검증

실행 불변식은 코드로 강제한다. qualified hint도 실행 좌표나 과거 `element_id`를 반환하지 않고,
현재 `Scene.version`에 속한 후보를 좁힌 뒤 normal `Grounder.resolve()`를 다시 호출한다.
