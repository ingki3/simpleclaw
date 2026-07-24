"""``study_status`` operator native tool 단위 테스트 (BIZ-395).

Agent Study Wiki on-disk 레이아웃(topics.yaml + daily/*.md + index.sqlite)을 임시
디렉터리에 만들어 store/handler 동작을 격리 검증한다. 수집 파이프라인이 없어도
graceful 하게 동작하는지, operator action(refresh/archive)이 topics.yaml 을 올바르게
변경하는지, dispatch operator gate 가 닫혀 있을 때 차단되는지를 확인한다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
import yaml

from simpleclaw.agent.study_status import (
    StudyWikiStore,
    handle_study_status,
    resolve_wiki_dir,
)
from simpleclaw.agent.tool_dispatch import dispatch_tool_call
from simpleclaw.llm.models import ToolCall

# 모든 age 계산의 기준 시각 — 테스트 결정성을 위해 고정.
_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


def _now() -> datetime:
    return _NOW


def _write_wiki(
    tmp_path,
    topics: list[dict],
    *,
    daily: dict[str, str] | None = None,
):
    """임시 wiki 디렉터리를 구성하고 경로를 반환한다."""
    wiki = tmp_path / "agent_wiki"
    wiki.mkdir()
    (wiki / "topics.yaml").write_text(
        yaml.safe_dump({"topics": topics}, allow_unicode=True), encoding="utf-8"
    )
    if daily:
        ddir = wiki / "daily"
        ddir.mkdir()
        for name, body in daily.items():
            (ddir / name).write_text(body, encoding="utf-8")
    return wiki


def _store(wiki, **kw) -> StudyWikiStore:
    kw.setdefault("now", _now)
    return StudyWikiStore(wiki, **kw)


# ---------------------------------------------------------------------------
# 미구성 / graceful degradation
# ---------------------------------------------------------------------------


class TestNotConfigured:
    def test_status_on_missing_wiki_is_not_configured(self, tmp_path):
        store = _store(tmp_path / "nope")
        report = store.status_report()
        assert report.configured is False
        assert report.total_topics == 0
        assert report.note is not None

    def test_handler_status_missing_wiki_ok_false_configured(self, tmp_path):
        out = json.loads(
            handle_study_status({"action": "status"}, store=_store(tmp_path / "nope"))
        )
        assert out["ok"] is True
        assert out["configured"] is False
        assert out["active_topics"] == []


# ---------------------------------------------------------------------------
# status / topics 조회
# ---------------------------------------------------------------------------


class TestStatusReport:
    def test_status_separates_active_stale_and_runs(self, tmp_path):
        wiki = _write_wiki(
            tmp_path,
            topics=[
                {
                    "id": "fresh",
                    "title": "Fresh topic",
                    "status": "active",
                    "confidence": 0.9,
                    "last_studied_at": "2026-06-29T06:00:00+00:00",  # 6h ago → fresh
                },
                {
                    "id": "old",
                    "title": "Old topic",
                    "status": "active",
                    "confidence": 0.8,
                    "last_studied_at": "2026-06-20T06:00:00+00:00",  # >72h → stale
                },
                {
                    "id": "gone",
                    "title": "Archived topic",
                    "status": "archived",
                    "confidence": 0.4,
                },
            ],
            daily={
                "2026-06-28.md": "# 2026-06-28\n어제 공부",
                "2026-06-29.md": "# 2026-06-29\n오늘 공부한 내용",
            },
        )
        report = _store(wiki).status_report()
        assert report.configured is True
        assert report.total_topics == 3
        assert {t.id for t in report.active_topics} == {"fresh", "old"}
        assert [t.id for t in report.stale_topics] == ["old"]
        assert report.last_run is not None
        assert report.last_run.date == "2026-06-29"
        assert report.last_run.total_runs == 2

    def test_stale_when_never_studied(self, tmp_path):
        wiki = _write_wiki(
            tmp_path,
            topics=[{"id": "x", "title": "X", "status": "active"}],
        )
        report = _store(wiki).status_report()
        assert [t.id for t in report.stale_topics] == ["x"]

    def test_topics_excludes_archived_by_default(self, tmp_path):
        wiki = _write_wiki(
            tmp_path,
            topics=[
                {"id": "a", "title": "A", "status": "active"},
                {"id": "b", "title": "B", "status": "archived"},
            ],
        )
        out = json.loads(handle_study_status({"action": "topics"}, store=_store(wiki)))
        assert [t["id"] for t in out["topics"]] == ["a"]

        out_all = json.loads(
            handle_study_status(
                {"action": "topics", "include_archived": True}, store=_store(wiki)
            )
        )
        assert {t["id"] for t in out_all["topics"]} == {"a", "b"}


# ---------------------------------------------------------------------------
# low confidence (index.sqlite 우선, topic 폴백)
# ---------------------------------------------------------------------------


class TestLowConfidence:
    def test_low_confidence_from_index(self, tmp_path):
        wiki = _write_wiki(
            tmp_path, topics=[{"id": "t", "title": "T", "confidence": 0.95}]
        )
        conn = sqlite3.connect(wiki / "index.sqlite")
        conn.execute(
            "CREATE TABLE study_items (id TEXT, topic_id TEXT, title TEXT, "
            "confidence REAL, source TEXT)"
        )
        conn.executemany(
            "INSERT INTO study_items VALUES (?,?,?,?,?)",
            [
                ("1", "t", "weak claim", 0.2, "http://a"),
                ("2", "t", "strong claim", 0.9, "http://b"),
            ],
        )
        conn.commit()
        conn.close()

        items = _store(wiki).low_confidence_items()
        assert len(items) == 1
        assert items[0].title == "weak claim"
        assert items[0].kind == "item"
        assert items[0].source == "http://a"

    def test_low_confidence_topic_fallback_without_index(self, tmp_path):
        wiki = _write_wiki(
            tmp_path,
            topics=[
                {"id": "a", "title": "A", "confidence": 0.3},
                {"id": "b", "title": "B", "confidence": 0.8},
            ],
        )
        items = _store(wiki).low_confidence_items()
        assert [i.topic_id for i in items] == ["a"]
        assert items[0].kind == "topic"


# ---------------------------------------------------------------------------
# show / refresh / archive
# ---------------------------------------------------------------------------


class TestShowRefreshArchive:
    def test_show_returns_topic_and_page(self, tmp_path):
        wiki = _write_wiki(tmp_path, topics=[{"id": "t", "title": "T"}])
        page_dir = wiki / "topics" / "t"
        page_dir.mkdir(parents=True)
        (page_dir / "overview.md").write_text("# T\nbody", encoding="utf-8")

        out = json.loads(
            handle_study_status({"action": "show", "topic_id": "t"}, store=_store(wiki))
        )
        assert out["topic"]["id"] == "t"
        assert "body" in out["page"]

    def test_show_unknown_topic_errors(self, tmp_path):
        wiki = _write_wiki(tmp_path, topics=[{"id": "t", "title": "T"}])
        out = json.loads(
            handle_study_status(
                {"action": "show", "topic_id": "zzz"}, store=_store(wiki)
            )
        )
        assert out["ok"] is False

    def test_archive_persists_status(self, tmp_path):
        wiki = _write_wiki(
            tmp_path, topics=[{"id": "t", "title": "T", "status": "active"}]
        )
        out = json.loads(
            handle_study_status(
                {"action": "archive", "topic_id": "t"}, store=_store(wiki)
            )
        )
        assert out["ok"] is True
        assert out["topic"]["is_archived"] is True
        # 파일에 실제로 반영됐는지 다시 읽어 확인.
        saved = yaml.safe_load((wiki / "topics.yaml").read_text(encoding="utf-8"))
        assert saved["topics"][0]["status"] == "archived"

    def test_refresh_sets_request_flag(self, tmp_path):
        wiki = _write_wiki(
            tmp_path, topics=[{"id": "t", "title": "T", "status": "active"}]
        )
        out = json.loads(
            handle_study_status(
                {"action": "refresh", "topic_id": "t"}, store=_store(wiki)
            )
        )
        assert out["ok"] is True
        assert out["topic"]["refresh_requested_at"] is not None
        saved = yaml.safe_load((wiki / "topics.yaml").read_text(encoding="utf-8"))
        assert saved["topics"][0]["refresh_requested_at"] is not None

    def test_refresh_revives_archived_topic(self, tmp_path):
        wiki = _write_wiki(
            tmp_path, topics=[{"id": "t", "title": "T", "status": "archived"}]
        )
        view = _store(wiki).refresh_topic("t")
        assert view.is_archived is False
        assert view.status == "active"

    def test_refresh_missing_topic_id_errors(self, tmp_path):
        wiki = _write_wiki(tmp_path, topics=[{"id": "t", "title": "T"}])
        out = json.loads(handle_study_status({"action": "refresh"}, store=_store(wiki)))
        assert out["ok"] is False
        assert "topic_id" in out["error"]


# ---------------------------------------------------------------------------
# wiki_dir 해석 + action 검증
# ---------------------------------------------------------------------------


class TestResolveAndValidation:
    def test_resolve_wiki_dir_from_config(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.safe_dump({"study": {"wiki_dir": str(tmp_path / "custom_wiki")}}),
            encoding="utf-8",
        )
        assert resolve_wiki_dir(cfg) == tmp_path / "custom_wiki"

    def test_resolve_wiki_dir_default_when_missing(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.safe_dump({"agent": {}}), encoding="utf-8")
        resolved = resolve_wiki_dir(cfg)
        assert resolved.name == "agent_wiki"

    def test_unknown_action_errors(self, tmp_path):
        wiki = _write_wiki(tmp_path, topics=[])
        out = json.loads(
            handle_study_status({"action": "frobnicate"}, store=_store(wiki))
        )
        assert out["ok"] is False


# ---------------------------------------------------------------------------
# operator gate (dispatch 경계)
# ---------------------------------------------------------------------------


class _FakeOrchestrator:
    def __init__(self, config_path):
        self._config_path = config_path


class TestOperatorGate:
    @pytest.mark.asyncio
    async def test_dispatch_blocks_non_operator(self, tmp_path):
        call = ToolCall(id="1", name="study_status", arguments={"action": "status"})
        result = await dispatch_tool_call(
            _FakeOrchestrator(tmp_path / "config.yaml"),
            call,
            operator_tools=False,
        )
        assert "operator context" in result

    @pytest.mark.asyncio
    async def test_dispatch_allows_operator(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.safe_dump({"study": {"wiki_dir": str(tmp_path / "agent_wiki")}}),
            encoding="utf-8",
        )
        call = ToolCall(id="1", name="study_status", arguments={"action": "status"})
        result = await dispatch_tool_call(
            _FakeOrchestrator(cfg),
            call,
            operator_tools=True,
        )
        out = json.loads(result)
        assert out["ok"] is True
        # wiki 가 아직 없으므로 not configured.
        assert out["configured"] is False
