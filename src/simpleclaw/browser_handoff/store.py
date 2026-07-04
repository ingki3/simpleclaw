"""브라우저 handoff 요청과 추출 페이지 텍스트를 저장하는 TTL JSON store.

SQLite를 도입하지 않고 작은 JSON 파일들만 사용한다. Native Messaging host는 Chrome
확장 프로그램에서 짧게 실행되는 프로세스이므로, 단순 파일 store가 디버깅과 설치
경로 안정성 면에서 충분하다.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from simpleclaw.browser_handoff.models import (
    BrowserHandoffPage,
    BrowserHandoffRequest,
    to_json_dict,
    utc_now_iso,
)

_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid"}


def normalize_url(url: str) -> str:
    """요청/수신 매칭용 URL을 정규화한다.

    서버에는 전달되지 않는 fragment와 흔한 tracking query를 제거한다. 나머지 query는
    순서를 보존해 페이지 의미를 바꾸지 않는다.
    """

    parsed = urlparse(url.strip())
    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower = key.lower()
        if lower in _TRACKING_QUERY_KEYS or any(
            lower.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES
        ):
            continue
        filtered_query.append((key, value))
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            "",
            urlencode(filtered_query, doseq=True),
            "",
        )
    )


def _parse_iso(value: str) -> datetime:
    """``datetime.fromisoformat`` 결과를 timezone-aware UTC로 정규화한다."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class BrowserHandoffStore:
    """브라우저 handoff 요청/페이지 payload를 JSON 파일로 보관한다."""

    def __init__(
        self,
        root: str | Path,
        *,
        ttl_seconds: int = 600,
        max_chars: int = 50_000,
    ) -> None:
        self.root = Path(root).expanduser()
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_chars = max(1_000, int(max_chars))
        self.requests_dir = self.root / "requests"
        self.pages_dir = self.root / "pages"
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.pages_dir.mkdir(parents=True, exist_ok=True)

    def create_request(self, url: str) -> BrowserHandoffRequest:
        """URL handoff 요청을 생성하고 TTL 만료시각과 함께 저장한다."""

        now = datetime.now(UTC)
        request = BrowserHandoffRequest(
            request_id=uuid.uuid4().hex,
            url=url,
            normalized_url=normalize_url(url),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=self.ttl_seconds)).isoformat(),
        )
        self._write_json(self.requests_dir / f"{request.request_id}.json", to_json_dict(request))
        return request

    def get_request(self, request_id: str) -> BrowserHandoffRequest | None:
        """요청 ID로 pending/received 요청을 읽는다."""

        data = self._read_json(self.requests_dir / f"{request_id}.json")
        if not data:
            return None
        return BrowserHandoffRequest(**data)

    def receive_page(self, payload: dict) -> BrowserHandoffPage:
        """Chrome extension payload를 저장하고 관련 요청 상태를 received로 갱신한다."""

        url = str(payload.get("url") or "").strip()
        title = str(payload.get("title") or "").strip()
        text = str(payload.get("text") or "").strip()
        request_id = payload.get("request_id") or None
        if isinstance(request_id, str):
            request_id = request_id.strip() or None
        else:
            request_id = None

        normalized = normalize_url(url)
        warning: str | None = None
        if request_id is None:
            matched = self._latest_pending_for_url(normalized)
            if matched is not None:
                request_id = matched.request_id
            else:
                warning = "no_pending_request"

        if len(text) > self.max_chars:
            text = text[: self.max_chars] + f"\n\n... [truncated at {self.max_chars} chars]"

        page = BrowserHandoffPage(
            request_id=request_id,
            url=url,
            normalized_url=normalized,
            title=title,
            text=text,
            extracted_at=utc_now_iso(),
            warning=warning,
        )
        page_name = request_id or "latest"
        self._write_json(self.pages_dir / f"{page_name}.json", to_json_dict(page))
        self._write_json(self.root / "latest.json", to_json_dict(page))
        if request_id:
            self._mark_request_received(request_id)
        return page

    def read(self, request_id: str) -> BrowserHandoffPage | None:
        """요청 ID에 해당하는 추출 페이지를 읽는다."""

        data = self._read_json(self.pages_dir / f"{request_id}.json")
        return BrowserHandoffPage(**data) if data else None

    def read_latest(self, url: str | None = None) -> BrowserHandoffPage | None:
        """가장 최근 페이지를 읽고, URL이 주어지면 정규화 URL이 같은 경우만 반환한다."""

        data = self._read_json(self.root / "latest.json")
        if not data:
            return None
        page = BrowserHandoffPage(**data)
        if url and page.normalized_url != normalize_url(url):
            return None
        return page

    def expire_old_requests(self) -> int:
        """TTL이 지난 pending 요청을 expired로 표시하고 개수를 반환한다."""

        now = datetime.now(UTC)
        expired = 0
        for path in self.requests_dir.glob("*.json"):
            data = self._read_json(path)
            if not data or data.get("status") != "pending":
                continue
            if _parse_iso(str(data["expires_at"])) <= now:
                data["status"] = "expired"
                self._write_json(path, data)
                expired += 1
        return expired

    def _latest_pending_for_url(self, normalized_url: str) -> BrowserHandoffRequest | None:
        candidates: list[BrowserHandoffRequest] = []
        for path in self.requests_dir.glob("*.json"):
            data = self._read_json(path)
            if not data or data.get("status") != "pending":
                continue
            req = BrowserHandoffRequest(**data)
            if req.normalized_url == normalized_url:
                candidates.append(req)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.created_at, reverse=True)
        return candidates[0]

    def _mark_request_received(self, request_id: str) -> None:
        path = self.requests_dir / f"{request_id}.json"
        data = self._read_json(path)
        if not data:
            return
        data["status"] = "received"
        self._write_json(path, data)

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def valid_request_id(value: str) -> bool:
    """URL fragment로 전달해도 안전한 request id인지 검사한다."""

    return bool(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", value))
