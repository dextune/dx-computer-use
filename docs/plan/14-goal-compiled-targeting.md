---
title: "HPCU Runtime 개발 계획 14 — 목표 편찬 Targeting Pack"
version: "1.0"
date: "2026-08-22"
parent: "AGENTS.md, docs/plan/06-grounding-confidence.md, docs/plan/08-model-gateway.md"
language: "ko-KR"
---

# 14. 목표 편찬 Targeting Pack

사이트·CTA·봇·동의 문자열을 코드나 케이스 YAML에 넣지 않는다.
사용자 목표마다 MiniMax M3가 **이번 요청의 어휘 팩**을 편찬하고,
공통 루프는 그 팩과 기하만 본다.

진행 체크는 `docs/plan/tasklist.md`의 **T-*** 항목.
EvidenceContract 종류는 02 / `hpcu/schemas/evidence.py`가 소유한다.
Gateway·MiniMax 전송은 08이 소유한다. 이 문서는 **편찬 계약과 matcher**만 소유한다.

---

## 1. 한 줄 진단

> 케이스가 무한이면 키워드 사전도 무한이다.
> 공통 런타임에 `쿠팡`/`구매하기`/`Accept all`을 넣는 순간 사이트 스크립트가 된다.

틀린 대응: 네이버가 막히면 구글로 바꾸고 구글 토큰을 YAML에 적는다.
맞는 대응: **어떤 사이트든** 목표 문장 → 예측 토큰 셋 → 제네릭 매칭.

---

## 2. 모델 호출 시점

AGENTS 불변식 “정상 경로는 0 call”은 **실행 루프**다.

| 시점 | 호출 | 하는 일 | 하지 않는 일 |
|---|---|---|---|
| plan-time | 태스크당 MiniMax-M3 **≤1** | TargetingPack JSON 편찬 | 클릭, 좌표, `element_id`, 상품 선택 |
| run-time | 기본 **0** | pack 토큰으로 관찰·픽·증거 | 사이트 사전 조회 |
| grounding miss | 예산 내 추가 1 | `{"target_id": "..."}` 만 | 새 사이트 사전 |

감독(Grok)은 상품·클릭을 고르지 않는다.

---

## 3. 남길 것 / 지울 것

**남긴다 (도메인 무관 알고리즘)**

- OCR, 줄 묶기, Hangul 음절 사이 공백 정규화
- 창 bbox, chrome을 **창 높이 비율**로 자르기
- 창 선택은 **면적·종횡비** (브랜드 제목 목록 아님)
- Grounder 점수식, Executor, FrameStore
- “팩의 토큰이 blob에 있으면 매칭” 제네릭 matcher

**지운다 (도메인 사전)**

- `BOT_HINTS` / `CONSENT_HINTS` / `_FORBID` / `BROWSER_HINTS` 브랜드
- `looks_like_product` · `looks_like_offer`의 `원`/`구매하기` 기본값
- 케이스 YAML의 `ocr_any` / `title_any` / 사이트 키워드
- 러너의 고객센터·TEL skip 리스트

테스트 fixture pack과 **사용자 goal 문자열**은 예외다. 프로덕션 `hpcu/` 상수에 사이트·CTA를 두지 않는다.

---

## 4. TargetingPack

`hpcu/schemas/targeting.py` — frozen dataclass. 사이트 enum 없음. 정규식 필드 없음
(모델 정규식은 예측 불가). 매칭은 정규화 부분문자열 + Grounder만.

| 필드 | 뜻 |
|---|---|
| `goal_id` | 이번 목표 id |
| `source` | `"model"` \| `"goal_tokens"` |
| `ready_any` | 페이지가 열린 신호 |
| `success_any` | 성공 증거. 비면 `ready_any`와 동일 |
| `forbid_any` | 보이면 실패 |
| `pick_query` | Grounder/OCR 매칭 구절 |
| `pick_required` | true면 요소를 하나 골라 클릭 |
| `dismiss_any` | 동의/닫기 후보 (이번 목표에서 예측) |
| `blocked_any` | 진행 불가 페이지 |
| `ignore_any` | 픽에서 제외 |

길이 한도는 `config/runtime-config.yaml` (토큰 개수, 토큰 글자 수). 코드에 매직 넘버 금지.

이미 있는 `EvidenceContract`와 합치지 않는다. 저것은 완료 조건 종류, 이것은 **이번 목표의 어휘**.

클릭·`element_id`·좌표 키가 JSON에 있으면 **폐기**.

---

## 5. TargetingCompiler

입력: 사용자 목표, 있으면 `start_url`. **화면 요약은 넣지 않는다** (아직 관찰 전).

