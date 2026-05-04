"""Unit tests for the Protected Section model (BIZ-72).

이 테스트는 다음 세 종류의 invariant를 모두 커버한다:
1. 정상 마커 안 본문은 안전하게 갱신된다.
2. 마커 외부 영역은 어떤 호출에서도 byte-for-byte 보존된다.
3. 마커가 누락·오염된 경우 ProtectedSectionError가 던져져 호출자가 fail-closed 처리할 수 있다.
"""

from __future__ import annotations

import pytest

from simpleclaw.memory.protected_section import (
    ManagedSection,
    ProtectedSectionMalformed,
    ProtectedSectionMissing,
    append_to_section,
    build_initial_template,
    ensure_initialized,
    find_managed_sections,
    get_managed_section,
    get_section_body,
    has_managed_section,
    replace_section_body,
)


# ---------------------------------------------------------------------------
# find_managed_sections — 파싱·검증
# ---------------------------------------------------------------------------


class TestFindManagedSections:
    def test_no_markers_returns_empty(self):
        assert find_managed_sections("# User\n\n## Preferences\n- ko\n") == []

    def test_single_section(self):
        text = (
            "# User\n"
            "\n"
            "<!-- managed:dreaming:insights -->\n"
            "- item 1\n"
            "<!-- /managed:dreaming:insights -->\n"
        )
        sections = find_managed_sections(text)
        assert len(sections) == 1
        assert sections[0].name == "insights"

    def test_body_offset_excludes_markers(self):
        text = "<!-- managed:dreaming:foo -->\nbody\n<!-- /managed:dreaming:foo -->\n"
        sec = find_managed_sections(text)[0]
        # 본문은 marker 사이의 모든 문자(앞뒤 \n 포함)를 포함한다 — 포맷 정규화는 replace 시에 한다
        body = text[sec.body_offset : sec.body_end_offset]
        assert "body" in body

    def test_multiple_sections_in_order(self):
        text = (
            "<!-- managed:dreaming:journal -->\n"
            "j\n"
            "<!-- /managed:dreaming:journal -->\n"
            "between\n"
            "<!-- managed:dreaming:clusters -->\n"
            "c\n"
            "<!-- /managed:dreaming:clusters -->\n"
        )
        sections = find_managed_sections(text)
        assert [s.name for s in sections] == ["journal", "clusters"]

    def test_marker_with_extra_whitespace(self):
        # ``<!--   managed:dreaming:foo  -->`` 같은 공백 변형도 인식 — 인간이 손으로 편집하다
        # 공백을 약간 추가해도 파이프라인이 깨지지 않게 한다
        text = "<!--  managed:dreaming:foo  -->\nbody\n<!--   /managed:dreaming:foo  -->\n"
        sections = find_managed_sections(text)
        assert len(sections) == 1
        assert sections[0].name == "foo"

    def test_unclosed_section_raises(self):
        text = "<!-- managed:dreaming:foo -->\nbody\n"
        with pytest.raises(ProtectedSectionMalformed, match="닫히지 않은"):
            find_managed_sections(text)

    def test_orphan_end_marker_raises(self):
        text = "<!-- /managed:dreaming:foo -->\n"
        with pytest.raises(ProtectedSectionMalformed, match="매칭되는 시작"):
            find_managed_sections(text)

    def test_mismatched_names_raises(self):
        text = (
            "<!-- managed:dreaming:foo -->\n"
            "body\n"
            "<!-- /managed:dreaming:bar -->\n"
        )
        with pytest.raises(ProtectedSectionMalformed, match="짝 불일치"):
            find_managed_sections(text)

    def test_nested_sections_raises(self):
        text = (
            "<!-- managed:dreaming:outer -->\n"
            "<!-- managed:dreaming:inner -->\n"
            "body\n"
            "<!-- /managed:dreaming:inner -->\n"
            "<!-- /managed:dreaming:outer -->\n"
        )
        with pytest.raises(ProtectedSectionMalformed, match="중첩 금지"):
            find_managed_sections(text)

    def test_duplicate_section_names_raises(self):
        text = (
            "<!-- managed:dreaming:foo -->\n"
            "body1\n"
            "<!-- /managed:dreaming:foo -->\n"
            "<!-- managed:dreaming:foo -->\n"
            "body2\n"
            "<!-- /managed:dreaming:foo -->\n"
        )
        with pytest.raises(ProtectedSectionMalformed, match="여러 번 정의"):
            find_managed_sections(text)

    # BIZ-104 회귀: 운영자가 .agent/*.md 파일 최상단 doc 주석 안에 marker 토큰을 *문서 설명용*
    # 으로 그대로 적은 경우(.agent/MEMORY.md, AGENT.md, SOUL.md 의 실제 사고 케이스)에도
    # marker 파싱이 outer 주석을 진짜 marker 로 오인하지 않아야 한다. 잘못 처리되면
    # "같은 이름의 managed 섹션이 여러 번 정의됨" 으로 dreaming 이 fail-closed 된다.
    def test_marker_text_inside_outer_comment_is_ignored(self):
        text = (
            "# Memory\n"
            "\n"
            "<!--\n"
            "이 파일은 두 영역으로 구성된다:\n"
            "1. <!-- managed:dreaming:journal --> ~ <!-- /managed:dreaming:journal -->: 일자별 사실.\n"
            "2. <!-- managed:dreaming:clusters --> ~ <!-- /managed:dreaming:clusters -->: 클러스터.\n"
            "-->\n"
            "\n"
            "<!-- managed:dreaming:journal -->\n"
            "## 2026-04-28\n"
            "- entry\n"
            "<!-- /managed:dreaming:journal -->\n"
            "\n"
            "<!-- managed:dreaming:clusters -->\n"
            "<!-- /managed:dreaming:clusters -->\n"
        )
        # outer doc 주석이 첫 inner `-->` 에서 닫히는 HTML 명세를 그대로 반영한다 —
        # 단순 finditer 라면 같은 이름이 여러 번 나타나는 것으로 잘못 잡혀 실패한다.
        sections = find_managed_sections(text)
        assert [s.name for s in sections] == ["journal", "clusters"]

    def test_real_world_memory_md_doc_block_does_not_fail_preflight(self):
        # 운영 .agent/MEMORY.md 의 실제 doc 블록(BIZ-104 발견 시점)을 그대로 입력으로
        # 사용해, 같은 dreaming 사이클이 preflight 단계에서 트랩 되지 않음을 보장한다.
        # backtick 으로 escape 한 형태가 운영 진실의 출처이고, 이 테스트는 escape 가
        # 풀린 회귀(=원본 사고)에서도 preflight 가 통과해야 함을 강제한다.
        text = (
            "# Memory\n"
            "\n"
            "<!--\n"
            "SimpleClaw의 일자별 핵심 기억(MEMORY.md). 두 영역으로 구성된다:\n"
            "\n"
            "1. 마커 외부 영역:\n"
            "   - 사용자가 직접 적은 메모/맥락. 드리밍은 절대 손대지 않는다.\n"
            "\n"
            "2. <!-- managed:dreaming:journal --> ~ <!-- /managed:dreaming:journal -->:\n"
            "   - 드리밍 사이클이 일자별 사실/이벤트를 append하는 영역.\n"
            "\n"
            "3. <!-- managed:dreaming:clusters --> ~ <!-- /managed:dreaming:clusters -->:\n"
            "   - Phase 3 그래프형 드리밍이 활성화된 경우 클러스터 섹션이 upsert된다.\n"
            "-->\n"
            "\n"
            "<!-- managed:dreaming:journal -->\n"
            "## 2026-04-28\n"
            "- 어떤 사실\n"
            "<!-- /managed:dreaming:journal -->\n"
            "\n"
            "<!-- managed:dreaming:clusters -->\n"
            "<!-- /managed:dreaming:clusters -->\n"
        )
        sections = find_managed_sections(text)
        assert {s.name for s in sections} == {"journal", "clusters"}


