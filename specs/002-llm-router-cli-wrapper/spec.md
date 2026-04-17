# Feature Specification: 다중 LLM API 연동 및 외부 CLI 툴 서브프로세스 래핑

**Feature Branch**: `002-llm-router-cli-wrapper`
**Created**: 2026-04-17
**Status**: Draft
**Input**: User description: "config.yaml 설정 기반으로 Claude, Gemini, ChatGPT 등 다양한 LLM API를 유연하게 스위칭하는 라우팅 레이어와, 외부 CLI 도구를 서브프로세스로 호출하여 응답을 파싱하는 래핑 구조를 개발한다."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 설정 기반 LLM 모델 선택 및 메시지 전송 (Priority: P1)

사용자가 config.yaml에 사용할 LLM 모델(예: Claude)을 지정하면, 에이전트는 해당 모델의 API를 통해 메시지를 전송하고 응답을 받는다. 모델을 교체하고 싶을 때는 설정 파일의 모델명만 변경하면 코드 수정 없이 다른 LLM으로 전환된다.

**Why this priority**: LLM과의 통신은 에이전트의 핵심 기능이다. 설정 기반 모델 선택이 없으면 에이전트가 어떤 지능적 응답도 생성할 수 없다.

**Independent Test**: config.yaml에 특정 모델을 지정하고, 테스트 메시지를 전송했을 때 해당 모델로부터 유효한 응답이 반환되는지 검증한다.

**Acceptance Scenarios**:

1. **Given** config.yaml에 메인 LLM이 "claude"로 지정되어 있을 때, **When** 시스템 프롬프트와 사용자 메시지로 요청을 전송하면, **Then** Claude API를 통해 응답 텍스트가 반환된다.
2. **Given** config.yaml의 메인 LLM을 "gemini"로 변경했을 때, **When** 동일한 메시지로 요청을 전송하면, **Then** Gemini API를 통해 응답이 반환되며 코드 변경은 필요 없다.
3. **Given** config.yaml에 지정된 LLM의 API 키가 누락되어 있을 때, **When** 메시지 전송을 시도하면, **Then** 명확한 에러 메시지와 함께 실패하고 시스템은 안정적으로 유지된다.

---

### User Story 2 - 외부 CLI 도구 서브프로세스 래핑 (Priority: P2)

사용자가 config.yaml에 외부 CLI 도구(예: `claude cli`, `goose`)를 LLM 백엔드로 지정하면, 에이전트는 해당 CLI를 서브프로세스로 호출하고, 표준 출력의 응답 텍스트를 파싱하여 API 호출과 동일한 형태로 반환한다.

**Why this priority**: CLI 래핑은 API 키 없이도 로컬 CLI 도구를 LLM 백엔드로 활용할 수 있게 하며, 다양한 에이전트 도구와의 통합을 가능하게 한다.

**Independent Test**: 시스템에 설치된 CLI 도구(예: `echo` 명령으로 시뮬레이션)를 config에 지정하고, 메시지를 보냈을 때 CLI의 출력이 응답으로 파싱되어 반환되는지 검증한다.

**Acceptance Scenarios**:

1. **Given** config.yaml에 CLI 백엔드가 지정되어 있을 때, **When** 메시지를 전송하면, **Then** 해당 CLI가 서브프로세스로 호출되고 출력이 응답 텍스트로 반환된다.
2. **Given** 지정된 CLI 도구가 시스템에 설치되어 있지 않을 때, **When** 메시지 전송을 시도하면, **Then** CLI를 찾을 수 없다는 에러 메시지가 반환된다.
3. **Given** CLI 프로세스가 지정된 타임아웃(기본 120초) 내에 응답하지 않을 때, **When** 대기 시간이 초과되면, **Then** 타임아웃 에러가 발생하고 프로세스가 정리된다.

---

### User Story 3 - 페르소나 프롬프트와의 통합 (Priority: P3)

Phase 1에서 구현된 페르소나 파싱 엔진이 생성한 System Prompt가 LLM 호출 시 자동으로 주입된다. 사용자는 별도의 조작 없이 에이전트가 자신의 페르소나에 맞는 응답을 제공받는다.

**Why this priority**: 페르소나 통합은 에이전트의 개인화된 응답을 위해 필수적이지만, LLM 통신(US1)과 CLI 래핑(US2)이 먼저 동작해야 의미가 있다.

**Independent Test**: 페르소나 파일을 배치하고 LLM에 메시지를 보낸 후, 응답 생성 시 System Prompt에 페르소나 내용이 포함되었는지 확인한다.

**Acceptance Scenarios**:

1. **Given** 페르소나 파일이 존재하고 LLM 백엔드가 설정되어 있을 때, **When** 사용자가 메시지를 보내면, **Then** System Prompt에 페르소나 내용이 자동으로 포함된 상태에서 LLM이 호출된다.
2. **Given** 페르소나 파일이 존재하지 않을 때, **When** 사용자가 메시지를 보내면, **Then** System Prompt 없이 LLM이 호출되고 정상 응답이 반환된다.

---

### Edge Cases

- API 호출 중 네트워크 오류가 발생하면 어떻게 처리하는가?
  - 재시도 없이 즉시 에러를 반환하고, 에러 내용을 로그에 기록한다.
- API 응답이 빈 문자열인 경우 어떻게 처리하는가?
  - 빈 응답도 유효한 결과로 취급하되, 경고 로그를 남긴다.
- CLI 프로세스가 비정상 종료 코드를 반환하면 어떻게 처리하는가?
  - stderr 내용을 포함한 에러 메시지를 반환한다.
- 동시에 여러 LLM 호출이 발생하면 어떻게 처리하는가?
  - 각 호출은 독립적이며, 비동기(async) 호출을 지원하여 동시 실행이 가능하다.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 config.yaml의 설정을 읽어 지정된 LLM 백엔드(API 또는 CLI)로 메시지를 라우팅해야 한다.
- **FR-002**: 시스템은 최소 3개 이상의 LLM API 프로바이더(Claude, Gemini, ChatGPT)를 지원해야 한다.
- **FR-003**: 시스템은 외부 CLI 도구를 서브프로세스로 호출하고, 표준 출력을 응답 텍스트로 파싱하여 반환해야 한다.
- **FR-004**: 시스템은 API 키 등 인증 정보를 환경 변수(`.env`) 또는 config에서 읽어 사용해야 하며, 코드 내 하드코딩을 금지한다.
- **FR-005**: 시스템은 config.yaml의 LLM 설정 변경만으로 다른 모델로 전환할 수 있어야 하며, 코드 수정은 불필요해야 한다.
- **FR-006**: 시스템은 Phase 1에서 구현된 PromptAssembly의 assembled_text를 System Prompt로 주입하여 LLM을 호출할 수 있어야 한다.
- **FR-007**: 시스템은 CLI 서브프로세스에 대해 설정 가능한 타임아웃(기본 120초)을 적용하고, 초과 시 프로세스를 종료해야 한다.
- **FR-008**: 시스템은 비동기(async) 호출을 지원하여 여러 LLM 요청을 동시에 처리할 수 있어야 한다.

### Key Entities

- **LLMBackend**: 개별 LLM 프로바이더를 나타내는 개체. 백엔드 유형(API/CLI), 프로바이더 이름, 모델명, 인증 정보 참조를 속성으로 가진다.
- **LLMRequest**: LLM에 전송할 요청을 나타내는 개체. 시스템 프롬프트, 사용자 메시지, 대상 백엔드를 속성으로 가진다.
- **LLMResponse**: LLM으로부터 받은 응답을 나타내는 개체. 응답 텍스트, 사용된 백엔드 정보, 토큰 사용량(가능한 경우)을 속성으로 가진다.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 설정 변경만으로 3개 이상의 LLM 프로바이더 간 전환이 가능해야 한다.
- **SC-002**: API 기반 LLM 호출 시 요청부터 응답 수신까지의 과정이 에러 없이 완료되어야 한다.
- **SC-003**: CLI 래핑 호출 시 서브프로세스 기동부터 응답 파싱까지 정상 동작해야 한다.
- **SC-004**: API 키 누락, CLI 미설치, 타임아웃 등 모든 에러 시나리오에서 시스템이 명확한 에러 메시지를 반환하고 안정적으로 유지되어야 한다.
- **SC-005**: 페르소나 System Prompt가 LLM 호출에 자동으로 포함되어야 한다.

## Assumptions

- 각 LLM 프로바이더의 공식 Python SDK가 사용 가능하며, 별도의 인증 흐름(OAuth 등) 없이 API 키만으로 호출할 수 있다.
- CLI 도구는 사용자가 사전에 시스템에 설치해 둔 상태이며, 이 feature에서 설치를 담당하지 않는다.
- LLM 라우팅은 사용자가 config.yaml에 명시한 규칙 기반 수동 라우팅이며, 자동 분배(Auto-Router)는 범위에 포함하지 않는다.
- 스트리밍 응답(SSE)은 이 feature의 초기 범위에 포함하지 않으며, 전체 응답을 한 번에 받는 방식만 지원한다.
- 대화 이력(conversation history) 관리는 이 feature 범위에 포함하지 않으며, 단일 요청-응답만 처리한다.
