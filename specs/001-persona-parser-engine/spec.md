# Feature Specification: 페르소나 설정 파싱 엔진 및 프롬프트 인젝터

**Feature Branch**: `001-persona-parser-engine`
**Created**: 2026-04-17
**Status**: Draft
**Input**: User description: "AGENT.md, USER.md, MEMORY.md 등 마크다운 기반 페르소나 설정 파일을 파싱하여 구조화된 데이터로 변환하고, LLM API 호출 시 System Prompt에 자동 주입하는 엔진을 개발한다."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 페르소나 파일 로드 및 파싱 (Priority: P1)

사용자가 에이전트를 처음 기동하면, 시스템은 워크스페이스 내의 페르소나 설정 파일(`AGENT.md`, `USER.md`, `MEMORY.md`)을 자동으로 탐색하고 읽어들인다. 각 파일의 마크다운 콘텐츠는 섹션별로 구조화된 데이터로 변환되어, 이후 프롬프트 구성에 즉시 사용 가능한 상태가 된다.

**Why this priority**: 파일을 읽고 파싱하는 것은 전체 기능의 기반이다. 이 단계 없이는 프롬프트 주입도, 메모리 활용도 불가능하다.

**Independent Test**: 테스트용 마크다운 파일 3종을 준비하고, 파싱 엔진에 전달했을 때 각 파일의 섹션 제목과 본문이 올바르게 구조화된 데이터로 변환되는지 검증한다.

**Acceptance Scenarios**:

1. **Given** 워크스페이스에 `AGENT.md`, `USER.md`, `MEMORY.md`가 존재할 때, **When** 에이전트가 기동되면, **Then** 세 파일 모두 파싱되어 구조화된 페르소나 데이터가 생성된다.
2. **Given** 워크스페이스에 `AGENT.md`만 존재하고 나머지는 없을 때, **When** 에이전트가 기동되면, **Then** 존재하는 파일만 파싱하고 누락된 파일에 대해 경고를 남긴다.
3. **Given** 파일 내용이 비어 있거나 마크다운 형식이 아닐 때, **When** 파싱을 시도하면, **Then** 빈 구조체를 반환하고 에러 없이 진행한다.

---

### User Story 2 - System Prompt 자동 조립 및 주입 (Priority: P2)

파싱된 페르소나 데이터를 바탕으로, LLM API를 호출할 때 System Prompt가 자동으로 조립된다. AGENT.md의 역할/톤 정의가 먼저 배치되고, USER.md의 사용자 정보가 이어지며, MEMORY.md의 핵심 기억이 마지막에 추가되는 일관된 순서로 구성된다.

**Why this priority**: 파싱된 데이터가 실제로 LLM에 주입되어야 에이전트가 페르소나를 갖고 응답할 수 있다. 파싱(P1) 다음으로 핵심적인 기능이다.

**Independent Test**: 샘플 페르소나 데이터로 프롬프트 인젝터를 호출하여 생성된 System Prompt 문자열이 예상 순서와 형식을 따르는지 검증한다.

**Acceptance Scenarios**:

1. **Given** 세 파일 모두 파싱이 완료된 상태에서, **When** 프롬프트 인젝터가 System Prompt를 조립하면, **Then** AGENT → USER → MEMORY 순서로 내용이 배치된 단일 문자열이 생성된다.
2. **Given** MEMORY.md가 누락되어 해당 데이터가 비어 있을 때, **When** 프롬프트를 조립하면, **Then** AGENT → USER 부분만으로 유효한 System Prompt가 생성된다.
3. **Given** 조립된 System Prompt가 있을 때, **When** LLM API 호출이 수행되면, **Then** System Prompt가 API 요청의 system 메시지 필드에 포함된다.

---

### User Story 3 - 파일 경로 탐색 규칙 적용 (Priority: P3)

시스템은 프로젝트 루트의 `.agent/` 디렉토리와 전역 경로(`~/.agents/main/`)를 모두 탐색하여 페르소나 파일을 찾는다. 동일한 파일이 양쪽에 존재할 경우 로컬(`.agent/`)이 전역보다 우선한다.

**Why this priority**: 파일 탐색 경로의 우선순위는 멀티 프로젝트 환경에서 중요하지만, 단일 워크스페이스에서도 기본 동작이 보장되므로 초기에는 후순위로 구현 가능하다.

**Independent Test**: 로컬과 전역 경로 양쪽에 동일 이름의 파일을 배치한 뒤, 로컬 파일의 내용이 우선 적용되는지 확인한다.

**Acceptance Scenarios**:

