# Model Reliability Remediation — 10-Case Verification History

- **실행일:** 2026-08-22
- **실행 표준시간:** UTC 파일 시각 기준
- **프로젝트:** `dx-computer-use`
- **목적:** 모델 응답 신뢰성 개선 S1~S4 적용 전후를 동일한 10개 화면 케이스로 비교하고, Runner 판정과 실제 화면 증거를 분리 기록
- **semantic provider:** `minimax`
- **semantic model 기록값:** `MiniMax-M3`
- **실행 surface:** Linux X11/Xvfb sandbox (`grokbot-gateway-sandbox`, `:1337`)
- **테스트 케이스 정의:** `/tmp/hpcu-previous-10-cases.yaml`에서 복원한 이전 10개 케이스
- **관련 설계:** `docs/plan/16-model-response-reliability.md`
- **관련 제품 게이트:** `docs/plan/15-command-to-plan-runtime-remediation.md` U1~U9

> 이 문서는 실행 결과의 이력을 보존한다. `success=true`는 CaseRunner의
> `verified_success`를 뜻하며, 사람이 모든 목표를 확인했다는 뜻이 아니다.
> 특히 `human_confirmed=false`인 실행에서는 화면 증거와 Runner 판정을 별도로
> 읽어야 한다.

---

## 1. 재현 명령

```bash
# sandbox 확인
python -c 'from hpcu.platform.linux.sandbox import probe; print(probe())'

# 동일 10개 케이스 실행
python -m hpcu.cases \
  --cases /tmp/hpcu-previous-10-cases.yaml \
  --out artifacts/test-runs/current-10-case-s5a-20260822

# 전체 자동 테스트
python -m pytest tests/ --strict-markers

# 정적/컴파일 확인
git diff --check
python -m compileall -q hpcu tests
```

주의: `/tmp/hpcu-previous-10-cases.yaml`은 저장소 밖의 실행 입력이다.
재현하려면 기존 10개 테스트 문서 또는 아래 케이스 표를 이용해 같은 YAML을
복원해야 한다. 저장소에는 실행 시 사용한 `cases.txt`를 함께 보존한다.

---

## 2. 케이스 입력

| 순번 | ID | 목표 | 시작 URL |
|---:|---|---|---|
| 1 | `coupang-fresh-popular` | 쿠팡 프레시에서 인기 상품 하나 선택 | `https://www.coupang.com/np/categories/194276` |
| 2 | `google-search-notebook` | 200만원 이하 노트북 검색 | `https://www.google.com/search?q=laptop+notebook+under+2000000+KRW` |
| 3 | `youtube-search-lofi` | YouTube에서 lofi 음악 찾기 | `https://www.youtube.com/results?search_query=lofi+hip+hop` |
| 4 | `wikipedia-seoul` | Wikipedia Seoul 문서 열기 | `https://en.wikipedia.org/wiki/Seoul` |
| 5 | `bbc-news` | BBC 뉴스 메인 페이지 열기 | `https://www.bbc.com/news` |
| 6 | `duckduckgo-linux` | DuckDuckGo에서 linux 검색 | `https://duckduckgo.com/?q=linux` |
| 7 | `github-explore` | GitHub Explore 페이지 열기 | `https://github.com/explore` |
| 8 | `wikipedia-python` | Wikipedia Python 프로그래밍 문서 열기 | `https://en.wikipedia.org/wiki/Python_(programming_language)` |
| 9 | `mdn-javascript` | MDN JavaScript 문서 열기 | `https://developer.mozilla.org/en-US/docs/Web/JavaScript` |
| 10 | `example-org` | example.org 안내 페이지 열기 | `https://example.org` |

---

## 3. 실행 이력

### 3.1 Baseline — 변경 전

- **산출물:** `artifacts/test-runs/current-10-case-20260822/`
- **Runner 성공:** **7/10 (70%)**
- **총 액션 이벤트:** 147
- **MiniMax 호출:** 23
- **모델 오류:** 1
- **토큰:** 18,900
- **주요 실패:**
  - `coupang-fresh-popular`: `model_schema_invalid`
  - `google-search-notebook`: `plan_compile_failed` / `ReadTimeout`
  - `example-org`: `model_schema_invalid`
- **로그 시각:** `stats.json` 2026-08-22 16:04:07 UTC
- **원본 로그:** `full-run.log`

### 3.2 S1~S4 적용 후 1차 재실행

- **산출물:** `artifacts/test-runs/current-10-case-s1s4-20260822/`
- **Runner 성공:** **9/10 (90%)**
- **총 액션 이벤트:** 175
- **MiniMax 호출:** 30
- **모델 오류:** 1
- **토큰:** 24,915
- **성공으로 바뀐 케이스:** baseline의 세 실패 중 `coupang`, `google`, `example-org`
- **새 실패:** `wikipedia-python` — 4회 `none`/`decision_required`
- **로그 시각:** `stats.json` 2026-08-22 16:30:06 UTC
- **원본 로그:** `full-run.log`

### 3.3 after-navigate evidence gate 조정 후 최신 실행

