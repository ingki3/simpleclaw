# LLM transport and profile contract

SimpleClaw keeps `LLMRequest` and `LLMResponse` as its provider-neutral
canonical contract.  A named `llm.routes` entry chooses a backend, and that
backend declares both a wire `transport` and an endpoint `profile`.

## Transport boundaries

| Wire transport | Intended endpoints | Profile examples |
| --- | --- | --- |
| `openai_chat` | OpenAI Chat Completions-compatible APIs | `openai`, `openrouter`, `gemini-openai` |
| `gemini` | Gemini native API | `gemini` |
| `anthropic` | Anthropic Messages API | `anthropic` |
| `openai_responses` | Reserved extension point; no implementation yet | Future direct OpenAI/xAI use |

`openai_chat` is the reusable wire format for compatible endpoints, not the
internal canonical format.  Endpoint-specific schema cleanup, request extras,
and declared capabilities belong to profiles, never model-name branches.

## Gemini A/B policy

`gemini-openai` uses `openai_chat` with Google's
`https://generativelanguage.googleapis.com/v1beta/openai/` base URL.  It is
opt-in only.  Native `gemini` remains the default for any route requiring
native thinking, thought-signature/tool replay, or unverified multimodal
parity.  The credential-gated smoke matrix records text and exact schema
results; unverified tool/replay, reasoning, and image cases remain explicit
XFAILs rather than optimistic capability claims.

## OpenAI Responses extension

OpenAI Responses is a separate wire protocol.  A configured
`openai_responses` transport fails with an actionable “not registered” error
until a dedicated implementation is added.  Do not route it through
`openai_chat`: reasoning items and tool replay metadata must stay isolated in
transport-level `provider_data` when a Responses transport is introduced.

## Operations

Provider clients and credentials are created during service startup.  Editing
`llm.routes`, a backend's `transport`/`profile`, model, or credential requires
a service restart; LLM hot reload is intentionally unsupported.  Before an
operator elects any live route change: retain the legacy config, take a
timestamped backup, validate the new config, run the provider smoke, restart,
and verify health, channel delivery, scheduler, dashboard, and redacted logs.