1. **Given** `.agent/AGENT.md`와 `~/.agents/main/AGENT.md`가 모두 존재할 때, **When** 파일 탐색이 수행되면, **Then** `.agent/AGENT.md`의 내용이 사용된다.
2. **Given** `.agent/`에는 파일이 없고 `~/.agents/main/`에만 존재할 때, **When** 파일 탐색이 수행되면, **Then** 전역 경로의 파일이 사용된다.
3. **Given** 양쪽 경로 모두에 파일이 없을 때, **When** 파일 탐색이 수행되면, **Then** 해당 파일 유형에 대해 빈 데이터를 반환하고 경고를 기록한다.

---

### Edge Cases

- 페르소나 파일의 인코딩이 UTF-8이 아닌 경우 어떻게 처리하는가?
  - UTF-8을 기본으로 가정하되, 디코딩 실패 시 에러를 기록하고 해당 파일을 건너뛴다.
- 파일 크기가 매우 클 때(예: MEMORY.md가 수백 KB) 어떻게 처리하는가?
  - 파싱은 전체 수행하되, 프롬프트 조립 시 토큰 예산 한도를 적용하여 초과분을 잘라낸다.
- 에이전트 구동 중 파일이 외부에서 수정되면 어떻게 반영하는가?
  - 기동 시점에 1회 로드하는 것을 기본 동작으로 하며, 런타임 리로드는 이 feature 범위에 포함하지 않는다.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 지정된 경로에서 `AGENT.md`, `USER.md`, `MEMORY.md` 파일을 자동으로 탐색하여 읽어야 한다.
- **FR-002**: 시스템은 마크다운 파일의 헤딩(#, ##, ### 등)을 기준으로 섹션을 분리하고, 각 섹션의 제목과 본문을 구조화된 데이터로 변환해야 한다.
- **FR-003**: 시스템은 로컬 경로(`.agent/`)와 전역 경로(`~/.agents/main/`)를 순서대로 탐색하며, 동일 파일명이 존재할 경우 로컬을 우선해야 한다.
- **FR-004**: 시스템은 파싱된 데이터를 AGENT → USER → MEMORY 순서로 조립하여 단일 System Prompt 문자열을 생성해야 한다.
- **FR-005**: 시스템은 조립된 System Prompt를 LLM API 호출 시 system 메시지 필드에 자동으로 포함해야 한다.
- **FR-006**: 시스템은 특정 파일이 누락되거나 비어 있더라도 에러 없이 동작하며, 사용 가능한 파일만으로 프롬프트를 구성해야 한다.
- **FR-007**: 시스템은 프롬프트 조립 시 결과 문자열의 토큰 수가 설정된 예산을 초과하면 뒷부분(MEMORY 영역)부터 잘라내야 한다.

### Key Entities

- **PersonaFile**: 개별 마크다운 파일을 나타내는 개체. 파일 유형(AGENT/USER/MEMORY), 원본 경로, 파싱된 섹션 목록을 속성으로 가진다.
- **Section**: 마크다운 헤딩 하나에 대응하는 개체. 헤딩 레벨, 제목, 본문 텍스트를 속성으로 가진다.
- **PromptAssembly**: 여러 PersonaFile의 데이터를 조합한 최종 System Prompt. 조립 순서, 토큰 수, 잘라냄 여부를 속성으로 가진다.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 세 종류의 페르소나 파일이 모두 존재할 때, 에이전트 기동 후 1초 이내에 파싱이 완료되어야 한다.
- **SC-002**: 파일 1~3개가 누락된 모든 조합(총 7가지)에서 에이전트가 에러 없이 기동되어야 한다.
- **SC-003**: 조립된 System Prompt에 각 파일의 핵심 내용이 올바른 순서(AGENT → USER → MEMORY)로 포함되어야 한다.
- **SC-004**: 토큰 예산 초과 시 잘라냄이 적용되어, 생성된 프롬프트가 예산 한도를 넘지 않아야 한다.

## Assumptions

- 페르소나 파일은 표준 마크다운(CommonMark) 형식을 따르며, UTF-8로 인코딩되어 있다.
- 에이전트 기동 시 1회 로드하는 것을 기본으로 하며, 런타임 중 파일 변경 감지(hot-reload)는 이 feature의 범위에 포함하지 않는다.
- 토큰 예산의 기본값은 `config.yaml`에서 제공되며, 이 feature에서는 설정값을 읽어 적용하는 것만 담당한다.
- LLM API 호출 인터페이스는 Phase 1의 두 번째 항목("다중 LLM API 연동")에서 별도로 구현되며, 이 feature에서는 해당 인터페이스에 System Prompt를 전달하는 것까지만 책임진다.
