---
title: "HPCU Runtime 개발 계획 11 — 평가·벤치마크·성능 SLO·개발 도구"
version: "1.1"
date: "2026-08-26"
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

## 5. Screen Understanding Engine 제품 gate

SUE release는 기존 `hpcu.qualification.gate`만으로 통과하지 않는다.
`hpcu.qualification.sue.qualify_product_sue()`에서 generic product gate와 SUE gate를
모두 통과해야 한다.

기본 SUE threshold:

- held-out 30개 이상, browser/terminal/desktop/structure-rich/structure-poor/pixels-only/
  duplicate/modal/slow/stale/viewport-scale/Korean-English 12 family 포함
- top1 precision, confident-local precision, ambiguity detection rate = 1.0
- deterministic target unnecessary semantic escalation rate = 0
- stale action, false completion, false executable target, false merge execution = 0
- coordinate replay = 0, unsafe drift = 0
- reference baseline 없는 성능 run은 qualification 실패
- first-actionable/grounding p95 regression 금지
- processed pixels와 full OCR는 baseline보다 감소해야 함

## 6. SUE benchmark artifact

`benchmarks/sue_artifacts.py`는 각 qualification run에서 다음을 생성한다.

```text
summary.json
stage-metrics.json
qualification.json
fixture-list.txt
reference-hardware.json
```

portable correctness는 `tests/integration/test_sue_held_out_matrix.py`의 36-case
indexed/reference parity matrix로 검증한다. Linux X11 terminal 실기는
`tests/e2e/test_sue_linux_terminal.py`로 별도 증거를 남긴다.

unit synthetic evidence만으로 최종 product qualification을 선언하지 않는다. 실제 reference
hardware A/B artifact와 필요한 live platform evidence가 있는 run만 최종 SUE 성능/실기 증거다.