- **산출물:** `artifacts/test-runs/current-10-case-s5a-20260822/`
- **Runner 성공:** **8/10 (80%)**
- **총 액션 이벤트:** 183
- **MiniMax 호출:** 34
- **모델 오류:** 0
- **토큰:** 31,009
- **실패:**
  - `wikipedia-seoul`: `decision_required`, 4회 action decision 후 클릭 없음
  - `github-explore`: `plan_compile_failed`, 2회 compile 후 화면 조작 없음
- **로그 시각:** `stats.json` 2026-08-22 18:48:26 UTC
- **원본 로그:** `full-run.log`
- **파일 해시:**

```text
bc1e01bea81c652c2ba9bb90ea4be2501085b29e3ca6789b9c84e7b7c40b612e9  full-run.log
3dd3780bb96743932d1670dcade3c02fb4a446f52e38c0b153088605b082ecd9  stats.json
```

이 실행의 결과는 1차 재실행보다 Runner 성공률이 낮다. 따라서 S1~S4의
효과를 단일 실행의 9/10 또는 8/10으로 일반화하지 않는다. provider 응답과
웹 화면은 실행마다 변동한다.

---

## 4. 최신 실행 케이스별 상세 결과

| 케이스 | Runner | attempts | actions | model calls | evidence | 판정 |
|---|---:|---:|---:|---:|---|---|
| Coupang | 성공 | 4 | 32 | 6 | satisfied | 화면은 Coupang 식품 카테고리. 상품을 정확히 고른 것은 미증명 |
| Google | 성공 | 0 | 12 | 1 | satisfied | URL·검색어·검색 결과가 확인되어 화면 목표와 일치 |
| YouTube | 성공 | 2 | 21 | 4 | satisfied | lofi 검색 후 영상 페이지 확인. 목표와 대체로 일치 |
| Wikipedia Seoul | 실패 | 4 | 27 | 5 | not evaluated | `none` decision 반복, 클릭/evidence 없음 |
| BBC News | 성공 | 1 | 17 | 3 | satisfied | 최종 화면이 `bbc.com/health`; 뉴스 메인 목표와 불일치 |
| DuckDuckGo Linux | 성공 | 2 | 17 | 3 | satisfied | Linux 검색 결과와 지식 패널 확인 |
| GitHub Explore | 실패 | 0 | 0 | 2 | not evaluated | compile 실패, 화면 조작 없음 |
| Wikipedia Python | 성공 | 3 | 26 | 5 | satisfied | Python 프로그래밍 문서 화면 확인 |
| MDN JavaScript | 성공 | 1 | 13 | 2 | satisfied | MDN JavaScript 문서 화면 확인 |
| Example.org | 성공 | 1 | 18 | 3 | satisfied | 최종 화면은 `iana.org/help/example-domains`; redirect 관련 화면 |

### 최신 실행의 중간 실패와 회복

`stats.json`은 최종 상태만이 아니라 일부 과거 실패 코드를 유지한다.
예를 들어 성공 케이스에 다음이 동시에 나타날 수 있다.

```json
{
  "success": true,
  "outcome": "verified_success",
  "failure_code": "decision_required",
  "failure": ""
}
```

이는 최종 성공 자체를 무효화하지는 않지만, 통계 소비자가 `failure_code`만
읽으면 성공을 실패로 오인할 수 있는 계측 결함이다. 최종 성공 시
`failure_code`를 빈 문자열로 정규화해야 한다.

---

## 5. 최신 증거 화면의 독립 확인

### 화면 목표와 일치한 케이스

- **Google:** 주소창에 `google.com/search?q=laptop+notebook+under+2000000+KRW`,
  검색창에 동일 검색어, 검색 결과가 보인다.
- **YouTube:** 주소창이 YouTube 영상 페이지이고 검색어가 `lofi hip hop`이다.
- **DuckDuckGo:** 검색어 `linux`, Linux 지식 패널과 검색 결과가 보인다.
- **Wikipedia Python:** 주소와 제목이 `Python (programming language)`로 일치한다.
- **MDN:** URL과 문서 제목이 `JavaScript`, MDN navigation/body가 보인다.

### Runner 성공이지만 엄격한 목표 증명이 부족한 케이스

- **Coupang:** 화면은 `coupang.com/np/categories/194276` 식품 카테고리다.
  하지만 선택 텍스트가 OCR 오염이 심하고 상품명으로 안정적으로 읽히지
  않는다. 최종 evidence token도 `리뷰`, `평점`, `인기`, `로켓배송` 같은
  범용 단어여서 특정 인기 신선식품 선택을 증명하지 못한다.
- **BBC:** 저장된 증거 화면은 `bbc.com/health`의 BBC Health 페이지다.
  `bbc.com/news` 메인 페이지를 열었다는 목표와 다르다. `BBC`, `Latest`,
  `Top stories` 토큰만으로 섹션까지 검증하지 못했다.
- **Example.org:** 저장된 증거 화면은 `iana.org/help/example-domains`의
  Example Domains 페이지다. `example.org`에서 연관 안내 페이지로
  redirect된 것으로 보이지만, redirect 허용 여부가 명시되어 있지 않다.

