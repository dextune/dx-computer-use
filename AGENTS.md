# HPCU Runtime

**Model-last. Pixels-last. Verification-first.**

매 클릭을 모델이 찍는 시스템이 아니다.
로컬 런타임이 관찰·실행·검증하고, AI는 계획과 모호성만 담당한다.

화면은 OS마다 달라도 런타임은 하나다.
Windows, Linux(호스트·Docker), macOS, 브라우저, VNC는 플러그인이다.

---

## 불변식

1. **모델은 비싼 인터럽트다.** 정상 경로는 0 call.
2. **픽셀은 최후 수단이다.** API → DOM/AX/UIA/AT-SPI → OCR/비전 → VLM.
3. **성공은 증거다.** 모델이 "완료"라고 해서 완료가 아니다.
4. **좌표를 저장해 재생하지 않는다.** 객체 ID와 selector ensemble.
5. **stale이면 실행하지 않는다.** 화면이 바뀌면 재탐색하거나 halt.
6. **없는 capability는 숨기지 않는다.** `UNSUPPORTED`를 반환한다.
7. **고위험은 승인 없이 안 된다.** 결제·삭제·권한.
8. **사이트·CTA 사전을 코드에 넣지 않는다.** 검출 셋은 목표마다 편찬한다. 원문: `docs/plan/14-goal-compiled-targeting.md`.
9. **의미 판단은 설정된 semantic provider만 한다.** 다른 LLM/VLM/provider, Grok, 로컬 휴리스틱은 target·상황·케이스 분류·성공·복구를 결정하지 않는다. 현재 기본 배포는 설정의 MiniMax-M3일 뿐이며, provider/model identity와 응답 schema가 맞지 않으면 fail closed 한다.
10. **화면 제어가 제품의 기본 경계다.** 브라우저·터미널·앱은 Capture → CPU Perception → Scene Graph → configured semantic provider Decision → Policy → `InputInjector.physical` → 재캡처 순서로 다룬다. DOM/CDP/API/직접 파일·stdout 검사는 화면 제어를 대신하지 않는다.
11. **CPU Perception은 중립 관찰만 만든다.** OCR, bbox, geometry, window structure, scene delta, hash는 허용하지만 “이것을 클릭”, “성공”, “CAPTCHA” 같은 의미 결론을 단독으로 만들지 않는다.
12. **모델 장애 시 꼼수를 쓰지 않는다.** configured semantic provider의 timeout·오류·schema 불일치·stale 응답이면 임의 target/fallback click을 실행하지 않고 `MODEL_FAILED`/`DECISION_REQUIRED`로 중단·재분석한다. Targeting fallback tokenizer는 어휘 생성에만 사용한다.
13. **행동 전후에 증거를 남긴다.** action 전 scene version을 검증하고 action 후 새 화면을 캡처·분석한다. 모델의 “완료” 문장이나 low-level click `ok`만으로 성공 보고하지 않는다.
14. **CAPTCHA·로그인·보안 확인은 우회하지 않는다.** solver, refresh 반복, alternate site/endpoint, credential 입력으로 접근 통제를 피하지 않고 `BLOCKED`/`HUMAN_HANDOFF`로 중단한다.
15. **터미널 AI 코딩은 격리된 화면 테스트일 뿐이다.** OpenCode는 terminal 안에서 사용자가 물리 입력으로 실행하도록 테스트할 수 있지만 HPCU의 semantic decision-maker가 아니다. sandbox/worktree 밖 파일·secret·destructive command를 사용하지 않는다.
16. **테스트 성공을 위해 케이스별 정답을 하드코딩하지 않는다.** goal/fixtures 이외의 site·CTA·OCR·좌표·광고·challenge 문자열, 정답 element ID를 production action path에 추가하지 않는다.
17. **개발 작업용 별도 Git 브랜치를 만들지 않는다.** 코드·문서·테스트 변경은 항상 `main`에서 직접 수행하고, 작업용 branch/worktree 생성·전환·푸시를 금지한다. 이미 존재하는 별도 브랜치의 변경이 있다면 새 브랜치를 만들지 말고 검증 후 `main`에 직접 통합한다.

---

## 레이어

```text
Capture (OS)  →  Perception (공통)  →  Scene Graph
Structure (OS) ↗                         ↓
                                   Executor → Input (OS)
```

- OS 코드는 `hpcu/platform/<os>/` 밖에 두지 않는다.
- `vision` / `scene_graph` / `runtime_core`는 `hpcu.platform`을 import하지 않는다.
- Capture와 Input은 같은 `session_id`의 같은 화면을 가리킨다.
- Docker+Xvfb는 새 OS가 아니다. Linux X11 플러그인이다.

상세: `docs/plan/01-system-architecture.md`, `docs/plan/03-observation-layer.md`

---

## 네이밍

원문: `docs/rules/naming-conventions.md` — **여기만 수정.** 아래는 요약.

