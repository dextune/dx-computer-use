# Test Case 03 — `youtube-search-lofi`

- **실행 배치**: 2026-08-22 10-case run
- **목표**: 유튜브에서 lofi 음악을 찾아줘
- **시작 URL**: `https://www.youtube.com/results?search_query=lofi+hip+hop`
- **원본 데이터**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/stats.json`
- **원본 실행 로그**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/full-run.log`

## 결과

| 항목 | 값 |
|---|---:|
| Runner 결과 | **성공** (`success=true`) |
| 시도 횟수 | 1 |
| 총 액션 | 17 |
| MiniMax 호출 | 2 |
| MiniMax 오류 | 0 |
| MiniMax 토큰 | 1,064 |
| compile 호출 | 1 |
| grounding 호출 | 1 |
| failure | 없음 |

## 실행 로그 요약

1. `pre_navigate`와 Desktop focus
2. 새 탭, omnibox 조작, URL 입력, Enter
3. `navigate_settle v=49`
4. `attempt-1 v=50`, `PageDown`, `scroll v=51`
5. MiniMax 모델 액션
6. `© lofi hip hop` 클릭
7. `post_click v=52`, `post_click` settle

액션 종류별 개수는 `capture=5`, `focus=1`, `key=5`, `click=2`, `type=1`, `settle=2`, `model=1`이다. 통계상 전체 MiniMax 2회 중 compile 호출은 action 모델 이벤트에 별도 표시되지 않는다.

## 선택 대상 분석

선택 텍스트는 다음과 같다.

```text
© lofi hip hop
```

이번 10개 중 성공 케이스 가운데 목표 어휘가 선택 텍스트에 가장 명확하게 포함된 사례다. 다만 `©`는 OCR 또는 아이콘 인식 artifact일 수 있으며, 이 텍스트만으로 실제 YouTube 영상 카드인지 제목/광고인지 확정할 수 없다.

## 로그가 증명하는 것

- navigation과 post-click settle이 timeout 없이 완료됐다.
- MiniMax 오류가 없었다.
- 한 번의 시도로 runner evidence가 만족됐다.
- 선택 텍스트에 목표 핵심어 `lofi hip hop`가 포함됐다.

## 로그만으로 증명할 수 없는 것

- 실제 브라우저가 YouTube에 있었는지
- `lofi hip hop`가 의도한 영상 카드인지 다른 텍스트 영역인지
- 클릭으로 영상을 열었는지 단순히 텍스트 영역을 클릭했는지
- 성공 evidence가 페이지 존재 확인인지 클릭 후 상태 확인인지

실행 배치에는 캡처 이미지가 저장되지 않았으므로 시각적 정답 여부는 별도 확인이 필요하다.

## 문제점 및 판단

- **중간 — semantic target 미확인**: 텍스트는 관련성이 높지만 role, bbox, 실제 영상 링크 여부가 보고서에 없다.
- **낮음 — layout 민감성**: 한 번의 `PageDown` 뒤 후보를 찾는 경로라 viewport와 광고 위치가 바뀌면 재현성이 떨어질 수 있다.
- **양호 — 실행 효율**: 1회 시도, 17액션, 오류 0회로 정상 경로는 효율적이다.

## 결론

**Runner 기준 성공이며, 목표와 관련된 OCR 텍스트도 확인됐다.** 그러나 화면 캡처가 없으므로 실제 올바른 lofi 영상을 선택했다는 시각적 판정까지는 할 수 없다.
