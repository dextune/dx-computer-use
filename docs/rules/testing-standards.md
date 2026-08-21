---
title: "HPCU Runtime — 테스트 표준 및 검증 규칙"
version: "1.0"
date: "2026-08-21"
language: "ko-KR"
scope: "project-wide, enforced"
---

# 테스트 표준 및 검증 규칙

## 1. 핵심 원칙

> **입력 대비 기대 출력을 정의하고, 시뮬레이션으로 검증하고, 그 결과를 측정한다.**

테스트는 다음 세 단계로 구성된다:

1. **Given** — 입력 상태를 구성한다 (fixture, mock, scenario).
2. **When** — 테스트 대상 동작을 실행한다.
3. **Then** — 기대 출력과 실제 출력을 비교하고, 통과/실패를 판정한다.

모호한 "잘 동작한다"는 허용되지 않는다. 모든 테스트는 **정량적 판정 기준**을 가져야 한다.

---

## 2. 테스트 계층 구조

```text
tests/
├─ unit/                    # 단일 함수/메서드, mock 사용, 1ms 이내
│  ├─ test_grounder_candidate_scoring.py
│  ├─ test_verifier_evidence_eval.py
│  └─ test_recovery_loop_breaker.py
├─ integration/             # 모듈 간 결합, 실제 파일/DB, 100ms 이내
│  ├─ test_scene_graph_update_flow.py
│  ├─ test_observer_to_scene_builder.py
│  └─ test_executor_verifier_contract.py
├─ replay/                  # 저장된 trajectory 재생, fixture 기반
│  ├─ test_workflow_replay_login.py
│  └─ test_compiled_trajectory_determinism.py
├─ failure_injection/       # 장애 주입: 네트워크 단절, timeout, stale data
│  ├─ test_model_timeout_escalation.py
│  ├─ test_stale_decision_rejection.py
│  └─ test_observer_disconnect_recovery.py
└─ e2e/                     # 실제 OS/브라우저. 기본 PR 게이트 아님
   ├─ platform/
   │  ├─ test_browser_login_flow.py
   │  ├─ test_windows_notepad_action.py
   │  ├─ test_linux_x11_xvfb.py
   │  ├─ test_macos_ax.py
   │  └─ test_remote_vnc.py
   └─ test_cross_dsl_parity.py   # 동일 Action DSL, 플러그인만 다름
```

---

## 3. 테스트 작성 규칙

### 3.1 단위 테스트 (Unit)

모든 public 함수/메서드는 단위 테스트를 가져야 한다. private(`_` prefix) 함수는
public 함수의 테스트로 간접 커버리지가 확보되지 않으면 별도 테스트를 작성한다.

```python
# test_grounder_candidate_scoring.py
import pytest
from hpcu.grounder.candidate_scoring import score_candidates

def test_score_candidates_returns_highest_confidence_first():
    """후보 목록이 confidence 내림차순으로 정렬되어 반환된다."""
    # Given
    candidates = [
        Candidate(id="a", confidence=0.3),
        Candidate(id="b", confidence=0.9),
        Candidate(id="c", confidence=0.5),
    ]
    # When
    result = score_candidates(candidates)
    # Then
    assert result[0].id == "b"
    assert result[0].confidence == 0.9
    assert result[2].id == "a"
    assert all(
        result[i].confidence >= result[i + 1].confidence
        for i in range(len(result) - 1)
    )

def test_score_candidates_empty_list_returns_empty():
    """빈 후보 목록은 빈 리스트를 반환한다."""
    result = score_candidates([])
    assert result == []

def test_score_candidates_threshold_filters_low_confidence():
    """confidence_threshold 미만 후보는 제외된다."""
    candidates = [
        Candidate(id="a", confidence=0.1),
        Candidate(id="b", confidence=0.8),
    ]
    result = score_candidates(candidates, confidence_threshold=0.5)
    assert len(result) == 1
    assert result[0].id == "b"
```

### 3.2 통합 테스트 (Integration)

모듈 경계를 넘는 데이터 흐름을 검증한다. 실제 파일/DB를 사용하며 mock은 최소화한다.

```python
# test_scene_graph_update_flow.py
@pytest.mark.integration
async def test_observer_delta_updates_scene_graph():
    """Observer의 delta가 SceneGraph에 정확히 반영된다."""
    # Given
    observer = BrowserObserver(config=test_config)
    scene = SceneGraph()

    # When — 첫 관찰
    delta = await observer.observe()
    scene.update(delta)

    # Then
    assert scene.version == 1
    assert len(scene.elements) == len(delta.added)

    # When — 버튼 클릭 후 delta
    await observer.click("#submit-btn")
    delta2 = await observer.observe()
    scene.update(delta2)

    # Then
    assert scene.version == 2
    assert any(e.id == "#success-msg" for e in scene.elements)
```

### 3.3 재생 테스트 (Replay)

저장된 trajectory를 재생하여 동일한 결과가 나오는지 검증한다.

