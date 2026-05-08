"""BIZ-138 — 봇 wiring 경로가 ``.agent/`` 로 회귀하지 않는지 검증하는 단위 테스트.

배경
----
BIZ-133 으로 운영 디렉터리가 저장소 working tree(``.agent/``) 외부의
``~/.simpleclaw/`` 로 이전됐다. 그러나 ``scripts/run_bot.py`` 의
SafetyBackupManager wiring 만 같은 PR 에서 누락되어 ``.agent/AGENT.md`` 등의
하드코드가 살아남았고, 결과적으로 config.yaml 이 ``~/.simpleclaw/`` 를
가리키는데도 봇이 ``.agent/_safety_backup/{ts}/`` 에 빈 디렉터리만 만들고
dreaming preflight 가 ``.agent/MEMORY.md`` 를 못 찾아 abort 했다 (BIZ-138).

이 테스트의 역할
----------------
1. **소스 회귀 가드**: ``scripts/run_bot.py`` 에 ``.agent/...`` 리터럴이
   다시 들어오면 즉시 실패한다. 누군가 wiring 을 다시 하드코드로 되돌리는
   것을 막는다.
2. **config 라우팅 가드**: ``load_persona_config`` / ``load_daemon_config`` /
   ``load_agent_config`` 가 ``~/.simpleclaw/`` 기본값을 그대로 반환하는지,
   그리고 사용자가 명시한 다른 경로(예: 임시 디렉터리)도 그대로 보존하는지
   확인한다 — 어떤 키 하나라도 ``.agent/`` 로 폴백하면 같은 회귀가 다시 난다.
3. **safety_backup wiring 함수 가드**: 봇 부팅 시 SafetyBackupManager 입력에
   해당하는 경로 9 종 + DB 2 종 + backup_root 가 모두 config 의 운영 디렉터리
   기준으로 풀리는지 시뮬레이션한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from simpleclaw.config import (
    _AGENT_DEFAULTS,
    _DAEMON_DEFAULTS,
    _DEFAULTS as _PERSONA_DEFAULTS,
    load_agent_config,
    load_daemon_config,
    load_persona_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_BOT_PATH = REPO_ROOT / "scripts" / "run_bot.py"

# 운영 데이터 sidecar 가 ``.agent/`` 안에 들어가면 안 된다 — BIZ-133/138 회귀 가드.
# safety_backup_files / safety_backup_databases / backup_root 의 입력으로 쓰이는
# basename 들을 모은 목록. 이들이 ``.agent/...`` 와 결합된 리터럴이 소스에 다시
# 등장하면 즉시 실패한다.
FORBIDDEN_LITERALS = (
    ".agent/AGENT.md",
    ".agent/USER.md",
    ".agent/MEMORY.md",
    ".agent/SOUL.md",
    ".agent/insights.jsonl",
    ".agent/suggestions.jsonl",
    ".agent/insight_blocklist.jsonl",
    ".agent/dreaming_runs.jsonl",
    ".agent/HEARTBEAT.md",
    ".agent/_safety_backup",
    ".agent/conversations.db",
    ".agent/daemon.db",
    ".agent/active_projects.jsonl",
)


# ----------------------------------------------------------------------
# 1. 소스 회귀 가드 — run_bot.py 에 ``.agent/...`` 리터럴이 부활하면 실패
# ----------------------------------------------------------------------


def test_run_bot_does_not_hardcode_legacy_agent_paths():
    """``scripts/run_bot.py`` 에 ``.agent/{운영파일}`` 리터럴이 다시 들어오지 않는지 확인.

    BIZ-138 회귀 가드. 누군가 SafetyBackupManager wiring 을 ``.agent/...`` 로
    되돌리면(예: persona_local_dir 헬퍼를 거치지 않고 빠른 경로로 재하드코드)
    이 테스트가 즉시 실패한다.
    """
    text = RUN_BOT_PATH.read_text(encoding="utf-8")
    offenders = [literal for literal in FORBIDDEN_LITERALS if literal in text]
    assert not offenders, (
        f"run_bot.py 에 BIZ-133/138 회귀가 발생했습니다 — "
        f"다음 리터럴은 config 키 또는 ``persona_local_dir`` 로 풀어야 합니다: {offenders}"
    )


def test_run_bot_does_not_construct_path_from_dot_agent_root():
    """``_Path(".agent/...")`` / ``Path(".agent/...")`` 호출이 남아있지 않은지.

    위 ``FORBIDDEN_LITERALS`` 검사와 별도로, ``Path`` 생성자 내부에 ``.agent/``
    를 직접 넘기는 패턴 자체를 차단한다 — 새 sidecar 가 추가될 때 자동으로
    회귀를 잡기 위함.
    """
    text = RUN_BOT_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"_?Path\(\s*[\"']\.agent/")
    matches = pattern.findall(text)
    assert not matches, (
        "run_bot.py 에 ``Path(\".agent/...\")`` 형태의 하드코드가 남아 있습니다. "
        "config 키(``daemon.dreaming.*_file``, ``persona.local_dir``, "
        "``daemon.status_file``, ``daemon.dreaming.safety_backup_dir``) 를 사용하세요."
    )


# ----------------------------------------------------------------------
# 2. config 기본값 가드 — 모든 운영 경로가 ``~/.simpleclaw/`` 로 시작
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, defaults",
    [
        ("local_dir", _PERSONA_DEFAULTS),
        ("db_path", _AGENT_DEFAULTS),
    ],
)
def test_persona_and_agent_defaults_use_simpleclaw_root(key, defaults):
    """페르소나 라이브 디렉터리·agent DB 의 기본 경로가 ``~/.simpleclaw/`` 로 시작."""
    assert defaults[key].startswith("~/.simpleclaw"), (
        f"{key} 의 기본값이 ``~/.simpleclaw/`` 외부로 회귀했습니다: {defaults[key]}"
    )


def test_daemon_defaults_all_under_simpleclaw_root():
    """데몬 + dreaming 의 모든 파일·디렉터리 기본 경로가 ``~/.simpleclaw/`` 로 시작.

    하나라도 ``.agent/`` 등으로 회귀하면 BIZ-138 같은 분리 사고가 다시 발생한다.
    """
    flat_keys = ("pid_file", "status_file", "db_path")
    for k in flat_keys:
        assert _DAEMON_DEFAULTS[k].startswith("~/.simpleclaw"), (
            f"daemon.{k} 기본값이 ``~/.simpleclaw/`` 외부로 회귀: {_DAEMON_DEFAULTS[k]}"
        )

    dreaming = _DAEMON_DEFAULTS["dreaming"]
    sidecar_keys = (
        "insights_file",
        "suggestions_file",
        "blocklist_file",
        "runs_file",
        "safety_backup_dir",
    )
    for k in sidecar_keys:
        assert dreaming[k].startswith("~/.simpleclaw"), (
            f"daemon.dreaming.{k} 기본값이 ``~/.simpleclaw/`` 외부로 회귀: {dreaming[k]}"
        )

    ap = dreaming["active_projects"]
    assert ap["sidecar_path"].startswith("~/.simpleclaw"), (
        f"daemon.dreaming.active_projects.sidecar_path 기본값 회귀: {ap['sidecar_path']}"
    )


# ----------------------------------------------------------------------
# 3. config 라우팅 가드 — 사용자 override 가 그대로 보존되는지
# ----------------------------------------------------------------------


_SAMPLE_CONFIG_TEMPLATE = """\
agent:
  db_path: "{base}/conversations.db"
  workspace_dir: "{base}/workspace"
