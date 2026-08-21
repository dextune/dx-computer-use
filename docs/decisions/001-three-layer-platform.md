---
title: "ADR 001 — Capture / Perception / Input 3층 분리"
date: "2026-08-21"
status: "accepted"
language: "ko-KR"
---

# ADR 001. Capture · Perception · Input을 한 Observer에 몰지 않는다

## 상태

Accepted.

## 맥락

HPCU는 브라우저, Windows, Linux 호스트, Docker Linux, macOS, 원격 VNC를
같은 Action DSL로 다룬다. 픽셀 획득 API와 마우스/키보드 주입은 OS마다 다르다.
OCR·Scene Graph·Grounding은 OS를 모른다.

한 `Observer`가 grab+parse+click를 모두 가지면 크로스 OS 확장이
예외 분기가 된다.

## 결정

1. `CaptureBackend`, `StructureObserver`, `InputInjector`를 별도 ABC로 둔다.
2. Perception (`hpcu/vision`, `hpcu/scene_graph`)은 `hpcu.platform`을 import하지 않는다.
3. OS 코드는 `hpcu/platform/<os>/`에만 둔다.
4. Docker Linux와 호스트 Linux는 같은 Linux 플러그인이다. 배포만 다르다.
5. 없는 capability는 fallback으로 숨기지 않고 `UNSUPPORTED`를 반환한다.

스펙 본문은 `docs/plan/03-observation-layer.md`다. 이 ADR은 이유만 기록한다.
표를 여기에 복사하지 않는다.

## 결과

- 새 OS/원격 화면 = 플러그인 패키지 하나 + registry 세 줄
- 제어 루프와 vision은 OS 없이도 테스트 가능
- Wayland 포털, macOS TCC 같은 권한은 플러그인 `probe()`로 격리

## 대안 (기각)

- OS별 포크된 런타임: Action DSL과 Scene Graph가 갈라짐
- 픽셀-only 공통 경로: 브라우저/UIA 이점을 버림
- 단일 Observer 내부 if-os: Phase 8에서 파열
