# MiniMax M3 (global) API 구현 사양 — OpenAI 호환성 + 차이점

> 작성일: 2026-08-21 (KST)
> 작성 근거: 실측 응답 기반 (verify-minimax.sh, 단발 ping 호출)
> 상태: **사양 확정 아님** — 미검증 영역 표기. 정식 API 문서가 공개되면 갱신 필요.

## 1. 결론

**부분적으로 OpenAI Chat Completions API 호환.** 응답 envelope (`id`,
`object: "chat.completion"`, `choices[].message`, `usage`)과 SSE streaming
프레임워크는 OpenAI 표준을 따름. 그러나 본문 응답 처리 방식에서 다음
차이가 있음 — 이 차이를 모르고 그대로 매핑하면 **추론 텍스트가 desktop에
노출**됨:

| 항목 | OpenAI 표준 | MiniMax M3 실측 | 영향 |
| --- | --- | --- | --- |
| 추론 위치 | `reasoning_content` 별도 필드 | `message.content` 안에 ` ` `<think>...` ` `>`로 직렬화 | content를 그대로 yield하면 안 됨, strip 필수 |
| `reasoning_effort: "none"` | 일부 모델이 존중 | **모델이 무시** (항상 reasoning_tokens>0) | effort 파라미터만으로는 추론 off 불가능 |
| `message.name` | 보통 없음 | 항상 `"MiniMax AI"` | 무해, 그냥 무시 |
| `message.audio_content` | 없음 | 항상 `""` (빈 문자열) | 무해 |
| `usage.total_characters` | 없음 | 있음 (글자 수) | 무해 |
| 안전 플래그 3종 | 없음 | `input_sensitive`, `output_sensitive`, `input_sensitive_type` | MiniMax 특유 (중국 모델 관행으로 보임) |

이외의 헤더/필드(예: `system_fingerprint`, `service_tier`,
`prompt_tokens_details.cached_tokens`)는 OpenAI 표준과 의미 일치.

## 2. Base URL / 인증

```
POST https://api.minimax.io/v1/chat/completions
Authorization: Bearer <MINIMAX_API_KEY>
Content-Type: application/json
```

`MINIMAX_API_KEY`는 `sk-cp-` 접두사를 가진 125 chars 토큰.
`openai-compatible.mjs:152`에서 그대로 `Bearer ${apiKey}` 헤더로 보냄.

> 주의: 정확한 base URL은 본 문서 작성 시점에 실측한 값. 만약 MiniMax가
> 리전 prefix(`/global/v1`, `/cn/v1` 등) 또는 자체 도메인을 제공하면
> 별도 엔드포인트일 가능성 있음. 정식 문서 확인 권장.

## 3. 요청 스키마

`/chat/completions` 본문은 OpenAI 표준과 호환 추정. 실측 호출:

```json
{
  "model": "MiniMax-M3",
  "messages": [{"role": "user", "content": "ping"}],
  "max_tokens": 16,
  "stream": false
}
```

### 3.1 호환성 확인된 필드

| 필드 | 동작 | 비고 |
| --- | --- | --- |
| `model` | ✅ 필수. `"MiniMax-M3"` | |
| `messages` | ✅ OpenAI 형식 (`role`/`content`) | system/user/assistant/tool 모두 가능 추정 |
| `max_tokens` | ✅ 적용됨 (16 토큰에서 stop) | |
| `temperature` | 추정 ✅ | 미실측 |
| `stream` | ✅ 적용 (`true`/`false`) | streaming 검증은 §6 참조 |
| `tools` / `tool_choice` | ❓ 미검증 | OpenAI 표준 따를 가능성, computer-use에 critical |
| `top_p`, `frequency_penalty`, `presence_penalty`, `n`, `stop`, `seed`, `response_format`, `logprobs`, `user` | ❓ 미검증 | 대부분 OpenAI 호환 추정 |

### 3.2 모델 특유 필드 — `reasoning_effort`

```json
"reasoning_effort": "none"
```

**모델이 무시하는 것으로 실측됨.** 실측 응답:

```json
"usage": {
  "completion_tokens_details": {
    "reasoning_tokens": 18   // ← effort="none" 인데도 reasoning 발생
  }
}
```

가능성:
1. API는 받지만 모델이 무시 — reasoning은 항상 on
2. 별도 매개변수가 있음 (예: `thinking: {type: "disabled"}`, `disable_thinking: true`) — 미공개

→ **현재는 content strip에 의존**. 정식 문서에서 off 토글 확인 후
`openai-compatible.mjs`의 body 빌드 분기에 hook 추가 권장.

## 4. 응답 스키마 (비-streaming, 실측)

```json
{
  "id": "06d786f49994f311a2d16f285a2a2181",
  "object": "chat.completion",
  "created": 1787319284,
  "model": "MiniMax-M3",
  "choices": [
    {
      "finish_reason": "length",     // 또는 "stop", "tool_calls" 등
      "index": 0,
      "message": {
        "role": "assistant",
        "name": "MiniMax AI",         // ← OpenAI 표준에 없는 추가 필드
        "content": "<think>\nThe user just said \"ping\". This is a common informal greeting or test\n</think>\n\n",
        "audio_content": ""             // ← OpenAI 표준에 없는 추가 필드
      }
    }
  ],
  "usage": {
    "prompt_tokens": 177,
    "completion_tokens": 16,
    "total_tokens": 193,
    "total_characters": 0,             // ← OpenAI 표준에 없는 추가 필드
    "prompt_tokens_details": {
      "cached_tokens": 128
    },
    "completion_tokens_details": {
      "reasoning_tokens": 18
    }
  },
  "input_sensitive": false,           // ← MiniMax 특유 (안전 플래그)
  "output_sensitive": false,
  "input_sensitive_type": 0
}
```

### 4.1 필드 분류

**OpenAI 표준과 의미 일치** (그대로 매핑/표시 가능):

- `id`, `object`, `created`, `model`, `choices[].finish_reason`, `choices[].index`
- `choices[].message.role`
- `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens`
- `usage.prompt_tokens_details.cached_tokens` (Anthropic과 다른 OpenAI의 캐시 필드)
- `usage.completion_tokens_details.reasoning_tokens` (OpenAI 2024 추가)

**MiniMax 추가** (무해, 무시 가능):

- `choices[].message.name` — 항상 `"MiniMax AI"` (assistant 식별자)
- `choices[].message.audio_content` — 텍스트 모델에서는 항상 빈 문자열
- `usage.total_characters` — 응답 글자 수
- `input_sensitive`, `output_sensitive` — bool, 민감 정보 검열 플래그로 추정
- `input_sensitive_type` — int, `0`/비-제로 값의 의미 미공개

### 4.2 `content` 필드 — 핵심 차이

```text
<think>
The user just said "ping". This is a common informal greeting or test
</think>

