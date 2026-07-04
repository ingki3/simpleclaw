"""Chrome Extension Native Messaging 기반 브라우저 handoff 패키지.

자동 ``web_fetch``/headless 브라우저가 403·Cloudflare·human verification으로
막힌 경우, 사용자가 로컬 Chrome에서 접근 확인만 수행하고 SimpleClaw가 확장
프로그램의 명시적 승인 이벤트를 통해 현재 탭 텍스트를 회수하도록 돕는다.
"""

from simpleclaw.browser_handoff.models import BrowserHandoffPage, BrowserHandoffRequest
from simpleclaw.browser_handoff.store import BrowserHandoffStore

__all__ = ["BrowserHandoffPage", "BrowserHandoffRequest", "BrowserHandoffStore"]
