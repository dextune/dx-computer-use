---
title: "HPCU Runtime 개발 계획 00 — 프로젝트 개요 및 목표"
version: "1.1"
date: "2026-08-21"
parent: "docs/dev-init-001.md (§1~4, §31~32)"
language: "ko-KR"
---

# 00. 프로젝트 개요 및 목표

## 1. 문서군 안내

본 문서는 `docs/dev-init-001.md`(개발 기획안 v0.1)를 구현 가능한 수준으로 상세화한
개발 계획 문서군의 인덱스이며, 프로젝트의 목표와 범위를 확정한다.

| 문서 | 제목 | 원본 근거 섹션 |
|---|---|---|
| `00-overview-and-goals.md` | 프로젝트 개요 및 목표 (본 문서) | §1~4, §31~32 |
| `01-system-architecture.md` | 시스템 아키텍처 및 런타임 구조 | §5, §18, §20, §23~24 |
| `02-schemas-and-coordinates.md` | 데이터 스키마 및 좌표 시스템 | §9, §11, §14~15, §29 |
| `03-observation-layer.md` | 관찰 계층 (Capture·Structure·Perception·Input) | §6~7, §13, §15 |
| `04-vision-ocr-pipeline.md` | CPU 영상처리·OCR 파이프라인 | §8, §20.3 |
| `05-scene-graph-tracking.md` | Scene Graph 및 객체 추적 | §9~10 |
| `06-grounding-confidence.md` | Grounding·Confidence·AI 승격 정책 | §11~13, §16, §12 |
| `07-action-dsl-runtime.md` | Action DSL·실행기·검증기·Recovery | §14~15 |
| `08-model-gateway.md` | 모델 게이트웨이 및 MiniMax M3 연동 | §16~17 + minimax-m3-api-spec.md |
| `09-workflow-compiler.md` | 워크플로우 컴파일러·경험 축적 | §19 |
| `10-safety-security.md` | 안전·보안·정책 엔진 | §28 |
| `11-quality-benchmark.md` | 평가·벤치마크·성능 SLO·개발 도구 | §20.5, §26~27 |
| `12-roadmap-phases.md` | 개발 로드맵 (Phase 0~8) | §25, §32 |
| `13-common-pipeline-remediation.md` | 공통 파이프라인 개선 (루프·delta·Grounder) | 코드–문서 검수 |
| `14-goal-compiled-targeting.md` | 목표 편찬 Targeting Pack | 목표마다 MiniMax가 어휘 팩 편찬 |

진행 관리는 `docs/plan/tasklist.md`에서 체크박스 방식으로 수행한다.

### 1.1 문서 역할 — 한 주제는 한 문서가 소유한다

문서는 **우선순위로 충돌을 해결하지 않는다.** 같은 표·같은 계약이 두 곳에
있으면 한쪽이 stale인 것이다. 고칠 때는 소유 문서만 고치고 나머지는 링크로 둔다.

| 주제 | 소유 (여기만 수정) | 나머지는 |
|---|---|---|
| 가치·불변식 한 줄 | `AGENTS.md` | plan에 풀어 쓴 것과 **같은 뜻**이어야 함. 새 규칙 발명 금지 |
| 왜 그렇게 결정했는가 | `docs/decisions/*.md` | 스펙 본문은 plan. ADR에 API를 늘리지 않음 |
| 목표·비목표·용어·문서 맵 | `00-overview-and-goals.md` (본 문서) | — |
| 런타임 모듈, ABC 위치, repo | `01-system-architecture.md` | 03이 관찰 상세를 가짐 |
| 캡처·구조·인식·입력, capability matrix | `03-observation-layer.md` | 01은 링크만 |
| 스키마·좌표 | `02-schemas-and-coordinates.md` (없으면 코드 `hpcu/schemas/`) | 표 복사 금지 |
| Action DSL·실행·검증 | `07-action-dsl-runtime.md` (없으면 `dev-init-001.md` §14~15) | — |
| 이름 | `docs/rules/naming-conventions.md` | — |
| 테스트·마커 | `docs/rules/testing-standards.md` | 03은 관찰 테스트만 링크 |
| 할 일 | `docs/plan/tasklist.md` | 설계를 여기서 바꾸지 않음 |
| 공통 파이프 부채 해소 순서 | `13-common-pipeline-remediation.md` | 스펙은 01·03·06·07. 여기엔 순서만 |
| 목표 편찬 TargetingPack·matcher | `docs/plan/14-goal-compiled-targeting.md` | 06/08에 토큰 목록 복제 금지 |
| 원안(역사) | `docs/dev-init-001.md` | 새 결정의 집이 아님. plan이 생긴 주제는 plan을 고친다 |

