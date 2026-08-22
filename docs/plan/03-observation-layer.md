---
title: "HPCU Runtime 개발 계획 03 — 관찰 계층 (캡처·구조·인식·입력)"
version: "1.0"
date: "2026-08-21"
parent: "docs/dev-init-001.md (§6~7, §13, §15), docs/plan/01-system-architecture.md §2.5~2.6"
language: "ko-KR"
---

# 03. 관찰 계층 — Capture · Structure · Perception · Input

## 1. 한 줄 계약

> 화면은 OS마다 다르게 **얻고**, 같게 **이해하고**, OS마다 다르게 **누른다.**
> 공통 런타임은 픽셀과 트리를 해석한다. OS 플러그인은 버퍼와 입력을 소유한다.
>
> `Observer.observe()`의 산출은 `SceneDelta`(added/removed/modified) 하나다.
> `ObservationDelta` 스냅샷 필드는 쓰지 않는다.

대상 화면은 다음 중 하나다. 모두 같은 ABC에 등록된다.

| 화면 | 플러그인 패키지 | 비고 |
|---|---|---|
| 브라우저 | `hpcu.platform.browser` | Playwright/CDP. OS 위에 얹히지만 자체 Capture/Input |
| Windows 데스크톱 | `hpcu.platform.windows` | DXGI + UIA + SendInput |
| Linux 호스트 / Docker Linux | `hpcu.platform.linux` | X11 또는 PipeWire. 배포만 다름 |
| macOS | `hpcu.platform.macos` | ScreenCaptureKit + AX + CGEvent |
| 원격 (VNC/RDP/noVNC) | `hpcu.platform.remote` | 구조 트리 없음. 픽셀 + RFB 입력 |

Docker 안 Ubuntu+Xvfb+noVNC 샌드박스는 **새 OS가 아니다.**
Linux X11 Capture + (선택) Browser CDP + Remote VNC 표시 경로의 조합이다.

---

## 2. 왜 Observer 하나에 몰지 않는가

캡처 API, 접근성 트리, 마우스 주입은 OS마다 완전히 다르다.
OCR·Scene Graph·Grounding은 OS를 모른다.

한 `Observer` 구현에 grab + parse + click를 넣으면:

- Linux Wayland 권한 문제가 Windows UIA 코드와 섞인다
- Perception 최적화가 특정 캡처 백엔드에 묶인다
- 원격 화면처럼 트리가 없는 환경을 예외 분기로 처리하게 된다

따라서 네 계약으로 나눈다.

```text
CaptureBackend     픽셀 → FrameHandle          OS 플러그인
StructureObserver  트리 → 구조 요소             OS 플러그인 (없을 수 있음)
Perception         FrameHandle+구조 → Scene    공통. OS import 금지
InputInjector      Action → OS 이벤트           OS 플러그인
```

`Observer` ABC는 facade다. Capture+Structure를 묶어 `ObservationDelta`를 만든다.
`Executor`는 InputInjector만 본다.

---

## 3. 공통 DTO (모듈 경계)

모듈은 이 타입만 넘긴다. native handle은 플러그인 밖으로 나가지 않는다.

```python
@dataclass(frozen=True)
class FrameHandle:
    shm_id: str
    width: int
    height: int
    stride: int
    pixel_format: str          # "BGRA" | "BGR"
    timestamp_ns: int
    space: str                 # "screen_physical_px" 등
    dirty_rects: tuple[BoundingBox, ...]
    source: str                # "dxgi" | "x11" | "pipewire" | "sckit" | "cdp" | "vnc"


@dataclass(frozen=True)
class CaptureCapabilities:
    pixel_grab: bool
    dirty_rects: bool          # 백엔드가 dirty ROI를 주는지
    cursor_separate: bool
    max_fps: int
    requires_permission: tuple[str, ...]


@dataclass(frozen=True)
class InputCapabilities:
    semantic_invoke: bool
    physical_pointer: bool
    physical_keyboard: bool
    global_hotkey: bool
    background_input: bool     # 포커스 없는 창에 주입 가능
    requires_permission: tuple[str, ...]


class Capability(str, Enum):
    SUPPORTED = "supported"
    DEGRADED = "degraded"      # 동작하나 제약 있음 (Wayland portal, VNC lag)
    UNSUPPORTED = "unsupported"
```

`UNSUPPORTED`는 조용히 픽셀 클릭으로 바꾸지 않는다.
Executor가 fallback 순서를 고르고, 없으면 halt + failure code.

