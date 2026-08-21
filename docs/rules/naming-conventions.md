---
title: "HPCU Runtime — 네이밍 컨벤션"
version: "1.0"
date: "2026-08-21"
language: "ko-KR"
scope: "project-wide, enforced"
---

# 네이밍 컨벤션

## 1. 기본 원칙

- 모든 이름은 **의도를 명확히** 드러내야 한다. 축약은 금지한다.
- 파일명·디렉터리명·식별자명은 일관된 규칙을 따라야 하며, 위반은 CI에서 검사한다.
- 네이밍은 이 문서에 명시되지 않은 패턴을 임의로 생성하지 않는다.

---

## 2. 파일 및 디렉터리 명명

### 2.1 강제: kebab-case 소문자

**모든 파일명과 디렉터리명은 kebab-case 소문자로 작성한다.**

```
# 허용
docs/rules/naming-conventions.md
hpcu/runtime_core/control_loop.py
hpcu/observation/browser_adapter.py
tests/unit/test_grounder_candidate_scoring.py

# 금지
docs/rules/NamingConventions.md        ← CamelCase 금지
hpcu/runtime_core/controlLoop.py       ← camelCase 금지
hpcu/observation/browser-adapter.py    ← 하이픈 금지
hpcu/Observation/BrowserAdapter.py     ← PascalCase 디렉터리 금지
tests/unit/test_grounder_candidate_scoring.py  ← snake_case 사용
```

### 2.2 예외

하단의 파일·디렉터리만 컨벤션에서 제외한다:

| 파일/디렉터리 | 사유 |
|---|---|
| `README.md` | 생태계 표준 |
| `LICENSE` | 생태계 표준 |
| `.env` | 생태계 표준 |
| `.gitignore` | Git 규약 |
| `__init__.py` | Python 규약 |
| `pyproject.toml` | Python 규약 |
| `Dockerfile` | 생태계 표준 |
| `Makefile` | 생태계 표준 |
| `AGENTS.md` | 에이전트 엔트리포인트 규약 |
| `.github/` 하위 모든 파일 | GitHub Actions 규약 |

---

## 3. Python 코드 네이밍

### 3.1 모듈/패키지

| 대상 | 규칙 | 예시 |
|---|---|---|
| 패키지 디렉터리 | snake_case | `runtime_core/`, `scene_graph/`, `platform/windows/` |
| 모듈 파일 | snake_case | `control_loop.py`, `browser_adapter.py`, `capture_backend.py` |
| 테스트 모듈 | `test_` prefix + snake_case | `test_control_loop.py` |

### 3.2 클래스

| 대상 | 규칙 | 예시 |
|---|---|---|
| 클래스 | PascalCase | `Observer`, `SceneGraph`, `BrowserAdapter` |
| ABC (추상 기반 클래스) | PascalCase, suffix 없음 | `Observer`, `Grounder`, `Verifier`, `CaptureBackend`, `InputInjector` |
| 구현 클래스 | PascalCase + 구체적 명칭 | `BrowserObserver`, `Win32UIAObserver`, `LinuxX11Capture`, `MacosCgEventInjector` |
| 예외 클래스 | PascalCase + `Error` suffix | `ObservationError`, `GroundingError`, `StaleDecisionError` |
| 데이터 클래스 (DTO) | PascalCase, 동사형 금지 | `FrameHandle`, `SceneDelta`, `UIElement`, `CaptureCapabilities`, `InputCapabilities` |

### 3.3 함수/메서드

| 대상 | 규칙 | 예시 |
|---|---|---|
| public 함수 | snake_case | `observe()`, `resolve_target()`, `update_scene()` |
| private 함수 | `_` prefix + snake_case | `_parse_raw_tree()`, `_compute_hash()` |
| 콜백/핸들러 | `on_` prefix + snake_case | `on_frame_ready()`, `on_scene_update()` |
| 팩토리 함수 | `create_` prefix + snake_case | `create_observer()`, `create_executor()` |

### 3.4 변수/상수

| 대상 | 규칙 | 예시 |
|---|---|---|
| 지역 변수 | snake_case | `element_id`, `scene_version`, `delta` |
| 인스턴스 속성 | snake_case | `self.max_retries`, `self.confidence_threshold` |
| 모듈 레벨 상수 | UPPER_SNAKE_CASE | `MAX_FRAME_QUEUE_SIZE`, `DEFAULT_TIMEOUT_MS` |
| Enum 멤버 | UPPER_SNAKE_CASE | `Tier.TEXT_LLM`, `ActionType.LEFT_CLICK` |

---

## 4. JSON Schema / 설정 파일 네이밍

| 대상 | 규칙 | 예시 |
|---|---|---|
| 스키마 파일 | kebab-case + `.schema.json` | `ui-element.schema.json`, `scene.schema.json` |
| 설정 파일 | kebab-case + `.yaml` | `runtime-config.yaml`, `slo-thresholds.yaml` |
| 워크플로우 파일 | kebab-case + `.workflow.json` | `login-gmail.workflow.json` |
| 필드 키 | snake_case | `"element_id"`, `"scene_version"`, `"confidence"` |
| Enum 값 | snake_case | `"left_click"`, `"text_input"`, `"key_press"` |

---

## 5. 금지 패턴

아래 패턴은 어떤 대상에도 사용하지 않는다.

| 패턴 | 사유 |
|---|---|
| 헝가리안 표기법 (`bFlag`, `szName`) | 시대에 뒤쳐짐, IDE가 타입을 표시 |
| 약어/축약 (`obs`, `scn`, `grnd`, `exec`) | 의도 불명확. 전체 단어 사용 |
| 타입 인코딩 (`cls_`, `iface_`) | Python typing으로 충분 |
| 단일 문자 변수명 (`i`, `j`, `x` 제외 루프 카운터) | 가독성 저하 |
| `Data`, `Info`, `Manager`, `Handler` 같은 무의미 suffix | 구체적 역할 명시 |

---

## 6. 검증 도구

CI 파이프라인에서 다음 검사를 수행한다:

```bash
# 파일명 kebab-case 검사 (예외 목록 대조)
find . -type f -name '*.py' -o -name '*.md' -o -name '*.yaml' -o -name '*.json' \
  | grep -vE '(README|LICENSE|__init__|Dockerfile|Makefile|\.github/)' \
  | grep -E '[A-Z]' && echo "VIOLATION: non-kebab-case filename" && exit 1

# Python 코드 내 금지 패턴 린트
ruff check --select N8  # pep8-naming
```

---

## 7. 이 문서의 적용 범위

- 본 문서는 프로젝트 전체에 적용된다.
- 신규 파일·코드는 반드시 본 컨벤션을 따른다.
- 기존 코드의 위반 사항은 발견 즉시 수정한다. 점진적 마이그레이션 없음.
- 위반이 merge되면 책임자가 즉시 fix commit으로 교정한다.