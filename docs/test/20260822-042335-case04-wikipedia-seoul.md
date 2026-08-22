# Test Case 04 — `wikipedia-seoul`

- **실행 배치**: 2026-08-22 10-case run
- **목표**: 위키피디아에서 Seoul 문서를 열어줘
- **시작 URL**: `https://en.wikipedia.org/wiki/Seoul`
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
| MiniMax 토큰 | 706 |
| compile 호출 | 1 |
| grounding 호출 | 1 |
| failure | 없음 |

## 실행 로그 요약

navigation settle 후 한 번의 attempt에서 `PageDown`, MiniMax grounding, 클릭, post-click capture, settle 순서로 끝났다. 선택 텍스트는 다음이다.

```text
Seoul ‘%q 218 languages ~
```

액션 종류별 개수는 `capture=5`, `focus=1`, `key=5`, `click=2`, `type=1`, `settle=2`, `model=1`이다. 통계에는 MiniMax 오류 1회가 있지만 최종 모델 action은 성공으로 기록되어 있다. 오류의 위치와 회복 과정은 action 상세에 나타나지 않는다.

## 로그가 증명하는 것

- 단일 시도로 runner가 성공 판정했다.
- 선택 텍스트에 목표 핵심어 `Seoul`이 포함되어 있다.
- compile 1회와 grounding 1회가 집계됐다.
- 저수준 입력과 post-click settle은 오류 없이 완료됐다.

## 로그만으로 증명할 수 없는 것

- 실제 클릭 대상이 Wikipedia Seoul 문서/링크인지
- `218 languages`가 페이지의 실제 텍스트인지 주변 영역과 합쳐진 OCR인지
- 한 번 발생한 MiniMax 오류가 fallback인지 재시도인지
- post-click settle이 의미 있는 문서 전환을 검증했는지

## 문제점 및 판단

- **중간 — OCR 오염**: `Seoul` 외의 문자열은 노이즈가 많다.
- **중간 — 모델 오류 상세 부족**: 오류 수는 통계에 있지만 어떤 호출이 실패했는지와 response 상태가 없다.
- **낮음 — semantic role 미기록**: 선택 텍스트만 있고 element role/selector가 보고서용 dataset에 없다.

## 결론

**Runner 기준 성공.** 목표 키워드가 포함되어 세 케이스 중 관련성은 양호하지만, 캡처 이미지 없이 올바른 Wikipedia 문서가 실제로 열렸는지는 미확인이다.
