---
name: kr-stock-skill
description: "Primary skill for Korean market numeric answers: KRX stocks, KOSPI/KOSDAQ indices, ETFs, USD/KRW and FX, latest snapshots, listings, history, and JSON market summaries. Use this over generic realtime/news search for Korean market numbers; use Naver mobile stock pages first for Korean stocks and KOSPI/KOSDAQ snapshots, with FinanceDataReader for listings, history, FX, and fallback checks."
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
  contract_id: skill.kr-stock-skill.input
  version: "1"
  owner_ref:
    type: skill
    name: kr-stock-skill
  json_schema:
    type: object
    properties:
      args:
        type: string
        minLength: 1
        pattern: '(^|\s)--json(?:\s|$)'
    required: [args]
    additionalProperties: false
output_contract:
  contract_id: skill.kr-stock-skill.output
  version: "1"
  owner_ref:
    type: skill
    name: kr-stock-skill
  json_schema:
    type: object
argument_binding:
  binding_id: shell-argv.v1
  owner_ref:
    type: skill
    name: kr-stock-skill
  binding:
    strategy: shell
    order: [args]
---

# KR Stock Skill

## Script

Target: `scripts/kr_stock.py`
