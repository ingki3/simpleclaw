# 보안

SimpleClaw는 다층 보안 구조로 에이전트의 명령 실행을 제어합니다.

## 보안 계층

```
[1] CommandGuard — 위험 명령 패턴 감지 (35+ 패턴)
  ↓
[2] 환경변수 필터링 — subprocess에 API 키/토큰 전달 차단
  ↓
[3] 프로세스 격리 — os.setsid로 프로세스 그룹 분리
  ↓
[4] 타임아웃 — 무한 실행 방지 (기본 60초)
  ↓
[5] 화이트리스트 — Telegram 접근 제어
```

## CommandGuard

LLM이 생성한 셸 명령을 실행하기 전에 위험 패턴을 검사합니다.

### 감지 패턴 (35+)

| 카테고리 | 패턴 예시 |
|---------|----------|
| 파일 삭제 | `rm -r`, `rm -f`, `shred`, `mkfs`, `dd` |
| Git 파괴 | `git push --force`, `git reset --hard`, `git clean` |
| DB 파괴 | `DROP TABLE`, `TRUNCATE`, `DELETE` (WHERE 없이) |
| 권한 상승 | `chmod 777`, `chown -R root`, `sudo` |
| 파이프 공격 | `curl \| bash`, `wget \| sh` |
| 시스템 | `reboot`, `shutdown`, `kill -9` |
| 네트워크 | `nc`, `nmap` |

### 허용 목록

특정 위험 명령을 명시적으로 허용할 수 있습니다:

```yaml
security:
  command_guard:
    enabled: true
    allowlist:
      - "git push --force origin dev"  # 특정 명령만 허용
```

### 차단 시 동작

위험 명령이 감지되면 `DangerousCommandError`가 발생하고, 사용자에게 "Command blocked (dangerous pattern)" 메시지를 반환합니다.

## 환경변수 필터링

subprocess 실행 시 민감한 환경변수를 자동으로 제거합니다:

- `*_API_KEY` — API 키
- `*_TOKEN` — 인증 토큰
- `*_SECRET` — 시크릿
- `*_PASSWORD` — 비밀번호

`env_passthrough`로 특정 변수를 명시적으로 전달할 수 있습니다:

```yaml
security:
  env_passthrough:
    - "GOOGLE_API_KEY"    # 이 변수만 subprocess에 전달
```

## Workspace 격리

스킬 명령은 격리된 workspace 디렉토리에서 실행됩니다:

- 기본 경로: `.agent/workspace` (`config.yaml`의 `agent.workspace_dir`로 변경 가능)
- 스킬 subprocess의 `cwd`가 workspace로 설정됨
- 환경변수 `AGENT_WORKSPACE`로 절대 경로가 자동 주입됨
- 스킬 스크립트가 생성하는 파일이 프로젝트 루트를 오염시키지 않음

## 프로세스 격리

스킬/레시피의 셸 명령은 격리된 프로세스 그룹에서 실행됩니다:

- `os.setsid()`로 별도 프로세스 그룹 생성
- 타임아웃 시 `SIGKILL`로 프로세스 그룹 전체 종료
- stdout/stderr 캡처 및 크기 제한

## 서브 에이전트 격리

서브 에이전트는 추가적인 격리 환경에서 실행됩니다:

```yaml
sub_agents:
  max_concurrent: 3          # 동시 실행 제한
  default_timeout: 300       # 5분 타임아웃
  workspace_dir: "workspace/sub_agents"
  default_scope:
    allowed_paths: []        # 접근 가능 경로 제한
    network: false           # 네트워크 접근 차단
```

## 설정

```yaml
security:
  command_guard:
    enabled: true
    allowlist: []
  env_passthrough: []
```

## 관련 파일

- `src/simpleclaw/security/guard.py` — CommandGuard, 위험 패턴 정의
- `src/simpleclaw/security/env_filter.py` — 환경변수 필터링
- `src/simpleclaw/security/process.py` — 프로세스 격리 및 종료
