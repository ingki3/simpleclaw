# SimpleClaw Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-04-18

## Active Technologies
- Python 3.11+ + `apscheduler>=3.10` (scheduling), existing `simpleclaw` modules (memory, recipes) (006-heartbeat-cron-scheduler)
- SQLite (cron jobs, execution logs, wait states) via existing patterns + HEARTBEAT.md status file (006-heartbeat-cron-scheduler)

- Python 3.11+ + `markdown-it-py` (마크다운 파싱), `tiktoken` (토큰 카운팅), `PyYAML` (config.yaml 로드) (001-persona-parser-engine)

## Project Structure

```text
src/
tests/
```

## Commands

```bash
# 전체 테스트
.venv/bin/python -m pytest tests/

# 단위 테스트만 (빠름, CI 필수)
.venv/bin/python -m pytest tests/unit/

# 특정 모듈 테스트
.venv/bin/python -m pytest tests/unit/test_agent.py -v

# 린터
.venv/bin/python -m ruff check src/
```

## Code Style

Python 3.11+: Follow standard conventions

## Recent Changes
- 006-heartbeat-cron-scheduler: Added Python 3.11+ + `apscheduler>=3.10` (scheduling), existing `simpleclaw` modules (memory, recipes)

- 001-persona-parser-engine: Added Python 3.11+ + `markdown-it-py` (마크다운 파싱), `tiktoken` (토큰 카운팅), `PyYAML` (config.yaml 로드)

<!-- MANUAL ADDITIONS START -->
## 개발 작업 시 참고
- AGENT.md 파일의 지침을 참고할 것

## 테스트 계층

| 계층 | 경로 | 목적 | API 키 필요 |
|------|------|------|-------------|
| 단위 테스트 | `tests/unit/` | 개별 모듈 로직 검증 | 아니오 |
| 통합 테스트 | `tests/integration/` | 모듈 간 연동 검증 | 일부 |
| 시나리오 테스트 | `tests/test_*_scenarios.py` | 실제 사용 시나리오 | 예 |
| E2E 테스트 | `tests/test_e2e_*.py` | 전체 파이프라인 | 예 |

## 테스트 작성 규칙

1. **새 기능 추가 시 반드시 단위 테스트 동반**
2. **LLM 호출이 필요한 테스트는 router를 mock** — `orchestrator._router.send = AsyncMock(...)`
3. **ReAct 응답 형식 사용** — mock 응답은 `"Thought: ...\nAnswer: ..."` 또는 `"Thought: ...\nAction: {...}"` 형식
4. **스킬 실행 테스트는 subprocess를 mock**
5. **async 테스트는 `@pytest.mark.asyncio` 필수**
6. **기능 변경 후 반드시 `pytest tests/unit/` 통과 확인** 후 전체 테스트 실행
7. **Cron 테스트는 `process_cron_message()` 사용** — 대화 히스토리와 격리됨

## Agent 실행

```bash
# 포그라운드
.venv/bin/python scripts/test_telegram.py

# 백그라운드
nohup .venv/bin/python scripts/test_telegram.py > .agent/bot.log 2>&1 &
```
<!-- MANUAL ADDITIONS END -->
