# Test Case 06 — `duckduckgo-linux`

- **실행 배치**: 2026-08-22 10-case run
- **목표**: DuckDuckGo에서 linux를 검색해줘
- **시작 URL**: `https://duckduckgo.com/?q=linux`
- **원본 데이터**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/stats.json`
- **원본 실행 로그**: `/tmp/grok-goal-f73a6e2ef612/implementer/test-run/full-run.log`

## 결과

| 항목 | 값 |
|---|---:|
| Runner 결과 | **실패** (`success=false`) |
| 시도 횟수 | 4 / 최대 4 |
| 총 액션 | 51 |
| MiniMax 호출 | 2 |
| MiniMax 오류 | 0 |
| MiniMax 토큰 | 1,416 |
| compile 호출 | 1 |
| grounding 호출 | 1 |
| failure | `evidence_unmet` |

## 실행 로그 요약

네 번의 attempt 모두 `PageDown → click → post_click-timeout`이었다. 선택 텍스트는 다음과 같다.

1. `(6 linux Q Protection. Privacy. Peace of mind. =`
2. `< > X © getstarted.tiktok.com/kr-boost-business?lang=koRattr_source=google&attr_`
3. `빠른 틱 톡 이 성 과 처 를 음 이어도, 원한다면, 예성 더 4`
4. `빠른 틱 톡 이 성 처 과 음 를 이어도, 원한다면, oil] @ 여 BEE 기 를 구시가 클 릭 함으로써 카이 TikTok ae 상업적 88,`

액션 종류는 `capture=30`, `focus=1`, `key=8`, `click=5`, `type=1`, `settle=5`, `model=1`이다.

## 실패 원인 분석

두 번째 후보에는 `getstarted.tiktok.com`이 직접 포함되고, 세 번째와 네 번째에도 TikTok 관련 텍스트가 섞여 있다. 목표인 DuckDuckGo/Linux 결과를 나타내는 일관된 후보가 아니다. 첫 후보의 `linux`도 보호/프라이버시 문맥에 섞여 있어 실제 검색 결과인지 알 수 없다.

## 로그가 증명하는 것

- 네 번 시도했고 MiniMax 저수준 오류는 없었다.
- compile 1회, grounding 1회가 집계됐다.
- 모든 클릭 후 settle이 timeout됐다.
- 최종 evidence는 만족되지 않았다.
- 후보 텍스트에 TikTok 및 광고/개인정보 문맥이 강하게 나타났다.

## 로그만으로 증명할 수 없는 것

- DuckDuckGo 페이지가 실제로 표시됐는지
- TikTok 텍스트가 광고, consent UI, iframe, overlay 중 무엇인지
- `linux` 검색 결과가 화면에 있었지만 OCR에서 누락된 것인지
- click이 페이지를 이동시켰는지, 아무 효과가 없었는지

## 문제점 및 판단

- **높음 — 비관련 target 선택**: TikTok 도메인과 광고 문구가 선택됐다.
- **높음 — OCR/영역 결합 오류 가능성**: 마지막 선택은 여러 문장과 언어가 하나의 후보로 합쳐졌다.
- **중간 — retry 전략의 비다양성**: 매번 PageDown 후 새 후보를 클릭하지만 기준 화면으로 돌아가거나 overlay를 명시적으로 해제하지 않는다.

## 결론

**Runner 기준 실패.** DuckDuckGo 자체의 네트워크 실패로 단정할 수 없으며, 현 로그로는 광고/overlay/노이즈가 Linux 검색 target보다 우선된 것이 가장 유력하다.