# ---------------------------------------------------------------------------
# get_managed_section / get_section_body — 단일 섹션 조회
# ---------------------------------------------------------------------------


class TestGetManagedSection:
    def test_returns_matching_section(self):
        text = (
            "<!-- managed:dreaming:foo -->\nfoo body\n<!-- /managed:dreaming:foo -->\n"
            "<!-- managed:dreaming:bar -->\nbar body\n<!-- /managed:dreaming:bar -->\n"
        )
        sec = get_managed_section(text, "bar")
        assert isinstance(sec, ManagedSection)
        assert sec.name == "bar"

    def test_missing_section_raises(self):
        text = "<!-- managed:dreaming:foo -->\nbody\n<!-- /managed:dreaming:foo -->\n"
        with pytest.raises(ProtectedSectionMissing, match="bar"):
            get_managed_section(text, "bar")

    def test_get_body_returns_content_between_markers(self):
        text = (
            "header before\n"
            "<!-- managed:dreaming:foo -->\n"
            "- a\n- b\n"
            "<!-- /managed:dreaming:foo -->\n"
            "footer after\n"
        )
        body = get_section_body(text, "foo")
        # 줄바꿈이 포함될 수 있으므로 strip 후 비교
        assert body.strip("\n") == "- a\n- b"


# ---------------------------------------------------------------------------
# replace_section_body — 마커 외부 보존
# ---------------------------------------------------------------------------


