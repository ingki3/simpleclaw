---
name: google-news-search-skill
description: "Google News RSS search for recent, date-bounded news discovery. Use for freshness-sensitive news context, market/stock narrative drivers, and source/date checks. Do not use as the source of truth for prices, index levels, financial metrics, or live scores."
capability:
  domains: [market, news]
  read_only: true
  side_effects: false
  freshness_sensitive: true
  direct_answer: true
  requires_confirmation: false
  coverage: full_coverage
  input_contract: query.v1
  output_contract: asset_result.v1
input_contract:
  contract_id: skill.google-news-search-skill.input
  version: "1"
  owner_ref:
    type: skill
    name: google-news-search-skill
  json_schema:
    type: object
    examples:
      - args: '--query "OpenAI" --format json'
    properties:
      args:
        type: string
        minLength: 1
        pattern: '(^|\s)--format(?:=|\s+)json(?:\s|$)'
    required: [args]
    additionalProperties: false
output_contract:
  contract_id: skill.google-news-search-skill.output
  version: "1"
  owner_ref:
    type: skill
    name: google-news-search-skill
  json_schema:
    type: object
argument_binding:
  binding_id: shell-argv.v1
  owner_ref:
    type: skill
    name: google-news-search-skill
  binding:
    strategy: shell
    order: [args]
---

# Google News Search Skill

## Script

Target: `scripts/google_news_search.py`
