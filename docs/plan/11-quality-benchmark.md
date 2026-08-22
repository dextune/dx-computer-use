---
title: "HPCU Runtime 개발 계획 11 — 평가·벤치마크·성능 SLO·개발 도구"
version: "1.0"
date: "2026-08-21"
parent: "docs/dev-init-001.md (§20.5, §26~27)"
language: "ko-KR"
---

# 11. 평가·벤치마크·성능 SLO·개발 도구

## 1. 성능 SLO

| 지표 | 목표 |
|---|---|
| 결정론적 action dispatch | p95 30ms |
| 1080p delta scene update | p95 150ms |
| 변경 없는 프레임 처리 | p95 10ms |
| 컴파일된 워크플로우 | 모델 호출 0회 |
| stale model action 실행 | 0회 |
| 검증 없는 완료 선언 | 0회 |

## 2. 벤치마크 하니스

`benchmarks/harness.py` — `BenchmarkHarness` 클래스.
지표: total_time_us, model_call_count, action_count, success,
stale_rejections, step_latencies_us.

## 3. 평가 지표

| 분류 | 지표 |
|---|---|
| 정확도 | task success rate |
| Grounding | target hit rate, false-action rate |
| 효율 | action steps / human reference steps |
| 속도 | end-to-end latency, p50/p95 step latency |
| 모델 | calls/task, tokens/task |
| 복구 | recovery success rate |
| 검증 | false completion rate |

## 4. 테스트 환경

- 1920×1080, 2560×1440, 3840×2160
- 100%, 125%, 150% scaling
- light/dark theme
- 한국어/영어
- 다중 모니터, 가림/팝업, 느린 네트워크