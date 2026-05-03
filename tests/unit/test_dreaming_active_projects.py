"""BIZ-74 Active-Projects: dreaming 파이프라인과의 통합 동작 검증.

DoD 핵심 시나리오:

1. **SimpleClaw + Multica 픽스처 대화** — LLM 응답에 두 프로젝트가 들어오면, sidecar에
   양쪽 다 누적되고 USER.md의 ``managed:dreaming:active-projects`` 섹션에 두 카드가
   모두 등재된다 (이슈 본문 명시 DoD).
2. **In-place 갱신** — 다음 사이클에서 같은 프로젝트가 다시 관측되면 새 카드가 append
   되는 게 아니라 기존 본문이 통째로 교체된다(``replace_section_body``).
3. **first_seen 보존** — 두 사이클을 거쳐도 첫 등록 시각이 유지된다.
4. **윈도우 외 항목 자동 비노출** — sidecar에는 보관하되 USER.md 섹션에서는 사라진다.
5. **마커 외부 보호** — BIZ-72 가드와 동일하게, ``## Preferences`` 같은 사용자
   소유 영역은 byte-for-byte 보존된다.
6. **opt-in** — ``active_projects_file=None`` 이면 USER.md에 active-projects 마커가
   없어도 dreaming 이 정상 동작한다 (기존 워크스페이스와의 호환성).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.active_projects import (
    ActiveProject,
    ActiveProjectStore,
    normalize_name,
)
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.memory.protected_section import get_section_body


# ----------------------------------------------------------------------
# 픽스처 — managed 마커가 모두 포함된 USER.md / MEMORY.md
# ----------------------------------------------------------------------


_USER_TEMPLATE = (
    "# User Profile\n"
    "\n"
    "## Preferences (USER-OWNED — DREAMING MUST NOT TOUCH)\n"
    "- Primary language: Korean\n"
    "- Calendar: ingki3@gmail.com\n"
    "\n"
    "<!-- managed:dreaming:insights -->\n"
    "<!-- /managed:dreaming:insights -->\n"
    "\n"
    "<!-- managed:dreaming:active-projects -->\n"
    "_최근 윈도우에 식별된 활성 프로젝트가 없습니다._\n"
    "<!-- /managed:dreaming:active-projects -->\n"
)

_MEMORY_TEMPLATE = (
    "# Core Memory\n"
    "\n"
    "<!-- managed:dreaming:journal -->\n"
    "<!-- /managed:dreaming:journal -->\n"
)


def _llm_returning_active_projects(
    *,
    memory: str = "",
    user_insights: str = "",
    active_projects: list[dict] | None = None,
):
    """주어진 active_projects 리스트를 반환하는 mock LLM router.

    LLM 응답 형식이 dreaming.py 에서 기대하는 JSON 스키마와 정확히 일치하도록 한다.
    """
    payload = json.dumps(
        {
            "memory": memory,
            "user_insights": user_insights,
            "soul_updates": "",
            "agent_updates": "",
            "active_projects": active_projects or [],
        }
    )
    response = MagicMock()
    response.text = payload
    router = MagicMock()
    router.send = AsyncMock(return_value=response)
    return router


@pytest.fixture
def workspace(tmp_path):
    """active-projects 마커를 포함한 USER.md + sidecar 경로가 모두 설정된 워크스페이스."""
    memory = tmp_path / "MEMORY.md"
    user = tmp_path / "USER.md"
    sidecar = tmp_path / "active_projects.jsonl"
    memory.write_text(_MEMORY_TEMPLATE, encoding="utf-8")
    user.write_text(_USER_TEMPLATE, encoding="utf-8")

    store = ConversationStore(tmp_path / "conv.db")
    pipeline = DreamingPipeline(
        store,
        memory,
        user_file=user,
        active_projects_file=sidecar,
        active_projects_window_days=7,
    )
    return {
        "tmp_path": tmp_path,
        "store": store,
        "pipeline": pipeline,
        "memory": memory,
        "user": user,
        "sidecar": sidecar,
    }


# ----------------------------------------------------------------------
# Scenario 1 — DoD 핵심: SimpleClaw + Multica 둘 다 등재
# ----------------------------------------------------------------------


class TestSimpleClawAndMulticaFixture:
    """이슈 본문 명시: '대화 분석 결과 SimpleClaw·Multica 두 프로젝트가 모두 추출되어 섹션에 등재되는지'."""

    @pytest.mark.asyncio
    async def test_both_projects_extracted_and_rendered(self, workspace):
        ws = workspace
        # 시뮬레이션: 5-01~5-03에 SimpleClaw 빌드와 Multica 운영을 동시에 한 형님의 대화.
        ws["store"].add_message(
            ConversationMessage(
                role=MessageRole.USER,
                content="SimpleClaw 메모리 파이프라인에서 dreaming이 AGENT.md를 망가뜨리는 사고가 있어서 BIZ-66을 평가했어.",
            )
        )
        ws["store"].add_message(
            ConversationMessage(
                role=MessageRole.USER,
                content="동시에 Multica 플랫폼 릴리스 QA를 돌리는데 admin UI surface stacking 회귀를 잡아야 해.",
            )
        )

        ws["pipeline"]._router = _llm_returning_active_projects(
            memory="## 2026-05-03\n- BIZ-66 평가 완료\n- Multica admin 회귀 수정",
            active_projects=[
                {
                    "name": "SimpleClaw",
                    "role": "솔로 빌더 — 메모리 파이프라인 개선 트랙",
                    "recent_summary": "BIZ-66 평가 후 sub-issue 10건 분할, A/B 머지 리뷰.",
                },
                {
                    "name": "Multica",
                    "role": "플랫폼 빌드/QA 운영자",
                    "recent_summary": "Admin UI surface stacking 회귀 수정 PR 진행.",
                },
            ],
        )
        result = await ws["pipeline"].run()
        assert result is not None

        # USER.md 섹션 본문에 두 프로젝트가 등재되어야 한다.
        text = ws["user"].read_text(encoding="utf-8")
        body = get_section_body(text, "active-projects")
        assert "## SimpleClaw" in body, body
        assert "## Multica" in body, body
        assert "메모리 파이프라인 개선" in body
        assert "플랫폼 빌드/QA 운영자" in body
        assert "Admin UI surface stacking" in body

        # sidecar에도 두 항목이 정확히 1줄씩 누적되어 있어야 한다.
        sidecar_lines = [
            line for line in ws["sidecar"].read_text(encoding="utf-8").splitlines() if line
        ]
        assert len(sidecar_lines) == 2
        keys = {
            normalize_name(json.loads(line)["name"]) for line in sidecar_lines
        }
        assert keys == {"simpleclaw", "multica"}

    @pytest.mark.asyncio
    async def test_outside_marker_user_content_is_preserved(self, workspace):
        """active-projects 섹션 갱신이 ``## Preferences`` 같은 사용자 영역을 침범하지 않는다."""
        ws = workspace
        before_outside = ws["user"].read_text(encoding="utf-8").split(
            "<!-- managed:dreaming:insights -->"
        )[0]

        ws["pipeline"]._router = _llm_returning_active_projects(
            active_projects=[
                {"name": "SimpleClaw", "role": "r", "recent_summary": "s"}
            ],
        )
        ws["store"].add_message(
            ConversationMessage(role=MessageRole.USER, content="hi")
        )
        await ws["pipeline"].run()

        after_outside = ws["user"].read_text(encoding="utf-8").split(
            "<!-- managed:dreaming:insights -->"
        )[0]
        # outside 영역 byte-for-byte 보존.
        assert after_outside == before_outside
        assert "Calendar: ingki3@gmail.com" in after_outside


