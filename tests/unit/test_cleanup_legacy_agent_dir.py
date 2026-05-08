"""BIZ-138 — ``scripts/cleanup_legacy_agent_dir.py`` 단위 테스트.

격리 스크립트가:
1. dry-run 시 파일을 실제로 이동하지 않는다.
2. ``--apply`` 시 라이브 파일·디렉터리·마이그레이션 사이드카를 격리 디렉터리로 이동한다.
3. 프로젝트 자산(``skills/``, ``recipes/``)은 건드리지 않는다.
4. 격리 대상이 없을 때 0 카운터로 깨끗하게 종료한다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "cleanup_legacy_agent_dir.py"


@pytest.fixture(scope="module")
def cleanup_module():
    """``scripts/cleanup_legacy_agent_dir.py`` 를 모듈로 직접 import.

    ``scripts/`` 는 PYTHONPATH 에 없으므로 importlib 으로 로드한다.
    """
    spec = importlib.util.spec_from_file_location(
        "cleanup_legacy_agent_dir", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _populate_legacy_agent_dir(source: Path) -> None:
    """BIZ-138 시나리오의 잔여 파일 일습을 source 에 만들어둔다."""
    source.mkdir(parents=True, exist_ok=True)
    (source / "conversations.db").write_bytes(b"sqlite-stub")
    (source / "conversations.db-wal").write_bytes(b"wal-stub")
    (source / "daemon.db").write_bytes(b"daemon-stub")
    (source / "AGENT.md").write_text("agent body", encoding="utf-8")
    (source / "HEARTBEAT.md").write_text("heart", encoding="utf-8")
    (source / "_safety_backup").mkdir()
    (source / "_safety_backup" / "20260506_030000").mkdir()
    (source / "_safety_backup" / "20260506_030000" / "MEMORY.md").write_text(
        "snap", encoding="utf-8"
    )
    (source / "workspace").mkdir()
    (source / "workspace" / "tmp.txt").write_text("scratch", encoding="utf-8")
    # 마이그레이션 사이드카 — 패턴 매칭 검증용.
    (source / "conversations.db.backup-0001-20260505T182242").write_bytes(b"bk")
    (source / "daemon.db.backup-0002-20260505T182242-wal").write_bytes(b"bk")
    # 프로젝트 자산 — 격리되면 안 된다.
    (source / "skills").mkdir()
    (source / "skills" / "SKILL.md").write_text("skill", encoding="utf-8")
    (source / "recipes").mkdir()
    (source / "recipes" / "recipe.yml").write_text("recipe", encoding="utf-8")


def test_dry_run_moves_nothing(tmp_path: Path, cleanup_module):
    """dry-run 모드는 source 의 어떤 파일도 옮기지 않는다."""
    source = tmp_path / "agent"
    quarantine = tmp_path / "quarantine"
    _populate_legacy_agent_dir(source)

    counters = cleanup_module.quarantine(source, quarantine, apply=False)

    # 카운터는 옮겨질 예정 항목 수를 반환하지만 파일시스템은 변하지 않아야 한다.
    assert counters["moved"] > 0
    assert counters["skipped"] == 0
    assert (source / "conversations.db").exists()
    assert (source / "_safety_backup" / "20260506_030000" / "MEMORY.md").exists()
    assert not quarantine.exists()


def test_apply_moves_live_files_and_dirs(tmp_path: Path, cleanup_module):
    """--apply 시 라이브 파일/디렉터리/사이드카가 모두 격리 디렉터리로 이동."""
    source = tmp_path / "agent"
    quarantine = tmp_path / "quarantine"
    _populate_legacy_agent_dir(source)

    counters = cleanup_module.quarantine(source, quarantine, apply=True)

    assert counters["moved"] > 0
    assert counters["skipped"] == 0

    # 라이브 파일은 source 에서 사라지고 quarantine 에 나타난다.
    assert not (source / "conversations.db").exists()
    assert (quarantine / "conversations.db").exists()
    assert not (source / "daemon.db").exists()
    assert (quarantine / "daemon.db").exists()
    assert not (source / "AGENT.md").exists()
    assert (quarantine / "AGENT.md").read_text(encoding="utf-8") == "agent body"

    # 디렉터리는 통째로 옮겨진다 — 내부 구조 보존.
    assert not (source / "_safety_backup").exists()
    assert (quarantine / "_safety_backup" / "20260506_030000" / "MEMORY.md").exists()
    assert not (source / "workspace").exists()
    assert (quarantine / "workspace" / "tmp.txt").exists()

    # 마이그레이션 사이드카 패턴도 잡힌다.
    assert not (source / "conversations.db.backup-0001-20260505T182242").exists()
    assert (quarantine / "conversations.db.backup-0001-20260505T182242").exists()
    assert (quarantine / "daemon.db.backup-0002-20260505T182242-wal").exists()


def test_apply_preserves_project_assets(tmp_path: Path, cleanup_module):
    """``skills/`` 와 ``recipes/`` 같은 프로젝트 자산은 절대 격리되지 않는다."""
    source = tmp_path / "agent"
    quarantine = tmp_path / "quarantine"
    _populate_legacy_agent_dir(source)

    cleanup_module.quarantine(source, quarantine, apply=True)

    # 프로젝트 자산은 source 에 그대로 남는다.
    assert (source / "skills" / "SKILL.md").exists()
    assert (source / "recipes" / "recipe.yml").exists()
    # quarantine 에 해당 자산이 들어가 있으면 안 된다.
    assert not (quarantine / "skills").exists()
    assert not (quarantine / "recipes").exists()


def test_no_targets_returns_zero(tmp_path: Path, cleanup_module):
    """source 가 비었거나 프로젝트 자산만 있을 때 카운터가 0 으로 종료."""
    source = tmp_path / "agent"
    source.mkdir()
    (source / "skills").mkdir()
    (source / "skills" / "SKILL.md").write_text("only assets", encoding="utf-8")

    counters = cleanup_module.quarantine(
        source, tmp_path / "quarantine", apply=True,
    )
    assert counters == {"moved": 0, "skipped": 0}


def test_missing_source_returns_zero(tmp_path: Path, cleanup_module):
    """source 디렉터리가 아예 없으면 깨끗이 0 으로 종료(에러 없음)."""
    counters = cleanup_module.quarantine(
        tmp_path / "nonexistent", tmp_path / "quarantine", apply=True,
    )
    assert counters == {"moved": 0, "skipped": 0}
