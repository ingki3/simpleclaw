---
name: google-calendar-skill
description: A skill to interact with Google Calendar (list calendars, list events, create, delete) using the user's OAuth credentials.
capability:
  domains: [calendar]
  read_only: false
  side_effects: true
  freshness_sensitive: true
  direct_answer: true
  requires_confirmation: true
  coverage: full_coverage
  input_contract: query.v1
  output_contract: asset_result.v1
input_contract:
  contract_id: skill.google-calendar-skill.input
  version: "1"
  owner_ref: {type: skill, name: google-calendar-skill}
  json_schema:
    type: object
    examples:
      - args: list --calendar-id primary --days 7 --limit 10
    properties:
      args: {type: string, minLength: 1, pattern: '^(?:calendars|list|create|delete)(?:\s|$)'}
    required: [args]
    additionalProperties: false
output_contract:
  contract_id: skill.google-calendar-skill.output
  version: "1"
  owner_ref: {type: skill, name: google-calendar-skill}
  json_schema: {type: object}
argument_binding:
  binding_id: shell-argv.v1
  owner_ref: {type: skill, name: google-calendar-skill}
  binding: {strategy: shell, order: [args]}
---

# Google Calendar Skill

Registered example: `{"skill_name":"google-calendar-skill","args":"list --calendar-id primary --days 7 --limit 10"}`

Mutation-capable asset: automatic read-only dispatch is denied; confirmation is required.

## Script

Target: `scripts/gcal.py`
