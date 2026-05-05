"""BIZ-132 Phase 2 — preflight 자가 복원 통합 테스트.

검증 시나리오:
1. 라이브 파일이 부재하고 ``safety_backup_manager`` 에 동일 basename 백업이 있으면,
   preflight 가 1회 한정으로 복원 후 사이클을 정상 종료한다.
2. ``safety_backup_manager`` 가 없거나 매치가 없을 때는 레거시
   ``memory-backup/{stem}.{ts}.bak`` 에서 폴백 복원한다.
3. 백업이 어디에도 없으면 fail-closed (ProtectedSectionMissing).
4. 마커 손상(``ProtectedSectionMalformed``) 은 자가 복원 대상이 아니다 — 그대로 abort.
5. 복원이 일어나면 ``dreaming_runs.jsonl`` 의 details 에 ``recovered_from`` 이 기록된다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.dreaming_runs import DreamingRunStore
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.memory.safety_backup import SafetyBackupManager


# ────────────────────────────────────────────────────────────────────
# 헬퍼 — managed 마커가 들어 있는 starter 파일
# ────────────────────────────────────────────────────────────────────


_USER_OUTSIDE = "# User Profile\n\n## Identity (USER-OWNED)\n- Name: 형님\n\n"
_MEMORY_OUTSIDE = "# Core Memory\n\n## Static facts (USER-OWNED)\n- a fact\n\n"
_SOUL_OUTSIDE = "# Soul\n\n## Personality (USER-OWNED)\n- 따뜻함\n\n"
_AGENT_OUTSIDE = "# SimpleClaw Agent\n\n## Identity (USER-OWNED)\n- 한 줄\n\n"


def _write_marker_file(path: Path, outside: str, section: str) -> None:
    path.write_text(
        outside
        + f"<!-- managed:dreaming:{section} -->\n"
        + f"<!-- /managed:dreaming:{section} -->\n",
        encoding="utf-8",
    )


def _llm_router(
    memory: str = "## d\n- m", user: str = "", soul: str = "", agent: str = ""
):
    payload = json.dumps(
        {
            "memory": memory,
            "user_insights": user,
            "soul_updates": soul,
            "agent_updates": agent,
        }
    )
    response = MagicMock()
    response.text = payload
    router = MagicMock()
    router.send = AsyncMock(return_value=response)
    return router


@pytest.fixture
def setup(tmp_path):
    """4종 파일 + safety backup manager + runs store 가 모두 wired 된 워크스페이스."""
    memory = tmp_path / "MEMORY.md"
    user = tmp_path / "USER.md"
    soul = tmp_path / "SOUL.md"
    agent = tmp_path / "AGENT.md"
    _write_marker_file(memory, _MEMORY_OUTSIDE, "journal")
    _write_marker_file(user, _USER_OUTSIDE, "insights")
    _write_marker_file(soul, _SOUL_OUTSIDE, "dreaming-updates")
    _write_marker_file(agent, _AGENT_OUTSIDE, "dreaming-updates")

    backup_root = tmp_path / "_safety_backup"
    runs_file = tmp_path / "dreaming_runs.jsonl"

    store = ConversationStore(tmp_path / "conv.db")

    # 사이클 시계는 호출마다 다른 timestamp 가 나오도록 카운터 기반.
    counter = {"n": 0}

    def fake_clock():
        counter["n"] += 1
        return datetime(2026, 5, 5, 12, 0, counter["n"])

    safety = SafetyBackupManager(
        backup_root=backup_root,
        files=[memory, user, soul, agent],
        max_cycles=7,
        clock=fake_clock,
    )

    pipeline = DreamingPipeline(
        store,
        memory,
        user_file=user,
        soul_file=soul,
        agent_file=agent,
        runs_file=runs_file,
        safety_backup_manager=safety,
    )
    pipeline._router = _llm_router(memory="## d\n- m")

    store.add_message(ConversationMessage(role=MessageRole.USER, content="trigger"))

    return {
        "tmp_path": tmp_path,
        "memory": memory,
        "user": user,
        "soul": soul,
        "agent": agent,
        "backup_root": backup_root,
        "runs_file": runs_file,
        "pipeline": pipeline,
        "store": store,
        "safety": safety,
    }


def _last_run_record(runs_file: Path) -> dict:
    """dreaming_runs.jsonl 의 마지막 행을 dict 로 반환."""
    runs = DreamingRunStore(runs_file)
    rec = runs.last_run()
    assert rec is not None
    return rec.to_dict()


# ────────────────────────────────────────────────────────────────────
# 1. safety backup 으로부터 자가 복원
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_file_restored_from_safety_backup(setup):
    """첫 사이클이 백업을 만든 뒤, 라이브 파일을 지우고 두 번째 사이클이 복원."""
    s = setup
    # 1차 사이클 — backup 디렉터리가 만들어지고 4종 파일이 거기 보관된다.
    result1 = await s["pipeline"].run()
    assert result1 is not None
    cycles = sorted(p.name for p in s["backup_root"].iterdir() if p.is_dir())
    assert len(cycles) == 1

    # 운영자/외부 작업이 라이브 MEMORY.md 를 통째로 삭제한 사고 시뮬레이션.
    expected_text = s["memory"].read_text(encoding="utf-8")
    s["memory"].unlink()

    # 새 메시지가 있어야 사이클이 의미 있게 돈다.
    s["store"].add_message(
        ConversationMessage(role=MessageRole.USER, content="post-incident")
    )

    # 2차 사이클 — preflight 가 부재를 발견하고 safety 백업에서 복원해야 한다.
    result2 = await s["pipeline"].run()
    assert result2 is not None
    assert s["memory"].is_file()
    # 복원본은 1차 사이클이 LLM append 후 만들어둔 백업본이므로 본문에 "## d" 가 살아있고
    # 외부 영역도 보존됨.
    restored = s["memory"].read_text(encoding="utf-8")
    assert restored == expected_text or "<!-- managed:dreaming:journal -->" in restored


@pytest.mark.asyncio
async def test_recovered_from_recorded_in_runs_metadata(setup):
    s = setup
    await s["pipeline"].run()  # backup 누적
    s["memory"].unlink()
    s["store"].add_message(
        ConversationMessage(role=MessageRole.USER, content="post-incident")
    )

    await s["pipeline"].run()
    last = _last_run_record(s["runs_file"])
    assert "recovered_from" in (last.get("details") or {})
    assert "MEMORY.md" in last["details"]["recovered_from"]
    # 복원 소스 경로 문자열은 backup 디렉터리 안에 위치
    assert "_safety_backup" in last["details"]["recovered_from"]["MEMORY.md"]


# ────────────────────────────────────────────────────────────────────
# 2. 레거시 memory-backup 폴백
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_falls_back_to_legacy_memory_backup(tmp_path):
    """safety backup 이 없거나 매치가 없으면 ``memory-backup/`` 의 .bak 을 사용."""
    memory = tmp_path / "MEMORY.md"
    user = tmp_path / "USER.md"
    _write_marker_file(memory, _MEMORY_OUTSIDE, "journal")
    _write_marker_file(user, _USER_OUTSIDE, "insights")

    # 레거시 memory-backup 디렉터리에 ``MEMORY.20260101_000000.bak`` 을 미리 세팅.
    legacy_dir = tmp_path / "memory-backup"
    legacy_dir.mkdir()
    legacy_bak = legacy_dir / "MEMORY.20260101_000000.bak"
    legacy_bak.write_text(memory.read_text(encoding="utf-8"), encoding="utf-8")

    store = ConversationStore(tmp_path / "conv.db")
    pipeline = DreamingPipeline(
        store,
        memory,
        user_file=user,
        runs_file=tmp_path / "runs.jsonl",
        # safety_backup_manager 미주입 → 레거시 경로만 활성.
    )
    pipeline._router = _llm_router(memory="## d\n- ok")

    # 라이브 MEMORY.md 삭제 — preflight 가 legacy .bak 으로 복원해야 한다.
    memory.unlink()
    store.add_message(ConversationMessage(role=MessageRole.USER, content="hi"))

    result = await pipeline.run()
    assert result is not None
    assert memory.is_file()
    last = _last_run_record(tmp_path / "runs.jsonl")
    rec_from = (last.get("details") or {}).get("recovered_from", {})
    assert "MEMORY.md" in rec_from
    assert ".bak" in rec_from["MEMORY.md"]


# ────────────────────────────────────────────────────────────────────
# 3. 백업이 전혀 없으면 fail-closed
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_backup_available_fails_closed(tmp_path):
    """safety/memory-backup 모두 비어있고 라이브 파일이 부재 → 사이클 abort."""
    memory = tmp_path / "MEMORY.md"
    user = tmp_path / "USER.md"
    # USER.md 만 marker 갖춘 상태로 만들고 MEMORY.md 는 처음부터 부재.
    _write_marker_file(user, _USER_OUTSIDE, "insights")

    store = ConversationStore(tmp_path / "conv.db")
    pipeline = DreamingPipeline(
        store,
        memory,
        user_file=user,
        runs_file=tmp_path / "runs.jsonl",
    )
    pipeline._router = _llm_router()
    store.add_message(ConversationMessage(role=MessageRole.USER, content="x"))

    result = await pipeline.run()
    assert result is None
    # MEMORY.md 는 fail-closed 이므로 자동 생성되지 않는다.
    assert not memory.exists()
    last = _last_run_record(tmp_path / "runs.jsonl")
    assert last["skip_reason"] == "preflight_failed"
    # 복원 발생하지 않았으므로 details 에 recovered_from 가 없다.
    assert "recovered_from" not in (last.get("details") or {})


# ────────────────────────────────────────────────────────────────────
# 4. 마커 손상은 자가 복원 대상이 아님
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_malformed_marker_does_not_trigger_restore(setup):
    """파일이 *존재* 하지만 마커가 손상된 경우 — 자가 복원 시도하지 않고 즉시 abort."""
    s = setup
    # 1차 사이클로 backup 누적.
    await s["pipeline"].run()
    cycles_before = sorted(s["backup_root"].iterdir())

    # MEMORY.md 의 closing marker 만 남겨 ProtectedSectionMalformed 를 유발.
    s["memory"].write_text(
        _MEMORY_OUTSIDE + "<!-- /managed:dreaming:journal -->\n",
        encoding="utf-8",
    )
    snapshot = s["memory"].read_text(encoding="utf-8")

    s["store"].add_message(
        ConversationMessage(role=MessageRole.USER, content="malformed")
    )

    result = await s["pipeline"].run()
    # malformed → preflight fails → 사이클 abort. 백업으로 *복원하지 않는다*.
    assert result is None
    assert s["memory"].read_text(encoding="utf-8") == snapshot

    # runs 행은 preflight_failed, recovered_from 없음.
    last = _last_run_record(s["runs_file"])
    assert last["skip_reason"] == "preflight_failed"
    assert "recovered_from" not in (last.get("details") or {})

    # 사이클 직전 safety_backup snapshot 은 그대로 한 번 더 만들어졌어야 한다(가시성).
    cycles_after = sorted(s["backup_root"].iterdir())
    assert len(cycles_after) >= len(cycles_before)


# ────────────────────────────────────────────────────────────────────
# 5. 자가 복원은 한 회차에 1회만
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_restore_resets_per_cycle(setup):
    """한 회차에서 복원이 일어났더라도, 다음 회차에서는 카운터가 리셋되어 다시 가능."""
    s = setup
    # 1차 — 백업 적재.
    await s["pipeline"].run()

    # 2차 — MEMORY 삭제, 복원 발생.
    s["memory"].unlink()
    s["store"].add_message(ConversationMessage(role=MessageRole.USER, content="r1"))
    await s["pipeline"].run()
    assert s["memory"].is_file()

    # 3차 — 다시 MEMORY 삭제. 같은 파이프라인 인스턴스에서 카운터가 리셋되어야 한다.
    s["memory"].unlink()
    s["store"].add_message(ConversationMessage(role=MessageRole.USER, content="r2"))
    result = await s["pipeline"].run()
    assert result is not None
    assert s["memory"].is_file()


# ────────────────────────────────────────────────────────────────────
# 6. 사이클 직전 snapshot — Phase 1 행위 확인
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_runs_before_preflight_each_cycle(setup):
    """매 사이클의 preflight 직전에 snapshot 이 실행되어 backup 디렉터리가 누적."""
    s = setup
    await s["pipeline"].run()
    s["store"].add_message(ConversationMessage(role=MessageRole.USER, content="2nd"))
    await s["pipeline"].run()
    cycles = sorted(p.name for p in s["backup_root"].iterdir() if p.is_dir())
    assert len(cycles) == 2
