"""trace_context 모듈 단위 테스트.

contextvars 기반 trace_id 전파, 환경변수 주입/채택, ``trace_scope`` 컨텍스트
매니저 동작을 검증한다.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from simpleclaw.logging.trace_context import (
    TRACE_ID_ENV_VAR,
    adopt_env_trace_id,
    get_trace_id,
    inject_trace_id_env,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
    trace_scope,
)


@pytest.fixture(autouse=True)
def _clear_trace_state(monkeypatch):
    """각 테스트 시작 시 trace 컨텍스트와 env를 깨끗한 상태로 보장한다."""
    monkeypatch.delenv(TRACE_ID_ENV_VAR, raising=False)
    token = set_trace_id("")
    yield
    reset_trace_id(token)


class TestTraceContext:
    def test_default_is_empty(self):
        assert get_trace_id() == ""

    def test_new_trace_id_is_unique_and_set(self):
        tid1 = new_trace_id()
        assert tid1
        assert get_trace_id() == tid1
        tid2 = new_trace_id()
        assert tid2 != tid1
        assert get_trace_id() == tid2

    def test_set_and_reset(self):
        token = set_trace_id("explicit-id")
        assert get_trace_id() == "explicit-id"
        reset_trace_id(token)
        assert get_trace_id() == ""

    def test_trace_scope_yields_and_restores(self):
        token = set_trace_id("outer")
        try:
            with trace_scope() as tid:
                assert tid
                assert get_trace_id() == tid
            # with 블록 종료 시 이전 값으로 복원
            assert get_trace_id() == "outer"
        finally:
            reset_trace_id(token)

    def test_trace_scope_with_explicit_id(self):
        with trace_scope("custom-trace") as tid:
            assert tid == "custom-trace"
            assert get_trace_id() == "custom-trace"

    @pytest.mark.asyncio
    async def test_propagates_to_async_tasks(self):
        """asyncio.create_task로 띄운 자식 태스크가 부모의 trace_id를 본다."""
        with trace_scope("parent-trace"):
            seen: dict[str, str] = {}

            async def child():
                seen["child"] = get_trace_id()

            await asyncio.create_task(child())
            assert seen["child"] == "parent-trace"


class TestEnvInjection:
    def test_inject_when_trace_set(self):
        with trace_scope("xyz"):
            env = inject_trace_id_env({"FOO": "bar"})
            assert env[TRACE_ID_ENV_VAR] == "xyz"
            assert env["FOO"] == "bar"

    def test_inject_noop_when_unset(self):
        # 컨텍스트에 trace_id가 없으면 빈 변수를 만들지 않는다
        env = inject_trace_id_env({"FOO": "bar"})
        assert TRACE_ID_ENV_VAR not in env

    def test_inject_does_not_overwrite(self):
        with trace_scope("ctx-trace"):
            env = {TRACE_ID_ENV_VAR: "preset"}
            result = inject_trace_id_env(env)
            assert result[TRACE_ID_ENV_VAR] == "preset"

    def test_adopt_env_picks_up(self, monkeypatch):
        monkeypatch.setenv(TRACE_ID_ENV_VAR, "from-env")
        adopted = adopt_env_trace_id()
        assert adopted == "from-env"
        assert get_trace_id() == "from-env"

    def test_adopt_env_empty_does_not_override(self, monkeypatch):
        with trace_scope("existing"):
            monkeypatch.delenv(TRACE_ID_ENV_VAR, raising=False)
            adopted = adopt_env_trace_id()
            assert adopted == ""
            # 환경변수가 비어 있으면 컨텍스트를 덮어쓰지 않음
            assert get_trace_id() == "existing"
