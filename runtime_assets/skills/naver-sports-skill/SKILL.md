---
name: naver-sports-skill
description: "Retrieve Naver Sports structured live scores, completed results, and standings with typed event states and source provenance."
capability:
  domains: [sports]
  intents: [current_result, completed_result, live_score, standings, ranking, leaderboard]
  read_only: true
  side_effects: false
  freshness_sensitive: true
  direct_answer: true
  requires_confirmation: false
  coverage: full_coverage
  input_contract: query.v1
  output_contract: asset_result.v1
  fallback_modes: [answer_with_evidence]
  retry_statuses: [failed_retryable]
input_contract:
  contract_id: skill.naver-sports-skill.input
  version: "1"
  owner_ref: {type: skill, name: naver-sports-skill}
  json_schema:
    type: object
    examples:
      - args: --mode results --category kbo --date 2026-08-02 --limit 10 --json
    properties:
      args: {type: string, minLength: 1}
    required: [args]
    additionalProperties: false
output_contract:
  contract_id: skill.naver-sports-skill.output
  version: "1"
  owner_ref: {type: skill, name: naver-sports-skill}
  json_schema: {type: object}
argument_binding:
  binding_id: shell-argv.v1
  owner_ref: {type: skill, name: naver-sports-skill}
  binding: {strategy: shell, order: [args]}
---

# Naver Sports Skill

Use the bundled stdlib helper as the numeric source of truth. Preserve returned values
exactly and never fill missing scores, states, ranks, or participants from memory, news,
search results, display text, or broadcast links.

## Registered SimpleClaw invocation

Call the registered skill with `args`; do not put a bare subcommand in `command`.

```text
execute_skill(skill_name="naver-sports-skill", args="--mode live --category kbo --date today --limit 10 --json")
execute_skill(skill_name="naver-sports-skill", args="--mode results --category kbo --date 2026-08-02 --limit 10 --json")
execute_skill(skill_name="naver-sports-skill", args="--mode standings --category epl --limit 10 --json")
execute_skill(skill_name="naver-sports-skill", args="--mode standings --category kbo --date today --season auto --limit 10 --json")
```

## Inputs

- `--mode live|results|standings`
  - `live`: current events with provider `STARTED` state only.
  - `results`: completed conventional schedule events with provider `ENDED|RESULT` only.
  - `standings`: team/player rankings for the selected season.
- `--category`: KBO/MLB/NPB, supported football, basketball, volleyball,
  general/tennis, golf, and eSports aliases documented by `--help`.
- `--date today|YYYY-MM-DD`: KST reference date. `results` requires the requested
  result date; `live` immediately refreshes the same endpoint and uses the second result.
- `--season`: optional standings season code/year/title. Omit it or pass the
  `auto` sentinel to select the latest enabled/active season. Any other value is
  an explicit provider season and fails with `INVALID_ARGUMENT` when unknown.
- `--limit`: 1 through 20.
- `--json`: compatibility flag; stdout is always one bounded JSON document.

## Output contract

The helper emits one JSON object with `ok`, `side_effect`, `source`, `mode`, `category`, `season`,
`date`, `fetched_at`, `freshness`, `items`, `warnings`, and `error`. Non-empty results also
include an asset-owned, deterministic, bounded `answer` for user-facing presentation.

- Every success, normal-empty, error, and compact fallback result declares the
  read-only execution contract as top-level `side_effect=false`.
- `answer` is derived only from allowlisted normalized sports fields, is capped at
  3,500 characters, and never serializes nested raw provider objects.
- Every result item preserves `event_state`, `status_code`, score, winner, date,
  `fetched_at`, and `source_url`.
- Cancelled, suspended, postponed, unknown, or score-incomplete events are never
  promoted to final results; `results` preserves their typed state in `excluded_events`.
- Normal empty is `ok=true`, `items=[]`, plus an explicit `message`.
- Upstream/input/schema failure is `ok=false` with stable `error.code`,
  `error.message`, and `error.retryable`.
- Naver data can lag the venue. State this limitation when answering.

## Direct diagnostics

```bash
python3 scripts/naver_sports.py --mode live --category kbo --date today --json
python3 scripts/naver_sports.py --mode results --category kbo --date 2026-08-02 --json
python3 scripts/naver_sports.py --mode standings --category pga --limit 10 --json
```
