# Implementation Plan: 스킬 로더 및 MCP 클라이언트

**Branch**: `003-skill-loader-mcp-client` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)

## Summary

SKILL.md 파싱 기반 스킬 디스커버리, 로컬/전역 우선순위 적용, 비동기 서브프로세스 스킬 실행 엔진, 그리고 MCP 프로토콜 클라이언트를 구현한다.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: `mcp` (MCP 클라이언트 SDK), `markdown-it-py` (SKILL.md 파싱, 기존 의존성)
**Testing**: pytest, pytest-asyncio
**Project Type**: library (에이전트 코어의 하위 모듈)

## Constitution Check

| Principle | Status |
|-----------|--------|
| I. Python-Only Core | PASS |
| II. Lightweight Dependencies | PASS |
| III. Configuration-Driven | PASS |
| IV. Explicit Security | PASS |
| V. Test-After | PASS |
| VI. Persona Integrity | N/A |
| VII. Extensibility via Isolation | PASS |

## Project Structure

```text
src/simpleclaw/skills/
├── __init__.py
├── models.py           # SkillDefinition, SkillResult, ToolDefinition
├── discovery.py        # 스킬 디렉토리 탐색 및 SKILL.md 파싱
├── executor.py         # 스킬 스크립트 비동기 서브프로세스 실행
└── mcp_client.py       # MCP 서버 연결 및 도구 호출

tests/
├── unit/
│   ├── test_skill_discovery.py
│   ├── test_skill_executor.py
│   └── test_mcp_client.py
└── fixtures/
    └── skills/
        ├── test-skill/
        │   ├── SKILL.md
        │   └── run.py
        └── another-skill/
            ├── SKILL.md
            └── run.sh
```