class TestReplaceSectionBody:
    def test_replaces_only_inside_markers(self):
        text = (
            "# Header — user owned\n"
            "## Preferences\n"
            "- ko\n"
            "\n"
            "<!-- managed:dreaming:insights -->\n"
            "old body\n"
            "<!-- /managed:dreaming:insights -->\n"
            "\n"
            "## Footer — also user owned\n"
        )
        new_text = replace_section_body(text, "insights", "fresh body")
        assert "fresh body" in new_text
        assert "old body" not in new_text
        # 마커 외부는 byte-for-byte 보존
        assert "# Header — user owned" in new_text
        assert "## Preferences" in new_text
        assert "## Footer — also user owned" in new_text
        assert "<!-- managed:dreaming:insights -->" in new_text
        assert "<!-- /managed:dreaming:insights -->" in new_text

    def test_outside_text_is_byte_for_byte_preserved(self):
        # 비-ASCII, 다중 줄바꿈, trailing 공백까지 포함해 정확 보존을 검증
        outside_before = "# 用户\n\n  spaced  \n## section\n"
        outside_after = "\n\n## tail\n  \n"
        text = (
            outside_before
            + "<!-- managed:dreaming:x -->\n"
            + "old\n"
            + "<!-- /managed:dreaming:x -->"
            + outside_after
        )
        new_text = replace_section_body(text, "x", "new content here")
        # 마커 위·아래 외부 영역이 입력과 정확히 일치
        assert new_text.startswith(outside_before + "<!-- managed:dreaming:x -->")
        assert new_text.endswith("<!-- /managed:dreaming:x -->" + outside_after)

    def test_replace_with_empty_body(self):
        text = "<!-- managed:dreaming:x -->\nold\n<!-- /managed:dreaming:x -->\n"
        new_text = replace_section_body(text, "x", "")
        # 빈 본문이면 마커만 인접해 남는다
        assert new_text == "<!-- managed:dreaming:x -->\n<!-- /managed:dreaming:x -->\n"

    def test_replace_normalizes_extra_newlines(self):
        text = "<!-- managed:dreaming:x -->\nold\n<!-- /managed:dreaming:x -->\n"
        new_text = replace_section_body(text, "x", "\n\n\nnew\n\n\n")
        # 입력의 과한 줄바꿈은 정규화되어 단일 \n 으로 둘러싸인다
        assert (
            new_text
            == "<!-- managed:dreaming:x -->\nnew\n<!-- /managed:dreaming:x -->\n"
        )

    def test_replace_missing_section_raises(self):
        text = "no markers here\n"
        with pytest.raises(ProtectedSectionMissing):
            replace_section_body(text, "x", "new")


# ---------------------------------------------------------------------------
# append_to_section — 누적 append
# ---------------------------------------------------------------------------


