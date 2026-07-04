"""운영자 승인형 SimpleClaw 런타임 재시작 도구.

``restart_runtime``은 operator context와 명시 ``confirm=true``가 모두 충족될 때만
macOS LaunchAgent ``kickstart -k``를 실행한다. 재시작 직후에는 PID 변경,
Admin API health, Telegram/scheduler/dashboard 상태, FD count를 다시 조회해
운영자가 성공/실패와 다음 진단(``log_debug``) 필요 여부를 한 번에 볼 수 있게 한다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from simpleclaw.config import load_admin_api_config, load_daemon_config, load_telegram_config

RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]
Sleep = Callable[[float], None]
UrlOpen = Callable[[urllib.request.Request, float], Any]

DEFAULT_CONFIG_PATH = Path("/Users/simplist/.simpleclaw/config.yaml")
_LAUNCHAGENT_LABEL = "com.simpleclaw.agent"
_ALLOWED_METHOD = "launchagent_kickstart"
_SECRET_KEY_RE = re.compile(r"(token|secret|password|api[_-]?key|authorization)", re.I)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"(gh[pousr]_[A-Za-z0-9_]+)|"
    r"([A-Za-z0-9_]{16,}:[A-Za-z0-9_\-]{20,})"
)


def handle_restart_runtime(
    args: dict[str, Any],
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    scheduler: object | None = None,
    run_command: RunCommand | None = None,
    urlopen: UrlOpen | None = None,
    sleep: Sleep | None = None,
    cwd: str | Path | None = None,
) -> str:
    """Function Calling 핸들러용 JSON 문자열을 반환한다.

    Args:
        args: ``method``/``confirm``/``reason`` tool arguments.
        config_path: live ``config.yaml`` 경로. cwd/PID/Admin/Telegram 설정 조회 기준.
        scheduler: 주입된 CronScheduler/APScheduler 객체. post-check에만 사용한다.
        run_command: 테스트 주입용 subprocess runner.
        urlopen: 테스트 주입용 HTTP GET 함수.
        sleep: 테스트에서 재시작 대기를 제거하기 위한 주입점.
        cwd: 테스트 주입용 current working directory.

    Returns:
        재시작 시도와 post-health 검증 결과. 실패도 예외 대신 ``ok=false`` JSON으로
        반환한다.
    """
    runner = run_command or _run_command
    opener = urlopen or urllib.request.urlopen
    sleeper = sleep or time.sleep
    config = Path(config_path).expanduser()
    method = str(args.get("method") or _ALLOWED_METHOD)
    reason = str(args.get("reason") or "").strip()
    current_cwd = Path(cwd).expanduser() if cwd is not None else Path.cwd()

    payload: dict[str, Any] = {
        "ok": False,
        "method": method,
        "approval_required": False,
        "config_path": str(config),
        "reason": reason,
        "cwd_check": _cwd_check(current_cwd, config),
    }
    if method != _ALLOWED_METHOD:
        payload["error"] = f"unsupported method: {method}"
        return _dump(payload)
    if args.get("confirm") is not True:
        payload["approval_required"] = True
        payload["error"] = "restart_runtime requires explicit operator confirmation: confirm=true"
        return _dump(payload)
    if not payload["cwd_check"]["ok"]:
        payload["error"] = "live cwd check failed; refusing to restart from a non-live checkout"
        return _dump(payload)

    daemon_cfg = _safe_load_daemon_config(config)
    pid_file = Path(str(daemon_cfg.get("pid_file", ""))).expanduser()
    before_pid = _read_pid_file(pid_file)

    launchctl = runner(["launchctl", "kickstart", "-k", _launchagent_target()])
    payload["launchctl"] = _command_summary(launchctl)
    sleeper(1.0)

    after_pid = _read_pid_file(pid_file)
    target_pid = after_pid or before_pid or os.getpid()
    payload["pid"] = {
        "pid_file": str(pid_file),
        "before": before_pid,
        "after": after_pid,
        "changed": bool(before_pid and after_pid and before_pid != after_pid),
    }
    payload["process"] = _collect_process(target_pid, runner)
    payload["health"] = _collect_admin_health(config, opener)
    payload["post_checks"] = {
        "telegram": _telegram_check(config),
        "scheduler": _scheduler_check(scheduler, payload["health"]),
        "dashboard": _dashboard_check(payload["health"]),
        "fd": _fd_check(target_pid, runner),
    }

    payload["ok"] = (
        payload["launchctl"]["ok"]
        and payload["pid"]["changed"]
        and payload["health"]["ok"]
    )
    if not payload["ok"]:
        payload["next_step"] = "Run log_debug with action=errors or action=admin_api for restart failure details."
    return _dump(payload)


def _run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """LaunchAgent 재시작/조회 명령을 timeout과 함께 실행한다."""
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )


def _cwd_check(current_cwd: Path, config_path: Path) -> dict[str, Any]:
    """현재 cwd가 live config가 놓인 checkout인지 확인한다."""
    expected = config_path.parent
    try:
        ok = current_cwd.resolve() == expected.resolve()
    except OSError:
        ok = False
    return {"ok": ok, "cwd": str(current_cwd), "expected": str(expected)}


def _launchagent_target() -> str:
    """현재 사용자 GUI domain의 SimpleClaw LaunchAgent target을 반환한다."""
    return f"gui/{os.getuid()}/{_LAUNCHAGENT_LABEL}"


def _safe_load_daemon_config(config_path: Path) -> dict[str, Any]:
    """config 로드 실패가 전체 JSON 생성을 막지 않도록 빈 dict로 폴백한다."""
    try:
        return load_daemon_config(config_path)
    except Exception:  # noqa: BLE001
        return {}


def _read_pid_file(path: Path) -> int | None:
    """pid_file을 읽어 정수 PID로 반환한다. 없거나 깨졌으면 None."""
    if not str(path):
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def _command_summary(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """subprocess 결과를 redaction과 길이 제한을 거쳐 JSON에 담는다."""
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": _compact(_redact(result.stdout or "")),
        "stderr": _compact(_redact(result.stderr or "")),
    }


def _collect_process(pid: int, runner: RunCommand) -> dict[str, Any]:
    """재시작 후 대상 PID가 살아 있는지 ps로 확인한다."""
    result = runner(["ps", "-p", str(pid), "-o", "pid=,ppid=,stat=,etime=,command="])
    return {
        "pid": pid,
        "alive": result.returncode == 0 and bool(result.stdout.strip()),
        "ps": _compact(_redact(result.stdout.strip())),
        "error": _compact(_redact(result.stderr.strip())) or None,
    }


def _collect_admin_health(config_path: Path, opener: UrlOpen) -> dict[str, Any]:
    """Admin API /health를 GET으로 조회한다. 인증 토큰은 결과에 포함하지 않는다."""
    try:
        cfg = load_admin_api_config(config_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"config load failed: {exc}"}
    if not cfg.get("enabled"):
        return {"ok": False, "enabled": False}

    url = f"http://{cfg['bind_host']}:{cfg['bind_port']}/admin/v1/health"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {cfg.get('token_secret', '')}"},
        method="GET",
    )
    try:
        with opener(req, float(cfg.get("read_timeout_seconds", 30))) as response:
            body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body) if body else {}
            status_ok = response.status == 200 and str(data.get("status", "ok")).lower() in {
                "ok",
                "healthy",
            }
            return {"ok": status_ok, "url": url, "status": response.status, "body": data}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "error": str(exc)}


def _telegram_check(config_path: Path) -> dict[str, Any]:
    """Telegram 설정 로드 결과를 시크릿 없이 post-check로 요약한다."""
    try:
        cfg = load_telegram_config(config_path)
    except Exception as exc:  # noqa: BLE001
        return {"configured": False, "error": str(exc)}
    return {
        "configured": bool(cfg.get("bot_token")),
        "streaming_enabled": bool((cfg.get("streaming") or {}).get("enabled")),
        "buttons_enabled": bool((cfg.get("buttons") or {}).get("enabled")),
    }


def _scheduler_check(scheduler: object | None, health: dict[str, Any]) -> dict[str, Any]:
    """주입 scheduler 우선, 없으면 health body의 scheduler 요약을 사용한다."""
    if scheduler is not None:
        state = getattr(scheduler, "state", None)
        return {
            "available": True,
            "running": bool(getattr(scheduler, "running", False)),
            "state": state if isinstance(state, (str, int, float, bool, type(None))) else str(state),
        }
    body = health.get("body") if isinstance(health.get("body"), dict) else {}
    health_scheduler = body.get("scheduler") if isinstance(body, dict) else None
    if isinstance(health_scheduler, dict):
        return {"available": True, **health_scheduler}
    return {"available": False}


def _dashboard_check(health: dict[str, Any]) -> dict[str, Any]:
    """Admin health body의 dashboard 상태를 정규화한다."""
    body = health.get("body") if isinstance(health.get("body"), dict) else {}
    dashboard = body.get("dashboard") if isinstance(body, dict) else None
    if isinstance(dashboard, dict):
        return {
            "registered": bool(dashboard.get("registered", dashboard.get("enabled", False))),
            **dashboard,
        }
    return {"registered": False}


def _fd_check(pid: int, runner: RunCommand) -> dict[str, Any]:
    """재시작 후 대상 PID의 열린 FD 개수를 조회한다."""
    result = runner(["lsof", "-p", str(pid)])
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    count = max(0, len(lines) - 1) if lines else 0
    return {"ok": result.returncode == 0, "count": count, "error": result.stderr.strip() or None}


def _redact(value: Any) -> Any:
    """dict/list/string payload 안의 시크릿 키와 대표 시크릿 값을 재귀 마스킹한다."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY_RE.search(str(key)) else _redact(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub(lambda m: f"{m.group(1) or ''}[REDACTED]", value)
    return value


def _compact(text: str, limit: int = 1000) -> str:
    """긴 stdout/stderr/ps 문자열이 tool 결과를 과도하게 키우지 않게 자른다."""
    return text if len(text) <= limit else f"{text[:limit]}…[truncated]"


def _dump(payload: dict[str, Any]) -> str:
    """JSON 직렬화 전 최종 redaction을 강제한다."""
    return json.dumps(_redact(payload), ensure_ascii=False, sort_keys=True)


__all__ = ["DEFAULT_CONFIG_PATH", "handle_restart_runtime"]