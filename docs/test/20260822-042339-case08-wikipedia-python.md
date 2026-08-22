# Test Case 08 — `wikipedia-python`

- **실행 배치**: 2026-08-22 10-case run
- **목표**: 위키피디아에서 Python 프로그래밍 문서를 열어줘
- **시작 URL**: `https://en.wikipedia.org/wiki/Python_(programming_language)`
- **원본 데이터**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/stats.json`
- **원본 실행 로그**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/full-run.log`

## 결과

| 항목 | 값 |
|---|---:|
| Runner 결과 | **실패** (`success=false`) |
| 시도 횟수 | 4 / 최대 4 |
| 총 액션 | 50 |
| MiniMax 호출 | 2 |
| MiniMax 오류 | 0 |
| MiniMax 토큰 | 1,333 |
| compile 호출 | 1 |
| grounding 호출 | 1 |
| failure | `evidence_unmet` |

## 실행 로그 요약

시도별 선택 텍스트는 다음과 같다.

1. `된 iastz0days-skil`
2. `@ mahlernim / google-timeline-visualizer W Star 23k`
3. `AGENT? 24 : "@`
4. `| | © 2 lene ag 2 RY 12x amo ago`

각 attempt가 `PageDown → click → post_click captures → post_click-timeout`으로 종료됐다. 기록상 첫 attempt에만 `model` action 이벤트가 있고 2~4번째에는 별도 model 이벤트가 없다. 통계상 grounding은 1회다.

## 실패 원인 분석

선택 텍스트가 Python/Wikipedia 문서와 관련된 안정적인 표현이 아니다. 특히 두 번째 후보는 Google Timeline Visualizer repository로 보이는 문자열이며, 목표와 직접 무관하다. 네 번 모두 post-click timeout으로 끝났고 evidence가 unmet였다.

## 로그가 증명하는 것

- 네 번의 retry가 실행됐다.
- MiniMax 오류 없이도 evidence를 만들지 못했다.
- compile/grounding 호출은 각각 1회로 집계됐다.
- 모든 attempt가 post-click timeout이다.
- 일부 재시도는 별도 model action 없이 후보를 선택했다.

## 로그만으로 증명할 수 없는 것

- 실제 Wikipedia Python 페이지 로드 여부
- 선택 후보의 실제 role/좌표
- 후속 attempt가 fresh grounding인지 이전 후보 재사용인지
- timeout이 잘못된 클릭인지 페이지 상태/settle 문제인지

## 문제점 및 판단

- **높음 — 비관련 후보**: Google Timeline Visualizer 등 목표와 무관한 텍스트가 선택됐다.
- **높음 — 재시도 계측 부족**: 통계상 grounding 1회인데 여러 attempt가 진행돼, retry별 fresh grounding 여부를 로그만으로 추적할 수 없다.
- **중간 — stale 후보 가능성**: 후속 모델 이벤트가 없고 후보가 바뀌기는 하지만, 후보 생성 출처가 기록되지 않는다.

## 결론

**Runner 기준 실패.** MiniMax 오류가 원인은 아니며, OCR/grounding 및 retry 관측 경로가 Python Wikipedia target을 안정적으로 찾지 못한 사례다.
