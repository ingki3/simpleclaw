"""운영자용 런타임 상태 스냅샷 수집 도구.

``runtime_status`` native tool은 운영자가 봇 프로세스, LaunchAgent, git HEAD,
Admin API health, 포트/FD, scheduler 상태를 한 번에 확인하기 위한 read-only 진단
도구다. 프로세스 재시작·파일 수정·배포 같은 side effect는 수행하지 않고,
외부 명령도 ``ps``/``git``/``lsof``/``launchctl print`` 같은 조회 명령으로 제한한다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from simpleclaw.config import load_admin_api_config, load_daemon_config

RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]
UrlOpen = Callable[[urllib.request.Request, float], Any]

_ALLOWED_INCLUDE = frozenset({
    "process",
    "launchd",
    "git",
    "health",
    "ports",
    "fd",
    "scheduler",
})
_DEFAULT_INCLUDE = ("process", "launchd", "git", "health", "ports", "fd", "scheduler")
_SECRET_KEY_RE = re.compile(r"(token|secret|password|api[_-]?key|authorization)", re.I)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"(gh[pousr]_[A-Za-z0-9_]+)|"
    r"([A-Za-z0-9_]{16,}:[A-Za-z0-9_\-]{20,})"
)


def handle_runtime_status(
    args: dict[str, Any],
    *,
    config_path: str | Path,
    scheduler: object | None = None,
    run_command: RunCommand | None = None,
    urlopen: UrlOpen | None = None,
) -> str:
    """Function Calling 핸들러용 JSON 문자열을 반환한다.

    Args:
        args: ``include``와 ``verbose`` 옵션을 담은 tool arguments.
        config_path: live ``config.yaml`` 경로. PID/Admin API 설정 조회에만 사용한다.
        scheduler: 주입된 CronScheduler. 없으면 scheduler 상태는 unavailable로 표시한다.
        run_command: 테스트 주입용 subprocess runner.
        urlopen: 테스트 주입용 HTTP GET 함수.

    Returns:
        시크릿이 마스킹된 JSON 문자열. 예외는 섹션별 ``error`` 필드로 축약한다.
    """
    runner = run_command or _run_command
    opener = urlopen or urllib.request.urlopen
    config_path = Path(config_path).expanduser()
    include = _normalize_include(args.get("include"))
    verbose = bool(args.get("verbose", False))

    daemon_cfg = _safe_load_daemon_config(config_path)
    target_pid = _read_pid_file(daemon_cfg.get("pid_file")) or os.getpid()

    payload: dict[str, Any] = {
        "ok": True,
        "read_only": True,
        "current_pid": os.getpid(),
        "target_pid": target_pid,
        "config_path": str(config_path),
        "include": include,
    }

    if "process" in include:
        payload["process"] = _collect_process(target_pid, runner, verbose=verbose)
    if "launchd" in include:
        payload["launchd"] = _collect_launchd(runner)
    if "git" in include:
        payload["git"] = _collect_git(Path.cwd(), runner)
    if "health" in include:
        payload["health"] = _collect_admin_health(config_path, opener)
    if "ports" in include:
        payload["ports"] = _collect_ports(target_pid, runner, verbose=verbose)
    if "fd" in include:
        payload["fd"] = _collect_fd(target_pid, runner)
    if "scheduler" in include:
        payload["scheduler"] = _collect_scheduler(scheduler)

    return json.dumps(_redact(payload), ensure_ascii=False, sort_keys=True)


def _normalize_include(raw: object) -> list[str]:
    """tool argument의 include 값을 안전한 섹션 목록으로 정규화한다."""
    if raw is None or not isinstance(raw, list):
        return list(_DEFAULT_INCLUDE)
    include = [str(item) for item in raw if str(item) in _ALLOWED_INCLUDE]
    return include or list(_DEFAULT_INCLUDE)


def _run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """조회 전용 명령을 timeout과 함께 실행한다."""
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=3,
    )


def _safe_load_daemon_config(config_path: Path) -> dict[str, Any]:
    """config 로드 실패가 전체 status를 막지 않도록 daemon 기본값으로 폴백한다."""
    try:
        return load_daemon_config(config_path)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"daemon config load failed: {exc}"}


def _read_pid_file(path_value: object) -> int | None:
    """pid_file을 읽어 정수 PID로 반환한다. 없거나 깨졌으면 None."""
    if not isinstance(path_value, str) or not path_value:
        return None
    path = Path(path_value).expanduser()
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def _collect_process(pid: int, runner: RunCommand, *, verbose: bool) -> dict[str, Any]:
    """대상 PID의 ps/cwd/HOME 정보를 조회한다."""
    result: dict[str, Any] = {
        "pid": pid,
        "current_process": pid == os.getpid(),
        "home": str(Path.home()),
    }
    if pid == os.getpid():
        result["cwd"] = os.getcwd()

    ps = runner(["ps", "-p", str(pid), "-o", "pid=,ppid=,stat=,etime=,command="])
    result["alive"] = ps.returncode == 0 and bool(ps.stdout.strip())
    if ps.stdout.strip():
        result["ps"] = ps.stdout.strip() if verbose else _compact_command(ps.stdout.strip())
    if ps.stderr.strip() and verbose:
        result["ps_error"] = ps.stderr.strip()

    if "cwd" not in result:
        cwd = _cwd_for_pid(pid, runner)
        if cwd:
            result["cwd"] = cwd
    return result


def _cwd_for_pid(pid: int, runner: RunCommand) -> str | None:
    """lsof로 다른 프로세스의 cwd를 조회한다. 실패 시 None."""
    out = runner(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    for line in out.stdout.splitlines():
        if line.startswith("n") and len(line) > 1:
            return line[1:]
    return None


def _compact_command(ps_line: str) -> str:
    """verbose=False일 때 긴 command line을 앞부분만 남긴다."""
    return ps_line if len(ps_line) <= 240 else f"{ps_line[:237]}..."


def _collect_launchd(runner: RunCommand) -> dict[str, Any]:
    """현재 사용자 launchd 도메인의 SimpleClaw 관련 항목을 조회한다."""
    uid = os.getuid()
    result = runner(["launchctl", "print", f"gui/{uid}"])
    entries = [line.strip() for line in result.stdout.splitlines() if "simpleclaw" in line.lower()]
    return {
        "ok": result.returncode == 0,
        "domain": f"gui/{uid}",
        "simpleclaw_entries": entries[:20],
        "error": result.stderr.strip() or None,
    }


def _collect_git(cwd: Path, runner: RunCommand) -> dict[str, Any]:
    """현재 working tree의 HEAD/branch를 조회한다."""
    root = _git_output(["git", "rev-parse", "--show-toplevel"], runner)
    head = _git_output(["git", "rev-parse", "--short", "HEAD"], runner)
    branch = _git_output(["git", "branch", "--show-current"], runner)
    return {"cwd": str(cwd), "root": root, "branch": branch, "head": head}


def _git_output(cmd: list[str], runner: RunCommand) -> str | None:
    """git 조회 명령의 stdout 첫 줄만 반환한다."""
    result = runner(cmd)
    if result.returncode != 0:
        return None
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else None


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
            return {"ok": True, "url": url, "status": response.status, "body": data}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "url": url, "status": exc.code, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "error": str(exc)}


def _collect_ports(pid: int, runner: RunCommand, *, verbose: bool) -> dict[str, Any]:
    """대상 PID의 TCP listen 포트를 lsof로 조회한다."""
    result = runner(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"])
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {
        "ok": result.returncode == 0 or bool(lines),
        "listen": lines if verbose else lines[:10],
        "truncated": not verbose and len(lines) > 10,
        "error": result.stderr.strip() or None,
    }


def _collect_fd(pid: int, runner: RunCommand) -> dict[str, Any]:
    """대상 PID의 열린 FD 개수를 조회한다."""
    if pid == os.getpid():
        try:
            return {"ok": True, "count": len(list(Path("/dev/fd").iterdir()))}
        except OSError:
            pass
    result = runner(["lsof", "-p", str(pid)])
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    count = max(0, len(lines) - 1) if lines else 0
    return {"ok": result.returncode == 0, "count": count, "error": result.stderr.strip() or None}


def _collect_scheduler(scheduler: object | None) -> dict[str, Any]:
    """주입된 CronScheduler/APScheduler의 상태를 side effect 없이 요약한다."""
    if scheduler is None:
        return {"available": False}
    result: dict[str, Any] = {
        "available": True,
        "running": bool(getattr(scheduler, "running", False)),
        "state": getattr(scheduler, "state", None),
    }
    get_jobs = getattr(scheduler, "get_jobs", None)
    if callable(get_jobs):
        try:
            jobs = list(get_jobs())
            result["job_count"] = len(jobs)
            result["jobs"] = [_serialize_job(job) for job in jobs[:20]]
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
    return result


def _serialize_job(job: object) -> dict[str, Any]:
    """APScheduler Job처럼 보이는 객체를 운영자가 읽을 수 있는 최소 dict로 변환한다."""
    return {
        "id": getattr(job, "id", None),
        "name": getattr(job, "name", None),
        "next_run_time": str(getattr(job, "next_run_time", None)),
    }


def _redact(value: Any) -> Any:
    """dict/list/string payload 안의 시크릿 키와 대표 시크릿 값을 재귀 마스킹한다."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for child_key, child_value in value.items():
            if _SECRET_KEY_RE.search(str(child_key)):
                redacted[str(child_key)] = "[REDACTED]"
            else:
                redacted[str(child_key)] = _redact(child_value)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub(lambda m: f"{m.group(1) or ''}[REDACTED]", value)
    return value


__all__ = ["handle_runtime_status"]