관련 실패 코드 (스키마 `hpcu/schemas/failure_codes.py`에 추가):

| 코드 | 의미 |
|---|---|
| `CAPTURE_PERMISSION_DENIED` | 화면 녹화/포털 거부 |
| `CAPTURE_BACKEND_UNAVAILABLE` | 세션 타입과 백엔드 불일치 (Wayland에 XTest 등) |
| `STRUCTURE_TREE_EMPTY` | 접근성 트리가 비었음 → 픽셀 경로 |
| `INPUT_PERMISSION_DENIED` | 접근성/입력 포털 거부 |
| `INPUT_SEMANTIC_UNSUPPORTED` | Invoke 불가, physical 후보 |
| `INPUT_PHYSICAL_UNSUPPORTED` | 물리 입력 불가 (잠긴 세션, 원격 정책) |
| `CAPABILITY_MISSING` | 요청한 경로가 matrix상 없음 |

---

## 4. CaptureBackend — OS별 픽셀 획득

Perception은 이 백엔드를 모른다. ring buffer와 tile hash는 `hpcu/capture/`의 공통 코드.

### 4.1 Windows — DXGI Desktop Duplication

- dirty rectangle, moved rect를 그대로 `FrameHandle.dirty_rects`에 넣는다
- per-monitor DPI는 capture 시 메타로 기록, 변환은 `hpcu/coordinates/`
- 커서 분리 캡처 가능하면 `cursor_separate=true`

### 4.2 Linux X11 / Xvfb / Docker

- `XShmGetImage` 또는 동등한 shared-memory grab
- Docker+Xvfb 샌드박스는 이 백엔드. DISPLAY=`:99` 같은 값은 config
- dirty rect 없으면 공통 tile hash가 ROI를 만든다
- 호스트 Linux와 **같은 플러그인**, env만 다름 (`display`, `xauthority`)

### 4.3 Linux Wayland

- PipeWire + desktop/screencast portal
- 포털 거부 = `CAPTURE_PERMISSION_DENIED`. XTest로 우회하지 않음
- compositor별 제약(GNOME/KDE/wlroots)은 capability `DEGRADED`로 표기

### 4.4 macOS — ScreenCaptureKit

- Screen Recording 권한 필수
- 논리/물리 픽셀, Retina scale을 FrameHandle 메타에 기록
- Quartz 원점(좌하단) 변환은 coordinates 계층. Capture는 물리 px + space 태그만

### 4.5 Browser — CDP / Playwright

- screencast 또는 필요 시 viewport screenshot
- 기본 경로는 픽셀이 아니라 DOM/AX. Capture는 픽셀 fallback·VLM용
- devicePixelRatio, viewport, CSS px는 FrameHandle.space로 명시

### 4.6 Remote — VNC/RDP

- RFB framebuffer. 구조 트리 없음
- 스케일/레터박스가 있으면 remote-local 변환 행렬을 등록
- noVNC(웹소켓 6080)는 사람이 보는 표시 경로. 런타임 grab은 컨테이너 내부 X11이
  더 정확하면 그것을 쓴다 (샌드박스: Xvfb grab > noVNC 재인코딩)

### 4.7 공통 캡처 파이프라인

```text
CaptureBackend.grab()
    → ring buffer (최신 프레임 우선, bounded)
    → tile hash / dirty merge          # hpcu/capture, OS 무관
    → Perception workers               # hpcu/vision, OS 무관
```

고정 sleep 금지. settle은 03이 아니라 `07`의 detector가 이벤트+ROI 안정으로 판정.

---

## 5. StructureObserver — 있으면 픽셀보다 먼저

우선순위는 01 §1과 동일하다. 트리가 비면 빈 delta를 주고 Perception만 돌린다.

| 플러그인 | API | 핵심 필드 |
|---|---|---|
| browser | Playwright AX / DOM / locator | role, name, ref, iframe/shadow |
| windows | UI Automation | AutomationId, ControlType, patterns |
| linux | AT-SPI | role, state, relation, events |
| macos | AXUIElement | role, title, actions |
| remote | 없음 | 항상 empty. 실패가 아님 |

구조 요소는 Scene fusion 때 `sources[].type = "dom"|"uia"|"atspi"|"ax"` 로 남긴다.