persona:
  local_dir: "{base}"
daemon:
  pid_file: "{base}/daemon.pid"
  status_file: "{base}/HEARTBEAT.md"
  db_path: "{base}/daemon.db"
  dreaming:
    insights_file: "{base}/insights.jsonl"
    suggestions_file: "{base}/suggestions.jsonl"
    blocklist_file: "{base}/insight_blocklist.jsonl"
    runs_file: "{base}/dreaming_runs.jsonl"
    safety_backup_dir: "{base}/_safety_backup"
    active_projects:
      enabled: true
      window_days: 7
      sidecar_path: "{base}/active_projects.jsonl"
"""


def test_config_loaders_preserve_user_paths(tmp_path: Path):
    """사용자가 지정한 경로(``base``)가 각 로더를 거쳐도 그대로 흘러나오는지.

    BIZ-138 회귀 시나리오: 어느 한 키라도 사용자 지정값을 무시하고 ``.agent/``
    기본값으로 폴백하면, 봇 부팅 시 운영 데이터가 두 디렉터리로 갈라진다.
    """
    base = tmp_path / "simpleclaw_home"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        _SAMPLE_CONFIG_TEMPLATE.format(base=base.as_posix()),
        encoding="utf-8",
    )

    persona = load_persona_config(cfg)
    agent = load_agent_config(cfg)
    daemon = load_daemon_config(cfg)

    base_str = base.as_posix()

    assert persona["local_dir"] == base_str
    assert agent["db_path"] == f"{base_str}/conversations.db"
    assert agent["workspace_dir"] == f"{base_str}/workspace"

    assert daemon["pid_file"] == f"{base_str}/daemon.pid"
    assert daemon["status_file"] == f"{base_str}/HEARTBEAT.md"
    assert daemon["db_path"] == f"{base_str}/daemon.db"

    dreaming = daemon["dreaming"]
    assert dreaming["insights_file"] == f"{base_str}/insights.jsonl"
    assert dreaming["suggestions_file"] == f"{base_str}/suggestions.jsonl"
    assert dreaming["blocklist_file"] == f"{base_str}/insight_blocklist.jsonl"
    assert dreaming["runs_file"] == f"{base_str}/dreaming_runs.jsonl"
    assert dreaming["safety_backup_dir"] == f"{base_str}/_safety_backup"
    assert dreaming["active_projects"]["sidecar_path"] == f"{base_str}/active_projects.jsonl"


# ----------------------------------------------------------------------
# 4. safety_backup 입력 시뮬레이션 — wiring 결과 9+2+1 종이 운영 디렉터리 안에
# ----------------------------------------------------------------------


def _expand(path_str: str | None) -> str | None:
    """run_bot.py 의 ``_expand`` 헬퍼와 동일 시그니처. 테스트에서 wiring 시뮬레이션."""
    if path_str is None:
        return None
    return str(Path(path_str).expanduser())


def _simulate_safety_backup_wiring(
    persona_cfg: dict, agent_cfg: dict, daemon_cfg: dict
) -> dict:
    """run_bot.py 의 SafetyBackupManager 입력 wiring 을 추출 시뮬레이션한다.

    실제 SafetyBackupManager 인스턴스화는 부수효과가 크므로(디렉터리 생성),
    경로 계산만 동일 로직으로 흉내내어 테스트한다. 실제 코드 변경 시 두
    버전이 동기화되어야 한다 — 회귀 시 이 테스트가 잡는다.
    """
    persona_local_dir = Path(persona_cfg["local_dir"]).expanduser()
    dreaming_cfg = daemon_cfg["dreaming"]
    active_projects_cfg = dreaming_cfg.get("active_projects", {}) or {}
    active_projects_file = (
        active_projects_cfg.get("sidecar_path")
        if active_projects_cfg.get("enabled", True)
        else None
    )

    files: list[Path] = [
        persona_local_dir / "AGENT.md",
        persona_local_dir / "USER.md",
        persona_local_dir / "MEMORY.md",
        persona_local_dir / "SOUL.md",
        Path(_expand(dreaming_cfg["insights_file"])),
        Path(_expand(dreaming_cfg["suggestions_file"])),
        Path(_expand(dreaming_cfg["blocklist_file"])),
        Path(_expand(dreaming_cfg["runs_file"])),
        Path(daemon_cfg["status_file"]).expanduser(),
    ]
    if active_projects_file:
        files.append(Path(_expand(active_projects_file)))

    databases: list[Path] = [
        Path(agent_cfg["db_path"]).expanduser(),
        Path(daemon_cfg["db_path"]).expanduser(),
    ]

    backup_root = Path(
        _expand(
            dreaming_cfg.get("safety_backup_dir", "~/.simpleclaw/_safety_backup")
        )
    )

    return {
        "files": files,
        "databases": databases,
        "backup_root": backup_root,
    }


def test_safety_backup_wiring_routes_under_user_base(tmp_path: Path):
    """config 가 임시 base 디렉터리를 가리키면 모든 wiring 경로가 그 안으로 떨어진다."""
    base = tmp_path / "simpleclaw_home"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        _SAMPLE_CONFIG_TEMPLATE.format(base=base.as_posix()),
        encoding="utf-8",
    )

    persona = load_persona_config(cfg)
    agent = load_agent_config(cfg)
    daemon = load_daemon_config(cfg)
    wiring = _simulate_safety_backup_wiring(persona, agent, daemon)

    base_resolved = base.resolve()
    for path in (*wiring["files"], *wiring["databases"], wiring["backup_root"]):
        # 모든 경로가 사용자 base 안으로 떨어져야 한다 — ``.agent/`` 같은 상대
        # 경로로 분기되면 즉시 실패.
        assert base_resolved in path.resolve().parents or path.resolve() == base_resolved, (
            f"wiring 경로가 base 디렉터리 외부로 회귀했습니다: {path}"
        )
        assert ".agent" not in path.parts, (
            f"wiring 경로에 ``.agent`` 세그먼트가 남아있습니다: {path}"
        )


def test_safety_backup_wiring_uses_simpleclaw_defaults_when_config_missing(tmp_path: Path):
    """config.yaml 이 없을 때도 wiring 결과가 ``~/.simpleclaw/`` 아래로 떨어진다.

    BIZ-138 의 1차 가드 — 배포 환경에서 config 한 줄이 빠져도 ``.agent/`` 로
    회귀하지 않도록 모든 기본값이 운영 디렉터리를 가리켜야 한다.
    """
    persona = load_persona_config(tmp_path / "missing.yaml")
    agent = load_agent_config(tmp_path / "missing.yaml")
    daemon = load_daemon_config(tmp_path / "missing.yaml")
    wiring = _simulate_safety_backup_wiring(persona, agent, daemon)

    home = Path("~").expanduser().resolve()
    expected_root = (home / ".simpleclaw").resolve()
    for path in (*wiring["files"], *wiring["databases"], wiring["backup_root"]):
        resolved = path.resolve()
        assert expected_root in resolved.parents or resolved == expected_root, (
            f"기본 wiring 경로가 ``~/.simpleclaw/`` 외부로 회귀: {path}"
        )
        assert ".agent" not in resolved.parts, (
            f"기본 wiring 경로에 ``.agent`` 세그먼트가 남아있음: {path}"
        )