따라서 최신 실행의 정직한 지표는 다음처럼 병기해야 한다.

| 지표 | 값 |
|---|---:|
| Runner `verified_success` | 8/10 |
| evidence PNG가 존재하는 케이스 | 8/10 |
| 화면과 목표가 명확히 일치 | 5/10 |
| 화면/redirect까지 넓게 인정 | 최대 6/10 |
| 사람이 확인한 케이스 | 0/10 (`human_confirmed=false`) |

---

## 6. 실패 상세

### 6.1 `wikipedia-seoul`

- plan compile은 완료
- action decision 4회 모두 `none`
- 화면 조작 없음
- `evidence_status=not_evaluated`
- `failure=decision_required`
- 모델 호출 5회(compile 1 + action decision 4)

현실적인 원인은 start URL이 이미 목표 문서인데도 compiled pack의
`pick_required`/action decision이 페이지의 현재 상태를 충분히 반영하지
못한 것이다. after-navigate evidence가 해당 pack의 모든 token을 충족하지
못하면 현재 runner는 모델의 action decision으로 넘어가며, 모델이 계속
`none`을 반환해 종료한다.

### 6.2 `github-explore`

- plan compile 2회
- `compile_call_count=2`
- `action_count=0`
- `evidence_status=not_evaluated`
- `failure=plan_compile_failed`

S3 retry가 timeout/네트워크 오류에는 대응하지만, provider가 2회 연속
유효한 TargetingPack을 반환하지 않는 schema/semantic compile 실패를
해결하지는 않는다. 이 경우 lexical fallback으로 실행을 강행하지 않는
현재 fail-closed 동작은 유지해야 한다.

---

## 7. 구현 변경과 결과의 관계

이번 변경은 `docs/plan/16-model-response-reliability.md`의 S1~S4를 적용했다.

| 변경 | 결과에서 확인된 효과 |
|---|---|
| S1 raw response preview | `decision_diagnostics`에 `no JSON object`, `unbalanced braces`, `reason_code must be a string`가 남음 |
| S2 JSON 추출 강화/extra key 허용 | 일부 malformed/fenced/provider 응답을 파싱하고 실행을 계속함 |
| S3 transient retry/backoff | timeout/일시 오류가 발생한 실행에서 재시도 경로 제공. 최신 실행은 모델 오류 0회 |
| S4 reanalysis evidence fallback | Coupang, Wikipedia Python, Example.org 등 reanalysis schema 오류 후 증거가 충분한 경우 성공 판정 |
| after-navigate gate 조정 | URL 자체가 목표 페이지인 경우 compiled token이 모두 보이면 action decision 없이 검증 가능. 단, 섹션/상품 수준 검증은 해결하지 않음 |

### 중요 한계

- provider/model 응답 identity 기록은 여전히 `minimax/MiniMax-M3`다. 별도
  모델로 바뀌었다는 주장을 이 산출물은 증명하지 않는다.
- `failure_code`가 최종 성공 후에도 과거 `decision_required`로 남는다.
- `evidence_tokens`가 모델 편찬 token이므로 token이 범용적이면 false success가
  가능하다.
- action target과 실제 사용자 목표가 같은지 독립 oracle이 없다.
- `human_confirmed=false`이므로 사람 확인 성공률은 0/10이다.

---

## 8. 원본 산출물과 무결성

각 실행 디렉터리는 다음을 포함한다.

- `stats.json`: 전체 집계와 케이스별 최종 상태
- `<case-id>.json`: 케이스별 action/model/evidence 상세
- `<case-id>-evidence.png`: evidence 시점의 화면 캡처(성공 케이스에 한함)
- `cases.txt`: 실행 순서
- `full-run.log`: 터미널 원본 출력

최신 실행 디렉터리:

```text
artifacts/test-runs/current-10-case-s5a-20260822/
```

baseline 및 1차 개선 실행도 같은 방식으로 보존한다.

> 원본 stdout 로그에는 인증 키/Authorization 문자열이 없음을 grep으로
> 확인했다. 다만 장기 보관 정책이 바뀌면 raw model response와 화면 캡처의
> 개인정보/민감 정보 여부를 별도로 점검해야 한다.

---

## 9. 다음 보완 우선순위

1. 최종 성공 시 stale `failure_code`와 중간 실패 상태를 분리한다.
2. GoalEnvelope/PlanIR(U2~U5)로 페이지·섹션·상품 단위의 독립 evidence를
   표현한다.
3. BBC처럼 같은 사이트 내 잘못된 섹션을 통과시키지 않도록 URL path,
   title, heading, target scope를 EvidenceContract에 명시한다.
4. Coupang은 상품명·상품 카드·선택 상태를 함께 증명하지 않으면 성공으로
   보고하지 않는다.
5. `github-explore`처럼 compile이 실패한 케이스는 현행 fail-closed를
   유지하되, raw compile response를 별도 redacted artifact로 보존한다.
6. 동일 케이스를 여러 번 실행해 provider 변동을 포함한 held-out 제품 게이트
   결과로 승격한다. 단일 10-case run을 일반화하지 않는다.
