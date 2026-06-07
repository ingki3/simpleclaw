"""Semantic memory config loader.

RAG 및 long-term memory 설정을 기본값과 병합한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# 시맨틱 메모리(RAG) 기본 설정값
# 모든 키는 안전한 기본값을 가진다 — config.yaml에 memory 섹션이 없어도 봇은 동작한다.
# enabled=False가 기본인 이유: sentence-transformers는 무거운 의존성이라
# 사용자가 명시적으로 켜야 한다(첫 인코딩 시 모델 다운로드 ~500MB 발생).
_MEMORY_DEFAULTS: dict = {
    "rag": {
        "enabled": False,
        "model": "intfloat/multilingual-e5-small",
        "top_k": 5,
        "similarity_threshold": 0.5,
    },
    "long_term": {
        "enabled": True,
        "top_k": 3,
        "min_confidence": 0.7,
        "promotion_threshold": 3,
        "context_budget_chars": 1600,
        "per_item_chars": 400,
        "insights_file": "~/.simpleclaw-agent/default/insights.jsonl",
        "active_projects_file": "~/.simpleclaw-agent/default/active_projects.jsonl",
        "active_projects_window_days": 7,
    },
}


def load_memory_config(config_path: str | Path) -> dict:
    """config.yaml에서 시맨틱 메모리(RAG) 설정을 로드한다.

    파일이 없거나 memory 키가 없으면 기본값(RAG 비활성)을 반환한다.
    """
    def _default_memory() -> dict:
        return {
            "rag": dict(_MEMORY_DEFAULTS["rag"]),
            "long_term": dict(_MEMORY_DEFAULTS["long_term"]),
        }

    config_path = Path(config_path)
    if not config_path.is_file():
        return _default_memory()

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return _default_memory()

    if not isinstance(data, dict):
        return _default_memory()

    memory = data.get("memory", {})
    if not isinstance(memory, dict):
        return _default_memory()

    rag = memory.get("rag", {})
    if not isinstance(rag, dict):
        rag = {}
    long_term = memory.get("long_term", {})
    if not isinstance(long_term, dict):
        long_term = {}

    return {
        "rag": {
            "enabled": rag.get("enabled", _MEMORY_DEFAULTS["rag"]["enabled"]),
            "model": rag.get("model", _MEMORY_DEFAULTS["rag"]["model"]),
            "top_k": rag.get("top_k", _MEMORY_DEFAULTS["rag"]["top_k"]),
            "similarity_threshold": rag.get(
                "similarity_threshold",
                _MEMORY_DEFAULTS["rag"]["similarity_threshold"],
            ),
        },
        "long_term": {
            key: long_term.get(key, value)
            for key, value in _MEMORY_DEFAULTS["long_term"].items()
        },
    }
