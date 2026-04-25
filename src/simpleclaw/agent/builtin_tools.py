"""Built-in tool handlers for the ReAct agent.

Each handler is a standalone function that receives explicit dependencies,
keeping AgentOrchestrator slim and testable.
"""

from __future__ import annotations

import logging
import re
import stat as _stat
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simpleclaw.daemon.scheduler import CronScheduler

logger = logging.getLogger(__name__)

# Names recognised by _dispatch_builtin in the orchestrator.
BUILTIN_TOOL_NAMES = frozenset({
    "cron", "cli", "web-fetch", "file-read", "file-write", "file-manage",
    "skill-docs",
})


# ------------------------------------------------------------------
# Path safety
# ------------------------------------------------------------------

def resolve_safe_path(
    raw_path: str,
    workspace_dir: Path,
    *,
    write: bool = False,
) -> Path | str:
    """Resolve a user-supplied path and validate safety boundaries.

    Returns the resolved ``Path`` on success, or an error string.
    """
    project_root = Path.cwd().resolve()
    workspace = workspace_dir.resolve()
    target = (project_root / raw_path).resolve()

    if write:
        if not str(target).startswith(str(workspace)):
            return (
                f"Error: write operations are restricted to the workspace "
                f"directory ({workspace_dir}). "
                f"Requested path: {raw_path}"
            )
    else:
        if not str(target).startswith(str(project_root)):
            return (
                f"Error: path is outside the project directory. "
                f"Requested path: {raw_path}"
            )
    return target


# ------------------------------------------------------------------
# web-fetch
# ------------------------------------------------------------------

_INTERNAL_URL_RE = re.compile(
    r"https?://(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|"
    r"192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|"
    r"\[::1\]|0\.0\.0\.0)",
    re.I,
)


async def handle_web_fetch(routing: dict) -> str:
    """Fetch a URL and return its text content."""
    import aiohttp

    url = routing.get("url", "")
    if not url:
        return "Error: 'url' field is required."

    if _INTERNAL_URL_RE.match(url):
        return "Error: internal/local network URLs are blocked."

    max_chars = 8000
    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return f"Error: HTTP {resp.status} — {resp.reason}"

                content_type = resp.content_type or ""
                body = await resp.text(errors="replace")

                if "html" in content_type:
                    body = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.S | re.I)
                    body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.S | re.I)
                    body = re.sub(r"<[^>]+>", " ", body)
                    body = re.sub(r"\s+", " ", body).strip()

                if len(body) > max_chars:
                    body = body[:max_chars] + f"\n\n... [truncated, total {len(body)} chars]"

                return body

    except aiohttp.ClientError as exc:
        return f"Error: request failed — {str(exc)[:200]}"
    except Exception as exc:
        return f"Error: {str(exc)[:200]}"


# ------------------------------------------------------------------
# file-read
# ------------------------------------------------------------------

def handle_file_read(routing: dict, workspace_dir: Path) -> str:
    """Read a file's text content."""
    raw_path = routing.get("path", "")
    if not raw_path:
        return "Error: 'path' field is required."

    result = resolve_safe_path(raw_path, workspace_dir, write=False)
    if isinstance(result, str):
        return result
    target = result

    if not target.is_file():
        return f"Error: file not found — {raw_path}"

    offset = routing.get("offset", 0)
    limit = routing.get("limit", 200)

    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"Error reading file: {str(exc)[:200]}"

    total = len(lines)

    if isinstance(offset, int) and offset < 0:
        offset = max(0, total + offset)

    offset = int(offset) if isinstance(offset, (int, float)) else 0
    limit = int(limit) if isinstance(limit, (int, float)) else 200

    selected = lines[offset:offset + limit]
    numbered = "\n".join(
        f"{offset + i + 1:>4} | {line}" for i, line in enumerate(selected)
    )

    header = f"[{raw_path}] lines {offset + 1}-{offset + len(selected)} of {total}"
    return f"{header}\n{numbered}"


# ------------------------------------------------------------------
# file-write
# ------------------------------------------------------------------