```

- **추론이 본문에 직접 포함됨.** OpenAI 표준의 `reasoning_content` /
  `reasoning` / `thinking` 분리 필드는 사용되지 않음 (이번 실측 한 건에선).
- 추론이 끝나면 본문 시작 위치에 `\n\n`이 한 번 더 들어감 (실측).
- multi-turn 시 이전 assistant 응답에도 `<think>`가 있을 가능성 → desktop
  replay 시 같은 strip 필요.

## 5. 스트리밍 (SSE)

- 형식 추정: OpenAI 표준 `data: {json}\n\n` + 종결 `data: [DONE]\n\n`
- 본문 chunk 단위로 도착 → **delta에 `<think>...`가 잘려서 올 가능성**:
  - chunk 1: `<think>The u`
  - chunk 2: `ser said ping...`
  - chunk 3: `...</think>Hello.`
  - chunk 4: `<think>Cross-bl`
  - chunk 5: `ock ...</think>After`
- chunk 경계를 넘는 `<think>`를 부분 yield하면 desktop에 `<think>The u`
  같은 깨진 태그가 노출됨. **state machine으로 부분 보류** 필수.

### 5.1 우리 구현 — `openai-compatible.mjs`의 strip state machine

`gateway/providers/openai-compatible.mjs:173-203`에서:

```
thinkState ∈ {"normal", "in-think"}
thinkBuf : string (cross-chunk 보류)

delta.content 도착 → thinkBuf += delta.content
  while pos < thinkBuf.length:
    if thinkState == "normal":
      startIdx = thinkBuf.indexOf("<think>", pos)
      if startIdx == -1: out += thinkBuf.slice(pos); pos = end
      else: out += thinkBuf.slice(pos, startIdx); thinkState = "in-think"; pos = startIdx + 7
    else:  # in-think
      endIdx = thinkBuf.indexOf("</think>", pos)
      if endIdx == -1: break       # partial — next chunk까지 보류
      else: thinkState = "normal"; pos = endIdx + 8

