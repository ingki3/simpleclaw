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

Registered example: `{"skill_name":"naver-sports-skill","args":"--mode results --category kbo --date 2026-08-02 --limit 10 --json"}`

Provider structured enum fields are the only authoritative event-state source.

## Script

Target: `scripts/naver_sports.py`
