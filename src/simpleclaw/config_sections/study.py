"""Agent Study Wiki config loader.

Study Wiki는 사용자 프로필/기억(USER.md, MEMORY.md, insights)과 분리된 "외부 세계
배경지식" 저장소다. 이 경계가 없으면 자동 브리핑에서 본 주제가 사용자 관심사로
과대 일반화되거나, 낡은 외부 사실이 사용자 메모리처럼 영속되는 문제가 생긴다.

이 모듈은 ``config.yaml``의 ``study:`` 섹션을 기본값과 병합해 반환하는 스켈레톤이다.
실제 study runner / wiki 파일 생성 / retrieval 연동은 후속 이슈 범위이며, 여기서는
설정 defaults만 고정한다(테스트로 보장).

설계 결정:
- 모든 기능은 opt-in(``enabled: false`` 기본). study는 외부 네트워크 호출을 동반할 수
  있으므로 사용자가 명시적으로 켜야 한다.
- 중첩 섹션(``retrieval.freshness_hours`` 등)은 재귀 병합한다. 사용자가 한 키만
  override 해도 나머지 기본값이 유지되도록 ``memory.py``의 평면 병합보다 한 단계
  일반화했다.
- ``wiki_dir``은 ``~`` 확장 후 ``Path``로 정규화한다(소비처에서 바로 쓸 수 있도록).
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Study Wiki 기본 설정값.
# config.yaml에 study 섹션이 없어도 봇은 동작한다 — 모든 키가 안전한 기본값을 가진다.
# 권장 shape(설계 문서 `docs/agent-study-wiki.md`)와 config.yaml.example 값이 일치한다.
_STUDY_DEFAULTS: dict = {
    "enabled": False,
    "wiki_dir": "~/.simpleclaw-agent/default/agent_wiki",
    "daily": {
        "enabled": False,
        "hour_kst": 6,
        "max_topics_per_run": 8,
        "max_sources_per_topic": 5,
    },
    "retrieval": {
        "enabled": False,
        "top_k": 4,
        "max_context_chars": 5000,
        "freshness_hours": {
            "high": 24,
            "medium": 72,
            "low": 168,
        },
    },
    "topic_evolution": {
        "auto_create": True,
        "min_interest_score": 0.55,
        "promote_threshold": 0.70,
        "decay_after_days": 14,
    },
    "safety": {
        "require_sources": True,
        "low_confidence_requires_disclaimer": True,
    },
}


def _deep_copy(value: object) -> object:
    """중첩 dict를 안전하게 복사한다.

    ``_STUDY_DEFAULTS``는 dict + scalar 만으로 구성되므로 ``copy.deepcopy`` 의존
    없이 충분하다. defaults 원본이 호출자에게 공유·변형되는 것을 막는 것이 목적이다.
    """
    if isinstance(value, dict):
        return {key: _deep_copy(val) for key, val in value.items()}
    return value


def _merge_section(defaults: dict, override: object) -> dict:
    """defaults 위에 사용자 override를 재귀 병합한다.

    defaults에 존재하는 키만 순회하므로, 사용자가 오타 낸 미지의 키는 조용히
    무시된다(스켈레톤 단계의 안전한 동작 — 향후 unknown-key 경고는 후속 이슈).
    중첩 dict는 한 키만 override 해도 나머지 기본값이 유지된다.

    Args:
        defaults: 기준이 되는 기본값 dict.
        override: 사용자가 준 값. dict가 아니면 무시하고 defaults 복사본을 반환.

    Returns:
        병합된 새 dict (defaults 원본은 변형하지 않음).
    """
    result: dict = _deep_copy(defaults)  # type: ignore[assignment]
    if not isinstance(override, dict):
        return result

    for key, default_value in defaults.items():
        if key not in override:
            continue
        user_value = override[key]
        if isinstance(default_value, dict):
            # 중첩 섹션은 한 단계 더 재귀 병합한다.
            result[key] = _merge_section(default_value, user_value)
        else:
            result[key] = user_value
    return result


def load_study_config(config_path: str | Path) -> dict:
    """config.yaml에서 Study Wiki(``study:``) 설정을 로드한다.

    파일이 없거나 study 키가 없거나 형식이 잘못되면 기본값(모든 기능 비활성)을
    반환한다. ``wiki_dir``은 ``~`` 확장 후 ``Path``로 정규화한다.

    Args:
        config_path: config.yaml 경로.

    Returns:
        defaults와 병합된 study 설정 dict.
    """
    config_path = Path(config_path)
    study_raw: dict = {}

    if config_path.is_file():
        try:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError):
            data = None
        if isinstance(data, dict):
            section = data.get("study", {})
            if isinstance(section, dict):
                study_raw = section

    merged = _merge_section(_STUDY_DEFAULTS, study_raw)

    # wiki_dir은 소비처(runner/retrieval)에서 바로 쓸 수 있도록 Path로 정규화.
    wiki_dir = merged.get("wiki_dir")
    if isinstance(wiki_dir, str) and wiki_dir:
        merged["wiki_dir"] = Path(wiki_dir).expanduser()

    return merged