# ----------------------------------------------------------------------
# Scenario 2 — In-place 갱신 vs. append
# ----------------------------------------------------------------------


class TestInPlaceUpdate:
    """동일 프로젝트가 두 사이클에 걸쳐 관측되면, 카드가 한 번만 보여야 한다."""

    @pytest.mark.asyncio
    async def test_repeat_observation_does_not_duplicate_card(self, workspace):
        ws = workspace
        # Round 1
        ws["pipeline"]._router = _llm_returning_active_projects(
            active_projects=[
                {
                    "name": "SimpleClaw",
                    "role": "초기 역할",
                    "recent_summary": "초기 요약",
                }
            ],
        )
        ws["store"].add_message(
            ConversationMessage(role=MessageRole.USER, content="round 1")
        )
        await ws["pipeline"].run()

        # Round 2 — 같은 프로젝트, 새 요약.
        ws["pipeline"]._router = _llm_returning_active_projects(
            active_projects=[
                {
                    "name": "SimpleClaw",
                    "role": "갱신 역할",
                    "recent_summary": "갱신 요약",
                }
            ],
        )
        ws["store"].add_message(
            ConversationMessage(role=MessageRole.USER, content="round 2")
        )
        await ws["pipeline"].run()

        body = get_section_body(
            ws["user"].read_text(encoding="utf-8"), "active-projects"
        )
        # 카드는 단 한 개여야 한다 (in-place 교체).
        assert body.count("## SimpleClaw") == 1
        # 본문은 가장 최신 사이클로 갱신.
        assert "갱신 역할" in body
        assert "갱신 요약" in body
        # 이전 사이클의 본문 흔적은 남아있지 않다.
        assert "초기 역할" not in body
        assert "초기 요약" not in body

    @pytest.mark.asyncio
    async def test_first_seen_is_preserved_across_cycles(self, workspace):
        ws = workspace
        # Round 1 — 첫 등록.
        ws["pipeline"]._router = _llm_returning_active_projects(
            active_projects=[
                {"name": "SimpleClaw", "role": "r1", "recent_summary": "s1"}
            ],
        )
        ws["store"].add_message(
            ConversationMessage(role=MessageRole.USER, content="round 1")
        )
        await ws["pipeline"].run()

        first_seen_after_round1 = ActiveProjectStore(ws["sidecar"]).load()[
            "simpleclaw"
        ].first_seen

        # Round 2 — 같은 프로젝트가 다시 관측됨 (다른 시각).
        ws["pipeline"]._router = _llm_returning_active_projects(
            active_projects=[
                {"name": "SimpleClaw", "role": "r2", "recent_summary": "s2"}
            ],
        )
        ws["store"].add_message(
            ConversationMessage(role=MessageRole.USER, content="round 2")
        )
        await ws["pipeline"].run()

        sidecar = ActiveProjectStore(ws["sidecar"]).load()
        sp = sidecar["simpleclaw"]
        # first_seen 은 round 1 시각에서 멈춰 있어야 한다.
        assert sp.first_seen == first_seen_after_round1
        # last_seen 은 round 2 시각으로 갱신되어야 한다.
        assert sp.last_seen >= first_seen_after_round1


