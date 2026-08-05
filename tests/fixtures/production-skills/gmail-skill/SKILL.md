---
name: gmail-skill
description: A skill to interact with Gmail, allowing the AI to search for emails and read their textual content using the user's OAuth credentials.
capability:
  domains: [email]
  read_only: true
  side_effects: false
  freshness_sensitive: true
  direct_answer: true
  requires_confirmation: false
  coverage: full_coverage
  input_contract: query.v1
  output_contract: asset_result.v1
input_contract:
  contract_id: skill.gmail-skill.input
  version: "1"
  owner_ref: {type: skill, name: gmail-skill}
  json_schema:
    type: object
    examples:
      - args: 'search --query "category:primary is:unread" --limit 5'
    properties:
      args: {type: string, minLength: 1, pattern: '^(?:search|read)(?:\s|$)'}
    required: [args]
    additionalProperties: false
output_contract:
  contract_id: skill.gmail-skill.output
  version: "1"
  owner_ref: {type: skill, name: gmail-skill}
  json_schema: {type: object}
argument_binding:
  binding_id: shell-argv.v1
  owner_ref: {type: skill, name: gmail-skill}
  binding: {strategy: shell, order: [args]}
---

# Gmail Skill

Registered example: `{"skill_name":"gmail-skill","args":"search --query \"category:primary is:unread\" --limit 5"}`

## Script

Target: `scripts/gmail.py`