thinkBuf = thinkBuf.slice(pos)
yield frames.text(out)   # out에 strip된 텍스트만
```

→ partial `<think>`도 절대 yield되지 않음, cross-chunk split 안전.
**unit test**: `test/test-strip-think.sh` (4-chunk split 케이스 포함).

### 5.2 `fullText` 누적

`fullText`에도 strip된 텍스트만 누적 (raw 안 함). `responseInfo`에 그대로
전달 — desktop 응답 표시 영역과 history에 깨진 태그가 남지 않음.

### 5.3 별도 reasoning 필드 (다른 모델 호환성)

`openai-compatible.mjs:193-201`은 다음 필드를 별도 검출하여
`frames.thinking(...)`으로 yield:

```js
const reasoningDelta =
  delta?.reasoning_content ??
  delta?.reasoning ??
  delta?.reasoning_details?.[0]?.text ??
  delta?.thinking ??
  null;
```

→ MiniMax M3는 이 필드를 안 쓰는 것으로 보이지만, **다른 모델의 reasoning
UI 호환성**을 위해 유지. 끄길 원하면 yield 부분 별도 PR.

## 6. 미검증 영역 — production 적용 전 확인 권장

| 항목 | 위험도 | 권장 검증 방법 |
| --- | --- | --- |
| **Streaming SSE 포맷 정확성** | 상 | `stream: true`로 호출, `curl -N`로 raw 수신, OpenAI 표준과 byte-level 비교 |
| **Function / tool calling** | 상 (computer-use에 필수) | 작은 tool schema 정의 후 호출, `tool_calls` delta 응답 확인 |
| **Multimodal (image)** | 상 | data URL을 user content에 넣어 호출, 거부 시 base64 다운샘플 또는 Chat→Responses 어댑터 |
| **Reasoning off 매개변수 (있다면)** | 중 | API 문서 확인 후 `openai-compatible.mjs` body 빌드 분기에 hook |
| **에러 응답 포맷** | 중 | 의도적으로 401/404/429 유발, 에러 envelope 표준 비교 |
| **Rate limit 헤더** | 중 | `x-ratelimit-*`, `retry-after` 헤더 응답 확인 |
| **다중 모델 ID** | 하 | 다른 모델 ID가 노출되는지 `GET /v1/models` 확인 |
| **Reasoning_effort 별도 필드 처리** | 하 | `"reasoning_effort"` 외 다른 추론 제어 필드 확인 |

## 7. 우리 구현 매핑

| 책임 | 위치 |
| --- | --- |
| 모델 엔트리 (`base_url`/`model`/`env_key`/`reasoning_effort`) | `models.json` "MiniMax M3 (global)" |
| Provider (`openai-compatible`) | `gateway/providers/openai-compatible.mjs` |
| 요청 body 빌드 (model/messages/tools) | `openai-compatible.mjs:127-144` |
| Bearer 인증 | `openai-compatible.mjs:150-153` |
| SSE 파싱 (data:, [DONE]) | `openai-compatible.mjs:175-205` |
| 추론 strip (state machine) | `openai-compatible.mjs:207-238` |
| `frames.text` / `frames.usage` / `frames.responseInfo` yield | `openai-compatible.mjs:239-247`, `259-268` |
| env 키 로드 | `scripts/load-env.sh` (`$ROOT/.env` → `$ROOT/../../.env` fallback) |
| direct upstream 검증 | `test/verify-minimax.sh` |
| strip unit test | `test/test-strip-think.sh` |

## 8. 권장 후속 작업

1. **streaming SSE 검증** — `curl -N` raw로 byte 단위 비교, OpenAI reference
   클라이언트(`openai` Python/Node SDK)와 동일 입력으로 cross-호환 테스트
2. **tool calling 검증** — `tools: [{type:"function", function:{...}}]` 정의 후
   computer-use 액션 1건 E2E. 실패 시 어댑터 필요
3. **MiniMax 공식 API 문서 입수** — `/v1/models`, `/v1/files`, `/v1/embeddings`
   등 다른 endpoint 존재 여부 + 정확한 reasoning off 매개변수 확인
4. **reasoning 토큰 비용 인식** — `completion_tokens_details.reasoning_tokens`
   가 `completion_tokens`에 포함되는지 별도 청구되는지. 비용 추적·UI
   표시에 영향

---

**문서 상태**: 미검증 영역 多. 정식 MiniMax API 문서 입수 후 갱신 필요.
**커밍 대상**: 본 문서는 커밍하지 않음 (사용자 지시). 정적본은
`docs/minimax-m3-api-spec.md`.