def handle_file_write(routing: dict, workspace_dir: Path) -> str:
    """Write content to a file (workspace only)."""
    raw_path = routing.get("path", "")
    content = routing.get("content", "")
    append = routing.get("append", False)

    if not raw_path:
        return "Error: 'path' field is required."
    if content is None:
        return "Error: 'content' field is required."

    result = resolve_safe_path(raw_path, workspace_dir, write=True)
    if isinstance(result, str):
        return result
    target = result

    try:
        target.parent.mkdir(parents=True, exist_ok=True)

        if append:
            with open(target, "a", encoding="utf-8") as f:
                f.write(content)
            return f"Success: appended {len(content)} chars to {raw_path}"
        else:
            target.write_text(content, encoding="utf-8")
            return f"Success: wrote {len(content)} chars to {raw_path}"

    except Exception as exc:
        return f"Error writing file: {str(exc)[:200]}"


# ------------------------------------------------------------------
# file-manage
# ------------------------------------------------------------------

def handle_file_manage(routing: dict, workspace_dir: Path) -> str:
    """Handle file management operations (list, mkdir, delete, info)."""
    operation = routing.get("operation", "")
    raw_path = routing.get("path", "")

    if not operation:
        return "Error: 'operation' field is required (list|mkdir|delete|info)."
    if not raw_path:
        return "Error: 'path' field is required."

    if operation == "list":
        result = resolve_safe_path(raw_path, workspace_dir, write=False)
        if isinstance(result, str):
            return result
        target = result

        if not target.is_dir():
            return f"Error: not a directory — {raw_path}"

        try:
            entries = sorted(target.iterdir())
            lines = []
            for e in entries[:100]:
                kind = "d" if e.is_dir() else "f"
                size = e.stat().st_size if e.is_file() else 0
                lines.append(f"  [{kind}] {e.name:40s}  {size:>10,} bytes")

            header = f"[{raw_path}] {len(entries)} entries"
            if len(entries) > 100:
                header += " (showing first 100)"
            return header + "\n" + "\n".join(lines) if lines else header

        except Exception as exc:
            return f"Error listing directory: {str(exc)[:200]}"

    elif operation == "mkdir":
        result = resolve_safe_path(raw_path, workspace_dir, write=True)
        if isinstance(result, str):
            return result
        try:
            result.mkdir(parents=True, exist_ok=True)
            return f"Success: directory created — {raw_path}"
        except Exception as exc:
            return f"Error creating directory: {str(exc)[:200]}"

    elif operation == "delete":
        result = resolve_safe_path(raw_path, workspace_dir, write=True)
        if isinstance(result, str):
            return result
        target = result

        if not target.exists():
            return f"Error: path not found — {raw_path}"

        try:
            if target.is_file():
                target.unlink()
                return f"Success: file deleted — {raw_path}"
            elif target.is_dir():
                target.rmdir()  # only empty dirs
                return f"Success: empty directory deleted — {raw_path}"
            else:
                return f"Error: unsupported path type — {raw_path}"
        except OSError as exc:
            return f"Error deleting: {str(exc)[:200]}"

    elif operation == "info":
        result = resolve_safe_path(raw_path, workspace_dir, write=False)
        if isinstance(result, str):
            return result
        target = result

        if not target.exists():
            return f"Error: path not found — {raw_path}"

        try:
            st = target.stat()
            kind = "directory" if target.is_dir() else "file"
            modified = datetime.fromtimestamp(st.st_mtime).isoformat(
                timespec="seconds"
            )
            perms = _stat.filemode(st.st_mode)
            return (
                f"[{raw_path}]\n"
                f"  type: {kind}\n"
                f"  size: {st.st_size:,} bytes\n"
                f"  modified: {modified}\n"
                f"  permissions: {perms}"
            )
        except Exception as exc:
            return f"Error getting info: {str(exc)[:200]}"

    else:
        return f"Error: unknown operation '{operation}'. Use list|mkdir|delete|info."


# ------------------------------------------------------------------
# skill-docs
# ------------------------------------------------------------------

