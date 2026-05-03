"""Unit tests for the active-projects module (BIZ-74).

본 파일은 dreaming 파이프라인과의 통합과 무관한 ``active_projects.py`` 자체의
계약(데이터 클래스 직렬화, 정규화, sidecar I/O, 병합, 윈도우 필터링, 렌더링)을
회귀로부터 보호한다. 파이프라인 레벨 통합 테스트는
``test_dreaming_active_projects.py``에서 별도로 다룬다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from simpleclaw.memory.active_projects import (
    ActiveProject,
    ActiveProjectStore,
    filter_active,
    merge_projects,
    normalize_name,
    render_section_body,
)


# ----------------------------------------------------------------------
# normalize_name
# ----------------------------------------------------------------------


class TestNormalizeName:
    """이름 정규화의 안정성 — 같은 프로젝트가 표기 차이만으로 다른 키가 되어선 안 된다."""

    def test_lowercases_and_strips_whitespace(self):
        assert normalize_name("  SimpleClaw  ") == "simpleclaw"

    def test_strips_punctuation_and_spaces(self):
        # 영어/한글이 섞인 표기에서도 알파벳·한글·숫자만 남는다.
        assert normalize_name("Simple-Claw v2!") == "simpleclawv2"
        assert normalize_name("심플 클로우") == "심플클로우"

    def test_empty_input_returns_empty(self):
        assert normalize_name("") == ""
        assert normalize_name("   ") == ""

    def test_same_project_via_different_punctuation_collides(self):
        # "SimpleClaw" / "Simple Claw" / "Simple-Claw" 모두 같은 키여야 함.
        a = normalize_name("SimpleClaw")
        b = normalize_name("Simple Claw")
        c = normalize_name("Simple-Claw")
        assert a == b == c


# ----------------------------------------------------------------------
# ActiveProject (de)serialization
# ----------------------------------------------------------------------


class TestActiveProjectSerialization:
    """to_dict / from_dict roundtrip — datetime ↔ ISO 문자열을 잃지 않는다."""

    def test_roundtrip_preserves_all_fields(self):
        original = ActiveProject(
            name="SimpleClaw",
            role="솔로 빌더 — 메모리 파이프라인 개선",
            recent_summary="BIZ-66 sub-issue 분할 후 A/B 머지 리뷰 진행.",
            first_seen=datetime(2026, 4, 28, 10, 0, 0),
            last_seen=datetime(2026, 5, 3, 17, 30, 0),
        )
        d = original.to_dict()
        restored = ActiveProject.from_dict(d)
        assert restored == original

    def test_from_dict_tolerates_missing_fields(self):
        # sidecar가 외부에서 일부 필드만 채워졌을 때 — 합리적 기본값으로 보강.
        restored = ActiveProject.from_dict({"name": "Foo"})
        assert restored.name == "Foo"
        assert restored.role == ""
        assert restored.recent_summary == ""
        assert isinstance(restored.first_seen, datetime)
        assert isinstance(restored.last_seen, datetime)


# ----------------------------------------------------------------------
# ActiveProjectStore (JSONL sidecar)
# ----------------------------------------------------------------------


class TestActiveProjectStore:
    def test_load_returns_empty_when_file_absent(self, tmp_path):
        store = ActiveProjectStore(tmp_path / "nope.jsonl")
        assert store.load() == {}

    def test_save_then_load_roundtrip(self, tmp_path):
        path = tmp_path / "active_projects.jsonl"
        store = ActiveProjectStore(path)
        original = {
            "simpleclaw": ActiveProject(
                name="SimpleClaw",
                role="솔로 빌더",
                recent_summary="요약",
                first_seen=datetime(2026, 4, 28),
                last_seen=datetime(2026, 5, 3),
            ),
            "multica": ActiveProject(
                name="Multica",
                role="플랫폼 빌드/QA",
                recent_summary="요약 2",
                first_seen=datetime(2026, 5, 1),
                last_seen=datetime(2026, 5, 2),
            ),
        }
        store.save_all(original)
        # 파일이 한 줄 = 한 항목 (JSONL).
        lines = [line for line in path.read_text().splitlines() if line]
        assert len(lines) == 2
        # 각 줄은 valid JSON.
        for line in lines:
            json.loads(line)

        reloaded = store.load()
        assert reloaded == original

    def test_load_skips_malformed_lines(self, tmp_path, caplog):
        path = tmp_path / "broken.jsonl"
        good = ActiveProject(
            name="Good", role="r", recent_summary="s",
            first_seen=datetime(2026, 5, 1), last_seen=datetime(2026, 5, 1),
        )
        path.write_text(
            "this is not json\n"
            + json.dumps(good.to_dict(), ensure_ascii=False) + "\n"
            + "{not closed\n",
            encoding="utf-8",
        )
        store = ActiveProjectStore(path)
        loaded = store.load()
        # 손상된 두 줄은 skip, 정상 한 줄만 살아남는다.
        assert list(loaded.keys()) == ["good"]

    def test_save_uses_atomic_rename(self, tmp_path):
        # tmp 파일이 남아있으면 안 되며(rename 후 사라짐), 본 파일이 정상.
        path = tmp_path / "x.jsonl"
        store = ActiveProjectStore(path)
        store.save_all({})  # 빈 dict 도 안전 (파일은 존재하지만 0바이트).
        assert path.is_file()
        assert not path.with_suffix(".jsonl.tmp").exists()


# ----------------------------------------------------------------------
# merge_projects
# ----------------------------------------------------------------------


class TestMergeProjects:
    """병합 규칙 — last_seen 갱신 / first_seen 보존 / 신규 항목 추가."""

    def test_new_observation_creates_entry(self):
        now = datetime(2026, 5, 3, 12, 0, 0)
        merged = merge_projects(
            {},
            [ActiveProject(name="SimpleClaw", role="r", recent_summary="s")],
            now=now,
        )
        assert "simpleclaw" in merged
        sp = merged["simpleclaw"]
        assert sp.first_seen == now
        assert sp.last_seen == now

    def test_existing_project_preserves_first_seen_and_updates_last_seen(self):
        first = datetime(2026, 4, 28, 9, 0, 0)
        existing = {
            "simpleclaw": ActiveProject(
                name="SimpleClaw", role="원래 역할", recent_summary="원래 요약",
                first_seen=first, last_seen=first,
            )
        }
        new = ActiveProject(
            name="SimpleClaw",
            role="새 역할",
            recent_summary="새 요약",
        )
        cycle_now = datetime(2026, 5, 3, 17, 0, 0)

        merged = merge_projects(existing, [new], now=cycle_now)
        sp = merged["simpleclaw"]
        # 핵심 invariant — first_seen 은 절대 늦춰지지 않는다.
        assert sp.first_seen == first
        assert sp.last_seen == cycle_now
        # 가장 최근 관측의 표기 / 역할 / 요약으로 갱신된다.
        assert sp.role == "새 역할"
        assert sp.recent_summary == "새 요약"

    def test_empty_role_or_summary_falls_back_to_existing(self):
        first = datetime(2026, 4, 28)
        existing = {
            "multica": ActiveProject(
                name="Multica", role="기존 역할", recent_summary="기존 요약",
                first_seen=first, last_seen=first,
            )
        }
        # 새 관측이 빈 값이라면 기존 값을 유지한다 — LLM이 한 사이클에서 단편적
        # 정보만 출력해도 메타가 휘발되지 않게 함.
        new = ActiveProject(name="Multica", role="", recent_summary="")
        merged = merge_projects(existing, [new], now=datetime(2026, 5, 3))
        m = merged["multica"]
        assert m.role == "기존 역할"
        assert m.recent_summary == "기존 요약"

    def test_unobserved_existing_entries_are_preserved(self):
        # 이번 회차에 관측되지 않은 기존 항목은 sidecar에 그대로 보존되어야 한다.
        # (윈도우 외부 처리는 filter_active 의 책임이지 merge 의 책임이 아님 — 책임 분리)
        first = datetime(2026, 4, 1)
        existing = {
            "old": ActiveProject(
                name="Old", role="r", recent_summary="s",
                first_seen=first, last_seen=first,
            )
        }
        merged = merge_projects(existing, [], now=datetime(2026, 5, 3))
        assert "old" in merged
        assert merged["old"].last_seen == first  # 관측 없으므로 갱신도 없음

    def test_blank_name_observation_is_ignored(self):
        merged = merge_projects(
            {},
            [ActiveProject(name="   ", role="x", recent_summary="y")],
            now=datetime(2026, 5, 3),
        )
        assert merged == {}

    def test_same_project_with_different_punctuation_merges(self):
        # "Simple-Claw" 와 "SimpleClaw" 는 정규화 후 같은 키여야 한다.
        existing = {
            "simpleclaw": ActiveProject(
                name="SimpleClaw", role="r1", recent_summary="s1",
                first_seen=datetime(2026, 4, 28),
                last_seen=datetime(2026, 4, 28),
            )
        }
        new = ActiveProject(name="Simple-Claw", role="r2", recent_summary="s2")
        merged = merge_projects(existing, [new], now=datetime(2026, 5, 3))
        # 같은 키로 합쳐졌고, 표기는 새 관측("Simple-Claw")으로 갱신된다.
        assert len(merged) == 1
        sp = merged["simpleclaw"]
        assert sp.name == "Simple-Claw"
        assert sp.first_seen == datetime(2026, 4, 28)
        assert sp.last_seen == datetime(2026, 5, 3)


# ----------------------------------------------------------------------
# filter_active
# ----------------------------------------------------------------------


class TestFilterActive:
    def test_includes_only_within_window(self):
        now = datetime(2026, 5, 3)
        projects = {
            "fresh": ActiveProject(
                name="Fresh", role="r", recent_summary="s",
                first_seen=now - timedelta(days=2),
                last_seen=now - timedelta(days=2),
            ),
            "stale": ActiveProject(
                name="Stale", role="r", recent_summary="s",
                first_seen=now - timedelta(days=30),
                last_seen=now - timedelta(days=20),
            ),
        }
        active = filter_active(projects, window_days=7, now=now)
        names = [p.name for p in active]
        assert names == ["Fresh"]

    def test_orders_by_last_seen_descending(self):
        now = datetime(2026, 5, 3)
        projects = {
            "a": ActiveProject(
                name="A", role="r", recent_summary="s",
                first_seen=now - timedelta(days=4),
                last_seen=now - timedelta(days=4),
            ),
            "b": ActiveProject(
                name="B", role="r", recent_summary="s",
                first_seen=now - timedelta(days=2),
                last_seen=now - timedelta(days=1),
            ),
            "c": ActiveProject(
                name="C", role="r", recent_summary="s",
                first_seen=now - timedelta(days=3),
                last_seen=now - timedelta(days=3),
            ),
        }
        active = filter_active(projects, window_days=7, now=now)
        # 최근 활동 순.
        assert [p.name for p in active] == ["B", "C", "A"]

    def test_zero_window_returns_empty(self):
        now = datetime(2026, 5, 3)
        projects = {
            "x": ActiveProject(
                name="X", role="r", recent_summary="s",
                first_seen=now, last_seen=now,
            )
        }
        assert filter_active(projects, window_days=0, now=now) == []
        assert filter_active(projects, window_days=-1, now=now) == []


# ----------------------------------------------------------------------
# render_section_body
# ----------------------------------------------------------------------


class TestRenderSectionBody:
    def test_renders_each_project_as_h2_block_with_role_summary_and_last_seen(self):
        projects = [
            ActiveProject(
                name="SimpleClaw",
                role="솔로 빌더 — 메모리 파이프라인 개선",
                recent_summary="BIZ-66 sub-issue 분할.",
                first_seen=datetime(2026, 4, 28),
                last_seen=datetime(2026, 5, 3, 12, 0, 0),
            ),
            ActiveProject(
                name="Multica",
                role="플랫폼 빌드/QA 운영자",
                recent_summary="릴리스·QA 사이클 운영.",
                first_seen=datetime(2026, 5, 1),
                last_seen=datetime(2026, 5, 2, 9, 0, 0),
            ),
        ]
        body = render_section_body(projects)
        assert "## SimpleClaw" in body
        assert "## Multica" in body
        assert "솔로 빌더 — 메모리 파이프라인 개선" in body
        assert "플랫폼 빌드/QA 운영자" in body
        assert "last_seen: 2026-05-03" in body
        assert "last_seen: 2026-05-02" in body

    def test_empty_list_renders_explicit_empty_message(self):
        # 새 세션에서 "섹션이 통째로 사라짐"보다 "비어있음을 명시"가 디버깅에 유리.
        body = render_section_body([])
        assert "활성 프로젝트가 없습니다" in body

    def test_omits_role_or_summary_when_empty(self):
        projects = [
            ActiveProject(
                name="Bare", role="", recent_summary="",
                first_seen=datetime(2026, 5, 1), last_seen=datetime(2026, 5, 1),
            )
        ]
        body = render_section_body(projects)
        assert "## Bare" in body
        assert "역할:" not in body
        assert "최근 활동:" not in body
        # last_seen 은 항상 표시(메타 추적 단서).
        assert "last_seen: 2026-05-01" in body
