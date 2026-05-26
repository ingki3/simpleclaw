"""BIZ-299 — Dreaming 파이프라인 파일별 분리 회귀 가드.

검증 대상:

1. ``run()`` 한 회차에 LLM 호출이 정확히 5회(memory/user/soul/agent/active_projects)
   발사된다. 각 호출의 ``LLMRequest`` 는 (a) 해당 파일의 YAML system_prompt 를
   쓰고, (b) ``dreaming.max_tokens.{key}`` 값을 ``max_tokens`` 로 박는다.
2. 한 호출이 raise 하면 사이클 전체가 abort 된다 — markdown 파일 어느 것도
   변경되지 않고, ``DreamingRunStore`` 행에 error 가 기록된다 (fail-closed).
3. ``[:8000]`` 입력 truncation 이 제거됐다 — 8000자보다 긴 대화가 그대로 LLM
   user_message 에 흘러간다.
4. 정상 종료 시 ``DreamingRunStore`` 행의 ``details["per_file"]`` 에 호출별
   duration_ms / max_tokens 가 영속된다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.dreaming_runs import DreamingRunStore
from simpleclaw.memory.models import ConversationMessage, MessageRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_managed_files(tmp_path):
    """4종 markdown 파일에 dreaming managed 마커를 세팅해 preflight 를 통과시킨다.

    Active-projects 섹션은 USER.md 의 별도 섹션으로 부착한다 — fail-closed
    preflight 가 active_projects 활성 시 이 섹션도 요구한다.
    """
    memory = tmp_path / "MEMORY.md"
    memory.write_text(
        "# Memory\n\n"
        "<!-- managed:dreaming:journal -->\n"
        "<!-- /managed:dreaming:journal -->\n"
    )
    user = tmp_path / "USER.md"
    user.write_text(
        "# User\n\n"
        "<!-- managed:dreaming:insights -->\n"
        "<!-- /managed:dreaming:insights -->\n\n"
        "<!-- managed:dreaming:active-projects -->\n"
        "<!-- /managed:dreaming:active-projects -->\n"
    )
    soul = tmp_path / "SOUL.md"
    soul.write_text(
        "# Soul\n\n"
        "<!-- managed:dreaming:dreaming-updates -->\n"
        "<!-- /managed:dreaming:dreaming-updates -->\n"
    )
    agent = tmp_path / "AGENT.md"
    agent.write_text(
        "# Agent\n\n"
        "<!-- managed:dreaming:dreaming-updates -->\n"
        "<!-- /managed:dreaming:dreaming-updates -->\n"
    )
    return {"memory": memory, "user": user, "soul": soul, "agent": agent}


def _per_file_responses():
    """memory/user/soul/agent/active_projects 각 호출에 대한 모의 응답 5개."""
    memory_resp = MagicMock()
    memory_resp.text = '{"memory": "## d\\n- learned"}'
    memory_resp.usage = {"input_tokens": 111, "output_tokens": 22}

    user_resp = MagicMock()
    user_resp.text = (
        '{"user_insights": "- 새 정보", '
        '"user_insights_meta": [{"topic": "주제", "text": "새 정보"}]}'
    )
    user_resp.usage = {"input_tokens": 121, "output_tokens": 33}

    soul_resp = MagicMock()
    soul_resp.text = '{"soul_updates": "- 반말 사용"}'
    soul_resp.usage = {"input_tokens": 131, "output_tokens": 11}

    agent_resp = MagicMock()
    agent_resp.text = '{"agent_updates": "- 캘린더 추가"}'
    agent_resp.usage = {"input_tokens": 141, "output_tokens": 12}

    ap_resp = MagicMock()
    ap_resp.text = (
        '{"active_projects": [{"name": "Foo", "role": "owner", '
        '"recent_summary": "kickoff"}]}'
    )
    ap_resp.usage = {"input_tokens": 151, "output_tokens": 44}
    return [memory_resp, user_resp, soul_resp, agent_resp, ap_resp]


def _pipeline_with_router(tmp_path, *, max_tokens=None, ap_sidecar=True):
    files = _seed_managed_files(tmp_path)
    store = ConversationStore(tmp_path / "conv.db")
    pipeline = DreamingPipeline(
        conversation_store=store,
        memory_file=files["memory"],
        user_file=files["user"],
        soul_file=files["soul"],
        agent_file=files["agent"],
        active_projects_file=(tmp_path / "active.jsonl") if ap_sidecar else None,
        runs_file=tmp_path / "dreaming_runs.jsonl",
        max_tokens=max_tokens,
    )
    return store, pipeline, files


# ---------------------------------------------------------------------------
# 1) 5회 호출 + system_prompt / max_tokens 가 각 파일별로 정확히 적용
# ---------------------------------------------------------------------------


class TestPerFileDispatch:
    @pytest.mark.asyncio
    async def test_five_distinct_llm_calls_with_per_file_max_tokens(self, tmp_path):
        """run() 한 회차에 5 호출이 발사되고, 각자 다른 system_prompt + max_tokens."""
        max_tokens = {
            "memory": 2048,
            "user": 1024,
            "soul": 512,
            "agent": 512,
            "active_projects": 768,
        }
        store, pipeline, _ = _pipeline_with_router(tmp_path, max_tokens=max_tokens)
        router = MagicMock()
        router.send = AsyncMock(side_effect=_per_file_responses())
        pipeline._router = router

        store.add_message(
            ConversationMessage(role=MessageRole.USER, content="hello")
        )
        await pipeline.run()

        # 정확히 5 호출.
        assert router.send.call_count == 5

        # 각 호출의 LLMRequest 를 꺼내 (a) system_prompt 가 YAML 별로 다른지
        # (b) max_tokens 가 dreaming.max_tokens.{key} 와 일치하는지 확인.
        requests = [c.args[0] for c in router.send.call_args_list]
        from simpleclaw.memory.prompt_loader import load_dreaming_prompt

        expected = [
            ("memory", 2048),
            ("user", 1024),
            ("soul", 512),
            ("agent", 512),
            ("active_projects", 768),
        ]
        for req, (name, tok) in zip(requests, expected):
            spec = load_dreaming_prompt(name)
            assert req.system_prompt == spec.system_prompt, name
            assert req.max_tokens == tok, name

    @pytest.mark.asyncio
    async def test_missing_max_tokens_keys_fall_back_to_provider_default(
        self, tmp_path
    ):
        """``max_tokens`` 인자가 부분만 채워지면 누락 키는 ``None`` 으로 떨어진다."""
        max_tokens = {"memory": 4096}  # user/soul/agent/active_projects 누락
        store, pipeline, _ = _pipeline_with_router(tmp_path, max_tokens=max_tokens)
        router = MagicMock()
        router.send = AsyncMock(side_effect=_per_file_responses())
        pipeline._router = router

        store.add_message(
            ConversationMessage(role=MessageRole.USER, content="hi")
        )
        await pipeline.run()

        requests = [c.args[0] for c in router.send.call_args_list]
        # memory 만 4096, 나머지는 None (프로바이더 기본값 사용).
        assert requests[0].max_tokens == 4096
        for req in requests[1:]:
            assert req.max_tokens is None

    @pytest.mark.asyncio
    async def test_active_projects_falls_back_to_user_cap(self, tmp_path):
        """``active_projects`` 키 누락 시 ``user`` cap 으로 떨어진다 (USER.md 산출물)."""
        max_tokens = {"user": 1234}  # active_projects 누락
        store, pipeline, _ = _pipeline_with_router(tmp_path, max_tokens=max_tokens)
        router = MagicMock()
        router.send = AsyncMock(side_effect=_per_file_responses())
        pipeline._router = router

        store.add_message(
            ConversationMessage(role=MessageRole.USER, content="x")
        )
        await pipeline.run()

        active_projects_request = router.send.call_args_list[4].args[0]
        assert active_projects_request.max_tokens == 1234


# ---------------------------------------------------------------------------
# 2) 입력 truncation 제거 — 8000+ 자 입력이 그대로 흐른다
# ---------------------------------------------------------------------------


class TestNoInputTruncation:
    @pytest.mark.asyncio
    async def test_long_input_passed_verbatim_to_each_llm_call(self, tmp_path):
        """8000자보다 긴 대화가 ``user_message`` 에 그대로 들어간다 — [:8000] 제거 검증."""
        store, pipeline, _ = _pipeline_with_router(tmp_path)
        router = MagicMock()
        router.send = AsyncMock(side_effect=_per_file_responses())
        pipeline._router = router

        long_text = "abcdefghij" * 1100  # 11000 chars
        store.add_message(
            ConversationMessage(role=MessageRole.USER, content=long_text)
        )
        await pipeline.run()

        # 5 호출 모두에서 user_message 에 11000 자 본문이 그대로 들어 있어야 한다.
        for c in router.send.call_args_list:
            req = c.args[0]
            assert long_text in req.user_message, (
                "LLM user_message lost long input — [:8000] truncation regressed"
            )


# ---------------------------------------------------------------------------
# 3) Fail-closed — 한 호출 실패 시 전체 abort + markdown 무변경 + error 메트릭
# ---------------------------------------------------------------------------


class TestFailClosedOnAnyCallFailure:
    @pytest.mark.asyncio
    async def test_failure_in_third_call_aborts_cycle_and_records_error(
        self, tmp_path
    ):
        """soul 호출이 실패하면 markdown 어느 것도 변경되지 않고 error 가 기록된다."""
        store, pipeline, files = _pipeline_with_router(tmp_path)
        before_snapshots = {
            name: p.read_text(encoding="utf-8") for name, p in files.items()
        }

        memory_resp, user_resp, _soul_resp, _agent_resp, _ap_resp = _per_file_responses()
        router = MagicMock()
        # memory 와 user 는 성공, soul 에서 RuntimeError. 이후 호출은 발사되지 않아야 한다.
        router.send = AsyncMock(
            side_effect=[memory_resp, user_resp, RuntimeError("soul boom")]
        )
        pipeline._router = router

        store.add_message(
            ConversationMessage(role=MessageRole.USER, content="hello")
        )
        result = await pipeline.run()

        # run() 은 None 을 반환하고, 모든 markdown 은 사이클 이전과 동일해야 한다.
        assert result is None
        for name, p in files.items():
            assert p.read_text(encoding="utf-8") == before_snapshots[name], name

        # soul 에서 실패했으므로 active_projects/agent 호출은 시도되지 않았다.
        assert router.send.call_count == 3

        # DreamingRunStore 행에 error 가 기록되고 per_file 메트릭에 실패가 노출된다.
        runs = DreamingRunStore(tmp_path / "dreaming_runs.jsonl").load()
        assert len(runs) >= 1
        last = runs[-1]
        assert last.status == "error"
        assert "RuntimeError" in (last.error or "")
        per_file = last.details.get("per_file") or {}
        assert "soul" in per_file
        assert "RuntimeError" in (per_file["soul"].get("error") or "")


# ---------------------------------------------------------------------------
# 4) 성공 사이클에서 per_file 메트릭(duration_ms / max_tokens) 가 영속된다
# ---------------------------------------------------------------------------


class TestPerFileMetricsPersisted:
    @pytest.mark.asyncio
    async def test_success_records_per_file_duration_and_max_tokens(self, tmp_path):
        max_tokens = {
            "memory": 100,
            "user": 200,
            "soul": 300,
            "agent": 400,
            "active_projects": 500,
        }
        store, pipeline, _ = _pipeline_with_router(tmp_path, max_tokens=max_tokens)
        router = MagicMock()
        router.send = AsyncMock(side_effect=_per_file_responses())
        pipeline._router = router

        store.add_message(
            ConversationMessage(role=MessageRole.USER, content="hi")
        )
        await pipeline.run()

        runs = DreamingRunStore(tmp_path / "dreaming_runs.jsonl").load()
        assert len(runs) >= 1
        last = runs[-1]
        assert last.status == "success"
        per_file = last.details.get("per_file") or {}
        for key in ("memory", "user", "soul", "agent", "active_projects"):
            assert key in per_file, f"per_file missing {key!r}"
            assert "duration_ms" in per_file[key]
            assert per_file[key]["max_tokens"] == max_tokens[key]
        # 프로바이더가 채워준 토큰 사용량도 그대로 보존.
        assert per_file["memory"]["input_tokens"] == 111
        assert per_file["memory"]["output_tokens"] == 22
