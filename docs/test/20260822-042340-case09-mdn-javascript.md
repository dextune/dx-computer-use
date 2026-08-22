# Test Case 09 — `mdn-javascript`

- **실행 배치**: 2026-08-22 10-case run
- **목표**: MDN에서 JavaScript 문서를 열어줘
- **시작 URL**: `https://developer.mozilla.org/en-US/docs/Web/JavaScript`
- **원본 데이터**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/stats.json`
- **원본 실행 로그**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/full-run.log`

## 결과

| 항목 | 값 |
|---|---:|
| Runner 결과 | **성공** (`success=true`) |
| 시도 횟수 | 1 |
| 총 액션 | 17 |
| MiniMax 호출 | 2 |
| MiniMax 오류 | 1 |
| MiniMax 토큰 | 721 |
| compile 호출 | 1 |
| grounding 호출 | 1 |
| failure | 없음 |

## 실행 로그 요약

navigation settle 후 `attempt-1`, `PageDown`, `scroll`, MiniMax, target click, post-click capture, settle의 짧은 정상 경로였다. 선택 텍스트는 다음과 같다.

```text
Web > JavaScript @tTheme — % English (US)
```

액션 종류별 개수는 `capture=5`, `focus=1`, `key=5`, `click=2`, `type=1`, `settle=2`, `model=1`이다. MiniMax 오류 1회가 통계에 있지만 최종 모델 액션과 runner 결과는 성공이다.

## 로그가 증명하는 것

- 한 번의 시도로 success 판정됐다.
- 선택 텍스트에 목표 핵심어 `JavaScript`가 포함된다.
- compile/grounding 호출이 각각 1회다.
- MiniMax 오류가 1회 있었지만 실행이 중단되지 않았다.
- post-click settle은 timeout 없이 완료됐다.

## 로그만으로 증명할 수 없는 것

- 오류가 발생한 MiniMax 호출의 시점과 response
- 성공이 fallback, 두 번째 응답, 기존 pack 중 무엇으로 이뤄졌는지
- 클릭한 text가 MDN의 실제 JavaScript 문서 링크/header인지
- `Theme`, `English (US)`가 같은 element인지 주변 OCR이 합쳐진 것인지

## 문제점 및 판단

- **중간 — 모델 오류 복구 로그 부족**: error count는 있으나 어떤 호출이 실패했고 어떻게 복구됐는지 기록되지 않는다.
- **중간 — target semantic 미확인**: `JavaScript`는 관련성이 높지만 실제 actionable element인지 화면 없이는 확정할 수 없다.
- **양호 — 효율성**: 17액션·1회 시도·settle 성공이다.

## 결론

**Runner 기준 성공.** 선택 텍스트가 목표와 직접 관련되어 성공 케이스 중 신뢰도가 높은 편이지만, 시각적 확인과 모델 오류 recovery trace가 부족하다.
