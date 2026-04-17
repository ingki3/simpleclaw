# Implementation Plan: 페르소나 설정 파싱 엔진 및 프롬프트 인젝터

**Branch**: `001-persona-parser-engine` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-persona-parser-engine/spec.md`

## Summary

마크다운 기반 페르소나 설정 파일(AGENT.md, USER.md, MEMORY.md)을 파싱하여 구조화된 데이터로 변환하고, LLM API 호출 시 System Prompt에 AGENT → USER → MEMORY 순서로 자동 주입하는 엔진을 구현한다. 로컬/전역 경로 이원 탐색과 토큰 예산 기반 잘라냄을 지원한다.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: `markdown-it-py` (마크다운 파싱), `tiktoken` (토큰 카운팅), `PyYAML` (config.yaml 로드)
**Storage**: 파일시스템 (마크다운 파일 읽기 전용), config.yaml
**Testing**: pytest
**Target Platform**: macOS / Linux (로컬 데몬 환경)
**Project Type**: library (에이전트 코어의 하위 모듈)
**Performance Goals**: 파일 3종 파싱 완료 1초 이내
**Constraints**: 외부 네트워크 불필요 (로컬 파일 I/O만), Python 표준 라이브러리 + 경량 패키지만 사용
**Scale/Scope**: 파일 3종, 각 최대 수백 KB

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Python-Only Core | PASS | 전체 구현을 Python으로 수행 |
| II. Lightweight Dependencies | PASS | markdown-it-py, tiktoken, PyYAML — 모두 경량 로컬 패키지, 외부 인프라 없음 |
| III. Configuration-Driven Flexibility | PASS | 토큰 예산, 파일 경로 등을 config.yaml에서 읽음 |
| IV. Explicit Security & Permission Scope | PASS | 파일시스템 읽기 전용, 민감 정보 미취급 |
| V. Test-After Implementation | PASS | pytest 기반 테스트 계획 |
| VI. Persona & Memory Integrity | PASS | 파싱 엔진은 읽기 전용으로 파일을 취급, 수정하지 않음 |
| VII. Extensibility via Isolation | PASS | 독립 모듈로 구현, 코어 오염 없음 |

**Gate Result**: ALL PASS — Phase 0 진행 가능

## Project Structure

### Documentation (this feature)

```text
specs/001-persona-parser-engine/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── persona_engine_api.md
└── tasks.md
```

### Source Code (repository root)

```text
src/
├── simpleclaw/
│   ├── __init__.py
│   ├── config.py              # config.yaml 로더
│   ├── persona/
│   │   ├── __init__.py
│   │   ├── parser.py          # 마크다운 파싱 엔진
│   │   ├── resolver.py        # 로컬/전역 경로 탐색 및 우선순위
│   │   ├── assembler.py       # System Prompt 조립 및 토큰 잘라냄
│   │   └── models.py          # PersonaFile, Section, PromptAssembly 데이터 클래스
│   └── llm/
│       └── __init__.py        # LLM API 인터페이스 스텁 (Phase 1-2에서 구현)

tests/
├── unit/
│   ├── test_parser.py
│   ├── test_resolver.py
│   └── test_assembler.py
├── integration/
│   └── test_persona_pipeline.py
└── fixtures/
    ├── agent.md
    ├── user.md
    └── memory.md
```

**Structure Decision**: 단일 프로젝트(Single project) 구조 선택. `src/simpleclaw/persona/` 패키지로 파싱 엔진을 격리하여, 이후 Phase에서 추가되는 모듈(llm, skill, scheduler 등)과 독립적으로 유지.

## Complexity Tracking

> 위반 사항 없음. 모든 Constitution 원칙을 충족함.
