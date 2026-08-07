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
  json_schema:
    type: object
    properties:
      mode: {}
      category: {}
      season:
        properties:
          code: {}
          title: {}
      date: {}
      fetched_at: {}
      freshness:
        properties:
          as_of: {}
      items:
        type: array
        items:
          properties:
            rank: {}
            team: {}
            name: {}
            wins: {}
            losses: {}
            draws: {}
            games_behind: {}
            title: {}
            status: {}
            event_state: {}
            status_code: {}
            participants:
              properties:
                away:
                  properties:
                    name: {}
                home:
                  properties:
                    name: {}
            score:
              properties:
                away: {}
                home: {}
            winner: {}
            date: {}
            fetched_at: {}
            source_url: {}
      warnings:
        type: array
        items: {}
      source:
        properties:
          urls:
            type: array
            items: {}
    x-simpleclaw-composition-fields:
      - mode
      - category
      - season.code
      - season.title
      - date
      - fetched_at
      - freshness.as_of
      - items[*].rank
      - items[*].team
      - items[*].name
      - items[*].wins
      - items[*].losses
      - items[*].draws
      - items[*].games_behind
      - items[*].title
      - items[*].status
      - items[*].event_state
      - items[*].status_code
      - items[*].participants.away.name
      - items[*].participants.home.name
      - items[*].score.away
      - items[*].score.home
      - items[*].winner
      - items[*].date
      - items[*].fetched_at
      - items[*].source_url
      - warnings[*]
      - source.urls[*]
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
