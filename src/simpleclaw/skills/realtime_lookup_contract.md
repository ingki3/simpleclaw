# realtime-lookup-skill contract

`realtime-lookup-skill` is the evidence-producing runtime skill used for live-fact questions (news, weather, sports scores, stock/market data). The orchestrator may call it internally before asking the model to regenerate a final answer, specifically to avoid synthetic `web_fetch` assistant `tool_calls` in provider history.

## Invocation

```json
{
  "name": "execute_skill",
  "arguments": {
    "skill_name": "realtime-lookup-skill",
    "args": "<original user query>"
  }
}
```

The skill should perform any required search/fetching itself and return a single JSON object.

## Return shape

```json
{
  "kind": "news|weather|sports|stocks|general",
  "query": "<normalized query>",
  "freshness": "live|recent|stale|unknown",
  "evidence": [
    {"title": "<source title>", "url": "https://...", "published_at": "optional ISO time", "snippet": "optional quote"}
  ],
  "facts": ["short factual claims grounded in evidence"],
  "limitations": ["missing source, stale timestamp, auth/API failure, etc."]
}
```

## Runtime behavior

- If the model tries to produce a final answer for a live-fact question before evidence exists, the orchestrator executes this skill internally.
- The result is inserted as a provider-safe user evidence message (`[realtime-lookup evidence] ...`), not as a synthetic assistant function call.
- Final answers must be grounded in `facts`/`evidence`; when evidence is empty or stale, state the limitation instead of guessing.
