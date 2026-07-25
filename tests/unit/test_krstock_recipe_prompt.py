"""한국장 장마감 요약 live asset 품질 회귀 테스트.

`krstock` 레시피와 `kr-stock-skill`은 SimpleClaw 저장소 밖의 live asset으로 운용된다.
이 테스트는 로컬 운영 환경에서 해당 asset이 미국장 요약 수준의 상세 장마감 리포트를
요구하는지 검증하고, CI처럼 live asset이 없는 환경에서는 명시적으로 skip한다.
"""

from pathlib import Path

import pytest

KRSTOCK_RECIPE = Path("/Users/simplist/.simpleclaw-agent/default/recipes/krstock/recipe.yaml")
KRSTOCK_SKILL = Path("/Users/simplist/.agents/skills/kr-stock-skill/SKILL.md")


def _read_live_asset(path: Path) -> str:
    """live asset이 있는 운영 머신에서만 내용을 읽는다."""
    if not path.exists():
        pytest.skip(f"live SimpleClaw asset not present: {path}")
    return path.read_text(encoding="utf-8")


def test_krstock_recipe_requires_detailed_close_report_sections() -> None:
    """장마감 요약이 짧은 숫자 나열로 퇴화하지 않도록 핵심 섹션을 고정한다."""
    content = _read_live_asset(KRSTOCK_RECIPE)

    required_fragments = [
        "장마감 상세 리포트 모드",
        "오늘 한국장은 한 줄로",
        "주요 시장 드라이버",
        "대형주·특징주 집중 점검",
        "다음 거래일 체크리스트",
        "최소 1,800~2,500자",
        "확인된 사실 / 미확보 영역 / 관전 포인트",
        "코스피(KS11)",
        "코스닥(KQ11)",
        "USD/KRW",
    ]

    missing = [fragment for fragment in required_fragments if fragment not in content]
    assert missing == []


def test_krstock_recipe_requires_market_summary_first() -> None:
    """krstock recipe는 구조화 summary를 개별 quote보다 먼저 요구해야 한다."""
    content = _read_live_asset(KRSTOCK_RECIPE)

    required_fragments = [
        "market-summary --json",
        "구조화 요약",
        "필수 조회보다 먼저",
        "수급/업종/거래대금/breadth",
    ]

    missing = [fragment for fragment in required_fragments if fragment not in content]
    assert missing == []


def test_krstock_recipe_unavailable_data_guardrails() -> None:
    """미확보 정량 데이터는 데이터 미확보로 남기고 뉴스 숫자를 승격하지 못하게 한다."""
    content = _read_live_asset(KRSTOCK_RECIPE)

    required_fragments = [
        "수급/업종/거래대금/breadth",
        "데이터 미확보",
        "status: unavailable",
        "뉴스 숫자를 구조화 수치로 승격하지 마",
        "임의 값을 만들지 마",
    ]

    missing = [fragment for fragment in required_fragments if fragment not in content]
    assert missing == []


def test_krstock_skill_documents_close_summary_workflow() -> None:
    """skill 문서에도 장마감 요약용 조회·작성 workflow를 남겨 recipe와 정합성을 맞춘다."""
    content = _read_live_asset(KRSTOCK_SKILL)

    required_fragments = [
        "For “한국장 장마감 요약”",
        "market-summary --json",
        "quote --symbol KS11",
        "quote --symbol KQ11",
        "quote --symbol USD/KRW",
        "news-search-skill은 숫자 source of truth가 아니라",
        "시장 드라이버",
        "다음 거래일 체크리스트",
        "1,800~2,500자",
        "status: unavailable",
    ]

    missing = [fragment for fragment in required_fragments if fragment not in content]
    assert missing == []