`dev-init-001.md`는 기획 원안이다. 구현 계약이 바뀌면 **plan을 고치고**,
원안 해당 절은 “plan 문서 참고”로 줄이거나 같은 문장으로 맞춘다.
plan과 원안이 다르면 plan이 틀린 게 아니라 **원안이 갱신되지 않은 것**이다.

---

## 2. 프로젝트 한 줄 정의

> **HPCU Runtime — High-Performance Computer Use Runtime**
> 사용자 자연어 목표를 받아 GUI를 로컬에서 결정론적으로 관찰·실행·검증하고,
> AI는 계획과 모호성 해소에만 개입하는 로컬 실행 계층.

아키텍처 슬로건: **Model-last, Pixels-last, Verification-first**

핵심 명제: 이 시스템은 매 클릭을 AI가 결정하는 시스템이 아니라,
**AI가 결정론적 프로그램을 지휘하고 예외만 해석하는 시스템**이다.

---

## 3. 제품 정의

HPCU Runtime은 다음을 수행하는 로컬 실행 계층이다.

1. 현재 GUI 상태를 여러 데이터 소스(API/CLI → DOM/접근성 → 픽셀)에서 관찰.
   픽셀 **획득**과 입력 **주입**은 OS 플러그인, 픽셀 **이해**는 공통 Perception
2. 화면 요소를 통합된 UI Scene Graph로 변환
3. 의미 조건(자연어 target query)을 실제 UI 요소 ID와 연결(Grounding)
4. 결정론적 Action DSL 실행
5. 결과를 화면 상태 및 외부 효과로 검증(Verifier)
6. 모호성에만 AI를 호출(Model Router)
7. 성공 경로를 재사용 가능한 워크플로우로 컴파일(Workflow Compiler)

---

## 4. 목표 (Goals)

| # | 목표 | 측정 기준 |
|---|---|---|
| G1 | 정상 경로에서 모델 호출 없이 수십 개 GUI 동작 연속 실행 | 컴파일된 워크플로우 모델 호출 0회 |
| G2 | 모델 한 번 판단으로 여러 로컬 단계 수행 | calls/task 최소화 (§26 지표) |
| G3 | 변경 영역만 증분 분석 | delta scene update p95 ≤ 150ms (1080p) |
| G4 | 절대 좌표가 아닌 재탐색 가능한 객체 참조 사용 | stale action 실행 0회 |
| G5 | AI 응답 지연 중에도 화면 재검증 후 실행 | MODEL_DECISION_STALE 거부율 100% |
| G6 | 반복 작업의 점진적 0-model-call 승격 | compiled replay success rate |
| G7 | 브라우저·Windows·Linux·macOS·원격을 동일 Action DSL로 조작 | 플랫폼별 adapter 통과율, capability 명시 거절 |

## 5. 비목표 (Non-Goals)

초기 버전에서 다음을 목표로 하지 않는다.

- 모든 앱/화면을 완벽히 이해하는 범용 시각 모델
- 게임 수준 실시간 연속 제어
- CAPTCHA·보안 확인·접근 통제 우회 (사람에게 넘김)
- 모델의 "완료했습니다" 선언만으로 작업 완료 인정
- 한 번의 성공을 검증 없이 자동 규칙으로 승격
- 모든 OS 플러그인을 첫 버전에서 동시 완성 (Browser+Windows 먼저.
  Linux/macOS/Remote는 **같은 ABC**에 나중에 등록. 아키텍처를 OS 수만큼 복제하지 않음)
- 픽셀 좌표를 영구 저장해 그대로 재생하는 단순 매크로

---

## 6. 설계 가치관 (원칙)

### 6.1 모델은 엔진이 아니라 비싼 인터럽트다

모델 호출 비용: 네트워크/추론 지연, 이미지 인코딩, 이력 재전송, 불필요한 재계획,
stale-decision 문제. 따라서 모델은 다음 경우에만 호출한다.

- 처음 접한 목표를 작업 DAG로 분해할 때
- 로컬 후보가 둘 이상으로 모호할 때
- 아이콘/그래픽 의미를 전통 알고리즘이 판단하지 못할 때
- 기존 복구 정책으로 실패를 해결하지 못할 때
- 사용자 의도를 추가 해석해야 할 때

### 6.2 가장 빠른 GUI 조작은 GUI를 사용하지 않는 것이다

허가된 API/CLI/DOM 조작이 가능하면 그것이 우선이다.
(예: 파일명 변경은 파일 API, 폼 입력은 Playwright locator, Windows 버튼은 UIA Invoke)

### 6.3 속도보다 잘못된 고속 실행이 더 위험하다

```text
성능 = 성공률 × 검증 가능성 ÷ (지연 + 모델 호출 비용 + 복구 비용)
```

Confidence가 낮으면 빠르게 실행하지 않는다.

### 6.4 성공은 모델의 문장이 아니라 증거로 판단한다