class TestAppendToSection:
    def test_appends_with_blank_line_separator(self):
        text = (
            "<!-- managed:dreaming:insights -->\n"
            "## Day 1\n- a\n"
            "<!-- /managed:dreaming:insights -->\n"
        )
        new_text = append_to_section(text, "insights", "## Day 2\n- b")
        body = get_section_body(new_text, "insights").strip("\n")
        assert body == "## Day 1\n- a\n\n## Day 2\n- b"

    def test_append_to_empty_section(self):
        text = (
            "<!-- managed:dreaming:insights -->\n"
            "<!-- /managed:dreaming:insights -->\n"
        )
        new_text = append_to_section(text, "insights", "## Day 1\n- first")
        body = get_section_body(new_text, "insights").strip("\n")
        assert body == "## Day 1\n- first"

    def test_append_empty_content_is_noop(self):
        text = (
            "header\n"
            "<!-- managed:dreaming:x -->\nbody\n<!-- /managed:dreaming:x -->\nfooter\n"
        )
        assert append_to_section(text, "x", "") == text
        assert append_to_section(text, "x", "   \n  \n") == text

    def test_append_outside_markers_is_impossible(self):
        # append_to_section은 어떤 호출에서도 마커 외부에 텍스트를 추가할 방법이 없다.
        # 본 테스트는 그 invariant를 임의의 입력으로 검증한다.
        text = (
            "BEFORE_OUTSIDE\n"
            "<!-- managed:dreaming:x -->\n"
            "<!-- /managed:dreaming:x -->\n"
            "AFTER_OUTSIDE\n"
        )
        new_text = append_to_section(text, "x", "## injected\nshould stay inside")
        # 외부 텍스트 위치에 "injected"가 새어나가지 않아야 한다
        before_marker = new_text.split("<!-- managed:dreaming:x -->")[0]
        after_marker = new_text.split("<!-- /managed:dreaming:x -->")[1]
        assert "injected" not in before_marker
        assert "injected" not in after_marker
        assert before_marker == "BEFORE_OUTSIDE\n"
        assert after_marker == "\nAFTER_OUTSIDE\n"

    def test_append_missing_section_raises(self):
        text = "# Memory\n\nuser content\n"  # no markers at all
        with pytest.raises(ProtectedSectionMissing):
            append_to_section(text, "journal", "## new\n- item")

    def test_append_malformed_marker_raises(self):
        text = "<!-- managed:dreaming:x -->\nbody\n"  # unclosed
        with pytest.raises(ProtectedSectionMalformed):
            append_to_section(text, "x", "more")


# ---------------------------------------------------------------------------
# has_managed_section / build_initial_template / ensure_initialized
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_has_managed_section(self):
        text = "<!-- managed:dreaming:foo -->\n<!-- /managed:dreaming:foo -->\n"
        assert has_managed_section(text, "foo") is True
        assert has_managed_section(text, "bar") is False

    def test_has_managed_section_propagates_malformed(self):
        # has_managed_section이 silent False를 반환하면 destructive overwrite의 빌미가 됨 →
        # 의도적으로 예외를 전파해 호출자가 인지하게 한다
        text = "<!-- managed:dreaming:foo -->\nno close\n"
        with pytest.raises(ProtectedSectionMalformed):
            has_managed_section(text, "foo")

    def test_build_initial_template_structure(self):
        tpl = build_initial_template("Memory", ["journal", "clusters"])
        assert "# Memory" in tpl
        assert "<!-- managed:dreaming:journal -->" in tpl
        assert "<!-- /managed:dreaming:journal -->" in tpl
        assert "<!-- managed:dreaming:clusters -->" in tpl
        # 생성된 템플릿은 자체 파싱이 통과해야 한다 — circular 검증
        sections = find_managed_sections(tpl)
        assert [s.name for s in sections] == ["journal", "clusters"]

    def test_ensure_initialized_creates_missing_file(self, tmp_path):
        target = tmp_path / "MEMORY.md"
        created = ensure_initialized(target, "Memory", ["journal"])
        assert created is True
        text = target.read_text(encoding="utf-8")
        assert "# Memory" in text
        assert has_managed_section(text, "journal")

    def test_ensure_initialized_skips_existing_content(self, tmp_path):
        # 이미 사용자 콘텐츠가 있는 파일은 절대 덮어쓰지 않는다 — 자동 마커 삽입은
        # destructive overwrite의 1차 원인이므로 본 모듈은 명시적으로 거부한다
        target = tmp_path / "USER.md"
        target.write_text("# User\n\nimportant manual content\n", encoding="utf-8")
        created = ensure_initialized(target, "User", ["insights"])
        assert created is False
        # 기존 내용 그대로
        assert target.read_text(encoding="utf-8") == "# User\n\nimportant manual content\n"

    def test_ensure_initialized_replaces_empty_file(self, tmp_path):
        target = tmp_path / "blank.md"
        target.write_text("\n  \n", encoding="utf-8")  # whitespace only
        created = ensure_initialized(target, "Blank", ["x"])
        assert created is True
        assert "<!-- managed:dreaming:x -->" in target.read_text(encoding="utf-8")
