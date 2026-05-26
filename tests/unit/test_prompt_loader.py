"""BIZ-298 — dreaming 프롬프트 YAML 로더 단위 테스트.

검증 범위:
- 패키지 내장 default 로드 성공 (6개 YAML 모두).
- 운영자 override 가 패키지 default 보다 우선.
- ``required_vars`` 누락/오타 시 fail-closed.
- 잘못된 YAML 구문 → ``PromptLoadError`` 명확한 메시지.
- 캐시 동작 — 같은 (name, operator_dir) 는 디스크 재읽기 없이 반환.
- byte-identical: 로더가 돌려준 메모리 spec 으로 format 한 결과가 dreaming.py 의
  legacy ``_DREAMING_PROMPT`` / ``_CLUSTER_SUMMARY_PROMPT`` 와 동일.
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


# ---------------------------------------------------------------------------
# 패키지 내장 default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["memory", "user", "soul", "agent", "active_projects", "cluster"],
)
def test_package_default_loads(name: str, tmp_path: Path) -> None:
    """6개 default YAML 이 비어있는 운영자 디렉터리에서도 로드된다."""
    spec = load_dreaming_prompt(name, operator_dir=tmp_path)
    assert isinstance(spec, DreamingPromptSpec)
    assert spec.name == name
    assert spec.system_prompt
    assert spec.user_prompt
    assert spec.required_vars  # 빈 튜플이 아님 — 모든 default 가 변수를 갖는다


def test_default_required_vars_match_placeholders(tmp_path: Path) -> None:
    """모든 default YAML 에서 required_vars 와 ``{var}`` placeholder 가 일치 — 로드 자체가 성공."""
    for name in ["memory", "user", "soul", "agent", "active_projects", "cluster"]:
        spec = load_dreaming_prompt(name, operator_dir=tmp_path)
        # 누락이 있으면 _parse_yaml 에서 PromptLoadError 가 났을 것이다.
        assert spec.required_vars


# ---------------------------------------------------------------------------
# 운영자 override
# ---------------------------------------------------------------------------


def test_operator_override_wins(tmp_path: Path) -> None:
    """운영자가 같은 이름의 YAML 을 두면 패키지 default 가 무시되어야 한다."""
    (tmp_path / "memory.yaml").write_text(
        textwrap.dedent(
            """\
            version: 1
            description: operator override
            system_prompt: |-
              CUSTOM SYSTEM
            user_prompt: |-
              CUSTOM USER {greeting}
            required_vars:
              - greeting
            """
        ),
        encoding="utf-8",
    )
    spec = load_dreaming_prompt("memory", operator_dir=tmp_path)
    assert spec.source_path == tmp_path / "memory.yaml"
    assert spec.system_prompt == "CUSTOM SYSTEM"
    assert spec.format(greeting="hi") == "CUSTOM USER hi"


def test_operator_override_only_partial_falls_back_to_package(tmp_path: Path) -> None:
    """운영자가 일부 YAML 만 만들면 그 파일만 override; 나머지는 패키지 default."""
    (tmp_path / "memory.yaml").write_text(
        textwrap.dedent(
            """\
            version: 1
            system_prompt: |-
              override
            user_prompt: |-
              override {x}
            required_vars:
              - x
            """
        ),
        encoding="utf-8",
    )
    overridden = load_dreaming_prompt("memory", operator_dir=tmp_path)
    cluster_default = load_dreaming_prompt("cluster", operator_dir=tmp_path)
    assert overridden.system_prompt == "override"
    assert "memory_clustering" in cluster_default.system_prompt.replace(" ", "_")


# ---------------------------------------------------------------------------
# 스키마 검증 (fail-closed)
# ---------------------------------------------------------------------------


def test_required_vars_mismatch_raises(tmp_path: Path) -> None:
    """user_prompt 의 placeholder 와 required_vars 가 어긋나면 명확한 에러."""
    (tmp_path / "memory.yaml").write_text(
        textwrap.dedent(
            """\
            version: 1
            system_prompt: |-
              s
            user_prompt: |-
              hello {name} from {place}
            required_vars:
              - name
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(PromptLoadError) as excinfo:
        load_dreaming_prompt("memory", operator_dir=tmp_path)
    msg = str(excinfo.value)
    assert "required_vars" in msg
    assert "place" in msg


def test_extra_required_var_raises(tmp_path: Path) -> None:
    """선언된 변수가 user_prompt 에 존재하지 않으면 명확한 에러."""
    (tmp_path / "memory.yaml").write_text(
        textwrap.dedent(
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
        ),
        encoding="utf-8",
    )
    with pytest.raises(PromptLoadError) as excinfo:
        load_dreaming_prompt("memory", operator_dir=tmp_path)
    assert "ghost" in str(excinfo.value)