```python
# test_workflow_replay_login.py
@pytest.mark.replay
async def test_login_workflow_replay_produces_same_evidence():
    """저장된 login workflow 재생 시 동일 evidence가 생성된다."""
    # Given
    workflow = Workflow.load("workflows/qualified/login-gmail.workflow.json")
    executor = ReplayExecutor(workflow)
    verifier = EvidenceVerifier()

    # When
    trajectory = await executor.replay()
    evidence = await verifier.evaluate(trajectory)

    # Then
    assert evidence.success is True
    assert evidence.matches_expected(workflow.expected_evidence)
    assert trajectory.model_call_count == 0  # 재생은 모델 호출 없음
```

### 3.4 장애 주입 테스트 (Failure Injection)

시스템이 비정상 상황에서 올바르게 실패하거나 복구하는지 검증한다.

```python
# test_stale_decision_rejection.py
@pytest.mark.failure_injection
async def test_model_response_after_scene_change_is_rejected():
    """모델 응답 도착 시점에 scene이 변경되었으면 결정이 거부된다."""
    # Given
    router = ModelRouter(timeout_ms=5000)
    stale_scene = SceneGraph(version=1)
    current_scene = SceneGraph(version=2)

    # When — 모델이 느리게 응답 (scene이 이미 변경됨)
    with patch.object(router, '_call_model', return_value=slow_response):
        decision = await router.resolve(
            node=test_node,
            scene=stale_scene,
            candidates=test_candidates,
        )

    # Then
    assert decision.is_stale(current_scene.version) is True
    # 라우터는 stale decision을 폐기하고 재시도하거나 escalation
    with pytest.raises(StaleDecisionError):
        executor.prepare(test_node, decision.target_id)
```

---

## 4. 테스트 실행 및 검증 프로세스

### 4.1 실행 명령

```bash
# 전체 테스트
pytest tests/ -v --strict-markers

# 계층별 실행
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest tests/replay/ -v
pytest tests/failure_injection/ -v

# 커버리지 리포트
pytest tests/ --cov=hpcu --cov-report=term --cov-report=html
```

### 4.2 필수 마커

모든 테스트는 다음 마커 중 하나를 반드시 지정해야 한다:

| 마커 | 설명 | 실행 시간 제한 |
|---|---|---|
| `@pytest.mark.unit` | 단일 함수/메서드 | ≤ 1ms per case |
| `@pytest.mark.integration` | 모듈 간 결합 | ≤ 100ms per case |
| `@pytest.mark.replay` | trajectory 재생 | ≤ 500ms per case |
| `@pytest.mark.failure_injection` | 장애 주입 | ≤ 1s per case |
| `@pytest.mark.e2e` | 실제 환경 | ≤ 30s per case |
| `@pytest.mark.platform_browser` | Playwright fixture | ≤ 30s |
| `@pytest.mark.platform_windows` | Windows runner 전용 | ≤ 30s |
| `@pytest.mark.platform_linux_x11` | Xvfb/Docker 샌드박스 | ≤ 30s |
| `@pytest.mark.platform_linux_wayland` | 전용 runner, 기본 CI 스킵 | ≤ 30s |
| `@pytest.mark.platform_macos` | macOS runner + TCC | ≤ 30s |
| `@pytest.mark.platform_remote` | VNC fixture | ≤ 30s |

마커 누락 시 CI에서 실패 처리한다.

### 4.3 커버리지 요구사항

| 기준 | 임계값 | 적용 대상 |
|---|---|---|
| 라인 커버리지 | ≥ 85% | `hpcu/` 전체 |
| 브랜치 커버리지 | ≥ 80% | `hpcu/` 전체 |
| 모듈별 최소 커버리지 | ≥ 70% | 각 `hpcu/<module>/` |
| ABC 인터페이스 메서드 | 100% | ABC에 정의된 모든 추상 메서드는 구현체 테스트에서 호출 검증 |

---

## 5. 시뮬레이션 및 검증 프레임워크

### 5.1 Fixture 기반 시뮬레이션

모든 테스트는 실제 외부 시스템(브라우저, Windows, 모델 API)에 의존하지 않고
**fixture**로 시뮬레이션 환경을 구성한다.

```python
# conftest.py — 공유 fixture
@pytest.fixture
def sample_scene_graph():
    """3개 버튼과 1개 입력 필드가 있는 표준 fixture scene."""
    return SceneGraph.from_dict({
        "version": 1,
        "elements": [
            {"id": "#btn-login", "type": "button", "text": "로그인",
             "bbox": [100, 200, 180, 230]},
            {"id": "#btn-cancel", "type": "button", "text": "취소",
             "bbox": [200, 200, 280, 230]},
            {"id": "#btn-help", "type": "button", "text": "도움말",
             "bbox": [300, 200, 380, 230]},
            {"id": "#input-email", "type": "textbox", "value": "",
             "bbox": [100, 100, 300, 130]},
        ],
    })

@pytest.fixture
def mock_model_response():
    """MiniMax M3의 표준 응답을 시뮬레이션."""
    return {
        "choices": [{
            "message": {
                "content": " thinking사용자가 로그인 버튼을 찾고 있습니다. response\n"
                           '{"target_id": "#btn-login", "confidence": 0.95}',
                "role": "assistant",
            }
        }]
    }
```

