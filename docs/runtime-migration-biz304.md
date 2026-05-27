# BIZ-304 운영 경로 마이그레이션 런북

목표:
1. 기존 `~/.simpleclaw` 런타임 자산을 `~/.simpleclaw-agent/default` 로 이전
2. `~/.simpleclaw` 를 배포 트리(코드)로 재구성
3. 배포 제외 파일(.gitignore 대상) 중 실행 필수 파일만 선별 복사

## 0) 사전 점검

```bash
set -euo pipefail

# 실행 중 프로세스 정지 (예시)
pkill -f "scripts/run_bot.py" || true
pkill -f "simpleclaw.daemon" || true

test -d ~/.simpleclaw
```

## 1) 롤백 가능한 백업 생성

```bash
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$HOME/.simpleclaw.backup.$STAMP"
cp -a "$HOME/.simpleclaw" "$BACKUP_DIR"
echo "backup=$BACKUP_DIR"
```

롤백 경로:

```bash
rm -rf "$HOME/.simpleclaw"
mv "$BACKUP_DIR" "$HOME/.simpleclaw"
```

## 2) 런타임 자산 이전 (`~/.simpleclaw` → `~/.simpleclaw-agent/default`)

`scripts/migrate_local_dir.py` 는 `--source` 를 받아 어떤 디렉터리에서든 라이브 자산만 선별 이전할 수 있다.

```bash
.venv/bin/python scripts/migrate_local_dir.py \
  --source ~/.simpleclaw \
  --target ~/.simpleclaw-agent/default
```

검증:

```bash
test -f ~/.simpleclaw-agent/default/AGENT.md
test -d ~/.simpleclaw-agent/default/workspace
```

## 3) `~/.simpleclaw` 를 배포 트리로 재구성

```bash
rm -rf ~/.simpleclaw
git clone <SIMPLECLAW_REMOTE_URL> ~/.simpleclaw
cd ~/.simpleclaw
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
```

## 4) 배포 제외 파일 중 필수 파일만 선별 복사

아래는 `.gitignore` 기준으로 "실행에 필요한 것만" 복사하는 최소 셋이다.

- `config.yaml` (필수)
- `.env` (운영 환경변수를 파일로 관리하는 경우에만)
- `web/admin/.env.local` (Admin Web 사용 시)

예시:

```bash
cp "$BACKUP_DIR/config.yaml" ~/.simpleclaw/config.yaml
test -f "$BACKUP_DIR/.env" && cp "$BACKUP_DIR/.env" ~/.simpleclaw/.env || true
test -f "$BACKUP_DIR/web/admin/.env.local" && \
  cp "$BACKUP_DIR/web/admin/.env.local" ~/.simpleclaw/web/admin/.env.local || true
```

## 5) 실행 검증

```bash
cd ~/.simpleclaw
.venv/bin/python -m pytest -q tests/unit/test_bot_wiring_paths.py
.venv/bin/python scripts/run_bot.py --help >/dev/null
```

운영 기동 후 로그 확인:

```bash
nohup .venv/bin/python scripts/run_bot.py > ~/.simpleclaw/bot.log 2>&1 &
sleep 2
tail -n 50 ~/.simpleclaw/bot.log
```

## DoD 체크리스트 매핑

- [x] `~/.simpleclaw-agent/default` 로 런타임 자산 이전: 2단계
- [x] `~/.simpleclaw` 배포본 실행 검증: 5단계
- [x] 비배포 필수 파일 선별/복사 내역: 4단계
- [x] 롤백 경로 명시: 1단계