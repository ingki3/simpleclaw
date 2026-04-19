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

cd src && pytest && ruff check .

## Code Style

Python 3.11+: Follow standard conventions

## Recent Changes
- 006-heartbeat-cron-scheduler: Added Python 3.11+ + `apscheduler>=3.10` (scheduling), existing `simpleclaw` modules (memory, recipes)

- 001-persona-parser-engine: Added Python 3.11+ + `markdown-it-py` (마크다운 파싱), `tiktoken` (토큰 카운팅), `PyYAML` (config.yaml 로드)

<!-- MANUAL ADDITIONS START -->
## 개발 작업 시 참고
- AGENT.md 파일의 지침을 참고할 것

## Git 브랜치 관리 규칙

**반드시 아래 워크플로우를 따를 것. 예외 없음.**

### 브랜치 구조
```
feature/xxx  ──(PR)──>  dev  ──(PR)──>  main
```

### 규칙
1. **`main`과 `dev`에 직접 push 금지** — 반드시 PR을 통해서만 merge
2. **모든 작업은 feature branch에서 수행**:
   - `dev`에서 분기: `git checkout dev && git checkout -b feature/작업명`
   - 작업 완료 후 `origin`에 push: `git push -u origin feature/작업명`
   - PR 생성: `feature/작업명` → `dev`
3. **`dev` → `main` 릴리스**:
   - dev에서 충분히 검증된 후 PR 생성: `dev` → `main`
4. **커밋 메시지**: 변경 사항을 명확히 요약, 한글 또는 영문
5. **PR 생성 시**: `gh pr create`로 생성, Summary와 Test plan 포함
<!-- MANUAL ADDITIONS END -->
