"""BIZ-301 — dreaming 프롬프트 YAML 로더 단위 테스트.

검증 범위:
- 실제 repo root 의 6개 YAML 모두 로드 성공.
- ``SIMPLECLAW_ROOT`` env / 명시 ``repo_root`` 인자로 격리된 트리에서 로드.
- repo root 해소 실패 (env 부재 + walk-up 실패) 시 ``PromptLoadError``.
- YAML 부재 / 잘못된 스키마 / placeholder 불일치 시 fail-closed.
- 캐시 동작 — 같은 (name, repo_root) 는 디스크 재읽기 없이 반환.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from simpleclaw.memory.prompt_loader import (
    DreamingPromptSpec,
    PromptLoadError,
    clear_cache,
    load_dreaming_prompt,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """각 테스트 사이에 캐시가 누설되지 않도록 초기화."""
    clear_cache()
    yield
    clear_cache()


def _make_fake_repo(
    tmp_path: Path,
    yamls: dict[str, str] | None = None,
    *,
    with_pyproject: bool = True,
) -> Path:
    """``prompts/dreaming/*.yaml`` 와 (선택) ``pyproject.toml`` 을 갖춘 가짜 repo 트리를 만든다.

    ``yamls`` 의 키가 basename, 값이 YAML 본문.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    if with_pyproject:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='fake'\n", encoding="utf-8")
    prompts_dir = tmp_path / "prompts" / "dreaming"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (yamls or {}).items():
        (prompts_dir / f"{name}.yaml").write_text(body, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# 실제 repo root 의 6개 default YAML
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["memory", "user", "soul", "agent", "active_projects", "cluster"],
)
def test_repo_default_loads(name: str) -> None:
    """6개 default YAML 이 실제 repo 트리에서 로드된다."""
    spec = load_dreaming_prompt(name)
    assert isinstance(spec, DreamingPromptSpec)
    assert spec.name == name
    assert spec.system_prompt
    assert spec.user_prompt
    assert spec.required_vars  # 빈 튜플이 아님 — 모든 default 가 변수를 갖는다
    # SoT 가 repo 의 prompts/dreaming/ 디렉터리인지 확인.
    assert spec.source_path.parent.name == "dreaming"
    assert spec.source_path.parent.parent.name == "prompts"


def test_default_required_vars_match_placeholders() -> None:
    """모든 default YAML 에서 required_vars 와 ``{var}`` placeholder 가 일치 — 로드 자체가 성공."""
    for name in ["memory", "user", "soul", "agent", "active_projects", "cluster"]:
        spec = load_dreaming_prompt(name)
        # 누락이 있으면 _parse_yaml 에서 PromptLoadError 가 났을 것이다.
        assert spec.required_vars


# ---------------------------------------------------------------------------
# repo_root 해소: 인자 / env / walk-up
# ---------------------------------------------------------------------------


def test_repo_root_arg_overrides_walk_up(tmp_path: Path) -> None:
    """``repo_root`` 인자가 명시되면 그 경로의 prompts/dreaming/ 만 본다."""
    _make_fake_repo(
        tmp_path,
        {
            "memory": textwrap.dedent(
                """\
                version: 1
                description: fake-tree memory
                system_prompt: |-
                  CUSTOM SYSTEM
                user_prompt: |-
                  CUSTOM USER {greeting}
                required_vars:
                  - greeting
                """
            )
        },
    )
    spec = load_dreaming_prompt("memory", repo_root=tmp_path)
    assert spec.source_path == tmp_path / "prompts" / "dreaming" / "memory.yaml"
    assert spec.system_prompt == "CUSTOM SYSTEM"
    assert spec.format(greeting="hi") == "CUSTOM USER hi"


def test_simpleclaw_root_env_overrides_walk_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SIMPLECLAW_ROOT`` env 가 설정되면 그 경로가 walk-up 결과를 덮어쓴다."""
    _make_fake_repo(
        tmp_path,
        {
            "memory": textwrap.dedent(
                """\
                version: 1
                description: env-driven memory
                system_prompt: |-
                  ENV SYS
                user_prompt: |-
                  ENV USER {x}
                required_vars:
                  - x
                """
            )
        },
    )
    monkeypatch.setenv("SIMPLECLAW_ROOT", str(tmp_path))
    spec = load_dreaming_prompt("memory")
    assert spec.source_path == tmp_path / "prompts" / "dreaming" / "memory.yaml"
    assert spec.system_prompt == "ENV SYS"


def test_explicit_arg_wins_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """명시적 ``repo_root`` 인자가 ``SIMPLECLAW_ROOT`` env 보다 우선한다."""
    env_root = tmp_path / "env_tree"
    arg_root = tmp_path / "arg_tree"
    _make_fake_repo(
        env_root,
        {
            "memory": textwrap.dedent(
                """\
                version: 1
                system_prompt: |-
                  ENV
                user_prompt: |-
                  ENV {x}
                required_vars: [x]
                """
            )
        },
    )
    _make_fake_repo(
        arg_root,
        {
            "memory": textwrap.dedent(
                """\
                version: 1
                system_prompt: |-
                  ARG
                user_prompt: |-
                  ARG {x}
                required_vars: [x]
                """
            )
        },
    )
    monkeypatch.setenv("SIMPLECLAW_ROOT", str(env_root))
    spec = load_dreaming_prompt("memory", repo_root=arg_root)
    assert spec.system_prompt == "ARG"


# ---------------------------------------------------------------------------
# 스키마 검증 (fail-closed)
# ---------------------------------------------------------------------------


def test_required_vars_mismatch_raises(tmp_path: Path) -> None:
    """user_prompt 의 placeholder 와 required_vars 가 어긋나면 명확한 에러."""
    _make_fake_repo(
        tmp_path,
        {
            "memory": textwrap.dedent(
                """\
                version: 1
                system_prompt: |-
                  s
                user_prompt: |-
                  hello {name} from {place}
                required_vars:
                  - name
                """
            )
        },
    )
    with pytest.raises(PromptLoadError) as excinfo:
        load_dreaming_prompt("memory", repo_root=tmp_path)
    msg = str(excinfo.value)
    assert "required_vars" in msg
    assert "place" in msg


def test_extra_required_var_raises(tmp_path: Path) -> None:
    """선언된 변수가 user_prompt 에 존재하지 않으면 명확한 에러."""
    _make_fake_repo(
        tmp_path,
        {
            "memory": textwrap.dedent(
                """\
                version: 1
                system_prompt: |-
                  s
                user_prompt: |-
                  hello {name}
                required_vars:
                  - name
                  - ghost
                """
            )
        },
    )
    with pytest.raises(PromptLoadError) as excinfo:
        load_dreaming_prompt("memory", repo_root=tmp_path)
    assert "ghost" in str(excinfo.value)


def test_missing_required_field(tmp_path: Path) -> None:
    """system_prompt 누락 시 fail-closed."""
    _make_fake_repo(
        tmp_path,
        {
            "memory": textwrap.dedent(
                """\
                version: 1
                user_prompt: |-
                  only user
                required_vars: []
                """
            )
        },
    )
    with pytest.raises(PromptLoadError) as excinfo:
        load_dreaming_prompt("memory", repo_root=tmp_path)
    assert "system_prompt" in str(excinfo.value)


def test_bad_yaml_syntax_raises(tmp_path: Path) -> None:
    """YAML 파싱 실패는 PromptLoadError 로 래핑되어야 한다 (사이클 abort 가능)."""
    _make_fake_repo(tmp_path)
    (tmp_path / "prompts" / "dreaming" / "memory.yaml").write_text(
        ": this is\n  - not: valid yaml: at all:\n    :::",
        encoding="utf-8",
    )
    with pytest.raises(PromptLoadError) as excinfo:
        load_dreaming_prompt("memory", repo_root=tmp_path)
    assert "invalid YAML" in str(excinfo.value)


def test_root_not_mapping_raises(tmp_path: Path) -> None:
    """루트가 매핑이 아니면 명확히 거부."""
    _make_fake_repo(tmp_path)
    (tmp_path / "prompts" / "dreaming" / "memory.yaml").write_text(
        "- just\n- a\n- list\n", encoding="utf-8"
    )
    with pytest.raises(PromptLoadError):
        load_dreaming_prompt("memory", repo_root=tmp_path)


def test_format_missing_var_raises(tmp_path: Path) -> None:
    """``spec.format()`` 에 required_var 가 누락되면 PromptLoadError."""
    _make_fake_repo(
        tmp_path,
        {
            "memory": textwrap.dedent(
                """\
                version: 1
                system_prompt: |-
                  s
                user_prompt: |-
                  hello {name}
                required_vars:
                  - name
                """
            )
        },
    )
    spec = load_dreaming_prompt("memory", repo_root=tmp_path)
    with pytest.raises(PromptLoadError) as excinfo:
        spec.format()  # type: ignore[call-arg]
    assert "name" in str(excinfo.value)


def test_missing_yaml_raises(tmp_path: Path) -> None:
    """``prompts/dreaming/{name}.yaml`` 자체가 없으면 명확히 거부 — fallback 없음."""
    _make_fake_repo(tmp_path)  # YAML 0개
    with pytest.raises(PromptLoadError) as excinfo:
        load_dreaming_prompt("memory", repo_root=tmp_path)
    msg = str(excinfo.value)
    assert "memory" in msg
    assert "not found" in msg


def test_repo_root_unresolvable_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env 부재 + walk-up 실패 (가짜 모듈 경로) 시 PromptLoadError.

    실제 모듈 위치는 simpleclaw 레포 안이므로 walk-up 이 항상 성공한다 — 이 경로를
    테스트하려면 _resolve_repo_root 의 walk-up 시작 지점을 우회해야 한다. 가장
    현실적인 시나리오는 env 가 명시적으로 잘못 설정된 경우 (존재하지 않는 경로):
    이때 PromptLoadError 가 ``not found`` 단계에서 잡힌다.
    """
    monkeypatch.setenv("SIMPLECLAW_ROOT", str(tmp_path / "does_not_exist"))
    with pytest.raises(PromptLoadError):
        load_dreaming_prompt("memory")


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------


def test_cache_avoids_disk_reread(tmp_path: Path) -> None:
    """같은 (name, repo_root) 로 호출하면 캐시된 인스턴스가 반환된다."""
    _make_fake_repo(
        tmp_path,
        {
            "memory": textwrap.dedent(
                """\
                version: 1
                system_prompt: |-
                  v1
                user_prompt: |-
                  v1
                required_vars: []
                """
            )
        },
    )
    spec1 = load_dreaming_prompt("memory", repo_root=tmp_path)
    # 파일을 수정해도 캐시가 유효한 한 같은 spec 이 돌아와야 한다.
    (tmp_path / "prompts" / "dreaming" / "memory.yaml").write_text(
        textwrap.dedent(
            """\
            version: 2
            system_prompt: |-
              v2
            user_prompt: |-
              v2
            required_vars: []
            """
        ),
        encoding="utf-8",
    )
    spec2 = load_dreaming_prompt("memory", repo_root=tmp_path)
    assert spec1 is spec2
    # refresh=True 면 다시 읽는다.
    spec3 = load_dreaming_prompt("memory", repo_root=tmp_path, refresh=True)
    assert spec3.system_prompt == "v2"
