# Test Case 07 — `github-explore`

- **실행 배치**: 2026-08-22 10-case run
- **목표**: GitHub Explore 페이지를 열어줘
- **시작 URL**: `https://github.com/explore`
- **원본 데이터**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/stats.json`
- **원본 실행 로그**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/full-run.log`

## 결과

| 항목 | 값 |
|---|---:|
| Runner 결과 | **실패** (`success=false`) |
| 시도 횟수 | 4 / 최대 4 |
| 총 액션 | 55 |
| MiniMax 호출 | 2 |
| MiniMax 오류 | 0 |
| MiniMax 토큰 | 1,244 |
| compile 호출 | 1 |
| grounding 호출 | 1 |
| failure | `evidence_unmet` |

## 실행 로그 요약

클릭 후보는 다음과 같다.

1. `ot TikTok©= 가입`
2. `AR …09`
3. `AR …09`
4. `GB TikTok 자 설 정 For 에서 Business 언제든지 이를 및 TikTok 해 제 할 Shop2| 수 있습니다 뉴스, 이벤트, 정 보 가 포 함 된 이 메 일 을 구 독 합니다. 사용`

모든 attempt는 navigation settle 후 `PageDown`, 클릭, 여러 post-click capture, `post_click-timeout` 순서로 종료됐다. attempts 2와 3은 동일한 `AR …09`를 다시 클릭했다.

## 실패 원인 분석

GitHub 또는 Explore를 나타내는 안정적인 후보가 한 번도 기록되지 않았다. 오히려 TikTok 가입/비즈니스/뉴스·이메일 문구가 선택됐다. 따라서 low-level click 성공과 semantic target 성공 사이의 차이가 명확하다.

## 로그가 증명하는 것

- 4회 시도와 4회 post-click timeout
- MiniMax 오류 0회
- compile/grounding 각각 1회
- attempts 2~3에서 동일 후보 반복
- 최종 evidence unmet

## 로그만으로 증명할 수 없는 것

- GitHub Explore 페이지가 실제로 로드됐는지
- `AR …09`가 무엇을 의미하는지
- TikTok 텍스트가 광고/overlay/iframe/OCR 오인식 중 무엇인지
- stale scene 또는 stale grounding이 실제 원인인지

## 문제점 및 판단

- **높음 — 목표와 무관한 후보**: GitHub/Explore 문자열이 없다.
- **높음 — OCR/grounding 오염**: 후보가 다국어 광고성 문구 또는 잘린 값이다.
- **중간 — 동일 후보 반복**: 재시도마다 새로운 scene을 사용했는지 확인하기 어렵고, 동일 target 재사용은 stale 후보 가능성을 제기한다.
- **중간 — 복구 경로 부족**: timeout 뒤 화면 재초기화나 target 재편찬 없이 같은 루프를 반복한다.

## 결론

**Runner 기준 실패.** GitHub가 접근 불가능했다고 단정하지 않으며, 로그상 target selection이 GitHub Explore와 무관해 evidence를 충족하지 못한 사례로 판단한다.
