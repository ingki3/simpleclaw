"""Admin API route group registry."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from simpleclaw.channels.admin_routes import (
    config,
    dreaming,
    insights,
    secrets,
    suggestions,
    system,
)
from simpleclaw.channels.admin_routes.common import (
    bind_route_group_handlers,
    register_route_groups,
)

ROUTE_GROUPS = (config, secrets, system, insights, suggestions, dreaming)


def register_admin_routes(app: web.Application, server: Any, prefix: str) -> None:
    """분리된 route group을 기존 URL 순서와 의미 그대로 등록한다."""
    register_route_groups(app, server, prefix, ROUTE_GROUPS)


def bind_admin_route_handlers(server_cls: type) -> None:
    """기존 테스트/내부 호출 호환을 위해 handler 함수를 서버 메서드로 바인딩한다."""
    bind_route_group_handlers(server_cls, ROUTE_GROUPS)