프롬프트에 사이트 목록을 넣지 않는다. 물을 것은 “성공 시 화면에 보일 짧은 토큰,
고르면 안 되는 토큰, 클릭 대상 구절”뿐이다. 모델이 특정 브랜드를 쓰는 것은
목표 문장에 그 브랜드가 있어서다.

- 모델: MiniMax-M3 only (`CountingGateway` 강제).
- 응답: schema 검증 + thinking strip (08).
- 실패·timeout·스키마 불일치: `GoalFallbackTokenizer`. 실행은 계속.

### GoalFallbackTokenizer

모델이 죽어도 사이트 사전으로 퇴행하지 않는다. **목표 문자열만** 본다.

- 공백·구두점 분리, 2글자 이상
- `ready_any` = `success_any` = 그 토큰
- `pick_query` = 목표 원문
- `pick_required` 기본 true. navigate 직후 증거가 맞으면 클릭 생략
- 동사 사전(`고르`/`찾아` …)을 새로 만들지 않는다

---

## 6. GenericMatcher

`hpcu/perception/matcher.py`. pack만 받는다.

- `mentions(scene, tokens)` — OCR + window title blob, `normalize_ocr_text`
- `blocked` / `consent_candidates` / `pick_candidates`
- chrome 밴드는 기하만. `ignore_any`는 pack

창 선택: 가장 큰 클라이언트에 가까운 bbox. 전체화면 Desktop(디스플레이와 거의 동일)·납작한 패널은 종횡비로 걸러낸다. 쉘 창 제목 문자열 금지.

---

## 7. CaseRunner 루프

1. `pack = compiler.compile(goal, start_url)` — MiniMax ≤1 또는 fallback 0
2. 물리 탐색 (focus / omnibox / type URL / Enter). URL은 스펙 데이터
3. settle = `ready_any`가 blob에 나타나거나 timeout (`config`)
4. `blocked_any` → 재탐색 또는 halt. 우회 없음
5. `dismiss_any` 매칭 요소 클릭
6. 증거 충족이고 클릭이 필수가 아니면 성공
7. 아니면 `pick_candidates` → Grounder. miss 시 configured semantic provider의 `target_id` JSON만 (예산 내)
8. 클릭 후 fresh scene과 독립 verifier를 확인한다. 모델이 “완료”라고 해서 완료가 아님

케이스 YAML에 남는 것: `id`, `goal`, `start_url`, retry 예산.
`ocr_any` / `title_any` / 사이트 키워드 **금지**.

통계: `compile_call_count`와 `grounding_call_count`를 구분하고 합이 `minimax_call_count`.
모델 id는 항상 `MiniMax-M3`.

---

## 8. 구현 순서

한 묶음이 테스트로 닫히기 전에 다음으로 가지 않는다. 체크는 tasklist **T-***.

| ID | 내용 |
|---|---|
| T1 | `TargetingPack` + `GenericMatcher` + `GoalFallbackTokenizer`. 모델 0 |
| T2 | `TargetingCompiler` + CountingGateway. fake MiniMax. slug MiniMax-M3 |
| T3 | Runner가 pack만 사용. 프로덕션 사전 삭제. 기하는 유지 |
| T4 | 케이스 YAML은 goal+url+예산만. 네이버 전용 케이스 없음 |
| T5 | compile/grounding 횟수 분리. 시크릿 미누출 |

---

## 9. 테스트 계약

- 프로덕션 `hpcu/`에서 사이트·CTA 상수 검색 0건. fixture pack은 테스트 파일에만
- 동일 scene, pack만 바꾸면 매칭이 바뀜
- 목표 `"쿠팡에서 생수 골라줘"` fallback 토큰은 **목표에서 파생**. 코드 상수에 쿠팡 없음
- pack을 주입한 로컬 경로: MiniMax 0
- 깨진 JSON → fallback 후 실행 계속

---

## 10. 비목표

- CAPTCHA·로그인·결제 우회
- 사이트별 어댑터, CDP 상품 스크랩
- Grok이 상품·클릭을 고르는 것
- MiniMax 이외 모델
- 픽셀 VLM 승격 (06 기존 티어)

---

## 11. 리스크

- 모델이 엉뚱한 토큰을 편찬하면 로컬이 틀린 증거를 성실히 찾는다 → 길이 한도는 config, 고위험은 기존 policy
- plan-time 1회를 “모델이 매 클릭”으로 오해하지 말 것
- 브랜드 힌트 없는 창 선택 → 면적·종횡비. 실패 시 가장 큰 창
