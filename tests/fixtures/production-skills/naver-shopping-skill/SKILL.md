---
name: naver-shopping-skill
description: A skill to search for products and their prices using the Naver Shopping API.
capability:
  domains: [shopping]
  read_only: true
  side_effects: false
  freshness_sensitive: true
  direct_answer: true
  requires_confirmation: false
  coverage: full_coverage
  input_contract: query.v1
  output_contract: asset_result.v1
input_contract:
  contract_id: skill.naver-shopping-skill.input
  version: "1"
  owner_ref: {type: skill, name: naver-shopping-skill}
  json_schema:
    type: object
    examples:
      - args: 'search --query "기계식 키보드" --limit 5'
    properties:
      args: {type: string, minLength: 1, pattern: '^(?:search)(?:\s|$)'}
    required: [args]
    additionalProperties: false
output_contract:
  contract_id: skill.naver-shopping-skill.output
  version: "1"
  owner_ref: {type: skill, name: naver-shopping-skill}
  json_schema: {type: object}
argument_binding:
  binding_id: shell-argv.v1
  owner_ref: {type: skill, name: naver-shopping-skill}
  binding: {strategy: shell, order: [args]}
---

# Naver Shopping Skill

Registered example: `{"skill_name":"naver-shopping-skill","args":"search --query \"기계식 키보드\" --limit 5"}`

Read-only product search only; no purchase, cart, or account mutation is registered.

## Script

Target: `scripts/search_products.py`
