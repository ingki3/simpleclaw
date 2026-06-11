"""ReAct 에이전트의 내장 도구 핸들러 모듈.

각 핸들러는 명시적 의존성을 인자로 받는 독립 함수로 구현되어 있어
AgentOrchestrator를 경량으로 유지하고 테스트를 용이하게 한다.

제공 도구:
- web-fetch: URL에서 웹 페이지 내용 가져오기
- file-read: 파일 텍스트 읽기 (프로젝트 내)
- file-write: 파일 쓰기 (워크스페이스 내)
- file-manage: 파일/디렉토리 관리 (list, mkdir, delete, info)
- skill-docs: 사용자 설치 스킬 문서(SKILL.md) 조회
- cron: ReAct 루프에서의 cron 작업 관리
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import shutil
import stat as _stat
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simpleclaw.daemon.scheduler import CronScheduler

logger = logging.getLogger(__name__)

# 정적 HTML fetch 결과가 이 길이(공백 strip 후) 미만이면 자동으로 헤드리스 폴백.
# JS 렌더링 SPA 가 빈 셸만 반환하는 경우(2026-05-08 사고: 27자) 대응.
STATIC_FALLBACK_THRESHOLD = 200

# BIZ-162: nohup/launchd/systemd 등으로 데몬을 띄우면 PATH 가 축소돼 fnm shim
# 디렉터리가 사라지고 ``shutil.which("agent-browser")`` 가 None 을 돌려준다.
# `_resolve_agent_browser` 가 PATH 가 비어 있어도 알려진 위치를 글롭 탐색해
# 헤드리스 폴백이 회귀하지 않도록 한다.
_AGENT_BROWSER_GLOB_CANDIDATES: tuple[str, ...] = (
    "~/.npm/_npx/*/node_modules/agent-browser/bin/agent-browser-darwin-arm64",
    "~/.npm/_npx/*/node_modules/agent-browser/bin/agent-browser-darwin-x64",
    "~/.npm/_npx/*/node_modules/agent-browser/bin/agent-browser-linux-x64",
    "~/.npm/_npx/*/node_modules/agent-browser/bin/agent-browser-linux-arm64",
    "~/.local/state/fnm_multishells/*/bin/agent-browser",
    "/usr/local/bin/agent-browser",
    "/opt/homebrew/bin/agent-browser",
)

# 오케스트레이터의 _dispatch_builtin에서 인식하는 내장 도구 이름 목록.
# BIZ-260: ``clarify`` 추가 — LLM 이 사용자에게 다지선다 질문을 던질 때 인라인
# 키보드로 렌더하는 채널 브리지.
BUILTIN_TOOL_NAMES = frozenset({
    "cron", "cli", "web-fetch", "file-read", "file-write", "file-manage",
    "skill-docs", "clarify",
})


# ------------------------------------------------------------------
# 경로 안전성 검증
# ------------------------------------------------------------------

def _is_within(path: Path, root: Path) -> bool:
    """``path`` 가 ``root`` 의 자손(또는 동일) 인지 안전하게 검사한다.

    BIZ-142: 기존 ``str(target).startswith(str(root))`` prefix 매칭은
    ``/Users/simplist/Dev/SimpleClaw`` 와 ``/Users/simplist/Dev/SimpleClaw-x``
    를 구별하지 못해 boundary 우회가 가능했다. ``Path.relative_to`` 는 부모
    체인을 따라 비교하므로 디렉터리 경계가 정확히 일치할 때만 True.
    """
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_safe_path(
    raw_path: str,
    workspace_dir: Path,
    *,
    write: bool = False,
    persona_local_dir: str | Path | None = None,
) -> Path | str:
    """사용자가 제공한 경로를 해석하고 안전 경계를 검증한다.

    BIZ-142:
    - ``~`` 를 사용자 홈 디렉터리로 확장한다 (운영 디렉터리 ``~/.simpleclaw/``
      가 프로젝트 루트 외부에 있어 필수).
    - 읽기 허용 루트에 프로젝트 루트 외에 ``persona_local_dir`` (보통
      ``~/.simpleclaw-agent/default``) 을 화이트리스트로 추가한다.
    - 경계 검사를 ``Path.relative_to`` 기반으로 바꿔 prefix-trick 을 차단한다.

    쓰기는 워크스페이스 디렉터리 내부로만 제한된다 (변경 없음).

    Args:
        raw_path: 호출자가 전달한 원본 경로 문자열.
        workspace_dir: 쓰기 허용 루트 (스킬·도구 출력 디렉터리).
        write: True 면 쓰기 경계, False 면 읽기 경계로 검증.
        persona_local_dir: 페르소나 운영 디렉터리. 읽기 허용 루트에
            추가된다. ``None`` 이면 프로젝트 루트만 허용.

    Returns:
        성공 시 해석된 ``Path``, 경계 위반 시 에러 문자열.
    """
    project_root = Path.cwd().resolve()
    workspace = workspace_dir.resolve()

    # BIZ-142: ``~`` 확장은 절대/상대 분기 전에 수행해야 한다 — 그러지 않으면
    # ``Path.cwd() / "~/.simpleclaw/..."`` 가 리터럴 ``~`` 디렉터리로 풀린다.
    expanded = Path(raw_path).expanduser()
    target = (
        expanded.resolve()
        if expanded.is_absolute()
        else (project_root / expanded).resolve()
    )

    if write:
        if not _is_within(target, workspace):
            return (
                f"Error: write operations are restricted to the workspace "
                f"directory ({workspace_dir}). "
                f"Requested path: {raw_path}"
            )
        return target

    allowed_read_roots = [project_root]
    if persona_local_dir is not None:
        allowed_read_roots.append(Path(persona_local_dir).expanduser().resolve())

    if not any(_is_within(target, root) for root in allowed_read_roots):
        return (
            f"Error: path is outside allowed read roots "
            f"({[str(r) for r in allowed_read_roots]}). "
            f"Requested path: {raw_path}"
        )
    return target


# ------------------------------------------------------------------
# web-fetch — 웹 페이지 가져오기
# ------------------------------------------------------------------

# 내부/로컬 네트워크 URL 패턴 — SSRF 방지를 위해 차단
_INTERNAL_URL_RE = re.compile(
    r"https?://(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|"
    r"192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|"
    r"\[::1\]|0\.0\.0\.0)",
    re.I,
)


_WEB_FETCH_MAX_CHARS = 8000

# BIZ-190 — Cloudflare/anti-bot 차단 페이지 시그니처. ``_fetch_static`` /
# ``_fetch_headless`` 양쪽 모두 200 OK 로 짧은 본문(예: 27자/202자) 을 돌려주는
# 케이스가 wikidocs.net, npmjs.com 등에서 관측됨. 시그니처가 매치되거나 본문이
# 매우 짧으면 LLM 이 같은 URL 을 agent-browser/cli 로 재시도해 max-iter 까지
# 소진하는 패턴(2026-05-13 BIZ-188 잔존 4건) 의 트리거. 명시적인
# ``FETCH_BLOCKED:`` 마커 응답으로 합성해 LLM 이 재시도를 멈추도록 한다.
_BLOCK_PAGE_SIGNATURES: tuple[str, ...] = (
    "just a moment",
    "checking your browser",
    "cloudflare",
    "verify you are human",
    "verifying you are human",
    "enable javascript and cookies",
    "access denied",
    "attention required",
    "please turn javascript on",
    "ddos protection",
    "몇 초 안에 이동하지 않는 경우",
    "google 검색 결과로 이동",
)

# 본문이 이 길이 미만이면 (시그니처가 안 잡혀도) 차단된 것으로 간주. 정적/헤드리스
# 양쪽이 모두 짧은 응답을 돌려준 경우만 적용 — 정상적인 짧은 페이지(에러 404 등)
# 까지는 잡지 않도록 ``handle_web_fetch`` 에서 fallback 경로를 거친 뒤에만 검사.
_BLOCK_PAGE_SHORT_THRESHOLD = 400


def _looks_like_block_page(body: str) -> bool:
    """본문이 Cloudflare/anti-bot 차단 페이지 모양인지 휴리스틱으로 판별한다.

    BIZ-190: 알려진 시그니처(소문자 매치) 또는 매우 짧은 본문(< 400 chars) 이면
    True. 시그니처는 정적 HTML 의 가시 텍스트(``_fetch_static`` 에서 태그가 이미
    제거된 상태) 또는 헤드리스 렌더 결과 모두에서 매치되도록 부분 문자열 검색.
    """
    if not body:
        return True
    stripped = body.strip()
    if len(stripped) < _BLOCK_PAGE_SHORT_THRESHOLD:
        return True
    lower = stripped.lower()
    return any(sig in lower for sig in _BLOCK_PAGE_SIGNATURES)


def _format_block_page_response(url: str, body: str, *, via: str) -> str:
    """차단 페이지로 판정된 응답을 LLM 이 재시도하지 않도록 합성 메시지로 포맷한다.

    BIZ-190: 응답 첫 줄에 ``FETCH_BLOCKED: <url>`` 마커를 박아 system prompt
    가드(``_GUARD_WEB_FETCH_PREFERRED``) 와 키워드 매치하도록 한다. 본문은
    진단용으로 첫 400자까지 동봉.
    """
    snippet = body.strip()[:400]
    return (
        f"FETCH_BLOCKED: {url}\n"
        f"This site appears to block automated fetching (detected via {via}). "
        "Both static fetch and the headless browser fallback returned a short "
        "or anti-bot body. Do NOT retry the same URL with agent-browser, cli, "
        "or another skill — reply to the user that the page cannot be "
        "retrieved automatically and offer a graceful alternative.\n"
        f"--- diagnostic body ({len(body.strip())} chars) ---\n{snippet}"
    )


async def _fetch_static(url: str) -> str:
    """정적 HTML/텍스트 fetch (aiohttp). 성공 시 본문, 실패 시 ``Error: ...`` 문자열."""
    import aiohttp

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

                if len(body) > _WEB_FETCH_MAX_CHARS:
                    body = (
                        body[:_WEB_FETCH_MAX_CHARS]
                        + f"\n\n... [truncated, total {len(body)} chars]"
                    )

                return body

    except aiohttp.ClientError as exc:
        return f"Error: request failed — {str(exc)[:200]}"
    except Exception as exc:
        return f"Error: {str(exc)[:200]}"


def _resolve_agent_browser(
    override: str | None = None,
) -> tuple[str | None, list[str]]:
    """`agent-browser` CLI 절대 경로를 탐색한다.

    탐색 순서:
      (a) ``override`` — 운영자가 ``agent.web_fetch.headless_binary`` 로 지정한 경로
      (b) ``shutil.which("agent-browser")`` — 호출자 PATH
      (c) 알려진 후보 위치 (`_AGENT_BROWSER_GLOB_CANDIDATES`) — npm npx 캐시,
          fnm 셸 shim, brew 등. nohup 등 PATH 가 축소된 데몬 환경 대응.

    Returns:
        ``(path_or_None, searched)`` 튜플. ``searched`` 는 진단 메시지에 동봉할
        수 있도록 시도한 출처 라벨 목록. 하나도 매치되지 않으면 ``path_or_None``
        은 None.
    """
    searched: list[str] = []

    if override:
        expanded = os.path.expanduser(override)
        searched.append(f"config override: {expanded}")
        if os.access(expanded, os.X_OK):
            return expanded, searched
        # 명시 경로가 실행 불가능하면 디스크 상태가 변했거나 오타일 가능성 — 경고만
        # 남기고 후순위(PATH/glob)로 넘어가 자동 복구를 시도한다.
        logger.warning(
            "agent.web_fetch.headless_binary set but not executable: %s",
            expanded,
        )

    path_via_which = shutil.which("agent-browser")
    searched.append("$PATH (shutil.which)")
    if path_via_which:
        return path_via_which, searched

    for pattern in _AGENT_BROWSER_GLOB_CANDIDATES:
        expanded = os.path.expanduser(pattern)
        searched.append(expanded)
        for hit in glob.glob(expanded):
            if os.access(hit, os.X_OK):
                return hit, searched

    return None, searched


async def _fetch_headless(
    url: str,
    *,
    headless_binary: str | None = None,
) -> str:
    """헤드리스 브라우저(`agent-browser` CLI)로 페이지를 렌더링하고 본문 텍스트를 반환한다.

    `agent-browser open <url>` → `wait --load load` → `get text body` → `close`.
    CLI 미설치/실패 시 ``Error: ...`` 반환.

    ``headless_binary`` 는 ``agent.web_fetch.headless_binary`` config 값. None 이면
    PATH + 알려진 후보 경로를 자동 탐색한다.
    """
    binary, searched = _resolve_agent_browser(headless_binary)
    if not binary:
        # nohup 등 PATH 가 축소된 환경에서 첫 진단을 빠르게 할 수 있도록, 검색한
        # 위치 목록을 그대로 동봉한다. 운영자는 이 메시지만 보고 config override
        # 로 즉시 봉합 가능.
        return (
            "Error: headless fallback unavailable — agent-browser not found.\n"
            f"Searched: {'; '.join(searched)}.\n"
            "Set `agent.web_fetch.headless_binary: <path>` in config.yaml to override."
        )

    # 같은 봇 안에서 동시 호출이 충돌하지 않도록 PID + 이벤트 루프 시간으로 세션 격리.
    loop = asyncio.get_event_loop()
    session_name = f"web-fetch-{os.getpid()}-{int(loop.time() * 1000) % 1_000_000}"
    common = [binary, "--session", session_name]

    async def _run(args: list[str], timeout: float) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *common, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    try:
        rc, _out, err = await _run(["open", url], timeout=45)
        if rc != 0:
            return f"Error: headless open failed — {err.strip()[:200]}"

        # BIZ-167: ``networkidle`` 은 wikidocs.net 처럼 background polling/analytics 가
        # 계속 도는 SPA 에서 사실상 영영 settle 하지 않아 wait 가 통째로 timeout 으로
        # 죽고 30 초를 낭비한다. ``load`` (DOMContentLoaded + 동기 리소스 로딩 완료)
        # 로 바꾸면 일반 페이지에서 즉시 풀리고, 정 안 풀려도 본문 회수는 가능하다.
        # timeout 도 8 초로 짧게 — load 자체는 보통 1~2초; 안 풀리면 빠르게 get text 단계로.
        try:
            await _run(["wait", "--load", "load"], timeout=8)
        except asyncio.TimeoutError:
            logger.info("agent-browser wait load timed out for %s; continuing", url)

        rc, out, err = await _run(["get", "text", "body"], timeout=30)
        if rc != 0:
            return f"Error: headless get text failed — {err.strip()[:200]}"

        body = re.sub(r"\s+", " ", out).strip()
        if len(body) > _WEB_FETCH_MAX_CHARS:
            body = (
                body[:_WEB_FETCH_MAX_CHARS]
                + f"\n\n... [truncated, total {len(body)} chars]"
            )
        return body

    except asyncio.TimeoutError:
        return "Error: headless rendering timed out."
    except FileNotFoundError as exc:
        return f"Error: headless fallback unavailable — {str(exc)[:200]}"
    except Exception as exc:
        return f"Error: headless rendering failed — {str(exc)[:200]}"
    finally:
        # 브라우저 데몬에 세션이 남지 않도록 정리. 실패는 무시.
        try:
            proc = await asyncio.create_subprocess_exec(
                *common, "close",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
        except Exception:
            pass


async def handle_web_fetch(
    routing: dict,
    *,
    headless_binary: str | None = None,
) -> str:
    """URL에서 웹 페이지를 가져와 텍스트 내용을 반환한다.

    기본 흐름: 정적 HTML fetch → 본문 길이가 ``STATIC_FALLBACK_THRESHOLD`` 미만이면
    `agent-browser` 헤드리스 경로로 자동 폴백 (JS 렌더링 SPA 대응).
    ``force_headless=True`` 이면 정적 단계 skip.

    ``headless_binary`` 는 운영자가 config 로 지정한 ``agent-browser`` 절대 경로
    (``agent.web_fetch.headless_binary``). None 이면 ``_resolve_agent_browser`` 가
    PATH + 알려진 후보 경로를 자동 탐색한다.
    """
    url = routing.get("url", "")
    if not url:
        return "Error: 'url' field is required."

    if _INTERNAL_URL_RE.match(url):
        return "Error: internal/local network URLs are blocked."

    force_headless = bool(routing.get("force_headless", False))

    if force_headless:
        logger.info("web_fetch force_headless=True for %s", url)
        body = await _fetch_headless(url, headless_binary=headless_binary)
        if body.startswith("Error:"):
            return body
        # BIZ-190: force_headless 경로에서도 결과가 차단 페이지 모양이면 LLM 이
        # 같은 URL 을 agent-browser 로 재시도하지 않도록 FETCH_BLOCKED 마커로 합성.
        if _looks_like_block_page(body):
            logger.info(
                "web_fetch force_headless=True returned block page for %s (%d chars)",
                url, len(body.strip()),
            )
            return _format_block_page_response(url, body, via="force_headless")
        return f"(via headless render; force_headless=True)\n\n{body}"

    static_text = await _fetch_static(url)
    if static_text.startswith("Error:"):
        return static_text

    static_len = len(static_text.strip())
    if static_len < STATIC_FALLBACK_THRESHOLD:
        logger.info(
            "web_fetch static returned %d chars (< %d), falling back to headless",
            static_len,
            STATIC_FALLBACK_THRESHOLD,
        )
        body = await _fetch_headless(url, headless_binary=headless_binary)
        if body.startswith("Error:"):
            # 폴백 자체가 실패하면 정적 결과라도 반환해 LLM 이 문맥을 잃지 않게.
            # BIZ-190: 다만 정적 본문 자체가 차단 페이지 모양이면 FETCH_BLOCKED
            # 마커로 합성 — LLM 이 agent-browser 로 또 시도하지 않도록.
            if _looks_like_block_page(static_text):
                logger.info(
                    "web_fetch static+headless both blocked for %s (static %d chars, "
                    "headless error: %s)",
                    url, static_len, body[:100],
                )
                return _format_block_page_response(
                    url, static_text, via="static (headless fallback failed)",
                )
            return (
                f"(headless fallback failed: {body[:200]}; returning static body)\n\n"
                f"{static_text}"
            )
        # BIZ-190: 헤드리스 폴백 결과가 또 차단 페이지 모양이면 명시적 FETCH_BLOCKED
        # 마커 응답으로 합성. 둘 다 시도했으니 LLM 이 추가 우회를 시도할 이유가 없음.
        if _looks_like_block_page(body):
            logger.info(
                "web_fetch static+headless both returned block page for %s "
                "(static %d chars, headless %d chars)",
                url, static_len, len(body.strip()),
            )
            return _format_block_page_response(
                url, body, via="static→headless fallback",
            )
        return f"(via headless render; static fetch returned {static_len} chars)\n\n{body}"

    return static_text


# ------------------------------------------------------------------
# file-read — 파일 읽기
# ------------------------------------------------------------------

def handle_file_read(
    routing: dict,
    workspace_dir: Path,
    *,
    persona_local_dir: str | Path | None = None,
) -> str:
    """파일의 텍스트 내용을 줄 번호와 함께 반환한다.

    offset/limit으로 읽을 범위를 제어할 수 있으며, 음수 offset은 파일 끝 기준이다.

    BIZ-142: ``persona_local_dir`` (보통 ``~/.simpleclaw-agent/default``) 도 읽기 허용 루트에
    포함된다. 호출자 (오케스트레이터) 가 ``persona.local_dir`` 설정값을 주입.
    """
    raw_path = routing.get("path", "")
    if not raw_path:
        return "Error: 'path' field is required."

    result = resolve_safe_path(
        raw_path, workspace_dir,
        write=False, persona_local_dir=persona_local_dir,
    )
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
# file-write — 파일 쓰기
# ------------------------------------------------------------------

def handle_file_write(routing: dict, workspace_dir: Path) -> str:
    """파일에 내용을 쓴다 (워크스페이스 디렉토리 내에서만 허용).

    append=True이면 기존 파일에 추가, 아니면 덮어쓴다.
    """
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
# file-manage — 파일 관리
# ------------------------------------------------------------------

def handle_file_manage(
    routing: dict,
    workspace_dir: Path,
    *,
    persona_local_dir: str | Path | None = None,
) -> str:
    """파일 관리 작업을 처리한다 (list, mkdir, delete, info).

    list/info는 프로젝트 + 페르소나 운영 디렉터리에서, mkdir/delete는 워크스페이스
    내에서만 허용된다. BIZ-142: ``persona_local_dir`` 가 주입되면 list/info 의
    허용 루트에 포함된다.
    """
    operation = routing.get("operation", "")
    raw_path = routing.get("path", "")

    if not operation:
        return "Error: 'operation' field is required (list|mkdir|delete|info)."
    if not raw_path:
        return "Error: 'path' field is required."

    if operation == "list":
        result = resolve_safe_path(
            raw_path, workspace_dir,
            write=False, persona_local_dir=persona_local_dir,
        )
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
        result = resolve_safe_path(
            raw_path, workspace_dir,
            write=False, persona_local_dir=persona_local_dir,
        )
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
# skill-docs — 스킬 문서 조회
# ------------------------------------------------------------------

def handle_skill_docs(routing: dict, skills_by_name: dict) -> str:
    """지정된 스킬의 SKILL.md 내용을 반환한다.

    정확한 이름 매칭을 먼저 시도하고, 실패 시 소문자·하이픈 변환 후 퍼지 매칭한다.

    BIZ-166: 응답 첫 부분에 ``execute_skill(skill_name=..., args=...)`` 형식과
    "uvx 금지" 가드 한 줄을 박제한다. SKILL.md 본문이 길어 모델이 도입부만 읽고
    포기해도 정확한 호출 형식을 학습하도록 하는 안전장치.
    """
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

    invocation_header = (
        f"## How to invoke '{skill.name}'\n"
        f"Call: `execute_skill(skill_name=\"{skill.name}\", "
        f"args=\"<positional args>\")`.\n"
        f"NEVER use `uvx {skill.name}`, `pipx run {skill.name}`, or "
        f"`pip install` — this skill is local-only and not on PyPI.\n"
    )

    skill_md = Path(skill.skill_dir) / "SKILL.md"
    if not skill_md.is_file():
        return (
            f"{invocation_header}\n"
            f"Skill '{skill.name}' has no documentation. "
            f"Description: {skill.description}"
        )

    try:
        content = skill_md.read_text(encoding="utf-8")
        if len(content) > 3000:
            content = content[:3000] + "\n... [truncated]"
        return (
            f"{invocation_header}\n"
            f"# Documentation for '{skill.name}'\n\n{content}\n\n"
            f"Use the EXACT commands shown above."
        )
    except OSError:
        return f"Error: could not read documentation for '{skill.name}'."


# ------------------------------------------------------------------
# cron — ReAct 액션 핸들러 (/cron 슬래시 명령과 별도)
# ------------------------------------------------------------------

def handle_cron_action(
    routing: dict,
    cron_scheduler: CronScheduler | None,
) -> str:
    """ReAct 루프에서 발생한 cron 액션을 처리한다.

    list, add, remove, enable, disable 액션을 지원한다.
    """
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


# ------------------------------------------------------------------
# clarify — 다지선다 질문 (BIZ-260)
# ------------------------------------------------------------------

def handle_clarify(
    routing: dict,
    pending_clarify: dict,
    *,
    chat_id: int | None,
) -> str:
    """LLM 이 호출한 ``clarify(question, options)`` 를 채널 브리지에 적재한다.

    - ``chat_id`` 가 None (cron 잡 등 비-사용자 채널 진입점) 이면 오류 응답:
      cron 컨텍스트에서 사용자에게 되묻는 것은 의미 없음.
    - 옵션 정규화·라벨 cap 검증은 ``normalize_options`` 가 담당.
    - ``pending_clarify[chat_id]`` 를 덮어써 한 chat 에서 동시에 두 clarify 가
      대기 상태가 되는 일을 막는다 (LLM 이 한 turn 안에 clarify 를 두 번 부르면
      마지막 호출만 사용자에게 도달 — 일관 동작).
    """
    from simpleclaw.agent.clarify import ClarifyRequest, normalize_options

    if chat_id is None:
        return (
            "Error: clarify is not supported in this context (no chat). "
            "Use clarify only in interactive messaging channels."
        )

    question = (routing.get("question") or "").strip()
    if not question:
        return "Error: 'question' field is required (non-empty string)."

    raw_options = routing.get("options")
    try:
        options = normalize_options(raw_options)
    except ValueError as exc:
        return f"Error: {exc}"

    pending_clarify[chat_id] = ClarifyRequest(
        question=question, options=options,
    )
    return (
        f"Clarification posted to user with {len(options)} options. "
        "The tool loop will end now; the user's reply (button tap or text) "
        "arrives as the next message."
    )


def _cron_list(cron_scheduler: CronScheduler) -> str:
    """cron 작업 목록을 포맷팅하여 반환한다 (ReAct 핸들러와 /cron 명령이 공유)."""
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
