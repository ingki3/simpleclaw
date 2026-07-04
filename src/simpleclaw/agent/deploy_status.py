"""운영자용 배포 상태 스냅샷 수집 도구.

``deploy_status`` native tool은 운영자가 live checkout이 origin/main 또는
origin/dev와 얼마나 어긋나 있는지, dirty file이 배포 범위와 겹치는지,
dev에 아직 main으로 릴리스되지 않은 커밋과 열린 PR이 있는지 read-only JSON으로
확인하기 위한 진단 도구다. pull/merge/restart/install 같은 side effect는 수행하지
않고 ``git``/``gh pr list`` 조회 명령만 실행한다.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]

_ALLOWED_COMPARE = frozenset({"main", "dev"})
_MAX_COMMITS = 20
_MAX_DIRTY_PATHS = 80
_MAX_PRS = 20


def handle_deploy_status(
    args: dict[str, Any],
    *,
    run_command: RunCommand | None = None,
) -> str:
    """Function Calling 핸들러용 JSON 문자열을 반환한다.

    Args:
        args: ``compare``와 ``include_prs`` 옵션을 담은 tool arguments.
        run_command: 테스트 주입용 subprocess runner.

    Returns:
        git/gh 조회 결과를 담은 read-only JSON 문자열. 조회 실패는 섹션별
        ``error``로 축약하고, gh 실패는 git-only summary를 막지 않는다.
    """
    runner = run_command or _run_command
    compare = _normalize_compare(args.get("compare"))
    include_prs = bool(args.get("include_prs", False))
    target = f"origin/{compare}"

    repo = _collect_repo(runner)
    origin_sync = _collect_origin_sync(target, runner)
    changed_paths = _changed_paths(target, runner)
    dirty = _collect_dirty(changed_paths, runner)
    deploy_range = _collect_commit_range(f"{target}..HEAD", runner)
    unreleased_dev = _collect_commit_range("origin/main..origin/dev", runner)

    payload: dict[str, Any] = {
        "ok": True,
        "read_only": True,
        "compare": compare,
        "repo": repo,
        "origin_sync": origin_sync,
        "dirty": dirty,
        "deploy_range": {
            "base": target,
            "head": "HEAD",
            **deploy_range,
        },
        "unreleased_dev": {
            "base": "origin/main",
            "head": "origin/dev",
            **unreleased_dev,
        },
    }
    if include_prs:
        pr_payload = _collect_open_prs(runner)
        payload["open_prs"] = pr_payload["open_prs"]
        payload["gh"] = pr_payload["gh"]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _normalize_compare(raw: object) -> str:
    """compare 인자를 main/dev 중 하나로 정규화한다."""
    value = str(raw or "main")
    return value if value in _ALLOWED_COMPARE else "main"


def _run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """조회 전용 명령을 timeout과 함께 실행한다."""
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )


def _collect_repo(runner: RunCommand) -> dict[str, Any]:
    """현재 checkout의 root/branch/HEAD/upstream을 요약한다."""
    return {
        "cwd": str(Path.cwd()),
        "root": _output(["git", "rev-parse", "--show-toplevel"], runner),
        "branch": _output(["git", "branch", "--show-current"], runner),
        "head": _output(["git", "rev-parse", "HEAD"], runner),
        "head_short": _output(["git", "rev-parse", "--short", "HEAD"], runner),
        "upstream": _output(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            runner,
        ),
    }


def _collect_origin_sync(target: str, runner: RunCommand) -> dict[str, Any]:
    """HEAD와 origin target의 ahead/behind 상태를 계산한다."""
    result = runner(["git", "rev-list", "--left-right", "--count", f"HEAD...{target}"])
    if result.returncode != 0:
        return {
            "target": target,
            "ahead": None,
            "behind": None,
            "status": "unknown",
            "error": _error_text(result),
        }
    parts = result.stdout.strip().split()
    if len(parts) < 2:
        return {
            "target": target,
            "ahead": None,
            "behind": None,
            "status": "unknown",
            "error": f"unexpected rev-list output: {result.stdout.strip()}",
        }
    ahead, behind = int(parts[0]), int(parts[1])
    return {"target": target, "ahead": ahead, "behind": behind, "status": _sync_status(ahead, behind)}


def _sync_status(ahead: int, behind: int) -> str:
    """ahead/behind count를 사람이 읽을 상태 문자열로 변환한다."""
    if ahead and behind:
        return "diverged"
    if ahead:
        return "ahead"
    if behind:
        return "behind"
    return "in_sync"


def _changed_paths(target: str, runner: RunCommand) -> set[str]:
    """deploy range에 포함된 파일 경로를 조회한다."""
    result = runner(["git", "diff", "--name-only", f"{target}...HEAD"])
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _collect_dirty(changed_paths: set[str], runner: RunCommand) -> dict[str, Any]:
    """tracked/untracked dirty path와 deploy range overlap을 요약한다."""
    result = runner(["git", "status", "--porcelain=v1"])
    if result.returncode != 0:
        return {"clean": False, "paths": [], "error": _error_text(result)}
    paths = [_parse_status_line(line, changed_paths) for line in result.stdout.splitlines() if line.strip()]
    return {
        "clean": not paths,
        "paths": paths[:_MAX_DIRTY_PATHS],
        "truncated": len(paths) > _MAX_DIRTY_PATHS,
    }


def _parse_status_line(line: str, changed_paths: set[str]) -> dict[str, Any]:
    """git status porcelain v1 한 줄을 path/status dict로 변환한다."""
    status = line[:2].strip() or line[:2]
    raw_path = line[3:] if len(line) > 3 else ""
    path = raw_path.split(" -> ")[-1].strip()
    return {
        "path": path,
        "status": status,
        "overlaps_deploy_range": path in changed_paths,
    }


def _collect_commit_range(range_spec: str, runner: RunCommand) -> dict[str, Any]:
    """git log --oneline 결과를 bounded commit 목록으로 변환한다."""
    result = runner([
        "git",
        "log",
        "--oneline",
        "--no-decorate",
        f"-{_MAX_COMMITS}",
        range_spec,
    ])
    if result.returncode != 0:
        return {"count": 0, "commits": [], "error": _error_text(result)}
    commits = [_parse_oneline(line) for line in result.stdout.splitlines() if line.strip()]
    return {"count": len(commits), "commits": commits}


def _parse_oneline(line: str) -> dict[str, str]:
    """git log --oneline 한 줄을 sha/subject로 나눈다."""
    sha, _, subject = line.partition(" ")
    return {"sha": sha, "subject": subject}


def _collect_open_prs(runner: RunCommand) -> dict[str, Any]:
    """gh pr list를 사용해 열린 PR 요약을 수집하고 실패 시 graceful fallback한다."""
    result = runner([
        "gh",
        "pr",
        "list",
        "--state",
        "open",
        "--limit",
        str(_MAX_PRS),
        "--json",
        "number,title,url,headRefName,baseRefName,state",
    ])
    if result.returncode != 0:
        return {"open_prs": [], "gh": {"available": False, "error": _error_text(result)}}
    try:
        raw = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return {"open_prs": [], "gh": {"available": False, "error": f"invalid gh json: {exc}"}}
    if not isinstance(raw, list):
        return {"open_prs": [], "gh": {"available": False, "error": "gh output is not a list"}}
    return {
        "open_prs": [_summarize_pr(item) for item in raw[:_MAX_PRS] if isinstance(item, dict)],
        "gh": {"available": True, "error": None},
    }


def _summarize_pr(item: dict[str, Any]) -> dict[str, Any]:
    """gh PR JSON에서 운영 판단에 필요한 필드만 남긴다."""
    return {
        "number": item.get("number"),
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "headRefName": item.get("headRefName", ""),
        "baseRefName": item.get("baseRefName", ""),
        "state": item.get("state", ""),
    }


def _output(cmd: list[str], runner: RunCommand) -> str | None:
    """명령 stdout 첫 줄을 반환하고 실패 시 None으로 축약한다."""
    result = runner(cmd)
    if result.returncode != 0:
        return None
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else None


def _error_text(result: subprocess.CompletedProcess[str]) -> str:
    """stderr/stdout에서 짧은 오류 문자열을 고른다."""
    return (result.stderr or result.stdout or "command failed").strip()


__all__ = ["handle_deploy_status"]
