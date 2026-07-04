"""Study Wiki config 로더 단위 테스트.

config skeleton 단계의 계약을 고정한다: 기본값(opt-in), 파일/섹션 부재 방어,
중첩 부분 override 병합, malformed 섹션 방어, wiki_dir Path 정규화.
"""

from pathlib import Path

from simpleclaw.config_sections.study import load_study_config


def test_load_study_config_defaults_when_missing(tmp_path: Path):
    """study 섹션이 없으면 안전한 기본값을 반환한다."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("agent: {}\n", encoding="utf-8")

    study = load_study_config(cfg)

    assert study["enabled"] is False
    assert study["daily"]["max_topics_per_run"] >= 1
    assert study["retrieval"]["top_k"] >= 1


def test_load_study_config_returns_defaults_when_file_absent(tmp_path: Path):
    """config 파일 자체가 없어도 기본값으로 동작한다."""
    cfg = tmp_path / "does-not-exist.yaml"

    study = load_study_config(cfg)

    assert study["enabled"] is False
    assert study["retrieval"]["freshness_hours"]["high"] == 24


def test_load_study_config_coerces_runtime_path(tmp_path: Path):
    """wiki_dir은 ~ 확장 후 Path로 정규화된다."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "study:\n"
        "  enabled: true\n"
        "  wiki_dir: ./agent_wiki\n",
        encoding="utf-8",
    )

    study = load_study_config(cfg)

    assert study["enabled"] is True
    assert str(study["wiki_dir"]).endswith("agent_wiki")


def test_load_study_config_merges_nested_partial_override(tmp_path: Path):
    """중첩 섹션의 한 키만 override 해도 나머지 기본값은 유지된다."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "study:\n"
        "  retrieval:\n"
        "    top_k: 9\n"
        "    freshness_hours:\n"
        "      high: 12\n",
        encoding="utf-8",
    )

    study = load_study_config(cfg)

    # 사용자가 지정한 값은 반영
    assert study["retrieval"]["top_k"] == 9
    assert study["retrieval"]["freshness_hours"]["high"] == 12
    # 같은 섹션의 미지정 키는 기본값 유지
    assert study["retrieval"]["enabled"] is False
    assert study["retrieval"]["freshness_hours"]["medium"] == 72
    assert study["retrieval"]["freshness_hours"]["low"] == 168


def test_load_study_config_ignores_malformed_section(tmp_path: Path):
    """study가 dict가 아니면(문자열 등) 기본값으로 폴백한다."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("study: not-a-mapping\n", encoding="utf-8")

    study = load_study_config(cfg)

    assert study["enabled"] is False
    assert study["topic_evolution"]["auto_create"] is True
