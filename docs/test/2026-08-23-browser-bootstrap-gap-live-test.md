# 2026-08-23 브라우저 Bootstrap 부재 — Live Sandbox 테스트 기록

## 1. 목적과 범위

Linux X11/Xvfb sandbox에서 화면 기반 브라우저 케이스를 실제로 실행해,
명령 → 계획 → grounding → 입력 → 검증 경로가 **브라우저가 시작되지 않은 빈
desktop**에서도 동작하는지 확인했다.

이 기록은 실행 결과를 보존하는 문서다. 성공/실패 판정은 runtime의 terminal
상태이며, 사람의 목표 달성 확인과 구분한다.

- 기준 브랜치: `main`
- 실행 환경: Linux X11/Xvfb `grokbot-gateway-sandbox`, noVNC 관찰 가능
- semantic provider/model: `minimax` / `MiniMax-M3`
- 안전 경계: 구매, 장바구니, 결제, 로그인, credential 입력을 실행하지 않음

## 2. 실행한 복잡 케이스

```yaml
id: naver-notebook-under-2m-most-recommended
goal: >
  네이버 쇼핑에서 200만원 이하 노트북을 찾아 추천 수가 가장 많은 상품 하나를
  선택해줘. 구매, 장바구니 추가, 결제, 로그인은 하지 마.
start_url: https://search.shopping.naver.com/
max_attempts: 5
max_model_calls: 12
```

재현 명령:

```bash
python -m hpcu.cases \
  --cases .tmp-naver-notebook-under-2m-most-recommended-live.yaml \
  --out artifacts/test-runs/live-naver-notebook-under-2m-most-recommended-20260823
```

## 3. Live 실행 결과

| 항목 | 결과 |
|---|---|
| Sandbox health | PASS |
| MiniMax plan compile | PASS — 1회, 1,160 tokens |
| Browser launch | FAIL — launch 단계 자체가 없음 |
| Physical/semantic input | 0회 |
| 최종 runtime 상태 | `failed` |
| 실패 코드 | `grounding_confidence_low` |
| 최종 scene | 빈 Xvfb desktop, `scene_version=1` |
| 최종 frame | `sandbox-shopping-1787476855401647097` |
| 실행 시간 | 6,621 ms |
| evidence | 빈 desktop screenshot만 생성; 목표 증거 없음 |

이전 단순 browser 케이스도 현재 환경에서 같은 성격의 사전 실패를 보였다.
Google 노트북 검색과 DuckDuckGo Linux 검색은 각각 plan compile 응답이 유효한
TargetingPack JSON으로 framing되지 않아 입력 0회 상태에서 fail-closed 됐다.
반면 본 복잡 케이스는 plan compile 자체는 성공했으므로, browser bootstrap
부재를 별도로 확인할 수 있었다.

## 4. 관찰된 결함

### P0 — Browser lifecycle/bootstrap 경로가 없음

현재 case adapter는 `start_url`이 있으면 `NAVIGATE` PlanIR 노드를 앞에
추가한다. 그러나 이 노드는 이미 관찰 가능한 browser surface/window가 있다고
가정한다.

빈 Xvfb desktop에서 실제 실행은 다음처럼 끝났다.

```text
빈 desktop scene
  → NAVIGATE node의 surface/target grounding
  → browser window 또는 주소 입력 surface를 찾지 못함
  → grounding_confidence_low
  → executor 호출 0회, browser launch 0회, terminal failure
```

`grounding_confidence_low`는 낮은 신뢰도에서 입력을 하지 않았다는 의미에서는
정확하다. 그러나 운영자가 보게 되는 증상은 "브라우저를 열어야 하는데 아무 것도
열지 못함"이다. 현재 failure code와 case report만으로는 browser process/window
bootstrap이 결여됐음을 직접 식별하기 어렵다.

## 5. 왜 fail-closed는 유지해야 하는가

이 실패를 좌표 클릭, 무작위 hotkey, 혹은 사이트별 문자열 fallback으로 해결하면
안 된다. 빈 desktop에서 그러한 방식은 대상 창과 입력 초점을 보장하지 못하며,
AGENTS.md의 stale/input/pixel-last 경계를 위반한다.

정식 해결은 platform capability에 기반한 명시적 lifecycle 단계여야 한다.

```text
Browser entry requested
  → platform이 browser launch capability를 보고
  → policy가 launch를 허용
  → launch browser process/application
  → 새 window가 scene에 관찰될 때까지 settle detector로 대기
  → 동일 session의 browser window를 grounding
  → 주소 입력 또는 browser-native navigation
  → postcondition과 화면 증거 검증
```

필요한 후속 작업:

1. `LAUNCH_APPLICATION` 또는 동등한 typed PlanIR action과 policy 분류를 정의한다.
2. Linux sandbox plugin에 명시적인 browser launch capability를 추가한다.
3. launch 이후 window observation/settle/postcondition 계약을 구현한다.
4. browser가 이미 열려 있는 경우와 없는 경우를 같은 entry contract에서 구분한다.
5. 빈 desktop → browser launch → URL navigation → visible page evidence의 e2e fixture를 추가한다.
6. browser가 없을 때의 실패를 `grounding_confidence_low`와 구분 가능한 lifecycle/capability failure로 기록한다.

비목표:

- browser process를 찾을 수 없다는 이유로 임의 좌표 클릭
- 로그인·CAPTCHA·보안 확인 우회
- 사이트별 CTA/상품/추천 수 사전 추가
- 제품 검색 결과만으로 구매·주문·장바구니 상태를 변경

## 6. 이번 설정 보강 및 관련 검증

MiniMax-M3가 reasoning에 출력 예산을 모두 소비해 JSON을 완성하지 못하는
실행을 앞서 관찰했다. 이를 위해 deployment와 fallback defaults를 일치시켜 다음
예산을 확대했다.

| 항목 | 이전 | 현재 |
|---|---:|---:|
| plan compile 출력 상한 | 1,024 | 131,072 |
| intent fill 출력 상한 | 256 | 32,768 |
| action decision / reanalysis 출력 상한 | 512 / 512 | 65,536 / 65,536 |
| provider call timeout | 30초 | 5분 |
| task 총 token 예산 | 4,096 | 393,216 |
| task 총 latency 예산 | 1분 | 30분 |
| task 호출 예산 | 3 | 12 |

CaseRunner도 case의 `max_model_calls`만 좁히고, deployment의 token/latency
한도는 유지하도록 변경했다. 작은 케이스 호출 예산에서 recovery reserve가 plan
compile을 막지 않도록 case-local reserve는 0으로 조정한다.

검증 결과:

| 검증 | 결과 |
|---|---|
| `tests/unit/test_cases.py` | PASS |
| `tests/unit/test_budget_policy.py` | PASS |
| `tests/unit/test_product_runtime_resource_policy.py` | PASS |
| 합계 | `16 passed in 0.48s` |
| Ruff (`runtime_config`, `product_runtime`, `case runner`, 관련 test) | PASS |
| `git diff --check` | PASS |

## 7. 결론

현재 runtime은 낮은 grounding confidence에서 browser를 추측 실행하거나 입력하지
않으므로, 안전 실패 특성은 유지한다. 하지만 browser entry가 요청된 빈 Linux
sandbox desktop을 실사용 가능 상태로 만들 lifecycle/bootstrap capability가 아직
없다.

따라서 이 live 실패는 provider 연결 장애나 노트북 추천 판단 실패가 아니라,
**browser가 존재하지 않는 start state를 product runtime이 지원하지 않는 P0
bootstrap 결함**으로 분류한다.
