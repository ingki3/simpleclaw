# Feature Specification: 명령 실행 보안 강화 및 멀티턴 도구 실행 루프

**Feature Branch**: `feature/security-and-multi-turn`
**Created**: 2026-04-24
**Status**: Implemented
**Reference**: [Hermes Agent](https://github.com/nousresearch/hermes-agent) (`tools/approval.py`, `tools/environments/local.py`, `run_agent.py`)

## 배경 및 동기

SimpleClaw의 명령 실행 파이프라인에 보안 취약점이 존재했음:
- LLM 라우터가 생성한 셸 명령이 검증 없이 `asyncio.create_subprocess_shell()`로 직접 실행
- subprocess에 API 키, 토큰 등 환경변수가 그대로 상속
- timeout 발생 시 프로세스가 kill되지 않는 경우 존재 (`agent.py`)
- 단일턴 실행 구조로 복잡한 작업(여러 스킬 순차 호출)을 처리할 수 없음

Hermes Agent의 보안 패턴과 멀티턴 루프 아키텍처를 참고하여 4가지 기능을 추가.

---

## User Scenarios & Testing

### User Story 1 - 위험 명령 감지 및 차단 (Priority: P1)

LLM이 위험한 셸 명령(파일 삭제, Git 파괴, DB drop 등)을 생성했을 때, 시스템이 실행 전에 차단하고 사용자에게 안전한 응답을 반환한다.

**Acceptance Scenarios**:

1. **Given** LLM 라우터가 `rm -rf /` 명령을 생성했을 때, **When** 명령 실행이 시도되면, **Then** `DangerousCommandError`가 발생하고 "Command blocked" 메시지가 반환된다.
2. **Given** `config.yaml`에 `allowlist: ["rm_recursive"]`이 설정되어 있을 때, **When** `rm -r /tmp/cache` 명령이 시도되면, **Then** 해당 패턴은 차단되지 않는다.
3. **Given** 유니코드 전각 문자로 `ｒｍ -rf /`가 입력될 때, **When** 명령 검사가 수행되면, **Then** NFKC 정규화 후 위험 패턴으로 감지된다.
4. **Given** Recipe YAML의 command step에 `DROP TABLE users;`가 포함될 때, **When** 레시피가 실행되면, **Then** 해당 step이 차단되고 실패 결과가 반환된다.

---

### User Story 2 - Subprocess 시크릿 스트리핑 (Priority: P1)

스킬, 레시피, 에이전트 명령 실행 시 API 키, 토큰 등 민감 환경변수가 subprocess에 전달되지 않는다.

**Acceptance Scenarios**:

1. **Given** `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`이 환경변수에 설정되어 있을 때, **When** 스킬 스크립트가 실행되면, **Then** 스크립트의 환경에서 해당 키가 존재하지 않는다.
2. **Given** `env_passthrough: ["GOOGLE_API_KEY"]`가 설정되어 있을 때, **When** 명령이 실행되면, **Then** `GOOGLE_API_KEY`만 subprocess 환경에 포함된다.
3. **Given** `PATH`, `HOME`, `LANG` 등 시스템 변수가 있을 때, **When** 필터링이 수행되면, **Then** 이 키들은 유지된다.

---

### User Story 3 - 프로세스 그룹 격리 (Priority: P2)

subprocess가 timeout되면 해당 프로세스와 자식 프로세스 전체가 종료된다.

**Acceptance Scenarios**:

1. **Given** 스킬 스크립트가 자식 프로세스를 생성하고 실행 중일 때, **When** timeout이 발생하면, **Then** 프로세스 그룹 전체가 SIGTERM → SIGKILL 순서로 종료된다.
2. **Given** 프로세스가 이미 종료된 상태일 때, **When** `kill_process_group`이 호출되면, **Then** `ProcessLookupError` 없이 안전하게 처리된다.

---

### User Story 4 - 멀티턴 도구 실행 루프 (Priority: P2)

사용자가 복합적인 요청("메일 확인하고 일정도 확인해줘")을 보내면, LLM이 여러 스킬을 순차적으로 호출하여 모든 결과를 종합한 응답을 생성한다.

**Acceptance Scenarios**:

1. **Given** 사용자가 "메일 확인하고 일정도 알려줘"라고 보낼 때, **When** 에이전트가 처리하면, **Then** gmail-skill → google-calendar-skill 순서로 호출되고, 두 결과를 합친 응답이 반환된다.
2. **Given** LLM이 첫 라우팅에서 `use_skill: false`를 반환할 때, **When** 에이전트가 처리하면, **Then** 루프가 1회만 실행되어 기존 단일턴과 동일하게 동작한다.
3. **Given** `max_tool_iterations: 3`으로 설정되어 있을 때, **When** LLM이 계속 도구를 요청하면, **Then** 3회 실행 후 루프가 종료되고 최선의 응답이 생성된다.
4. **Given** 이전 도구 실행 결과가 있을 때, **When** 다음 라우팅 호출이 수행되면, **Then** "Previous Tool Results" 섹션에 이전 결과가 포함되어 LLM이 맥락을 인지한다.

---

## 기술 구현 상세

### 모듈 구조

```
src/simpleclaw/security/
  __init__.py       # CommandGuard, filter_env, kill_process_group 등 export
  guard.py          # 위험 명령 패턴 매칭 (35개+ regex)
  env_filter.py     # 환경변수 차단/통과 필터 (fnmatch 패턴)
  process.py        # 프로세스 그룹 격리 (os.setsid + killpg)
```

### 적용 범위

| 실행 포인트 | Guard | Env Filter | Process Isolation |
|-----------|-------|-----------|-------------------|
| `agent.py:_execute_command()` | O | O | O |
| `skills/executor.py:execute_skill()` | - | O | O |
| `recipes/executor.py:_execute_command()` | O | O | O |

> 스킬 실행(`execute_skill`)은 `create_subprocess_exec` (배열 인자)를 사용하므로 셸 인젝션 위험이 낮아 Guard를 적용하지 않음.

### 위험 명령 패턴 카테고리 (35개)

| 카테고리 | 예시 | 패턴 키 |
|---------|------|---------|
| 파일 삭제 | `rm -rf`, `rm -f`, `shred` | `rm_recursive`, `rm_force`, `shred` |
| 디스크 파괴 | `mkfs`, `dd if=`, `fdisk` | `mkfs`, `dd`, `fdisk` |
| Git 파괴 | `git push --force`, `git reset --hard` | `git_force_push`, `git_reset_hard` |
| DB 파괴 | `DROP TABLE`, `TRUNCATE`, `DELETE FROM x;` | `drop_table`, `truncate_table`, `delete_no_where` |
| 권한 변경 | `chmod 777`, `chown -R root` | `chmod_wide`, `chown_root` |
| Pipe-to-shell | `curl \| bash`, `wget \| sh` | `curl_pipe_shell`, `wget_pipe_shell` |
| 시스템 명령 | `reboot`, `shutdown`, `init 0` | `reboot`, `shutdown`, `init_halt` |
| 시크릿 유출 | `env >`, `cat /etc/shadow` | `env_dump`, `read_shadow` |
| 컨테이너 탈출 | `--privileged`, `nsenter` | `privileged_container`, `nsenter` |

### 멀티턴 루프 흐름

```
process_message(text)
  |
  tool_messages = []
  for i in range(max_tool_iterations):
    |
    routing = _route_to_skill(text, tool_context=tool_messages)
    |
    if routing is None or not routing["use_skill"]:
      break
    |
    skill_name, result = _dispatch_routing(routing)
    tool_messages.append({"tool": skill_name, "result": result})
  |
  response = _generate_response(text, tool_messages=tool_messages)
```

### config.yaml 변경

```yaml
agent:
  max_tool_iterations: 5      # 멀티턴 최대 반복 횟수

security:
  command_guard:
    enabled: true
    allowlist: []              # 허용할 위험 패턴 키 (예: ["rm_recursive"])
  env_passthrough: []          # 차단 예외 환경변수 키
```

---

## 테스트 현황

| 테스트 파일 | 테스트 수 | 내용 |
|-----------|----------|------|
| `tests/unit/test_command_guard.py` | 38 | 위험 패턴 감지, allowlist, 정규화 |
| `tests/unit/test_env_filter.py` | 8 | 차단 패턴, passthrough, 기본값 |
| `tests/unit/test_process_isolation.py` | 6 | setsid, SIGTERM/SIGKILL, 에러 처리 |
| `tests/unit/test_multi_turn.py` | 7 | 멀티턴 루프, 반복 제한, 하위 호환 |

전체 **88개 관련 테스트** 통과.