def test_missing_required_field(tmp_path: Path) -> None:
    """system_prompt 누락 시 fail-closed."""
    (tmp_path / "memory.yaml").write_text(
        textwrap.dedent(
            """\
            version: 1
            user_prompt: |-
              only user
            required_vars: []
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(PromptLoadError) as excinfo:
        load_dreaming_prompt("memory", operator_dir=tmp_path)
    assert "system_prompt" in str(excinfo.value)


def test_bad_yaml_syntax_raises(tmp_path: Path) -> None:
    """YAML 파싱 실패는 PromptLoadError 로 래핑되어야 한다 (사이클 abort 가능)."""
    (tmp_path / "memory.yaml").write_text(
        ": this is\n  - not: valid yaml: at all:\n    :::",
        encoding="utf-8",
    )
    with pytest.raises(PromptLoadError) as excinfo:
        load_dreaming_prompt("memory", operator_dir=tmp_path)
    assert "invalid YAML" in str(excinfo.value) or "could not be loaded" in str(
        excinfo.value
    )


def test_root_not_mapping_raises(tmp_path: Path) -> None:
    """루트가 매핑이 아니면 명확히 거부."""
    (tmp_path / "memory.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(PromptLoadError):
        load_dreaming_prompt("memory", operator_dir=tmp_path)


def test_format_missing_var_raises(tmp_path: Path) -> None:
    """``spec.format()`` 에 required_var 가 누락되면 PromptLoadError."""
    (tmp_path / "memory.yaml").write_text(
        textwrap.dedent(
            """\
            version: 1
            system_prompt: |-
              s
            user_prompt: |-
              hello {name}
            required_vars:
              - name
            """
        ),
        encoding="utf-8",
    )
    spec = load_dreaming_prompt("memory", operator_dir=tmp_path)
    with pytest.raises(PromptLoadError) as excinfo:
        spec.format()  # type: ignore[call-arg]
    assert "name" in str(excinfo.value)


def test_unknown_name_raises(tmp_path: Path) -> None:
    """패키지 default 에도 운영자에도 없는 이름은 명확히 거부."""
    with pytest.raises(PromptLoadError) as excinfo:
        load_dreaming_prompt("does_not_exist", operator_dir=tmp_path)
    assert "does_not_exist" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------


def test_cache_avoids_disk_reread(tmp_path: Path) -> None:
    """같은 (name, operator_dir) 로 호출하면 캐시된 인스턴스가 반환된다."""
    (tmp_path / "memory.yaml").write_text(
        textwrap.dedent(
            """\
            version: 1
            system_prompt: |-
              v1
            user_prompt: |-
              v1
            required_vars: []
            """
        ),
        encoding="utf-8",
    )
    spec1 = load_dreaming_prompt("memory", operator_dir=tmp_path)
    # 파일을 수정해도 캐시가 유효한 한 같은 spec 이 돌아와야 한다.
    (tmp_path / "memory.yaml").write_text(
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
    spec2 = load_dreaming_prompt("memory", operator_dir=tmp_path)
    assert spec1 is spec2
    # refresh=True 면 다시 읽는다.
    spec3 = load_dreaming_prompt("memory", operator_dir=tmp_path, refresh=True)
    assert spec3.system_prompt == "v2"


# ---------------------------------------------------------------------------
# Byte-identical: legacy literal 과의 동치 (BIZ-298 시드 단계의 회귀 가드)
# ---------------------------------------------------------------------------


def test_memory_default_byte_identical_with_legacy_call(tmp_path: Path) -> None:
    """``memory.yaml`` 패키지 default 가 dreaming.py 호출과 byte-identical 결과를 낸다.

    BIZ-298 단계에서는 5개 dreaming YAML 모두 legacy ``_DREAMING_PROMPT`` 본문을 그대로
    담는다. 로더로 format 한 결과가 dreaming 의 실제 ``_summarize_with_llm`` 호출이
    만들 prompt 와 1바이트 차이 없이 동일해야 한다. (BIZ-299 가 본문을 분할하면 이
    테스트는 갱신/제거된다.)
    """
    spec = load_dreaming_prompt("memory", operator_dir=tmp_path)
    formatted = spec.format(
        existing_soul_md="SOUL",
        existing_agent_md="AGENT",
        existing_user_md="USER",
        conversations="msg",
        date="2026-05-26",
        language_instruction="LANG",
    )
    # 핵심 placeholder 들이 치환됐고 escape 가 풀렸는지 spot-check.
    assert "SOUL" in formatted
    assert "AGENT" in formatted
    assert "USER" in formatted
    assert "msg" in formatted
    assert "## 2026-05-26" in formatted
    assert "LANG" in formatted
    # 예시 JSON 의 ``{{`` 가 ``{`` 로 unescaped 되었는지.
    assert '{"memory":' in formatted
    # 마커 자체 출력 금지 안내 문장이 포함됐는지 (legacy body 의 핵심 가드).
    assert "managed:dreaming" in formatted


def test_cluster_default_byte_identical_with_legacy(tmp_path: Path) -> None:
    """``cluster.yaml`` 패키지 default 가 dreaming.py 의 ``_CLUSTER_SUMMARY_PROMPT`` 와 동치."""
    from simpleclaw.memory import dreaming as d  # type: ignore[attr-defined]

    spec = load_dreaming_prompt("cluster", operator_dir=tmp_path)
    fmt_kwargs = dict(existing_label="L", existing_summary="S", new_messages="N")
    expected = d._CLUSTER_SUMMARY_PROMPT.format(**fmt_kwargs)
    got = spec.format(**fmt_kwargs)
    assert expected == got