| 대상 | 규칙 | 예 |
|---|---|---|
| docs/config/schema 파일 | kebab-case | `runtime-config.yaml`, `ui-element.schema.json` |
| Python 패키지·모듈 | snake_case | `hpcu/scene_graph/`, `browser_adapter.py` |
| 테스트 모듈 | `test_` + snake_case | `test_control_loop.py` |
| 클래스 / ABC | PascalCase, ABC는 suffix 없음 | `CaptureBackend`, `LinuxX11Capture` |
| 예외 | `Error` suffix | `StaleDecisionError` |
| DTO | PascalCase, 동사 금지 | `FrameHandle`, `SceneDelta` |
| 함수 | snake_case. private `_`, 콜백 `on_`, 팩토리 `create_` | `create_observer()` |
| 상수 / Enum 멤버 | UPPER_SNAKE_CASE | `MAX_FRAME_QUEUE_SIZE` |
| JSON 키 / enum 값 | snake_case | `"element_id"`, `"left_click"` |
| 워크플로우 파일 | kebab-case + `.workflow.json` | `login-gmail.workflow.json` |

강제:

- 이름은 의도를 드러낸다. **축약 금지** (`obs`, `scn`, `exec` 불가).
- 문서·설정 파일명에 CamelCase 금지. Python **모듈** 파일에는 하이픈 금지.
- 예외 파일: `README.md`, `LICENSE`, `AGENTS.md`, `Dockerfile`, `Makefile`, `pyproject.toml`, `.env`, `.gitignore`, `__init__.py`, `.github/`.
- `Data` / `Info` / `Manager` / `Handler` 같은 빈 suffix 금지.
- 이 문서에 없는 네이밍 패턴을 만들지 않는다.

---

## 코딩 룰

원문: `docs/plan/01-system-architecture.md` §2, §9. 스택은 Python 3.12+.

**모듈**

- 결합점은 ABC + `PluginRegistry`뿐. 모듈은 스스로 등록하지 않는다.
- 생성자 주입. 모듈 안에서 의존성을 `import`하거나 전역을 잡지 않는다.
- 레지스트리에서 빠져도 루프가 죽지 않는다. 없으면 skip/fallback.
- 모듈 경계 DTO만. 상대 모듈 내부 타입 import 금지. 순환 import 금지.
- 경계를 넘는 객체는 불변 (`frozen` dataclass).

**데이터 / 성능**

- 이미지는 `FrameHandle`(shm)만. bytes/base64 복사 금지.
- 채널은 bounded queue. 오래된 프레임 폐기, 최신 우선.
- Scene은 delta만 전파.
- 임계값·timeout·confidence는 `config/*.yaml`. 코드 하드코딩 금지.
- 사이트·브랜드·CTA·봇·동의 문자열 사전 금지. 목표 편찬 TargetingPack만. 원문: `docs/plan/14-goal-compiled-targeting.md`.
- 실행 루프 정상 경로 0 call. 목표 편찬은 태스크당 configured semantic provider ≤1 (plan-time).
- 고정 `time.sleep` 금지. settle detector / `wait_until`.

**플랫폼**

- native API는 `hpcu/platform/<os>/`만.
- 권한 거부를 XTest 등으로 우회하지 않는다.
- 시크릿은 `.env`. 커밋 금지. 로그에 키·비밀번호 금지.

**실행**

- action은 `policy.allow` 후에만.
- postcondition 검증 없이 commit 금지.
- 모델 응답은 schema 검증 + stale check 후에만 사용.

---

## 테스트

원문: `docs/rules/testing-standards.md`

- Given / When / Then. 기대 출력이 없으면 테스트가 아니다.
- 마커 필수: `unit` | `integration` | `replay` | `failure_injection` | `e2e` | `platform_*`
- 커버리지: 라인 ≥ 85%, 브랜치 ≥ 80%, 모듈 ≥ 70%, ABC 메서드 100%.
- PR 게이트: unit + integration + replay + failure_injection + fake platform + browser fixture.
- OS 실기(`platform_windows` 등)는 기본 required 아님.
- fake capture/input만 unit에서. 실제 DXGI/X11/CGEvent 호출 금지.
- 금지: `time.sleep`, unit에서 실네트워크, 테스트 간 상태 공유, `assert True`, `except: pass`.

---

## 작업 방식

- **항상 `main`에서 직접 작업한다. 별도 개발 브랜치·임시 브랜치·worktree를 만들거나 사용하지 않는다.**
- Git 작업 시작 전 현재 branch가 `main`인지 확인하고, 아니라면 새 branch를 만들지 말고 `main`으로 복귀한 뒤 작업한다.
- 커밋과 원격 반영 대상은 `main` 하나로 고정한다. 사용자가 명시적으로 다른 저장소 정책을 지시하지 않는 한 PR용 branch도 만들지 않는다.
- 할 일: `docs/plan/tasklist.md` — 설계를 여기서 바꾸지 않는다.
- `[x]`는 테스트로 증명된 것만.
- 문서 맵(한 주제 한 곳): `docs/plan/00-overview-and-goals.md` §1.1.
- 이 파일은 요약이다. 네이밍·테스트·스펙의 원문을 덮어쓰지 않는다.
- 계획만 있고 코드가 없으면 문서를 이긴 척하지 않는다.
