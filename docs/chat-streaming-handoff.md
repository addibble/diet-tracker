# Chat Streaming and Tool-Calling Handoff

## Scope

This document captures the recent work on long-running chat requests through
OpenRouter, Gemini tool calling, spinner/progress visibility, and the mismatch
between upstream completion state and what the web client shows.

The focus has been the `/api/meals/chat/stream` path used by the meal log chat UI.

## User-visible problems we were trying to fix

### 1. Immediate or early failures were unclear

Observed symptoms:

- Cloudflare `502 Bad gateway` pages from `diettracker.kndyman.com`
- frontend error text claiming timeout even when the failure happened immediately
- OpenRouter credit-limit failures presenting as generic server failures

### 2. Long-running requests looked hung

Observed symptoms:

- spinner ran for many minutes with no indication whether anything was still alive
- upstream keepalive age stopped updating after roughly 10 to 15 seconds
- requests sometimes stayed open even after OpenRouter logs showed the generation
  had already finished

### 3. Gemini tool-calling failures were silent or ambiguous

Observed symptoms:

- OpenRouter activity showed `finish_reason="error"` with
  `native_finish_reason="MALFORMED_FUNCTION_CALL"`
- the backend did not convert that into a terminal failure in the streamed UI
- the spinner kept running because the backend heartbeat continued

### 4. The UI could not distinguish remote work from local tool execution

Observed symptoms:

- OpenRouter could finish with `finish_reason="tool_calls"`
- local tool execution could continue for a while afterward
- the spinner still mostly reflected stale upstream freshness instead of current work

## Important production observations

### Backend outage caused by `docker attach`

At one point the user attached to the backend container and hit `Ctrl-C`. That sent
SIGINT to uvicorn and shut down the backend. Frontend nginx logs showed upstream
connection refused errors and Cloudflare surfaced them as `502`.

This was not an OpenRouter problem. It explained the period where `/api/health`
and `/api/debug/logs` returned `502`.

### OpenRouter finished before the browser did

Two especially important OpenRouter metadata examples:

1. Generation ended with:
   `finish_reason="error"` and `native_finish_reason="MALFORMED_FUNCTION_CALL"`
2. Later generations ended successfully with:
   `finish_reason="stop"` or `finish_reason="tool_calls"`

In the second case, the web client still looked stalled. That told us the bottleneck
was no longer purely upstream. The backend either:

- was still doing local work after the provider round completed, or
- had parsed the upstream stream incorrectly and was still waiting for `[DONE]` or
  socket close.

## Changes already shipped

### `1b4c5dd` Improve chat gateway resilience and timeout handling

- normalized upstream error handling
- improved timeout/gateway messaging

### `90a61e8` Handle OpenRouter credit-limit errors in chat

- mapped OpenRouter `402` credit-limit failures to a specific frontend/backend error

### `6ae1bea` Add chat progress streaming and relax app-level timeouts

- introduced `/api/meals/chat/stream`
- added NDJSON heartbeat/status stream for the spinner
- relaxed nginx/app path timeouts for long requests

### `fbaf2e6` Show live upstream freshness in chat spinner

- displayed run id, latest upstream event, upstream age, request/completion ids

### `b272bca` Use OpenRouter SSE streaming with live spinner freshness

- switched chat internals to OpenRouter SSE streaming
- added upstream status callbacks from backend to stream route

### `4a1b0e5` Harden Gemini tool calling

This was the major tool-calling cleanup.

- explicitly instructed the model not to emit Python or namespaced tool calls
- narrowed the tool surface per turn instead of always exposing all tools
- used protocol-correct assistant/tool message history for tool loops
- added Gemini-specific forced-tool retry after generation-level tool-call failure
- treated `finish_reason="error"` as a real backend failure
- surfaced malformed function calls as `502` instead of spinning forever

### `e6ad76b` Show live chat activity during tool execution

- added separate activity tracking for:
  - upstream/OpenRouter work
  - local tool execution
  - finalization
  - backend state
- emitted `tool_call_started` and `tool_call_completed`
- updated the UI to show current activity source and active tool name

### `4d9820f` Flush chat status transitions immediately

This was added because the backend was still only sending status snapshots on the
heartbeat cadence. Short transitions like:

- `upstream_round_complete`
- `tool_calls_received`
- `tool_call_started`

could all happen between heartbeat ticks and never appear in the browser.

This commit changed `/api/meals/chat/stream` to:

- queue status snapshots whenever the callback fires
- flush them immediately to the NDJSON stream
- keep the older heartbeat snapshots only as idle fallback updates

## Code paths that matter

### Backend

- [backend/app/llm.py](/Users/drew/src/diet-tracker/backend/app/llm.py)
  - OpenRouter SSE parsing
  - Gemini/tool-calling logic
  - chat status event emission
- [backend/app/routers/parse.py](/Users/drew/src/diet-tracker/backend/app/routers/parse.py)
  - `/api/meals/chat/stream`
  - status queue, heartbeat fallback, NDJSON streaming

### Frontend

- [frontend/src/api.ts](/Users/drew/src/diet-tracker/frontend/src/api.ts)
  - streamed NDJSON progress parsing
- [frontend/src/pages/MealLogPage.tsx](/Users/drew/src/diet-tracker/frontend/src/pages/MealLogPage.tsx)
  - spinner text and metadata rendering