# ----------------------------------------------------------------------
# Scenario 3 — 윈도우 외 항목은 USER.md에서 사라지지만 sidecar에는 남는다
# ----------------------------------------------------------------------


class TestWindowFiltering:
    @pytest.mark.asyncio
    async def test_stale_project_disappears_from_section_but_stays_in_sidecar(
        self, workspace
    ):
        ws = workspace
        # 사전 조건: sidecar 에 30일 전 last_seen 의 stale 항목을 직접 심는다.
        old = datetime.now() - timedelta(days=30)
        ActiveProjectStore(ws["sidecar"]).save_all(
            {
                # sidecar 의 키는 normalize_name(name) — "StaleProject" → "staleproject".
                "staleproject": ActiveProject(
                    name="StaleProject",
                    role="옛 역할",
                    recent_summary="옛 요약",
                    first_seen=old,
                    last_seen=old,
                )
            }
        )

        # 사이클 — 신규 관측 1건.
        ws["pipeline"]._router = _llm_returning_active_projects(
            active_projects=[
                {"name": "SimpleClaw", "role": "r", "recent_summary": "s"}
            ],
        )
        ws["store"].add_message(
            ConversationMessage(role=MessageRole.USER, content="hi")
        )
        await ws["pipeline"].run()

        body = get_section_body(
            ws["user"].read_text(encoding="utf-8"), "active-projects"
        )
        # USER.md 섹션에서는 윈도우 외 항목이 사라져야 한다.
        assert "StaleProject" not in body
        assert "## SimpleClaw" in body

        # 그러나 sidecar 에는 여전히 두 항목이 모두 살아 있다 (decay 는 BIZ-78 영역).
        sidecar = ActiveProjectStore(ws["sidecar"]).load()
        assert "staleproject" in sidecar
        assert "simpleclaw" in sidecar