GTK/Qt는 AT-SPI가 비교적 낫고, Electron/캔버스/게임은 비는 경우가 많다.
빈 트리는 `STRUCTURE_TREE_EMPTY`이지 크래시가 아니다.

---

## 6. Perception — 공통, OS 코드 금지

`hpcu/vision/` 과 `hpcu/scene_graph/` 만 해당.

- OCR (PaddleOCR 주, Tesseract fallback)
- shape / component / template / pHash
- text-component association
- temporal tracking, Hungarian match

이 레이어는 `FrameHandle`과 구조 delta만 받는다.
`ctypes`, `Quartz`, `DXGI`, `pyatspi` import는 CI에서 실패 처리한다.

픽셀은 **공통 최후 수단**이지 공통 첫 수단이 아니다.
브라우저·UIA가 되면 vision 워커는 그 프레임을 skip할 수 있다.

---

## 7. InputInjector — semantic 우선, physical은 fallback

Executor 실행 순서 (dev-init §15.1과 동일, 구현은 이 ABC):

```text
고수준 Tool/API
  → InputInjector.semantic()     # Invoke, AXPress, locator.click, AT-SPI
    → hotkey (platform accelerator)
      → InputInjector.physical() # 클릭/키. 최신 bbox에서 안전 클릭점
```

### 7.1 OS별 물리 입력

| 플러그인 | 포인터/키 | 주의 |
|---|---|---|
| windows | `SendInput` | UIA 실패 시에만. DPI 변환 후 |
| linux-xtest | XTest / xdotool | X11, Xvfb, Docker DISPLAY |
| linux-portal | RemoteDesktop / input portal, ydotool | Wayland. 거부 시 halt |
| macos | `CGEventPost` | Accessibility 권한 |
| browser | CDP Input / Playwright | 페이지 포커스 |
| remote-vnc | RFB PointerEvent/KeyEvent | 클라이언트 스케일 보정 |

### 7.2 하지 않는 것

- Perception이나 Grounder가 OS API를 직접 호출
- bbox 중앙 무조건 클릭 (좌표 문서 §13.3)
- Wayland에서 XTest를 “일단 시도”
- 권한 없이 입력 주입을 성공한 척

클릭 직전 hit-test (`elementFromPoint`, UIA element-at-point, AT-SPI, AX)가
있으면 반드시 돌린다. 대상이 다르면 `TARGET_OCCLUDED` / 재탐색.

---

## 8. Capability Matrix

런타임은 부팅 시 matrix를 평가해 `config/platform-capabilities.yaml`에
실측값을 기록할 수 있다. 아래는 **설계 기본값**이다.

| Capability | Browser | Windows | Linux X11 | Linux Wayland | macOS | Remote VNC |
|---|---|---|---|---|---|---|
| pixel capture | supported | supported | supported | degraded* | supported | supported |
| dirty ROI from backend | degraded | supported | unsupported | unsupported | degraded | unsupported |
| structure tree | supported | supported | supported | degraded | supported | unsupported |
| semantic invoke | supported | supported | supported | degraded | supported | unsupported |
| physical pointer | supported | supported | supported | degraded* | supported | supported |
| physical keyboard | supported | supported | supported | degraded* | supported | supported |
| background input | unsupported | degraded | unsupported | unsupported | unsupported | unsupported |
| global hotkey | degraded | supported | degraded | unsupported | supported | unsupported |
| multi-monitor | supported | supported | supported | degraded | supported | unsupported |
| per-monitor DPI | n/a (dPR) | supported | degraded | degraded | supported | unsupported |

\* Wayland: 포털 승인 후에만 supported. 미승인은 `UNSUPPORTED` + 해당 실패 코드.

Docker Linux (Xvfb): Linux X11 행과 동일. 가상 모니터 1장, DPI 96 기본.

---

## 9. 권한과 설치 (플러그인 책임, core 책임 아님)

각 플랫폼 패키지는 `permissions.md`와 `probe()`를 제공한다.
core는 probe 결과만 본다.

| 플랫폼 | 캡처 | 구조/입력 | 설치 메모 |
|---|---|---|---|
| Windows | 대체로 추가 권한 없음 | UIA는 대상 프로세스 integrity | DPI awareness 선언 필수 |
| Linux X11 | DISPLAY, Xauthority | AT-SPI bus (`at-spi2-core`) | Docker는 Xvfb + dbus |
| Linux Wayland | xdg-desktop-portal, PipeWire | 동일 포털 / AT-SPI | compositor 문서 확인 |
| macOS | Screen Recording | Accessibility (입력·AX) | TCC 프롬프트. CLI는 서명/notarize 이슈 |
| Browser | 없음 | 없음 | Playwright 브라우저 바이너리 |
| Remote | 세션 비밀번호 | 세션 정책 | 자격증명 secret store, 로그 금지 |

