from __future__ import annotations

from pathlib import Path

from scripts.migrate_local_dir import migrate


def test_migrate_supports_simpleclaw_source_to_split_persona_target(tmp_path: Path):
    source = tmp_path / ".simpleclaw"
    target = tmp_path / ".simpleclaw-agent" / "default"
    source.mkdir(parents=True)

    # live file (should move)
    (source / "AGENT.md").write_text("agent", encoding="utf-8")
    # live dir (should move)
    (source / "workspace").mkdir()
    (source / "workspace" / "note.txt").write_text("ok", encoding="utf-8")
    # non-live artifact (should stay in source)
    (source / "config.yaml").write_text("runtime: true", encoding="utf-8")

    counters = migrate(source, target)

    assert counters["files"] == 1
    assert counters["dirs"] == 1
    assert (target / "AGENT.md").exists()
    assert (target / "workspace" / "note.txt").exists()
    assert (source / "config.yaml").exists()


def test_migrate_carries_active_projects_prefix_files(tmp_path: Path):
    source = tmp_path / "src"
    target = tmp_path / "dst"
    source.mkdir()

    (source / "active_projects.jsonl").write_text("{}\n", encoding="utf-8")
    (source / "active_projects.archive.1").write_text("old\n", encoding="utf-8")

    counters = migrate(source, target)

    assert counters["files"] == 2
    assert (target / "active_projects.jsonl").exists()
    assert (target / "active_projects.archive.1").exists()