### Tests added or expanded

- [backend/tests/test_chat.py](/Users/drew/src/diet-tracker/backend/tests/test_chat.py)
  - stream error cases
  - rapid transition flush coverage
- [backend/tests/test_llm_tool_calling.py](/Users/drew/src/diet-tracker/backend/tests/test_llm_tool_calling.py)
  - Gemini tool-calling behavior
  - tool lifecycle event coverage
- [backend/tests/test_llm_streaming.py](/Users/drew/src/diet-tracker/backend/tests/test_llm_streaming.py)
  - terminal `finish_reason` without `[DONE]`

## Specific parser/protocol bugs that were found

### 1. Generation-level errors inside `HTTP 200`

We originally only treated transport/HTTP failures as fatal. That was wrong for
streamed OpenRouter responses. A provider round can return `HTTP 200` and still end
with:

- `finish_reason="error"`
- `native_finish_reason="MALFORMED_FUNCTION_CALL"`

The backend now converts that into a terminal failure.

### 2. Waiting forever for `[DONE]`

The OpenRouter/Gemini stream could present a terminal `finish_reason` but not close
the stream cleanly right away. We were still waiting on `aiter_lines()` for `[DONE]`
or socket close. The parser now exits once a terminal `finish_reason` is seen.

### 3. Appending `"None"` into content

Terminal chunks can carry `delta.content = null`. Our text conversion helper was
turning that into the literal string `"None"`. That is now filtered to `""`.

### 4. Tool loop history shape

We were appending the assistant tool-call message back into history without an
explicit `role`. That was not the expected protocol shape and likely hurt Gemini
tool-calling reliability.

### 5. Status transitions lost between heartbeats

The backend knew about quick state changes but the stream route only emitted a
snapshot every 2.5 seconds. This hid brief but meaningful transitions in the UI.

## Current streamed status model

Each status event now carries both:

### Activity state

- `activity_source`
  - `backend`
  - `openrouter`
  - `local_tool`
  - `finalizing`
- `last_activity_event`
- `last_activity_event_age_ms`
- `active_tool_name`

### Upstream/OpenRouter state

- `last_upstream_event`
- `last_upstream_event_age_ms`
- `last_upstream_status_code`
- `openrouter_request_id`
- `openrouter_completion_id`
- `upstream_cf_ray`
- `upstream_attempt`
- `upstream_round`

The important distinction is:

- activity answers "what is the app doing right now?"
- upstream freshness answers "when did OpenRouter last say anything?"

## Why the UI could still look wrong before `4d9820f`

OpenRouter could legitimately complete a round with:

- `finish_reason="tool_calls"`

and immediately hand control back to local tool execution. If that happened quickly,
the status sequence might be:

1. `upstream_round_complete`
2. `tool_calls_received`
3. `tool_call_started`

all between heartbeat emissions. The UI would then continue showing an old upstream
event until the next heartbeat, which made it look like nothing had changed.

`4d9820f` addresses exactly that.

## Validation status

Each code change above was validated with the repository test cycle:

```bash
./tools/run_test_cycle.sh
```

Latest validated state on `main` includes:

- backend lint passing
- backend tests passing
- frontend build passing

## Remaining things to watch in production

### 1. Long local tool runs

If local tools themselves take a long time, the UI should now show that a tool is
running, but the user experience may still need more granular tool-specific progress.

Likely candidates:

- workout import / bulk creation
- large database mutation sequences
- any tool that internally loops over many objects

### 2. Multiple tool calls in one round

The current UI shows one `active_tool_name`. If the model emits several tool calls in
sequence, the spinner will reflect the current/latest tool, but not a full queue.

### 3. Provider/model-specific streaming quirks

Gemini via OpenRouter has already shown several non-obvious behaviors:

- generation-level errors inside `HTTP 200`
- tool-call rounds with successful provider completion but delayed local follow-up
- possibly sparse keepalive behavior

If new symptoms appear, check OpenRouter activity first to determine whether the
provider round actually finished and with what `finish_reason`.

### 4. Browser-side buffering

The current path is configured for streaming, but any future CDN/nginx/client change
that reintroduces buffering can make the spinner regress even if backend state is
correct.

## Recommended next debugging steps if issues continue

1. Compare the browser spinner state with OpenRouter activity for the same request.
2. Check whether the final upstream `finish_reason` is `stop`, `tool_calls`, or `error`.
3. Check backend logs for emitted status events around the same timestamp.
4. Confirm the browser received `last_activity_event` transitions in order.
5. If the request is stuck after `tool_call_started`, instrument the specific tool
   executor path being run.
6. If the request is stuck after `final_response_received`, inspect the parse/save
   path in `chat_meal_endpoint()` after `chat_meal()` returns.

## Practical takeaway

The problem has not been a single timeout. It has been a chain of separate issues:

- infrastructure failures
- generation-level upstream errors hidden inside `HTTP 200`
- imperfect Gemini tool-calling protocol usage
- stream parser edge cases
- and finally, UI visibility lag caused by snapshot-only status emission

The current code on `main` should be much closer to the real state of the request.
If the UI still drifts from reality after `4d9820f`, the next likely bottleneck is
inside the local tool execution path itself rather than the OpenRouter request.
