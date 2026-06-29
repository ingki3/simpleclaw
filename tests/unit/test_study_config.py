"""Agent Study Wiki 설정 로더(`load_study_config`) 단위 테스트.

study 섹션의 기본값과 경계(사용자 메모리와 분리된 wiki_dir)를 회귀 방지용으로
고정한다. 실제 study runner/retrieval 동작은 후속 이슈 범위이므로 여기서는
설정 스켈레톤만 검증한다.
"""

from pathlib import Path

from simpleclaw.config_sections.study import load_study_config


def test_load_study_config_defaults_when_missing(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("agent: {}\n", encoding="utf-8")

    study = load_study_config(cfg)

    assert study["enabled"] is False
    assert study["daily"]["max_topics_per_run"] >= 1
    assert study["retrieval"]["top_k"] >= 1


def test_load_study_config_coerces_runtime_path(tmp_path: Path):
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


def test_load_study_config_defaults_when_file_absent(tmp_path: Path):
    cfg = tmp_path / "missing.yaml"

    study = load_study_config(cfg)

    assert study["enabled"] is False
    assert isinstance(study["wiki_dir"], Path)
    assert study["safety"]["require_sources"] is True


def test_load_study_config_merges_nested_partial_override(tmp_path: Path):
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

    # 명시한 하위 키는 덮어쓰되, 누락된 키는 기본값으로 채워진다.
    assert study["retrieval"]["top_k"] == 9
    assert study["retrieval"]["freshness_hours"]["high"] == 12
    assert study["retrieval"]["freshness_hours"]["medium"] == 72
    assert study["topic_evolution"]["auto_create"] is True


def test_load_study_config_ignores_malformed_section(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("study: not-a-dict\n", encoding="utf-8")

    study = load_study_config(cfg)

    assert study["enabled"] is False
    assert study["daily"]["hour_kst"] == 6