`probe()`가 false면 해당 플러그인을 등록하지 않는다.
부분 가능(캡처만 되고 입력 불가)은 `DEGRADED`로 등록하고 semantic-only 또는
observe-only 모드로 돌린다. 입력 없는 observe-only는 허용한다.

---

## 10. 부트스트랩 선택

```text
config.platform 명시 → 그 플러그인만
아니면:
  playwright session 활성 → browser (+ 필요 시 native overlay)
  remote.display URL → remote + 가능하면 내부 native grab
  sys.platform:
    win32  → windows
    darwin → macos
    linux  → Wayland? pipewire : x11
```

한 프로세스에 Capture와 Input은 **같은 화면**을 가리켜야 한다.
Xvfb를 캡처하면서 호스트 마우스를 움직이면 안 된다.
`session_id`로 capture/input/structure를 묶는다.

---

## 11. 좌표

Capture가 채운 `FrameHandle.space`와 Input이 받는 `ScreenPoint.space`가
같거나, `hpcu/coordinates/`가 변환 가능한 쌍이어야 한다.

필수 변환 (구현은 02 문서 / `hpcu/coordinates/`):

- physical px ↔ logical / DPI
- monitor-local ↔ virtual desktop (음수 origin)
- viewport / CSS px ↔ screen (browser)
- Quartz bottom-left ↔ top-left (macOS)
- crop-local ↔ 원본 (VLM SoM)
- remote framebuffer ↔ local (레터박스, scale)

InputInjector.physical은 이미 변환된 점을 받는다. 플러그인 안에서 임의 scale하지 않는다.
예외: OS API가 자기 좌표계만 받을 때, 플러그인이 **한 번** 변환하고 trace에 남긴다.

---

## 12. 플러그인 추가 절차

1. `hpcu/platform/<name>/` 패키지 생성 (`capture.py`, `structure.py`, `input.py`, `probe.py`)
2. 세 ABC 구현. 해당 OS에서만 import되는 native 의존은 패키지 안에만
3. `capabilities()` / `probe()` 구현
4. 부트스트랩 `registry.register("capture.<name>", ...)` 세 줄
5. `tests/unit`에 fake backend로 계약 테스트
6. 해당 OS job에서만 `tests/e2e/platform/<name>`
7. 이 문서 §8 matrix 행 갱신

삭제: register 제거 + 패키지 삭제. core와 vision은 컴파일·테스트가 그대로 통과해야 한다.

---

## 13. 테스트 계약

| 계층 | OS 실기 | 방법 |
|---|---|---|
| Capture/Input ABC 계약 | 아니오 | fake backend, FrameHandle 불변, 복사 금지 어서션 |
| Perception | 아니오 | fixture 프레임 + 기대 SceneDelta |
| 좌표 변환 | 아니오 | DPI/음수 origin/Retina 테이블 |
| Windows DXGI/UIA | 예 (`@pytest.mark.platform_windows`) | CI 윈도우 runner |
| Linux X11 | 예 (`platform_linux_x11`) | Xvfb job 또는 샌드박스 컨테이너 |
| Linux Wayland | 예 (`platform_linux_wayland`) | 수동/전용 runner. 기본 CI 스킵 |
| macOS | 예 (`platform_macos`) | macOS runner + TCC fixture 문서 |
| Browser | 예 (`platform_browser`) | Playwright fixture, OS 무관 |
| Remote VNC | 예 (`platform_remote`) | 샌드박스 noVNC 또는 녹화된 RFB |

기본 PR 게이트는 unit + fake platform + browser fixture.
네이티브 e2e는 OS matrix job이며 required가 아닐 수 있다 (ADR로 지정).

마커 정의는 `docs/rules/testing-standards.md` §9. 여기서 마커를 새로 만들지 않는다.

---

## 14. 이 문서가 소유하는 것

캡처·구조·인식·입력 ABC의 *상세*, capability matrix, 권한, 부트스트랩 선택.
모듈이 repo 어디에 있는지는 `01-system-architecture.md`.
문서 맵은 `00-overview-and-goals.md` §1.1.
