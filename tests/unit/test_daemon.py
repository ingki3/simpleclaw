"""AgentDaemon 초기화 단위 테스트.

데몬 라이프사이클 통합은 ``tests/integration/test_daemon_pipeline.py`` 에서
검증한다. 이 파일은 시작/중지 없이 ``__init__`` 만 통과하는 가벼운 검증을
담당한다 — 특히 BIZ-139 회귀 가드(``~`` 경로 expanduser).
"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.daemon.daemon import AgentDaemon


def test_init_expands_tilde_paths(tmp_path, monkeypatch):
    """BIZ-139: config.yaml 의 ``~/.simpleclaw/...`` 경로가 홈 디렉터리로 풀려야 한다.

    수정 전에는 ``Path("~/.simpleclaw/daemon.db")`` 가 그대로 보관돼,
    ``DaemonStore(self._db_path)`` 호출 시 워킹 디렉터리 아래에 리터럴 ``~``
    폴더를 만들 위험이 있었다 (운영 디렉터리 이전 사고와 동일 클래스).
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    config = tmp_path / "config.yaml"
    config.write_text(
        "daemon:\n"
        "  heartbeat_interval: 1\n"
        '  pid_file: "~/.simpleclaw/daemon.pid"\n'
        '  status_file: "~/.simpleclaw/HEARTBEAT.md"\n'
        '  db_path: "~/.simpleclaw/daemon.db"\n'
    )

    daemon = AgentDaemon(config)

    expected_root = fake_home / ".simpleclaw"
    assert daemon._pid_file == expected_root / "daemon.pid"
    assert daemon._db_path == expected_root / "daemon.db"
    assert daemon._status_file == expected_root / "HEARTBEAT.md"

    # 어떤 경로에도 리터럴 ``~`` 가 남아 있으면 안 된다.
    for path in (daemon._pid_file, daemon._db_path, daemon._status_file):
        assert "~" not in str(path), f"expanduser 누락: {path}"
        assert not str(path).startswith("~"), f"expanduser 누락: {path}"


def test_init_passes_through_absolute_paths(tmp_path):
    """절대 경로는 expanduser 가 멱등적으로 동작해야 한다 (변경 없음)."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "daemon:\n"
        "  heartbeat_interval: 1\n"
        f'  pid_file: "{tmp_path}/daemon.pid"\n'
        f'  status_file: "{tmp_path}/HEARTBEAT.md"\n'
        f'  db_path: "{tmp_path}/daemon.db"\n'
    )

    daemon = AgentDaemon(config)

    assert daemon._pid_file == Path(f"{tmp_path}/daemon.pid")
    assert daemon._db_path == Path(f"{tmp_path}/daemon.db")
    assert daemon._status_file == Path(f"{tmp_path}/HEARTBEAT.md")
