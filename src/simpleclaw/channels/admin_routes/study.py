"""Admin API Agent Study Wiki 관찰성 route handlers (BIZ-395).

운영자가 Admin UI(또는 admin token 인증 호출)로 Agent 가 무엇을 공부했는지
점검하고, 잘못 공부한 topic 을 archive 하거나 즉시 refresh 요청을 걸 수 있게 한다.
operator native tool(`study_status`)과 동일한 ``StudyWikiStore`` 를 공유하므로
조회/조작 의미가 두 경로에서 일치한다.

설계 결정:
- **service 미주입 시 config 폴백.** 서버에 ``study_status_service`` 를 명시 주입하지
  않아도, ``config.yaml`` 의 ``study.wiki_dir`` 로 store 를 지연 구성한다. wiki 가
  아직 없으면 각 응답이 ``configured=false`` 로 명시 — 503 남발 대신 운영 진단에
  유리한 명시적 빈 상태를 돌려준다.
- **operator scope.** 이 라우트는 ``/admin/v1`` 아래에 mount 되며 admin auth wrapper
  (``server._wrap``) 를 거친다. admin token 자체가 운영자 경계이므로 일반 사용자
  runtime 표면에는 절대 노출되지 않는다.
- **refresh 는 무거운 수집을 동기 실행하지 않는다.** 다음 daily run 이 pick up 할
  요청 플래그만 남긴다(store.refresh_topic 참조).
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from simpleclaw.agent.study_status import (
    StudyStatusError,
    StudyWikiStore,
    _resolve_status_thresholds,
    resolve_wiki_dir,
)
from simpleclaw.channels.admin_api import _json_error, _json_ok

logger = logging.getLogger(__name__)


def register_routes(app: web.Application, server: Any, prefix: str) -> None:
    """Agent Study Wiki 조회/조작 routes를 등록한다."""
    app.router.add_get(
        f"{prefix}/study/status",
        server._wrap(server._handle_study_status),
    )
    app.router.add_get(
        f"{prefix}/study/topics",
        server._wrap(server._handle_study_topics),
    )
    app.router.add_get(
        f"{prefix}/study/topics/{{topic_id}}",
        server._wrap(server._handle_study_topic_detail),
    )
    app.router.add_post(
        f"{prefix}/study/topics/{{topic_id}}/refresh",
        server._wrap(server._handle_study_refresh),
    )
    app.router.add_post(
        f"{prefix}/study/topics/{{topic_id}}/archive",
        server._wrap(server._handle_study_archive),
    )


HANDLERS = (
    "_study_store",
    "_handle_study_status",
    "_handle_study_topics",
    "_handle_study_topic_detail",
    "_handle_study_refresh",
    "_handle_study_archive",
)


def _study_store(self) -> StudyWikiStore:
    """주입된 store 를 쓰거나, config.yaml 기준으로 지연 구성한다.

    명시 주입(테스트/특수 배포)이 우선이고, 없으면 운영 config 의 wiki_dir 과
    임계값으로 store 를 만든다 — Admin API 부팅 경로에 study 의존성을 강제로
    엮지 않기 위함.
    """
    injected = getattr(self, "_study_status_service", None)
    if injected is not None:
        return injected
    stale_hours, low_conf = _resolve_status_thresholds(self._config_path)
    return StudyWikiStore(
        resolve_wiki_dir(self._config_path),
        stale_after_hours=stale_hours,
        low_confidence_threshold=low_conf,
    )


async def _handle_study_status(self, request: web.Request) -> web.Response:
    """``GET /admin/v1/study/status``.

    최근 study run·active topic·stale topic·low-confidence item 을 한 번에 반환.
    wiki 미구성 시 ``configured=false`` 와 안내 ``note`` 를 담아 200 으로 응답한다.
    """
    store = self._study_store()
    try:
        report = store.status_report()
    except StudyStatusError as exc:
        return _json_error(500, f"study wiki 읽기 실패: {exc}")
    return _json_ok(report.to_dict())


async def _handle_study_topics(self, request: web.Request) -> web.Response:
    """``GET /admin/v1/study/topics?include_archived=``.

    topic registry 전체를 반환한다. ``include_archived`` 가 truthy 면 archived
    topic 도 포함한다(기본 active 만).
    """
    store = self._study_store()
    include_archived = (request.query.get("include_archived") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    try:
        topics = store.load_topics(include_archived=include_archived)
    except StudyStatusError as exc:
        return _json_error(500, f"topic registry 읽기 실패: {exc}")
    return _json_ok(
        {
            "configured": store.is_configured(),
            "wiki_dir": str(store.wiki_dir),
            "include_archived": include_archived,
            "count": len(topics),
            "topics": [t.to_dict() for t in topics],
        }
    )


async def _handle_study_topic_detail(self, request: web.Request) -> web.Response:
    """``GET /admin/v1/study/topics/{topic_id}``.

    단일 topic 메타와 ``topics/<id>/overview.md`` 본문을 반환. 없으면 404.
    """
    store = self._study_store()
    topic_id = (request.match_info.get("topic_id") or "").strip()
    if not topic_id:
        return _json_error(422, "topic_id path parameter is required")
    try:
        view = store.get_topic(topic_id)
    except StudyStatusError as exc:
        return _json_error(500, f"topic 조회 실패: {exc}")
    if view is None:
        return _json_error(404, f"topic not found: {topic_id}")
    return _json_ok({"topic": view.to_dict(), "page": store.topic_page(topic_id)})


async def _handle_study_refresh(self, request: web.Request) -> web.Response:
    """``POST /admin/v1/study/topics/{topic_id}/refresh``.

    다음 daily run 에서 재수집하도록 refresh 요청 플래그를 건다. 무거운 수집을
    동기로 실행하지 않는다.
    """
    store = self._study_store()
    topic_id = (request.match_info.get("topic_id") or "").strip()
    if not topic_id:
        return _json_error(422, "topic_id path parameter is required")
    try:
        view = store.refresh_topic(topic_id)
    except StudyStatusError as exc:
        return _json_error(404, str(exc))
    return _json_ok(
        {
            "topic": view.to_dict(),
            "message": (
                f"topic '{topic_id}' refresh 요청을 등록했습니다. "
                "다음 daily study run 에서 재수집됩니다."
            ),
        }
    )


async def _handle_study_archive(self, request: web.Request) -> web.Response:
    """``POST /admin/v1/study/topics/{topic_id}/archive``.

    잘못 공부한 topic 을 archived 로 표시해 retrieval/공부 대상에서 제외한다.
    """
    store = self._study_store()
    topic_id = (request.match_info.get("topic_id") or "").strip()
    if not topic_id:
        return _json_error(422, "topic_id path parameter is required")
    try:
        view = store.archive_topic(topic_id)
    except StudyStatusError as exc:
        return _json_error(404, str(exc))
    return _json_ok(
        {
            "topic": view.to_dict(),
            "message": f"topic '{topic_id}' 을 archive 했습니다.",
        }
    )
