"""`browser_handoff` native tool handler.

이 도구는 자동 fetch가 차단된 interactive URL 요청에서만 로컬 Chrome을 열고, 사용자가
Chrome 확장 프로그램에서 현재 탭 텍스트 전송을 승인할 때까지 TTL store를 기다린다.
cron/background 컨텍스트에서는 데스크톱 side effect를 막기 위해 실행하지 않는다.
"""

from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from simpleclaw.browser_handoff.store import BrowserHandoffStore, valid_request_id

_SENSITIVE_DOMAIN_KEYWORDS = (
    "bank",
    "paypal",
    "stripe",
    "checkout",
    "gmail",
    "mail.google",
    "accounts.google",
    "login",
    "signin",
    "auth",
    "admin",
)


def _is_internal_url(url: str) -> bool:
    """localhost/private network URL은 Chrome handoff에서도 차단한다."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True
    host = (parsed.hostname or "").strip().lower()
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _looks_sensitive(url: str) -> bool:
    """MVP에서 보수적으로 차단할 민감 URL인지 검사한다."""

    parsed = urlparse(url)
    target = f"{parsed.hostname or ''}{parsed.path}".lower()
    return any(keyword in target for keyword in _SENSITIVE_DOMAIN_KEYWORDS)


def append_request_fragment(url: str, request_id: str) -> str:
    """Chrome extension이 request_id를 읽도록 URL fragment에 값을 추가한다."""

    parsed = urlparse(url)
    fragment_items = parse_qsl(parsed.fragment, keep_blank_values=True)
    fragment_items = [(k, v) for k, v in fragment_items if k != "simpleclaw_request"]
    fragment_items.append(("simpleclaw_request", request_id))
    return urlunparse(parsed._replace(fragment=urlencode(fragment_items)))


async def open_chrome(url: str, chrome_app: str) -> None:
    """macOS `open -a`로 로컬 Chrome에 URL을 연다."""

    proc = await asyncio.create_subprocess_exec("open", "-a", chrome_app, url)
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"failed to open {chrome_app}: exit {proc.returncode}")


def _format_page_result(page) -> str:
    """도구 observation으로 반환할 페이지 본문을 포맷한다."""

    title = page.title or "(untitled)"
    warning = f"\nWarning: {page.warning}" if page.warning else ""
    return (
        f"BROWSER_HANDOFF_CONTENT: {page.request_id or 'latest'}\n"
        f"URL: {page.url}\n"
        f"Title: {title}{warning}\n"
        f"--- extracted text ({len(page.text)} chars) ---\n"
        f"{page.text}"
    )


async def handle_browser_handoff(
    args: dict,
    *,
    config: dict,
    interactive: bool,
    opener=None,
) -> str:
    """browser_handoff 도구 호출을 처리한다."""

    if not config.get("enabled", False):
        return "Error: browser_handoff is disabled in config."
    if not interactive:
        return "Error: browser_handoff is available only for interactive user requests."

    action = str(args.get("action") or "").strip()
    if action not in {"open_and_wait", "status", "read", "read_latest"}:
        return "Error: action must be one of open_and_wait, status, read, read_latest."

    store = BrowserHandoffStore(
        config.get("store_dir", "~/.simpleclaw-agent/default/browser-handoff"),
        ttl_seconds=int(config.get("request_ttl_seconds", 600)),
        max_chars=int(config.get("max_extracted_chars", 50_000)),
    )
    store.expire_old_requests()

    request_id = str(args.get("request_id") or "").strip()
    url = str(args.get("url") or "").strip()

    if action == "read":
        if not request_id or not valid_request_id(request_id):
            return "Error: valid request_id is required for read."
        page = store.read(request_id)
        return _format_page_result(page) if page else f"BROWSER_HANDOFF_PENDING: {request_id}"

    if action == "read_latest":
        page = store.read_latest(url or None)
        return _format_page_result(page) if page else "BROWSER_HANDOFF_NOT_FOUND: no extracted page text is available."

    if action == "status":
        if request_id and valid_request_id(request_id):
            req = store.get_request(request_id)
            page = store.read(request_id)
            if page:
                return _format_page_result(page)
            if req:
                return f"BROWSER_HANDOFF_STATUS: {request_id} status={req.status} url={req.url}"
        latest = store.read_latest(url or None)
        return _format_page_result(latest) if latest else "BROWSER_HANDOFF_STATUS: no matching request/content."

    # open_and_wait
    if not url:
        return "Error: url is required for open_and_wait."
    if _is_internal_url(url):
        return "Error: internal/local network URLs are blocked."
    policy = str(config.get("sensitive_domain_policy", "block"))
    if policy == "block" and _looks_sensitive(url):
        return "Error: sensitive/login/payment/admin pages are blocked for browser_handoff."

    req = store.create_request(url)
    open_url = append_request_fragment(url, req.request_id)
    opener = opener or open_chrome
    try:
        await opener(open_url, str(config.get("chrome_app", "Google Chrome")))
    except TypeError:
        await opener(open_url)
    except Exception as exc:  # noqa: BLE001 — tool 결과는 문자열 에러로 반환
        return f"Error: failed to open local Chrome — {str(exc)[:200]}"

    wait_seconds = int(args.get("wait_seconds", config.get("open_wait_seconds", 90)))
    wait_seconds = max(0, min(180, wait_seconds))
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while asyncio.get_running_loop().time() <= deadline:
        page = store.read(req.request_id)
        if page:
            return _format_page_result(page)
        if wait_seconds == 0:
            break
        await asyncio.sleep(0.5)

    return (
        f"BROWSER_HANDOFF_PENDING: {req.request_id}\n"
        f"URL: {url}\n"
        "Chrome was opened for this URL. Ask the user to complete any browser-side "
        "verification/login if needed and click the SimpleClaw extension button. "
        "Do not ask the user to copy/paste page text."
    )
