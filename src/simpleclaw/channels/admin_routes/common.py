"""Admin API route module registry/common binding helpers."""

from __future__ import annotations

from typing import Any

from aiohttp import web


def register_route_groups(
    app: web.Application,
    server: Any,
    prefix: str,
    route_groups: tuple[Any, ...],
) -> None:
    """분리된 route group들을 기존 URL 의미 그대로 앱에 mount한다."""
    for group in route_groups:
        group.register_routes(app, server, prefix)


def bind_route_group_handlers(server_cls: type, route_groups: tuple[Any, ...]) -> None:
    """라우트 모듈 함수를 ``AdminAPIServer`` 메서드 표면에 호환 바인딩한다."""
    for group in route_groups:
        for name in group.HANDLERS:
            handler = getattr(group, name)
            if name == "_read_json_body":
                # 기존 구현은 @staticmethod였고 호출부는 self._read_json_body(request)를 쓴다.
                handler = staticmethod(handler)
            setattr(server_cls, name, handler)
