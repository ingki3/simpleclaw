"""Admin API 서버 부팅 헬퍼 (BIZ-58).

``scripts/run_bot.py``에서 ``AdminAPIServer`` 인스턴스를 만들 때 사용하는
얇은 래퍼. 운영 진입점에서 토큰 검증·시크릿 매니저 주입·헬스 콜백 결합 같은
부팅 시 가드를 한곳에 모아 두어 통합 테스트가 같은 경로를 그대로 돌릴 수 있도록 한다.

설계 결정:

- **silent insecure 방지**: ``enabled=True``인데 토큰이 비어 있으면 ``RuntimeError``로
  부팅을 중단한다. 운영자는 토큰을 명시적으로 등록해야 한다.
- **enabled=false면 ``None`` 반환**: 호출자는 그냥 서버를 띄우지 않는다 — CI/개발
  편의를 위함.
- **헬스 콜백은 외부 주입**: 데몬의 메인 헬스(`HEARTBEAT.md`/대시보드)와 결합할
  때 호출자가 stat dict를 만들어 넘기면 ``/admin/v1/health`` 응답에 머지된다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from simpleclaw.channels.admin_api import AdminAPIServer
from simpleclaw.channels.admin_audit import AuditLog
from simpleclaw.config import load_admin_api_config
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.insights import InsightStore
from simpleclaw.security.secrets import SecretsManager

logger = logging.getLogger(__name__)


class AdminAPIBootError(RuntimeError):
    """Admin API 부팅 시 검증 실패를 나타내는 예외 — silent insecure 방지용."""


def build_admin_api_server(
    config_path: str | Path,
    *,
    secrets_manager: SecretsManager | None = None,
    audit_log: AuditLog | None = None,
    structured_logger: object | None = None,
    health_provider: Callable[[], dict] | None = None,
    restart_callback: Callable[[dict], object] | None = None,
    reload_callback: Callable[[str, dict], object] | None = None,
    admin_state_dir: str | Path | None = None,
    conversation_store: ConversationStore | None = None,
    insight_store: InsightStore | None = None,
) -> AdminAPIServer | None:
    """``config.yaml``에서 admin_api 설정을 읽어 ``AdminAPIServer``를 만든다.

    Args:
        config_path: ``config.yaml`` 경로. AdminAPIServer가 직접 읽고 쓴다.
        secrets_manager: 시크릿 회전/노출용 매니저. 미지정 시 기본 매니저 인스턴스.
        audit_log: 감사 로그. 미지정 시 기본 경로(``~/.simpleclaw/audit/``).
        structured_logger: ``/admin/v1/logs`` 응답을 채울 로거.
        health_provider: ``/admin/v1/health`` 응답에 머지할 추가 dict 콜백.
        restart_callback / reload_callback: 시스템 액션·hot reload 후크.
        admin_state_dir: pending 변경/reveal nonce 보관 위치.

    Returns:
        admin_api.enabled=False면 ``None``. 그 외에는 부팅 직전 상태의 서버 객체.

    Raises:
        AdminAPIBootError: enabled=True인데 토큰이 비어 있는 경우.
    """
    cfg = load_admin_api_config(config_path)
    if not cfg["enabled"]:
        logger.info("Admin API disabled via config — skipping bind.")
        return None

    token = cfg["token_secret"]
    if not token:
        # silent insecure 운용을 막는다 — 토큰 미설정은 항상 부팅 실패.
        raise AdminAPIBootError(
            "admin_api.token_secret이 비어 있습니다. "
            "keyring/file/env 어디에도 admin_api_token이 등록돼 있지 않거나 "
            "config.yaml의 참조 문자열이 잘못됐습니다. "
            "발급 예시: SecretsManager().store('keyring', 'admin_api_token', secrets.token_urlsafe(32))"
        )

    return AdminAPIServer(
        host=cfg["bind_host"],
        port=cfg["bind_port"],
        auth_token=token,
        config_path=config_path,
        audit_log=audit_log,
        secrets_manager=secrets_manager,
        admin_state_dir=admin_state_dir,
        restart_callback=restart_callback,
        reload_callback=reload_callback,
        structured_logger=structured_logger,
        health_provider=health_provider,
        cors_origins=cfg["cors_origins"],
        request_max_body_bytes=cfg["request_max_body_kb"] * 1024,
        # BIZ-77 — 인사이트 source 역추적 엔드포인트가 사용하는 의존성.
        # 둘 중 하나라도 None 이면 핸들러가 503 으로 명시적 disabled 응답.
        conversation_store=conversation_store,
        insight_store=insight_store,
    )


__all__ = ["AdminAPIBootError", "build_admin_api_server"]
