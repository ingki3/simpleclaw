"""브라우저 handoff 요청/페이지 payload 모델.

모델은 JSON 파일 store와 Chrome Native Messaging host 양쪽에서 공유된다. 쿠키,
폼 값, localStorage 같은 민감한 브라우저 상태는 의도적으로 표현하지 않는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

RequestStatus = Literal["pending", "received", "expired", "blocked", "error"]


@dataclass(frozen=True)
class BrowserHandoffRequest:
    """SimpleClaw가 로컬 Chrome에 열어 둔 URL handoff 요청."""

    request_id: str
    url: str
    normalized_url: str
    created_at: str
    expires_at: str
    status: RequestStatus = "pending"


@dataclass(frozen=True)
class BrowserHandoffPage:
    """Chrome Extension이 사용자 승인 후 Native Host로 보낸 현재 탭 텍스트."""

    request_id: str | None
    url: str
    normalized_url: str
    title: str
    text: str
    extracted_at: str
    source: str = "chrome_extension"
    warning: str | None = None


def utc_now_iso() -> str:
    """UTC ISO timestamp를 일관된 형식으로 반환한다."""

    return datetime.now(UTC).isoformat()


def to_json_dict(obj: object) -> dict:
    """dataclass 모델을 JSON 직렬화 가능한 dict로 변환한다."""

    return asdict(obj)  # type: ignore[arg-type]
