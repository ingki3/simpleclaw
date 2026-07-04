# Gemini Empty-Final / Tool Loop Refactoring Audit

## Verdict

- **Recommendation:** Small targeted refactor completed.
- **Confidence:** High.
- **Scope chosen:** Preserve provider finish diagnostics and add tool-loop logging for empty-final cases. Do **not** split `ToolLoopRunner` or move all fallback policy yet.

## Evidence

### AI Studio export summary

The supplied AI Studio response showed a model-level empty STOP rather than a tool failure or safety block:

| Signal | Value |
|---|---:|
| `functionResponse` count in request | 4 |
| Empty model turns in request | 4 |
| Final `finishReason` | `STOP` |
| Final output chars | 0 |
| `promptTokenCount` | 15073 |
| `totalTokenCount` | 15073 |

Interpretation: Gemini accepted the prompt/tool history and stopped without generating any output tokens. Runtime responsibility is therefore to preserve evidence/fallback quality and log enough diagnostics to explain the empty STOP.

### Current code metrics from clean `origin/dev`

| File | Lines | Main finding |
|---|---:|---|
| `src/simpleclaw/agent/tool_loop.py` | 720 | `ToolLoopRunner.run()` is long (`193` lines), but BIZ-414 fallback helpers are isolated and tested. |
| `src/simpleclaw/agent/orchestrator.py` | 1871 | Large orchestrator, but not the direct source of this empty-final failure. |
| `src/simpleclaw/llm/providers/gemini.py` | 427 | `send()`/`stream()` parsed text/tool calls/usage but did not preserve `finish_reason` or prompt/block diagnostics. |
| `src/simpleclaw/llm/models.py` | 160 | `LLMResponse` had no provider-neutral field for finish diagnostics. |

### Test coverage

Existing coverage already protects:

- empty final after tools,
- web_search title/URL evidence preservation,
- no-output/meta/error fallback handling,
- tool-loop budget exhaustion,
- Gemini max-token mapping and router propagation.

New coverage added:

- `tests/unit/test_gemini_provider_metadata.py`
  - empty `STOP` preserves `finish_reason` and zero output token diagnostics,
  - prompt/block reason is surfaced in diagnostics,
  - streaming path preserves final-chunk diagnostics too.

## Refactor Candidates

| Candidate | Benefit | Risk | Decision |
|---|---|---|---|
| Extract all empty-final fallback helpers from `tool_loop.py` into `empty_final_fallback.py` | Smaller `tool_loop.py`; easier isolated tests | Medium churn immediately after BIZ-414 hotfix; many existing tests import tool-loop behavior | **Defer**. Current helper group is cohesive enough and protected by tests. |
| Add provider finish diagnostics to `LLMResponse` | Explains `STOP + 0 output` without needing raw AI Studio payload; useful for logs and future provider triage | Low; optional fields preserve backward compatibility | **Done**. |
| Split `ToolLoopRunner.run()` into lifecycle phases | Could reduce the 193-line method | Higher risk; progress/mutation/footer/tool-dispatch concerns are intertwined | **Defer** until repeated changes hit the method again. |
| Add prompt-only “must answer after tools” rule | May reduce model empty STOP frequency | Prompt behavior can regress unrelated tasks; separate prompt governance issue is cleaner | **Separate follow-up if needed**. |

## What Changed

### `src/simpleclaw/llm/models.py`

Added optional fields to `LLMResponse`:

```python
finish_reason: str | None = None
diagnostics: dict | None = None
```

These are additive and backward-compatible. Existing callers can ignore them.

### `src/simpleclaw/llm/providers/gemini.py`

Added safe metadata extraction for both `send()` and `stream()`:

- `candidate.finish_reason` / `finishReason`,
- `response.prompt_feedback.block_reason` / `blockReason`,
- `usage_metadata.prompt_token_count`,
- `usage_metadata.candidates_token_count`,
- derived `empty_output_tokens` boolean.

### `src/simpleclaw/agent/tool_loop.py`

Updated final-answer logging so empty-final fallback logs include:

- final text char count,
- `finish_reason`,
- usage,
- diagnostics.

This makes the exact AI Studio failure mode visible in runtime logs if it recurs.

## Recommended Follow-up

1. **No broad refactor now.** BIZ-414 plus this diagnostics refactor addresses the observed failure class with limited risk.
2. If empty-final fallback changes again, then extract fallback policy into `src/simpleclaw/agent/empty_final_fallback.py` with dedicated tests.
3. If Gemini still frequently returns empty `STOP`, create a separate prompt-governance issue to add a generic final-answer-after-tools instruction in `prompts/system/tool_usage.yaml` or the relevant system prompt file.

## Do Not Change Yet

- Do not introduce LangGraph/LangChain.
- Do not change Gemini model/API key/config.
- Do not rewrite the whole tool loop.
- Do not add domain-specific musical/search heuristics to the orchestrator.
