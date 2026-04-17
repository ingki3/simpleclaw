# Implementation Plan: 다중 LLM API 연동 및 외부 CLI 툴 서브프로세스 래핑

**Branch**: `002-llm-router-cli-wrapper` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-llm-router-cli-wrapper/spec.md`

## Summary

config.yaml 설정 기반으로 Claude, Gemini, ChatGPT 등 LLM API 프로바이더를 유연하게 스위칭하는 라우팅 레이어와, 외부 CLI 도구를 비동기 서브프로세스로 호출하여 응답을 파싱하는 래핑 구조를 구현한다. Phase 1의 페르소나 엔진과 통합하여 System Prompt 자동 주입을 지원한다.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: `anthropic` (Claude API), `openai` (ChatGPT API), `google-genai` (Gemini API), `python-dotenv` (.env 로드)
**Storage**: N/A (stateless 요청-응답)
**Testing**: pytest, pytest-asyncio
**Target Platform**: macOS / Linux (로컬 데몬 환경)
**Project Type**: library (에이전트 코어의 하위 모듈)
**Performance Goals**: API 호출 자체는 외부 의존, 래핑 오버헤드 100ms 이내
**Constraints**: asyncio 기반 비동기, 환경 변수로 API 키 관리, CLI 타임아웃 120초
**Scale/Scope**: 단일 요청-응답, 동시 호출 지원

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Python-Only Core | PASS | 전체 Python 구현 |
| II. Lightweight Dependencies | PASS | 각 프로바이더 공식 SDK만 사용, 무거운 프레임워크 없음 |
| III. Configuration-Driven Flexibility | PASS | config.yaml로 LLM 선택, 수동 라우팅 원칙 준수 |
| IV. Explicit Security | PASS | API 키는 .env/환경 변수로 관리, 하드코딩 금지 |
| V. Test-After Implementation | PASS | pytest 기반 테스트 |
| VI. Persona & Memory Integrity | PASS | PromptAssembly 읽기 전용 소비 |
| VII. Extensibility via Isolation | PASS | llm/ 패키지로 독립 모듈화 |

**Gate Result**: ALL PASS

## Project Structure

### Documentation (this feature)

```text
specs/002-llm-router-cli-wrapper/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── llm_interface_api.md
└── tasks.md
```

### Source Code (repository root)

```text
src/simpleclaw/llm/
├── __init__.py              # 공개 API re-export
├── models.py                # LLMBackend, LLMRequest, LLMResponse 데이터 클래스
├── router.py                # config 기반 백엔드 선택 및 라우팅
├── providers/
│   ├── __init__.py
│   ├── base.py              # LLMProvider 추상 기반 클래스
│   ├── claude.py            # Anthropic Claude 프로바이더
│   ├── openai_provider.py   # OpenAI ChatGPT 프로바이더
│   └── gemini.py            # Google Gemini 프로바이더
└── cli_wrapper.py           # 외부 CLI 서브프로세스 래퍼

tests/
├── unit/
│   ├── test_router.py
│   ├── test_cli_wrapper.py
│   └── test_providers.py
└── integration/
    └── test_llm_pipeline.py
```

**Structure Decision**: 기존 `src/simpleclaw/llm/` 디렉토리를 확장. providers 서브패키지로 각 프로바이더를 격리하여 새 프로바이더 추가 시 기존 코드 수정 불필요.

## Complexity Tracking

> 위반 사항 없음.