### 5.2 기대값 검증 (Assertion Contract)

모든 테스트는 최소 하나의 명시적 assertion을 가져야 한다.
"예외가 발생하지 않는다"는 assertion이 아니다.

```python
# 금지
def test_do_something():
    result = do_something()
    # 예외 없으면 통과 — 금지

# 허용
def test_do_something_returns_valid_result():
    result = do_something()
    assert result is not None
    assert result.status == "ok"
    assert 0.0 <= result.confidence <= 1.0
```

---

## 6. CI/CD 게이트

### 6.1 Pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: pytest-unit
        name: unit tests
        entry: pytest tests/unit/ -v --strict-markers --tb=short
        language: system
        pass_filenames: false
        always_run: true
```

### 6.2 PR Merge 조건

다음 조건을 모두 충족해야 merge 가능:

1. 모든 unit test 통과
2. 모든 integration test 통과
3. replay test 통과
4. failure injection test 통과
5. 라인 커버리지 ≥ 85%
6. 브랜치 커버리지 ≥ 80%
7. `@pytest.mark` 마커 누락 0건
8. 신규 public 함수에 대한 테스트 누락 0건 (lint rule)

### 6.3 실패 처리

| 실패 유형 | 조치 |
|---|---|
| 테스트 실패 | 수정 또는 테스트 갱신 후 재실행 |
| 커버리지 미달 | 신규 코드에 대한 테스트 추가 |
| 마커 누락 | 적절한 마커 추가 |
| flaky test (3회 연속 불일치) | `@pytest.mark.flaky` 마커 + 즉시 수정 티켓 생성 |

---

## 7. 금지 패턴

| 패턴 | 사유 |
|---|---|
| `time.sleep()` 기반 대기 | 불안정. `await` 또는 `poll_until()` 사용 |
| 실제 네트워크 호출 (unit/integration) | 비결정적. mock/fixture 사용 |
| 테스트 간 상태 공유 | 격리 위반. 각 테스트는 독립적 |
| `assert True` / `assert 1 == 1` | 무의미. 삭제 |
| `try: ... except: pass` | 오류 은폐. `pytest.raises()` 사용 |
| 환경 변수 의존 (unit) | `monkeypatch.setenv()` 사용 |
| 파일 시스템 의존 (unit) | `tmp_path` fixture 사용 |
| 테스트 함수 내 조건문 | 테스트는 선형이어야 함. parametrize 사용 |

---

## 8. 문서 테스트

Markdown/문서에 포함된 코드 예제는 테스트 가능해야 한다.

```python
# docs/의 코드 블록 검증
pytest --doctest-modules docs/
```

문서 내 코드 예제가 실행 불가능하면 해당 문서는 게시되지 않는다.

---

## 9. 플랫폼 테스트 (크로스 OS)

네이티브 Capture/Input은 OS 실기 없이는 증명할 수 없다. 그래도 PR이
Windows 머신에 묶이면 안 된다.

### 9.1 기본 게이트 (모든 PR)

- `CaptureBackend` / `InputInjector` 계약: **fake** 구현
- Perception: fixture 프레임
- Browser: Playwright fixture (`platform_browser`)
- `hpcu.vision` / `hpcu.scene_graph` / `hpcu.runtime_core`가
  `hpcu.platform`을 import하면 실패

### 9.2 OS matrix (required 아님, ADR로 승격)

| Job | 마커 | 러너 |
|---|---|---|
| Windows | `platform_windows` | windows-latest |
| Linux X11 | `platform_linux_x11` | ubuntu + Xvfb 또는 샌드박스 컨테이너 |
| Linux Wayland | `platform_linux_wayland` | 수동/전용. 기본 스킵 |
| macOS | `platform_macos` | macos-latest (TCC는 문서화) |
| Remote | `platform_remote` | 샌드박스 noVNC 또는 RFB fixture |

실기 테스트가 없는 플러그인 변경은 merge 가능하나 Phase 8 완료 조건은
해당 job 통과다.

### 9.3 Fake backend 규칙

- 실제 DXGI/X11/CGEvent를 unit에서 호출하지 않는다
- `FrameHandle`에 bytes를 넣지 않는다 (shm_id만)
- capture.session_id != input.session_id 조합은 부팅 실패를 검증한다

---

## 10. 이 문서의 적용 범위

- 본 문서는 프로젝트 전체에 적용된다.
- 모든 PR은 본 문서의 CI 게이트를 통과해야 한다.
- 예외가 필요한 경우 ADR(`docs/decisions/`)에 명시적 근거를 기록한다.
- 위반 코드는 merge 후 24시간 이내에 fix commit으로 교정한다.