# Test Case 02 — `google-search-notebook`

- **실행 배치**: 2026-08-22 10-case run
- **목표**: 구글에서 200만원 이하 노트북을 검색해줘
- **시작 URL**: `https://www.google.com/search?q=laptop+notebook+under+2000000+KRW`
- **원본 데이터**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/stats.json`
- **원본 실행 로그**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/full-run.log`

## 결과

| 항목 | 값 |
|---|---:|
| Runner 결과 | **실패** (`success=false`) |
| 시도 횟수 | 4 / 최대 4 |
| 총 액션 | 62 |
| MiniMax 호출 | 3 |
| MiniMax 오류 | 2 |
| MiniMax 토큰 | 381 |
| compile 호출 | 1 |
| grounding 호출 | 2 |
| failure | `evidence_unmet` |

## 실행 로그 요약

모든 시도가 `PageDown → 대상 클릭 → post_click 관찰 → timeout` 패턴을 반복했다.

- navigation settle은 완료됐다.
- attempt 1에서 `| trot rbot mm` 클릭 후 `post_click-timeout`
- attempt 2에서 MiniMax 오류가 기록됐고 `‘About — th = le 4 i if`를 클릭한 뒤 timeout
- attempt 3에서 다시 `| trot rbot mm`를 클릭한 뒤 timeout
- attempt 4에서 `고가 About th _ | ^, = 내 i 4`를 클릭한 뒤 timeout

액션 종류는 `capture=40`, `focus=1`, `key=8`, `click=5`, `type=1`, `settle=5`, `model=2`이다. 통계상 MiniMax 오류는 2회지만 `actions`에 보이는 명시적 `MiniMax-M3 error` 이벤트는 1개다. **모델 오류의 세부 로그가 완전히 일치하지 않는 계측 문제**가 있다.

## 실패 원인 분석

최종 failure는 저수준 입력 실패가 아니라 `evidence_unmet`이다. 즉, 클릭 API 자체는 호출됐지만 클릭 후 성공 토큰 또는 구성된 증거를 얻지 못했다.

선택 텍스트가 다음처럼 모두 불안정하다.

```text
| trot rbot mm
‘About — th = le 4 i if
고가 About th _ | ^, = 내 i 4
```

노트북 검색 결과나 Google 검색 UI를 의미하는 안정적인 텍스트가 아니다. 또한 attempt 1과 attempt 3에서 동일한 OCR 문자열을 다시 클릭했다. 재시도 때 관찰·선택 전략이 충분히 달라지지 않았다.

## 로그가 증명하는 것

- 4회 시도를 모두 소진했다.
- 매 시도 후 `post_click-timeout`이 발생했다.
- MiniMax 오류가 2회 집계됐다.
- evidence가 끝까지 만족되지 않았다.
- compile/grounding 호출 수가 분리되어 기록됐다.

## 로그만으로 증명할 수 없는 것

- Google 검색 결과 화면이 정상적으로 표시됐는지
- 클릭 대상이 검색 결과인지 광고·헤더·빈 영역인지
- timeout의 원인이 잘못된 클릭인지, 페이지 로딩인지, settle detector인지
- 실제 URL 입력값이 정확했는지 (`type=url`만 기록되고 URL 자체는 action 로그에 없음)
- 두 번째 MiniMax 오류의 정확한 원인

## 문제점 및 판단

- **높음 — 잘못된 후보 선택 가능성**: OCR 문자열이 목표와 직접 관련되지 않는다.
- **높음 — 재시도 전략 비효율**: 동일 후보 재클릭과 반복 `PageDown`이 관찰됐다.
- **높음 — 모델 오류 계측 불일치**: 통계 오류 2회와 action 이벤트 1개의 상세성이 다르다.
- **중간 — 케이스 정의와 검증 목표의 불명확성**: URL은 이미 검색 결과 URL이고 `require_pick=false`인데, 실제 루프는 반복 클릭을 수행했다. 검색 결과를 여는 목표와 결과를 선택하는 목표가 섞였을 가능성이 있다.

## 결론

**Runner 기준 실패.** 가장 강한 로그 근거는 `evidence_unmet`와 4회의 post-click timeout이며, 주된 원인은 OCR/grounding 후보 품질 또는 증거 조건 불일치로 보인다. 화면 캡처 없이는 Google 페이지 자체의 상태까지 단정할 수 없다.