# ----------------------------------------------------------------------
# Scenario 4 — opt-in: active_projects_file=None 인 경우
# ----------------------------------------------------------------------


class TestOptInBehavior:
    @pytest.mark.asyncio
    async def test_disabled_when_sidecar_path_not_provided(self, tmp_path):
        """active_projects_file=None 이면 USER.md에 active-projects 마커가 없어도 정상 동작."""
        memory = tmp_path / "MEMORY.md"
        user = tmp_path / "USER.md"
        memory.write_text(
            "# Memory\n\n<!-- managed:dreaming:journal -->\n<!-- /managed:dreaming:journal -->\n"
        )
        user.write_text(
            "# User\n\n<!-- managed:dreaming:insights -->\n<!-- /managed:dreaming:insights -->\n"
        )
        store = ConversationStore(tmp_path / "conv.db")
        pipeline = DreamingPipeline(
            store,
            memory,
            user_file=user,
            # active_projects_file 미설정 → 본 기능 비활성.
        )

        pipeline._router = _llm_returning_active_projects(
            memory="## 2026-05-03\n- noop",
            active_projects=[
                {"name": "Foo", "role": "r", "recent_summary": "s"}
            ],
        )
        store.add_message(
            ConversationMessage(role=MessageRole.USER, content="hi")
        )
        # active-projects 마커가 없는 USER.md 라도 abort 되지 않아야 한다.
        result = await pipeline.run()
        assert result is not None
        # USER.md 에 active-projects 본문이 들어가지도 않아야 한다 (마커 자체가 없음).
        assert "## Foo" not in user.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_fail_closed_when_marker_missing_but_feature_enabled(self, tmp_path):
        """active_projects_file 이 설정됐는데 USER.md 에 마커가 없으면 fail-closed (BIZ-72)."""
        memory = tmp_path / "MEMORY.md"
        user = tmp_path / "USER.md"
        memory.write_text(
            "# Memory\n\n<!-- managed:dreaming:journal -->\n<!-- /managed:dreaming:journal -->\n"
        )
        # USER.md 에 insights 만 있고 active-projects 마커는 누락.
        user.write_text(
            "# User\n\n<!-- managed:dreaming:insights -->\n<!-- /managed:dreaming:insights -->\n"
        )
        before_user = user.read_text(encoding="utf-8")

        store = ConversationStore(tmp_path / "conv.db")
        pipeline = DreamingPipeline(
            store,
            memory,
            user_file=user,
            active_projects_file=tmp_path / "active_projects.jsonl",
        )
        pipeline._router = _llm_returning_active_projects(
            memory="## 2026-05-03\n- x",
            active_projects=[{"name": "Foo", "role": "r", "recent_summary": "s"}],
        )
        store.add_message(
            ConversationMessage(role=MessageRole.USER, content="hi")
        )

        # preflight 가 active-projects 마커 누락을 잡아 None 을 반환해야 한다.
        result = await pipeline.run()
        assert result is None
        # USER.md 도 변경되지 않아야 한다 (fail-closed).
        assert user.read_text(encoding="utf-8") == before_user
