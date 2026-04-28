# Feature Specification: Recipe 실행 엔진

**Feature Branch**: `004-recipe-engine`
**Created**: 2026-04-17
**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recipe YAML 로드 및 파싱 (Priority: P1)

사용자가 `.agent/recipes/` 디렉토리에 `recipe.yaml` 파일을 배치하면, 시스템은 해당 파일을 파싱하여 레시피의 이름, 설명, 필요 변수(Parameters), 실행 단계(Steps)를 구조화된 데이터로 변환한다.

**Why this priority**: Recipe를 파싱하고 이해하는 것이 실행의 기반이다.

**Independent Test**: 테스트용 recipe.yaml을 작성하고, 파서가 모든 필드를 올바르게 추출하는지 검증.

**Acceptance Scenarios**:

1. **Given** `.agent/recipes/daily-report/recipe.yaml`이 존재할 때, **When** 레시피를 로드하면, **Then** 이름, 설명, 변수, 단계가 구조화된 데이터로 반환된다.
2. **Given** recipe.yaml에 필수 필드가 누락되어 있을 때, **When** 파싱을 시도하면, **Then** 유효성 에러가 반환된다.
3. **Given** recipes 디렉토리가 없을 때, **When** 디스커버리를 수행하면, **Then** 빈 목록이 반환된다.

---

### User Story 2 - Recipe 단계별 실행 (Priority: P2)

파싱된 레시피의 Steps를 순서대로 실행한다. 각 Step은 프롬프트(에이전트에게 보내는 지시), 스킬 호출, 또는 시스템 명령 중 하나이다. 변수(Parameters)는 실행 시점에 바인딩되어 각 Step의 프롬프트에 치환된다.

**Why this priority**: 파싱(P1) 후 실제 실행까지 되어야 Recipe가 의미를 가진다.

**Acceptance Scenarios**:

1. **Given** 3단계 레시피가 로드된 상태에서, **When** 실행하면, **Then** 각 단계가 순서대로 실행되고 전체 결과가 반환된다.
2. **Given** 레시피에 `${date}` 변수가 있을 때, **When** date="2026-04-17"로 실행하면, **Then** 해당 변수가 치환된 프롬프트가 사용된다.
3. **Given** 중간 단계가 실패할 때, **When** 에러가 발생하면, **Then** 실행이 중단되고 실패 단계와 에러 내용이 보고된다.

---

### Edge Cases

- recipe.yaml이 유효하지 않은 YAML 형식이면 파싱 에러를 반환한다.
- 변수가 정의되었으나 실행 시 값이 제공되지 않으면 에러를 반환한다.
- 빈 Steps 목록을 가진 레시피는 경고와 함께 성공으로 처리한다.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 `.agent/recipes/` 디렉토리에서 recipe.yaml 파일을 탐색하고 파싱해야 한다.
- **FR-002**: 시스템은 레시피의 Steps를 정의된 순서대로 실행해야 한다.
- **FR-003**: 시스템은 레시피 변수(Parameters)를 실행 시 바인딩하고 프롬프트 내 변수를 치환해야 한다.
- **FR-004**: 시스템은 Step 실행 중 실패 시 즉시 중단하고 에러를 보고해야 한다.
- **FR-005**: 시스템은 사용 가능한 레시피 목록을 조회할 수 있어야 한다.
- **FR-006**: 시스템은 v2 레시피의 `instructions`를 Cron 실행 시 에이전트에 직접 전달해야 한다.
- **FR-007**: 시스템은 v2 레시피 실행 시(Cron, 슬래시 명령어 모두) 내장 변수(`today`, `today_ko`, `weekday`, `now`)를 자동으로 치환해야 한다.

### Key Entities

- **RecipeDefinition**: 레시피 메타데이터. 이름, 설명, 변수 목록, 단계 목록을 속성으로 가진다.
- **RecipeStep**: 레시피의 개별 단계. 단계 유형(prompt/skill/command), 내용, 순서를 속성으로 가진다.
- **RecipeResult**: 레시피 실행 결과. 성공 여부, 각 단계별 결과, 실패 단계 정보를 속성으로 가진다.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: recipe.yaml 파일이 올바르게 파싱되어 모든 필드가 구조화된 데이터로 변환되어야 한다.
- **SC-002**: 다단계 레시피가 순서대로 실행되고 전체 결과가 반환되어야 한다.
- **SC-003**: 변수 치환이 모든 Step에 올바르게 적용되어야 한다.
- **SC-004**: 실패 시 정확한 실패 단계와 에러 내용이 보고되어야 한다.
- **SC-005**: v2 레시피가 Cron 실행 시 instructions가 에이전트에 전달되어야 한다.
- **SC-006**: 내장 변수(`{{ today }}` 등)가 v2 레시피 실행 시점의 KST 날짜/시간으로 치환되어야 한다 (Cron, 슬래시 명령어 모두).

## Assumptions

- Recipe는 `.agent/recipes/{recipe-name}/recipe.yaml` 형태로 저장된다.
- Step 유형은 초기에 `prompt`(LLM에 보내는 지시)와 `command`(셸 명령 실행)만 지원하며, `skill` 유형은 향후 확장한다.
- 변수 치환은 `${variable_name}` 형식을 사용한다.
