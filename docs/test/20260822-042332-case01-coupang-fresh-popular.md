# Test Case 01 — `coupang-fresh-popular`

- **실행 배치**: 2026-08-22 10-case run
- **목표**: 쿠팡에서 쿠팡 프레시 카테고리에서 인기있는 상품 1개 골라줘
- **시작 URL**: `https://www.coupang.com/np/categories/194276`
- **원본 데이터**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/stats.json`
- **원본 실행 로그**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/full-run.log`

## 결과

| 항목 | 값 |
|---|---:|
| Runner 결과 | **성공** (`success=true`) |
| 시도 횟수 | 1 |
| 총 액션 | 19 |
| MiniMax 호출 | 2 |
| MiniMax 오류 | 0 |
| MiniMax 토큰 | 1,552 |
| compile 호출 | 1 |
| grounding 호출 | 1 |
| failure | 없음 |

## 실행 로그 요약

단일 시도에서 다음 흐름이 관찰됐다.

1. `pre_navigate` 캡처 → `Desktop` 포커스
2. 새 탭, omnibox 클릭, 위치 입력창 포커스, 전체 선택, URL 입력, Enter
3. `navigate_settle v=2~4` 캡처 후 `navigate_settle`
4. `attempt-1` 캡처 후 `PageDown`
5. MiniMax 모델 액션 1회
6. omnibox 이외의 대상 클릭 1회
7. `post_click v=7` 캡처 후 `post_click` settle

액션 종류별 개수는 `capture=7`, `focus=1`, `key=5`, `click=2`, `type=1`, `settle=2`, `model=1`이다. 통계의 MiniMax 2회 중 compile 호출은 `actions` 목록에 별도 `model` 이벤트로 기록되지 않는다.

## 선택 대상과 로그 품질

기록된 최종 선택 텍스트는 다음과 같다.

```text
{Le x 로 오늘 켓 바 밤 송 12 시 은 까지 내 일 주 도착! 문 해도 ^ =i — 수 - 아 가 Korean 물 타 스 베 베 사길 망 은 x 「 |
```

이 문자열은 한국어·영어·기호가 과도하게 섞여 있고, 상품명이나 쿠팡 프레시 항목으로 읽을 수 있는 안정적인 라벨이 아니다. 따라서 **클릭 API가 성공했다는 사실과 올바른 상품을 선택했다는 사실을 분리해야 한다.**

## 로그가 증명하는 것

- 탐색, 입력, 클릭 호출이 저수준 단계에서 오류 없이 반환됐다.
- compile 1회와 grounding 1회가 집계됐다.
- 클릭 후 캡처와 settle이 timeout 없이 완료됐다.
- Runner가 구성된 evidence 조건을 만족했다고 판정했다.

## 로그만으로 증명할 수 없는 것

- 실제로 쿠팡 페이지가 열렸는지
- 선택 텍스트가 실제 상품 카드인지 광고·헤더·다른 OCR 영역인지
- 클릭 좌표가 의도한 상품을 가리켰는지
- 클릭 후 상품 선택/페이지 이동이 실제로 발생했는지
- 성공 evidence가 의미적으로 올바른 상품 선택을 검증했는지

이번 실행에는 캡처 이미지가 저장되지 않았으므로 위 항목은 화면으로 확인할 수 없다.

## 문제점 및 판단

- **중요도 높음 — OCR/grounding 신뢰도 부족**: 선택 텍스트가 심하게 오염돼 semantic targeting의 품질을 확인할 수 없다.
- **중요도 중간 — 성공 판정의 독립성 부족 가능성**: `success=true`는 Runner의 evidence 판정이지, 사람이 확인한 시각적 성공 증거가 아니다.
- **중요도 낮음 — 실행 효율**: 19액션, 2회 모델 호출로 단일 시도 완료했지만, 정상 경로로 보이더라도 target 품질은 별도 검증이 필요하다.

## 결론

**Runner 기준 성공. 실제 사용자 목표 달성 여부는 미확인.** 이 케이스는 실행 경로와 통계 계측은 정상이나, 기록된 OCR 선택 텍스트만으로는 올바른 상품을 골랐다고 보고할 수 없다.
