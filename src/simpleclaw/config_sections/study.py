"""Agent Study Wiki config loader.

Agent Study Wiki는 사용자 프로필/기억(USER.md, MEMORY.md)과 분리된, 외부 세계
배경지식 저장소를 다룬다. 이 경계가 없으면 자동 뉴스 브리핑에서 본 주제가
사용자 관심사로 과대 일반화되거나, 오래된 외부 사실이 사용자 메모리처럼 남는다.

이 모듈은 ``study:`` 섹션의 설정값을 기본값과 병합하고, 런타임 경로(``wiki_dir``)를
``Path``로 정규화한다. 실제 study runner/wiki 파일 생성/retrieval 연동은 후속
이슈에서 다루며, 여기서는 설정 스켈레톤만 고정한다(설계 문서: docs/agent-study-wiki.md).

기본적으로 모든 enabled 플래그는 False다 — study runner는 외부 검색/LLM 비용을
유발하므로 사용자가 명시적으로 켜야 한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Agent Study Wiki 기본 설정값.
# 모든 키는 안전한 기본값을 가진다 — config.yaml에 study 섹션이 없어도 봇은 동작한다.
_STUDY_DEFAULTS: dict = {
    "enabled": False,
    # study runner가 외부 배경지식을 기록하는 디렉터리. 사용자 메모리 디렉터리와
    # 분리하여 "관심사"와 "세계 배경지식"의 경계를 파일 시스템 레벨에서 강제한다.
    "wiki_dir": "~/.simpleclaw-agent/default/agent_wiki",
    "daily": {
        "enabled": False,
        "hour_kst": 6,            # 매일 공부 실행 시각(KST)
        "max_topics_per_run": 8,  # 1회 실행에서 다룰 최대 topic 수(비용 상한)
        "max_sources_per_topic": 5,
    },
    "retrieval": {
        "enabled": False,
        "top_k": 4,               # 질문 시 맥락으로 끌어올 wiki 엔트리 수
        "max_context_chars": 5000,
        # freshness 등급별 "신선한 것으로 간주하는" 시간(hour). 등급이 낮을수록
        # 더 오래된 정보까지 허용한다(외부 사실은 시간이 지나면 신뢰도가 낮아짐).
        "freshness_hours": {
            "high": 24,
            "medium": 72,
            "low": 168,
        },
    },
    "topic_evolution": {
        "auto_create": True,          # 관심도가 충분하면 topic 자동 생성
        "min_interest_score": 0.55,   # topic 후보로 인정하는 최소 관심도
        "promote_threshold": 0.70,    # 정식 topic으로 승격하는 임계값
        "decay_after_days": 14,       # 활동 없는 topic을 감쇠 처리하기까지의 일수
    },
    "safety": {
        "require_sources": True,                    # 출처 없는 사실은 기록 금지
        "low_confidence_requires_disclaimer": True,  # 저신뢰 정보는 면책 문구 동반
    },
}


def _merge_section(defaults: dict, override: object) -> dict:
    """한 단계 중첩 섹션을 기본값과 병합한다.

    ``override``가 dict가 아니면(키 누락/형식 오류) 기본값 복사본을 반환한다.
    중첩 dict 값(예: ``retrieval.freshness_hours``)도 재귀적으로 병합하여, 사용자가
    일부 하위 키만 지정해도 나머지는 기본값이 채워지도록 한다.
    """
    if not isinstance(override, dict):
        return _deep_copy(defaults)

    merged: dict = {}
    for key, default_value in defaults.items():
        if isinstance(default_value, dict):
            merged[key] = _merge_section(default_value, override.get(key))
        else:
            merged[key] = override.get(key, default_value)
    return merged


def _deep_copy(value: dict) -> dict:
    """기본값 dict의 깊은 복사본을 만든다(공유 참조로 인한 상태 오염 방지)."""
    return {
        key: _deep_copy(sub) if isinstance(sub, dict) else sub
        for key, sub in value.items()
    }


def load_study_config(config_path: str | Path) -> dict:
    """config.yaml에서 Agent Study Wiki 설정을 로드한다.

    파일이 없거나 ``study`` 키가 없으면 기본값(study 비활성)을 반환한다.
    ``wiki_dir``는 ``~`` 확장을 거쳐 ``Path``로 정규화하여 런타임에서 바로
    사용할 수 있게 한다.
    """
    config_path = Path(config_path)
    study_raw: object = {}
    if config_path.is_file():
        try:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError):
            data = None
        if isinstance(data, dict):
            study_raw = data.get("study", {})

    study = _merge_section(_STUDY_DEFAULTS, study_raw)

    # 런타임 경로 정규화: 문자열 그대로 두면 매 호출부에서 expanduser를 반복해야
    # 하므로 로더에서 한 번만 Path로 변환한다.
    study["wiki_dir"] = Path(str(study["wiki_dir"])).expanduser()

    return study
