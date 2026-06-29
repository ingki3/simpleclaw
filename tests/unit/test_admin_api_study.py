"""Admin API Agent Study Wiki 라우트 테스트 (BIZ-395).

``/admin/v1/study/*`` 엔드포인트가 인증 wrapper 를 거쳐 study wiki 를 조회하고,
operator action(refresh/archive)이 topics.yaml 을 변경하는지 검증한다. study
파이프라인 의존 없이 임시 wiki 디렉터리 + 주입 store 로 격리한다.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import yaml

from simpleclaw.agent.study_status import StudyWikiStore
from simpleclaw.channels.admin_api import AdminAPIServer

HEADERS = {"Authorization": "Bearer test-token"}


@pytest.fixture
def wiki(tmp_path):
    """topic 2개(active/stale)와 daily note 1개를 담은 임시 wiki."""
    d = tmp_path / "agent_wiki"
    d.mkdir()
    (d / "topics.yaml").write_text(
        yaml.safe_dump(
            {
                "topics": [
                    {"id": "a", "title": "Alpha", "status": "active", "confidence": 0.9},
                    {"id": "b", "title": "Beta", "status": "archived", "confidence": 0.4},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    daily = d / "daily"
    daily.mkdir()
    (daily / "2026-06-29.md").write_text("# 2026-06-29\n공부 내용", encoding="utf-8")
    return d


@pytest.fixture
def server(tmp_path, wiki):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"agent": {}}), encoding="utf-8")
    return AdminAPIServer(
        auth_token="test-token",
        config_path=config_path,
        admin_state_dir=tmp_path / "admin",
        study_status_service=StudyWikiStore(wiki),
    )


@pytest_asyncio.fixture
async def client(server, aiohttp_client):
    return await aiohttp_client(server.get_app())


class TestStudyObservability:
    @pytest.mark.asyncio
    async def test_status_requires_auth(self, client):
        resp = await client.get("/admin/v1/study/status")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_status_returns_report(self, client):
        resp = await client.get("/admin/v1/study/status", headers=HEADERS)
        assert resp.status == 200
        body = await resp.json()
        assert body["configured"] is True
        assert body["total_topics"] == 2
        assert {t["id"] for t in body["active_topics"]} == {"a"}
        assert body["last_run"]["date"] == "2026-06-29"

    @pytest.mark.asyncio
    async def test_topics_include_archived_toggle(self, client):
        resp = await client.get("/admin/v1/study/topics", headers=HEADERS)
        body = await resp.json()
        assert [t["id"] for t in body["topics"]] == ["a"]

        resp_all = await client.get(
            "/admin/v1/study/topics?include_archived=true", headers=HEADERS
        )
        body_all = await resp_all.json()
        assert {t["id"] for t in body_all["topics"]} == {"a", "b"}

    @pytest.mark.asyncio
    async def test_topic_detail_404(self, client):
        resp = await client.get("/admin/v1/study/topics/zzz", headers=HEADERS)
        assert resp.status == 404


class TestStudyOperatorActions:
    @pytest.mark.asyncio
    async def test_archive_persists(self, client, wiki):
        resp = await client.post("/admin/v1/study/topics/a/archive", headers=HEADERS)
        assert resp.status == 200
        body = await resp.json()
        assert body["topic"]["is_archived"] is True
        saved = yaml.safe_load((wiki / "topics.yaml").read_text(encoding="utf-8"))
        statuses = {t["id"]: t["status"] for t in saved["topics"]}
        assert statuses["a"] == "archived"

    @pytest.mark.asyncio
    async def test_refresh_sets_flag(self, client, wiki):
        resp = await client.post("/admin/v1/study/topics/a/refresh", headers=HEADERS)
        assert resp.status == 200
        body = await resp.json()
        assert body["topic"]["refresh_requested_at"] is not None

    @pytest.mark.asyncio
    async def test_refresh_unknown_topic_404(self, client):
        resp = await client.post("/admin/v1/study/topics/zzz/refresh", headers=HEADERS)
        assert resp.status == 404


class TestStudyConfigFallback:
    """service 미주입 시 config.yaml 의 wiki_dir 로 지연 구성되는지 확인."""

    @pytest.mark.asyncio
    async def test_falls_back_to_config_wiki_dir(self, tmp_path, wiki, aiohttp_client):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"study": {"wiki_dir": str(wiki)}}), encoding="utf-8"
        )
        server = AdminAPIServer(
            auth_token="test-token",
            config_path=config_path,
            admin_state_dir=tmp_path / "admin",
            # study_status_service 미주입 — config 폴백 경로.
        )
        client = await aiohttp_client(server.get_app())
        resp = await client.get("/admin/v1/study/status", headers=HEADERS)
        assert resp.status == 200
        body = await resp.json()
        assert body["configured"] is True
        assert body["total_topics"] == 2