def handle_skill_docs(routing: dict, skills_by_name: dict) -> str:
    """Return SKILL.md content for a named skill."""
    name = routing.get("name", "")
    if not name:
        return "Error: 'name' field is required."

    # Exact match first, then fuzzy
    skill = skills_by_name.get(name)
    if skill is None:
        lower = name.lower().replace(" ", "-")
        for key, s in skills_by_name.items():
            if key.lower() == lower:
                skill = s
                break
    if skill is None:
        available = ", ".join(sorted(skills_by_name.keys()))
        return f"Error: skill '{name}' not found. Available: {available}"

    skill_md = Path(skill.skill_dir) / "SKILL.md"
    if not skill_md.is_file():
        return f"Skill '{name}' has no documentation. Description: {skill.description}"

    try:
        content = skill_md.read_text(encoding="utf-8")
        if len(content) > 3000:
            content = content[:3000] + "\n... [truncated]"
        return (
            f"# Documentation for '{name}'\n\n{content}\n\n"
            f"Use the EXACT commands shown above."
        )
    except OSError:
        return f"Error: could not read documentation for '{name}'."


# ------------------------------------------------------------------
# cron (ReAct action handler — separate from /cron slash command)
# ------------------------------------------------------------------

def handle_cron_action(
    routing: dict,
    cron_scheduler: CronScheduler | None,
) -> str:
    """Handle a cron Action from the ReAct loop."""
    if cron_scheduler is None:
        return "Error: CronScheduler not available."

    cron_action = routing.get("cron_action", "")

    if cron_action == "list":
        return _cron_list(cron_scheduler)

    if cron_action == "add":
        from simpleclaw.daemon.models import ActionType

        name = routing.get("name", "")
        cron_expr = routing.get("cron_expression", "")
        action_type_str = routing.get("action_type", "prompt")
        action_ref = routing.get("action_reference", "")

        if not name or not cron_expr or not action_ref:
            return "Error: name, cron_expression, action_reference are required."

        action_type = (
            ActionType.RECIPE if action_type_str == "recipe"
            else ActionType.PROMPT
        )

        if cron_scheduler.get_job(name):
            return f"Error: job '{name}' already exists."

        try:
            job = cron_scheduler.add_job(
                name=name,
                cron_expression=cron_expr,
                action_type=action_type,
                action_reference=action_ref,
            )
            return (
                f"Success: cron job created.\n"
                f"  name: {job.name}\n"
                f"  schedule: {job.cron_expression}\n"
                f"  type: {job.action_type.value}\n"
                f"  target: {job.action_reference}"
            )
        except Exception as exc:
            return f"Error: {str(exc)[:200]}"

    if cron_action == "remove":
        name = routing.get("name", "")
        if cron_scheduler.remove_job(name):
            return f"Success: job '{name}' removed."
        return f"Error: job '{name}' not found."

    if cron_action in ("enable", "disable"):
        from simpleclaw.daemon.models import CronJobNotFoundError
        name = routing.get("name", "")
        try:
            if cron_action == "enable":
                cron_scheduler.enable_job(name)
            else:
                cron_scheduler.disable_job(name)
            return f"Success: job '{name}' {cron_action}d."
        except CronJobNotFoundError:
            return f"Error: job '{name}' not found."

    return f"Error: unknown cron_action '{cron_action}'."


def _cron_list(cron_scheduler: CronScheduler) -> str:
    """Format a cron job listing (shared by ReAct handler and /cron command)."""
    jobs = cron_scheduler.list_jobs()
    if not jobs:
        return "📭 등록된 cron 작업이 없습니다."

    lines = ["📋 **Cron Jobs**\n"]
    for j in jobs:
        status = "✅" if j.enabled else "⏸️"
        ref = j.action_reference
        if len(ref) > 60:
            ref = ref[:57] + "..."
        lines.append(
            f"{status} **{j.name}** — `{j.cron_expression}` "
            f"({j.action_type.value}) → {ref}"
        )
    return "\n".join(lines)
