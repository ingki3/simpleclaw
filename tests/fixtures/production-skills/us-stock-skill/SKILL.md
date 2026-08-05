---
name: us-stock-skill
description: A skill to retrieve comprehensive US stock market data including company info, price charts, news, dividends, fundamentals, technical indicators, and screener search.
capability:
  domains: [market]
  read_only: true
  side_effects: false
  freshness_sensitive: true
  direct_answer: true
  requires_confirmation: false
  coverage: full_coverage
  input_contract: query.v1
  output_contract: asset_result.v1
input_contract:
  contract_id: skill.us-stock-skill.input
  version: "1"
  owner_ref: {type: skill, name: us-stock-skill}
  json_schema:
    type: object
    examples:
      - args: info --symbol AAPL --json
    properties:
      args: {type: string, minLength: 1, pattern: '(^|\s)--json(?:\s|$)'}
    required: [args]
    additionalProperties: false
output_contract:
  contract_id: skill.us-stock-skill.output
  version: "1"
  owner_ref: {type: skill, name: us-stock-skill}
  json_schema: {type: object}
argument_binding:
  binding_id: shell-argv.v1
  owner_ref: {type: skill, name: us-stock-skill}
  binding: {strategy: shell, order: [args]}
---

# US Stock Skill

Registered example: `{"skill_name":"us-stock-skill","args":"info --symbol AAPL --json"}`

## Script

Target: `scripts/us_stock.py`
