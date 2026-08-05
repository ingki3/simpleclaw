---
name: contract-fixture-step
description: Runtime Core가 모르는 operation-level 계약과 argv binding fixture.
capability:
  read_only: true
  side_effects: false
  requires_confirmation: false
  coverage: full_coverage
  input_contract: query.v1
  output_contract: asset_result.v1
input_contract:
  contract_id: skill.contract-fixture-step.input
  version: "1"
  owner_ref:
    type: skill
    name: contract-fixture-step
  json_schema:
    type: object
    properties:
      operation_value:
        type: string
        minLength: 1
    required:
      - operation_value
    additionalProperties: false
output_contract:
  contract_id: skill.contract-fixture-step.output
  version: "1"
  owner_ref:
    type: skill
    name: contract-fixture-step
  json_schema:
    type: object
    properties:
      operation_result:
        type: string
        minLength: 1
    required:
      - operation_result
    additionalProperties: false
argument_binding:
  binding_id: named-argv.v1
  owner_ref:
    type: skill
    name: contract-fixture-step
  binding:
    strategy: named
    order:
      - operation_value
---

# Contract fixture step

이 fixture는 테스트가 주입한 executor를 통해서만 실행되며 live 외부 호출을 하지 않습니다.