완료 인정 조건(하나 이상): 예상 UI 상태 출현 / 대상 요소 상태 속성 변경 /
독립 외부 효과 확인(파일·DB·API) / 명시적 UI 증거(배지·저장 표시·성공 알림).

### 6.5 화면은 달라도 런타임은 하나다

픽셀 획득과 입력 주입만 OS 플러그인이다. 이해(Perception)·Grounding·DSL·검증은 공통이다.
Linux 호스트, Docker 리눅스, Windows, macOS, VNC를 위해 제어 루프를 복제하지 않는다.
상세: `03-observation-layer.md`, ADR `001-three-layer-platform`.

---

## 7. MVP 성공 기준 (제품 출시 판단 기준)

MVP는 다음 7가지를 증명해야 한다.

1. 사용자 자연어 목표를 한 번 계획
2. 브라우저 또는 Windows 앱에서 10개 이상 action을 모델 재호출 없이 실행
3. 각 action의 결과를 로컬에서 검증
4. 접근성 실패 구간은 OCR/Scene Graph로 해결
5. 모호한 한 단계만 소형 모델에 넘김
6. 성공 trajectory를 다음 실행에서 모델 호출 없이 재생
7. UI가 바뀌면 추측하지 않고 재탐색 또는 halt

---

## 8. LLM/VLM 공급자 전략 (MiniMax M3)

본 프로젝트의 Tier 2 텍스트 LLM은 **MiniMax M3**(`docs/minimax-m3-api-spec.md`)를
1차 공급자로 사용한다. 핵심 제약:

- OpenAI Chat Completions와 부분 호환. 응답 `content`에 `<think>...</think>`가
  직렬화되므로 **strip state machine이 필수** (`08-model-gateway.md` §5).
- `reasoning_effort: "none"`이 무시됨 → 추론 off 불가, strip으로 대응.
- tool calling / multimodal(image) 입력은 **미검증** → Phase 4에서 검증 태스크 수행.
- 검증 실패 시 provider-independent adapter를 통해 다른 공급자로 교체 가능해야 함.

상세 사양과 구현 계획은 `08-model-gateway.md`에서 다룬다.

---

## 9. 용어집

| 용어 | 정의 |
|---|---|
| Scene Graph | DOM/접근성/OCR/비전 결과를 통합한 UI 객체 그래프 |
| UIElement | Scene Graph의 노드. 고유 ID와 속성·관계를 가짐 |
| scene_version | Scene Graph의 단조 증가 버전. stale 판정에 사용 |
| Grounding | 자연어/구조화 조건을 실제 요소 ID로 연결하는 행위 |
| SoM (Set-of-Mark) | 화면 후보에 번호 overlay를 표시해 VLM이 선택하게 하는 기법 |
| Evidence Contract | 작업 완료를 입증하는 조건 집합 (선언적 JSON) |
| Workflow Compiler | 성공 trajectory를 재사용 가능한 결정론 워크플로우로 변환 |
| Stale Decision | 모델 응답 도착 시점에 화면이 바뀌어 무효가 된 결정 |
| Loop Breaker | 동일 실패 반복을 감지해 복구 절차를 강제하는 모듈 |
| Tier 0~5 | 결정론 → 텍스트 LLM → 소형 VLM → 대형 VLM → 사람 승격 단계 |
| Selector Ensemble | 한 요소에 대해 여러 종류의 selector를 함께 보관하는 전략 |
| Dirty ROI | 캡처 프레임에서 실제로 변경된 영역 |

---

## 10. 문서 유지 규칙

- 각 Phase 종료 시 **소유 문서**의 가정·수치를 실측값으로 교체한다. 요약본에 숫자를 다시 쓰지 않는다.
- 표를 두 문서에 붙여 넣지 않는다. 필요하면 링크.
- 주요 설계 결정은 ADR로 `docs/decisions/`에 기록한다. 결정의 *이유*만 남기고 스펙은 plan.
  3층 분리: `docs/decisions/001-three-layer-platform.md` → 본문은 `03-observation-layer.md`.
- MiniMax API 사양은 `docs/minimax-m3-api-spec.md`가 소유. `08-model-gateway.md`는 연동만.
- 모든 파일명은 kebab-case 소문자 (`docs/rules/naming-conventions.md`).

## 11. 프로젝트 규칙 문서

| 문서 | 내용 |
|---|---|
| `docs/rules/naming-conventions.md` | 파일명, 디렉터리명, Python 식별자, JSON 키 네이밍 규칙 |
| `docs/rules/testing-standards.md` | 테스트 계층, 시뮬레이션/검증 프로세스, 커버리지 임계값, CI 게이트 |
| `AGENTS.md` | 에이전트·기여자가 지키는 방향과 불변식 (짧고 굵게) |
