"""Study wiki updater 의 부분 병합 정책 검증.

DoD:
- 기존 page 를 통째로 덮어쓰지 않는다(수동 섹션 보존).
- 관리 섹션("현재 상태"/"최근 업데이트"/.../"Sources")만 갱신한다.
- 저신뢰 항목은 "확인 필요"/open_questions 로 격리한다.
"""

from __future__ import annotations

from simpleclaw.study.wiki_updater import (
    LOW_CONFIDENCE_DISCLAIMER,
    SECTION_NEEDS_CHECK,
    SECTION_RECENT_UPDATES,
    SECTION_SOURCES,
    merge_open_questions,
    merge_study_update,
)


def test_merge_study_update_preserves_manual_notes():
    """이슈 명세의 계약 테스트 — 수동 메모 보존 + 갱신 내용/출처 반영."""
    existing = """# OpenAI

## 수동 메모
- 지우면 안 됨

## 최근 업데이트
- 오래된 항목
"""
    updated = merge_study_update(
        existing,
        topic_title="OpenAI",
        updates=["IPO 연기 보도는 confirmed가 아니라 reported로 표기한다."],
        sources=["https://example.com/openai-ipo"],
    )

    assert "수동 메모" in updated
    assert "reported" in updated
    assert "https://example.com/openai-ipo" in updated


def test_recent_updates_prepend_and_preserve_existing():
    """새 업데이트는 위로 prepend 되고 기존 항목은 아래에 남는다."""
    existing = "# T\n\n## 최근 업데이트\n- 옛날 항목\n"
    updated = merge_study_update(
        existing,
        topic_title="T",
        updates=["새 항목"],
    )
    new_idx = updated.index("새 항목")
    old_idx = updated.index("옛날 항목")
    assert new_idx < old_idx  # 최신이 위
    assert "옛날 항목" in updated  # 기존 보존


def test_new_managed_sections_appended_when_absent():
    """원문에 없던 관리 섹션(Sources 등)은 새로 추가된다."""
    updated = merge_study_update(
        "# T\n",
        topic_title="T",
        updates=["x"],
        sources=["https://a"],
        current_state="지금 상태",
    )
    assert f"## {SECTION_SOURCES}" in updated
    assert f"## {SECTION_RECENT_UPDATES}" in updated
    assert "현재 상태" in updated
    assert "지금 상태" in updated


def test_title_added_when_missing():
    """H1 제목이 없으면 topic_title 로 추가한다."""
    updated = merge_study_update("", topic_title="새 주제", updates=["a"])
    assert updated.startswith("# 새 주제")


def test_existing_title_not_overwritten():
    """이미 H1 이 있으면(수동 권위) 건드리지 않는다."""
    updated = merge_study_update(
        "# 운영자가 정한 제목\n\n## 수동\n- m\n",
        topic_title="다른 제목",
        updates=["a"],
    )
    assert "# 운영자가 정한 제목" in updated
    assert "# 다른 제목" not in updated


def test_low_confidence_routes_to_needs_check_with_disclaimer():
    """저신뢰 업데이트는 '최근 업데이트'가 아니라 '확인 필요'로 가고 면책이 붙는다."""
    updated = merge_study_update(
        "# T\n",
        topic_title="T",
        updates=["검증 안 된 루머"],
        confidence="low",
    )
    assert f"## {SECTION_NEEDS_CHECK}" in updated
    # 저신뢰 항목은 최근 업데이트 섹션에 나타나지 않아야 한다.
    assert SECTION_RECENT_UPDATES not in updated
    assert LOW_CONFIDENCE_DISCLAIMER in updated
    assert "검증 안 된 루머" in updated


def test_sources_dedup_and_accumulate():
    """Sources 는 기존 + 신규를 dedup 누적한다."""
    existing = "# T\n\n## Sources\n- https://a\n"
    updated = merge_study_update(
        existing,
        topic_title="T",
        sources=["https://a", "https://b"],
    )
    assert updated.count("https://a") == 1
    assert "https://b" in updated


def test_unmanaged_sections_kept_in_place():
    """관리 목록 밖 섹션은 원래 위치/내용 그대로 보존된다."""
    existing = (
        "# T\n\n"
        "## 운영자 판단\n- 이건 사람이 씀\n\n"
        "## 현재 상태\n- 자동\n"
    )
    updated = merge_study_update(
        existing,
        topic_title="T",
        current_state="갱신된 상태",
    )
    assert "## 운영자 판단" in updated
    assert "이건 사람이 씀" in updated
    assert "갱신된 상태" in updated


def test_merge_study_update_idempotent_on_repeat():
    """같은 입력을 두 번 병합해도 중복 항목이 쌓이지 않는다."""
    once = merge_study_update(
        "# T\n", topic_title="T", updates=["같은 항목"], sources=["https://a"]
    )
    twice = merge_study_update(
        once, topic_title="T", updates=["같은 항목"], sources=["https://a"]
    )
    assert twice.count("같은 항목") == 1
    assert twice.count("https://a") == 1


def test_timestamp_prefixes_update_line():
    """timestamp 가 주어지면 업데이트 라인에 붙는다."""
    updated = merge_study_update(
        "# T\n",
        topic_title="T",
        updates=["사건"],
        timestamp="2026-06-29",
    )
    assert "2026-06-29 — 사건" in updated


def test_merge_open_questions_accumulates_dedup():
    """open_questions.md 는 미해결 질문을 dedup 누적한다."""
    first = merge_open_questions("", ["질문1", "질문2"])
    assert "# Open Questions" in first
    assert "질문1" in first and "질문2" in first

    second = merge_open_questions(first, ["질문2", "질문3"])
    assert second.count("질문2") == 1
    assert "질문3" in second
