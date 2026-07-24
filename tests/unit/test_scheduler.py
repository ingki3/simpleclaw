"""Tests for the cron scheduler."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from simpleclaw.daemon import scheduler as scheduler_module
from simpleclaw.daemon.models import (
    ActionType,
    BackoffStrategy,
    CronJobNotFoundError,
    ExecutionStatus,
)
from simpleclaw.daemon.scheduler import (
    CronScheduler,
    _compute_backoff,
    _translate_cron_dow_to_apscheduler,
)
from simpleclaw.daemon.store import DaemonStore


class TestCronScheduler:
    @pytest.fixture
    def setup(self, tmp_path):
        store = DaemonStore(tmp_path / "test.db")
        apscheduler = MagicMock(spec=AsyncIOScheduler)
        scheduler = CronScheduler(store, apscheduler)
        return store, apscheduler, scheduler

    def test_add_job(self, setup):
        _, _, scheduler = setup
        job = scheduler.add_job(
            name="test-job",
            cron_expression="0 9 * * *",
            action_type=ActionType.PROMPT,
            action_reference="Hello",
        )
        assert job.name == "test-job"
        assert job.enabled is True

    def test_list_jobs(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("job-1", "0 9 * * *", ActionType.PROMPT, "Hello")
        scheduler.add_job("job-2", "30 8 * * *", ActionType.RECIPE, "recipe.yaml")
        jobs = scheduler.list_jobs()
        assert len(jobs) == 2

    def test_get_job(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("find-me", "0 9 * * *", ActionType.PROMPT, "Hello")
        job = scheduler.get_job("find-me")
        assert job is not None
        assert job.name == "find-me"
        assert scheduler.get_job("nonexistent") is None

    def test_update_job(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("updatable", "0 9 * * *", ActionType.PROMPT, "original")
        updated = scheduler.update_job("updatable", cron_expression="30 8 * * *")
        assert updated.cron_expression == "30 8 * * *"

    def test_update_nonexistent_raises(self, setup):
        _, _, scheduler = setup
        with pytest.raises(CronJobNotFoundError):
            scheduler.update_job("nonexistent", cron_expression="* * * * *")

    def test_remove_job(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("remove-me", "0 9 * * *", ActionType.PROMPT, "test")
        assert scheduler.remove_job("remove-me") is True
        assert scheduler.get_job("remove-me") is None
        assert scheduler.remove_job("nonexistent") is False

    def test_enable_disable_job(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("toggle", "0 9 * * *", ActionType.PROMPT, "test")
        disabled = scheduler.disable_job("toggle")
        assert disabled.enabled is False
        enabled = scheduler.enable_job("toggle")
        assert enabled.enabled is True

    def test_load_persisted_jobs(self, setup):
        store, apscheduler, scheduler = setup
        scheduler.add_job("persisted-1", "0 9 * * *", ActionType.PROMPT, "test")
        scheduler.add_job("persisted-2", "0 10 * * *", ActionType.RECIPE, "recipe")

        # Create a new scheduler instance to test loading
        new_scheduler = CronScheduler(store, apscheduler)
        count = new_scheduler.load_persisted_jobs()
        assert count == 2

    @pytest.mark.asyncio
    async def test_execute_prompt_job(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("exec-test", "0 9 * * *", ActionType.PROMPT, "Say hello")
        execution = await scheduler.execute_job("exec-test")
        assert execution.status.value == "success"
        assert "Prompt scheduled" in execution.result_summary

    @pytest.mark.asyncio
    async def test_execute_nonexistent_raises(self, setup):
        _, _, scheduler = setup
        with pytest.raises(CronJobNotFoundError):
            await scheduler.execute_job("nonexistent")


class TestRetryAndCircuitBreak:
    """BIZ-19: 작업별 재시도 정책과 누적 실패 시 자동 차단 동작 검증."""

    @pytest.fixture
    def setup(self, tmp_path, monkeypatch):
        # 백오프 sleep을 0초로 단축 — 테스트 시간 절약.
        async def _no_sleep(_seconds):
            return None

        monkeypatch.setattr(scheduler_module, "_sleep", _no_sleep)
        store = DaemonStore(tmp_path / "test.db")
        apscheduler = MagicMock(spec=AsyncIOScheduler)
        scheduler = CronScheduler(store, apscheduler)
        return store, apscheduler, scheduler

    def test_compute_backoff_linear(self):
        assert _compute_backoff(60, 1, BackoffStrategy.LINEAR) == 60
        assert _compute_backoff(60, 2, BackoffStrategy.LINEAR) == 120
        assert _compute_backoff(60, 3, BackoffStrategy.LINEAR) == 180

    def test_compute_backoff_exponential(self):
        assert _compute_backoff(60, 1, BackoffStrategy.EXPONENTIAL) == 60
        assert _compute_backoff(60, 2, BackoffStrategy.EXPONENTIAL) == 120
        assert _compute_backoff(60, 3, BackoffStrategy.EXPONENTIAL) == 240

    def test_compute_backoff_zero(self):
        assert _compute_backoff(0, 1, BackoffStrategy.EXPONENTIAL) == 0
        assert _compute_backoff(60, 0, BackoffStrategy.EXPONENTIAL) == 0

    def test_add_job_with_retry_policy(self, setup):
        _, _, scheduler = setup
        job = scheduler.add_job(
            name="retry-job",
            cron_expression="0 9 * * *",
            action_type=ActionType.PROMPT,
            action_reference="hello",
            max_attempts=5,
            backoff_seconds=10,
            backoff_strategy="linear",
            circuit_break_threshold=2,
        )
        assert job.max_attempts == 5
        assert job.backoff_seconds == 10
        assert job.backoff_strategy == BackoffStrategy.LINEAR
        assert job.circuit_break_threshold == 2

    def test_add_job_default_retry_policy(self, setup):
        _, _, scheduler = setup
        job = scheduler.add_job(
            "default-policy", "0 9 * * *", ActionType.PROMPT, "hi"
        )
        # 기본값: 3회/60s/exponential/threshold=5
        assert job.max_attempts == 3
        assert job.backoff_seconds == 60.0
        assert job.backoff_strategy == BackoffStrategy.EXPONENTIAL
        assert job.circuit_break_threshold == 5
        assert job.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, setup):
        store, _, scheduler = setup
        scheduler.add_job(
            "flaky", "0 9 * * *", ActionType.PROMPT, "hi",
            max_attempts=3, backoff_seconds=0,
        )

        calls = {"n": 0}

        async def flaky_action(_job):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return "ok"

        scheduler._run_action = flaky_action

        execution = await scheduler.execute_job("flaky")
        assert execution.status == ExecutionStatus.SUCCESS
        assert execution.attempt == 2

        # 두 개의 실행 레코드가 남아야 함: 시도 1(실패), 시도 2(성공).
        history = store.get_executions("flaky", limit=10)
        statuses = [(e.attempt, e.status) for e in history]
        # get_executions는 최신순 — 시도 2가 먼저.
        assert (2, ExecutionStatus.SUCCESS) in statuses
        assert (1, ExecutionStatus.FAILURE) in statuses

        # 성공 후 누적 실패는 리셋되어야 함.
        job_after = store.get_job("flaky")
        assert job_after.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_retry_exhaustion_increments_consecutive_failures(self, setup):
        store, _, scheduler = setup
        scheduler.add_job(
            "always-fail", "0 9 * * *", ActionType.PROMPT, "x",
            max_attempts=3, backoff_seconds=0,
            circuit_break_threshold=0,  # 차단 비활성으로 카운터만 검증
        )

        async def always_fail(_job):
            raise RuntimeError("boom")

        scheduler._run_action = always_fail

        execution = await scheduler.execute_job("always-fail")
        assert execution.status == ExecutionStatus.FAILURE
        assert execution.attempt == 3
        assert "boom" in execution.error_details

        history = store.get_executions("always-fail", limit=10)
        assert len(history) == 3
        assert all(e.status == ExecutionStatus.FAILURE for e in history)

        job_after = store.get_job("always-fail")
        assert job_after.consecutive_failures == 1
        assert job_after.enabled is True  # threshold=0이므로 비활성되지 않음

    @pytest.mark.asyncio
    async def test_circuit_break_disables_job_and_notifies(self, setup):
        store, apscheduler, scheduler = setup
        notifier = AsyncMock()
        scheduler.set_notifier(notifier)
        scheduler.add_job(
            "burnout", "0 9 * * *", ActionType.PROMPT, "x",
            max_attempts=1, backoff_seconds=0,
            circuit_break_threshold=2,
        )

        async def always_fail(_job):
            raise RuntimeError("api down")

        scheduler._run_action = always_fail

        # 1회차 실패: 카운터=1, 아직 차단되지 않음.
        await scheduler.execute_job("burnout")
        job_after_first = store.get_job("burnout")
        assert job_after_first.consecutive_failures == 1
        assert job_after_first.enabled is True
        notifier.assert_not_called()

        # 2회차 실패: 임계값 도달 → 자동 비활성 + 알림.
        await scheduler.execute_job("burnout")
        job_after_second = store.get_job("burnout")
        assert job_after_second.consecutive_failures == 2
        assert job_after_second.enabled is False
        notifier.assert_called_once()
        args, _ = notifier.call_args
        assert args[0] == "burnout"
        assert "auto-disabled" in args[1]
        # APScheduler에서도 등록 해제되어야 함.
        apscheduler.remove_job.assert_called_with("cron_burnout")

    @pytest.mark.asyncio
    async def test_enable_job_resets_consecutive_failures(self, setup):
        store, _, scheduler = setup
        scheduler.add_job(
            "comeback", "0 9 * * *", ActionType.PROMPT, "x",
            max_attempts=1, circuit_break_threshold=2, backoff_seconds=0,
        )

        async def fail(_job):
            raise RuntimeError("nope")

        scheduler._run_action = fail
        await scheduler.execute_job("comeback")
        # 1회 실패: 카운터=1, 아직 활성.
        assert store.get_job("comeback").consecutive_failures == 1

        # 사용자가 재활성화 → 카운터 리셋.
        reenabled = scheduler.enable_job("comeback")
        assert reenabled.enabled is True
        assert reenabled.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_max_attempts_one_means_no_retry(self, setup):
        _store, _, scheduler = setup
        scheduler.add_job(
            "no-retry", "0 9 * * *", ActionType.PROMPT, "x",
            max_attempts=1,
        )

        calls = {"n": 0}

        async def fail(_job):
            calls["n"] += 1
            raise RuntimeError("once")

        scheduler._run_action = fail
        execution = await scheduler.execute_job("no-retry")
        assert calls["n"] == 1
        assert execution.attempt == 1
        assert execution.status == ExecutionStatus.FAILURE


class TestRecipeDirResolution:
    """BIZ-202: cron action_reference 로 레시피 이름이 올 때 어떤 디렉터리에서 찾는가."""

    @pytest.fixture
    def setup(self, tmp_path):
        from simpleclaw.daemon.models import CronJob
        store = DaemonStore(tmp_path / "test.db")
        apscheduler = MagicMock(spec=AsyncIOScheduler)
        return store, apscheduler, tmp_path, CronJob

    def _write_recipe(self, root, name, description):
        rdir = root / name
        rdir.mkdir(parents=True)
        (rdir / "recipe.yaml").write_text(
            f"name: {name}\ndescription: {description}\nsteps: []\n"
            f"instructions: \"go {name}\"\n"
        )

    @pytest.mark.asyncio
    async def test_resolves_from_configured_recipes_dir(self, setup):
        store, apscheduler, tmp_path, CronJob = setup
        primary = tmp_path / "home" / "recipes"
        self._write_recipe(primary, "krstock", "PRIMARY")

        scheduler = CronScheduler(
            store, apscheduler,
            recipes_dir=primary,
            legacy_recipes_dir=None,
        )

        # 가짜 agent 를 주입해 instructions 분기로 들어가게 한다.
        captured = {}

        class FakeAgent:
            async def process_cron_message(self, instructions):
                captured["instructions"] = instructions
                return "ok"

        scheduler._agent = FakeAgent()
        job = CronJob(
            name="krstock-job",
            cron_expression="0 * * * *",
            action_type=ActionType.RECIPE,
            action_reference="krstock",
        )
        await scheduler._execute_action(job)
        assert captured["instructions"] == "go krstock"

    @pytest.mark.asyncio
    async def test_primary_missing_falls_back_to_legacy_with_warning(
        self, setup, caplog,
    ):
        store, apscheduler, tmp_path, CronJob = setup
        primary = tmp_path / "home" / "recipes"
        primary.mkdir(parents=True)
        legacy = tmp_path / "legacy" / "recipes"
        self._write_recipe(legacy, "old-recipe", "LEGACY")

        scheduler = CronScheduler(
            store, apscheduler,
            recipes_dir=primary,
            legacy_recipes_dir=legacy,
        )

        captured = {}

        class FakeAgent:
            async def process_cron_message(self, instructions):
                captured["instructions"] = instructions
                return "ok"

        scheduler._agent = FakeAgent()
        job = CronJob(
            name="legacy-job",
            cron_expression="0 * * * *",
            action_type=ActionType.RECIPE,
            action_reference="old-recipe",
        )
        with caplog.at_level("WARNING"):
            await scheduler._execute_action(job)
        assert captured["instructions"] == "go old-recipe"
        assert any("DEPRECATED" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_primary_wins_over_legacy_on_name_clash(self, setup):
        """primary 에도, legacy 에도 같은 이름의 레시피가 있으면 primary 가 우선."""
        store, apscheduler, tmp_path, CronJob = setup
        primary = tmp_path / "home" / "recipes"
        legacy = tmp_path / "legacy" / "recipes"
        self._write_recipe(primary, "shared", "PRIMARY")
        # legacy 쪽 instructions 는 다르게 — 어느 쪽이 로드됐는지 식별 가능하게.
        (legacy / "shared").mkdir(parents=True)
        (legacy / "shared" / "recipe.yaml").write_text(
            "name: shared\ndescription: LEGACY\nsteps: []\n"
            "instructions: \"go LEGACY\"\n"
        )

        scheduler = CronScheduler(
            store, apscheduler,
            recipes_dir=primary,
            legacy_recipes_dir=legacy,
        )

        captured = {}

        class FakeAgent:
            async def process_cron_message(self, instructions):
                captured["instructions"] = instructions
                return "ok"

        scheduler._agent = FakeAgent()
        job = CronJob(
            name="shared-job",
            cron_expression="0 * * * *",
            action_type=ActionType.RECIPE,
            action_reference="shared",
        )
        await scheduler._execute_action(job)
        assert captured["instructions"] == "go shared"  # PRIMARY 의 instructions

    def test_default_recipes_dir_is_runtime_root(self):
        """CronScheduler 가 명시적 recipes_dir 없이 생성되면 런타임 recipes 경로를 쓴다."""
        from pathlib import Path
        store = MagicMock()
        apscheduler = MagicMock(spec=AsyncIOScheduler)
        sched = CronScheduler(store, apscheduler)
        assert sched._recipes_dir == Path("~/.simpleclaw-agent/default/recipes").expanduser()


class TestRecipeCronInvokesLLM:
    """BIZ-243 — v1 (steps 기반) RECIPE cron 이 PROMPT step content 를 실제로
    LLM(`process_cron_message`) 으로 흘려보내는지 회귀 방지.

    2026-05-18 cron-krstock-auto 사고: 미지원 키(`tool:`/`prompt:`/`args:`) 가
    무성 폴백되어 빈 content PROMPT step 들이 생성, LLM 호출 없이 SUCCESS 만
    반환 → 빈 'Recipe completed: 2/2' 통지가 사용자에게 노출됨.
    """

    @pytest.fixture
    def setup(self, tmp_path):
        from simpleclaw.daemon.models import CronJob
        store = DaemonStore(tmp_path / "test.db")
        apscheduler = MagicMock(spec=AsyncIOScheduler)
        return store, apscheduler, tmp_path, CronJob

    def _write_v1_recipe(self, root, name, steps_yaml):
        rdir = root / name
        rdir.mkdir(parents=True)
        (rdir / "recipe.yaml").write_text(
            f"name: {name}\n"
            f"description: v1 recipe for {name}\n"
            f"steps:\n{steps_yaml}"
        )

    @pytest.mark.asyncio
    async def test_v1_recipe_with_prompt_step_calls_llm(self, setup):
        """PROMPT step 의 변수 치환된 content 가 process_cron_message 로 흘러간다."""
        store, apscheduler, tmp_path, CronJob = setup
        primary = tmp_path / "recipes"
        self._write_v1_recipe(
            primary, "krstock-like",
            "  - type: prompt\n"
            "    name: summarize\n"
            "    content: \"한국장 시황을 정리해줘\"\n",
        )

        scheduler = CronScheduler(
            store, apscheduler,
            recipes_dir=primary, legacy_recipes_dir=None,
        )

        captured: list[str] = []

        class FakeAgent:
            async def process_cron_message(self, text: str) -> str:
                captured.append(text)
                return "ok"

        scheduler._agent = FakeAgent()
        job = CronJob(
            name="cron-krstock-auto",
            cron_expression="0 16 * * 1-5",
            action_type=ActionType.RECIPE,
            action_reference="krstock-like",
        )
        result = await scheduler._execute_action(job)
        # LLM 이 실제로 한 번 호출되고, PROMPT content 가 입력으로 들어가야 한다.
        assert len(captured) == 1
        assert "한국장 시황" in captured[0]
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_v1_recipe_command_then_prompt_joins_outputs_to_llm(self, setup):
        """COMMAND stdout + PROMPT content 가 합쳐져 단일 LLM 호출로 전달된다."""
        store, apscheduler, tmp_path, CronJob = setup
        primary = tmp_path / "recipes"
        self._write_v1_recipe(
            primary, "two-step",
            "  - type: command\n"
            "    name: gather\n"
            "    content: \"echo MARKET_DATA\"\n"
            "  - type: prompt\n"
            "    name: brief\n"
            "    content: \"위 데이터로 브리핑 작성해줘\"\n",
        )

        scheduler = CronScheduler(
            store, apscheduler,
            recipes_dir=primary, legacy_recipes_dir=None,
        )

        captured: list[str] = []

        class FakeAgent:
            async def process_cron_message(self, text: str) -> str:
                captured.append(text)
                return "briefing"

        scheduler._agent = FakeAgent()
        job = CronJob(
            name="two-step-cron",
            cron_expression="0 9 * * *",
            action_type=ActionType.RECIPE,
            action_reference="two-step",
        )
        await scheduler._execute_action(job)
        assert len(captured) == 1
        # COMMAND 의 stdout 과 PROMPT 의 content 가 모두 LLM 입력에 포함되어야 한다.
        assert "MARKET_DATA" in captured[0]
        assert "브리핑" in captured[0]

    @pytest.mark.asyncio
    async def test_command_only_recipe_no_output_falls_back_without_llm(
        self, setup, caplog,
    ):
        """COMMAND-only 부수효과 레시피(출력 없음)는 LLM 호출 없이 폴백 통지로 가도 정상.

        이 경로는 silent no-op 이 아니라 의도된 동작 — recipe.steps 에 PROMPT 가
        없으므로 WARN 도 발생하지 않는다.
        """
        store, apscheduler, tmp_path, CronJob = setup
        primary = tmp_path / "recipes"
        self._write_v1_recipe(
            primary, "side-effect-only",
            "  - type: command\n"
            "    name: silent\n"
            "    content: \"true\"\n",  # exit 0, stdout empty
        )

        scheduler = CronScheduler(
            store, apscheduler,
            recipes_dir=primary, legacy_recipes_dir=None,
        )

        captured: list[str] = []

        class FakeAgent:
            async def process_cron_message(self, text: str) -> str:
                captured.append(text)
                return "should-not-be-called"

        scheduler._agent = FakeAgent()
        job = CronJob(
            name="silent-cron",
            cron_expression="0 0 * * *",
            action_type=ActionType.RECIPE,
            action_reference="side-effect-only",
        )
        with caplog.at_level("WARNING"):
            result = await scheduler._execute_action(job)
        assert captured == []  # LLM 호출 없음
        assert "Recipe completed" in result
        # PROMPT 가 없으므로 silent no-op 경고는 떨어지지 않아야 한다.
        assert not any(
            "no LLM input" in rec.message for rec in caplog.records
        )

    @pytest.mark.asyncio
    async def test_warn_when_prompt_step_recipe_yields_no_llm_input(
        self, setup, caplog,
    ):
        """안전망: 로더 우회로 빈 PROMPT 가 들어와도 silent 가 아니라 WARN 으로 노출.

        정상 경로(로더 통과)에서는 발생하지 않지만, 직접 RecipeDefinition 을 만들어
        executor 에 넘기는 다른 호출자가 같은 함정에 빠지는 것을 대비한 안전망.
        """
        from simpleclaw.recipes.models import (
            RecipeDefinition,
            RecipeStep,
            StepType,
        )

        store, apscheduler, tmp_path, CronJob = setup
        scheduler = CronScheduler(
            store, apscheduler,
            recipes_dir=tmp_path, legacy_recipes_dir=None,
        )

        # 로더를 우회한 합성 레시피 — 빈 content PROMPT step.
        synthetic = RecipeDefinition(
            name="synthetic",
            steps=[
                RecipeStep(
                    step_type=StepType.PROMPT, name="ghost", content=""
                ),
            ],
        )

        class FakeAgent:
            async def process_cron_message(self, text: str) -> str:
                raise AssertionError("LLM 호출이 발생해서는 안 된다")

        scheduler._agent = FakeAgent()

        async def fake_load(_path):
            return synthetic

        async def fake_exec(recipe, **_kwargs):
            from simpleclaw.recipes.models import (
                RecipeResult,
                StepResult,
                StepStatus,
            )
            return RecipeResult(
                recipe_name=recipe.name,
                success=True,
                step_results=[
                    StepResult(
                        step_name="ghost",
                        status=StepStatus.SUCCESS,
                        output="",  # 빈 PROMPT content
                    )
                ],
            )

        # 패치 — scheduler 내부의 lazy import 를 가로채서 합성 레시피를 흘려 넣는다.
        import simpleclaw.recipes.executor as executor_mod
        import simpleclaw.recipes.loader as loader_mod
        original_load = loader_mod.load_recipe
        original_exec = executor_mod.execute_recipe

        def sync_load(_path):
            return synthetic

        async def patched_exec(recipe, **kw):
            return await fake_exec(recipe, **kw)

        loader_mod.load_recipe = sync_load
        executor_mod.execute_recipe = patched_exec
        try:
            # action_reference 가 파일이어야 lazy load 로직이 통과
            ref = tmp_path / "synthetic.yaml"
            ref.write_text("name: synthetic")
            job = CronJob(
                name="synthetic-cron",
                cron_expression="0 0 * * *",
                action_type=ActionType.RECIPE,
                action_reference=str(ref),
            )
            with caplog.at_level("WARNING"):
                result = await scheduler._execute_action(job)
        finally:
            loader_mod.load_recipe = original_load
            executor_mod.execute_recipe = original_exec

        assert "Recipe completed" in result
        # silent no-op 회귀 시 운영자가 즉시 알 수 있도록 WARN 이 떨어져야 한다.
        assert any(
            "no LLM input" in rec.message for rec in caplog.records
        )


class TestRecipeCronTimeoutPropagation:
    """BIZ-423 — cron v1(steps) 레시피 실행 시 `settings.timeout` 이
    ``execute_recipe(timeout=...)`` 로 전달되는지 검증."""

    @pytest.fixture
    def setup(self, tmp_path):
        from simpleclaw.daemon.models import CronJob
        store = DaemonStore(tmp_path / "test.db")
        apscheduler = MagicMock(spec=AsyncIOScheduler)
        return store, apscheduler, tmp_path, CronJob

    def _write_recipe(self, root, name: str, settings_block: str):
        rdir = root / name
        rdir.mkdir(parents=True)
        (rdir / "recipe.yaml").write_text(
            f"name: {name}\n"
            "description: command bridge\n"
            + settings_block
            + "steps:\n"
            "  - type: command\n"
            "    name: run\n"
            "    content: echo hi\n",
            encoding="utf-8",
        )

    async def _run_and_capture(self, setup, settings_block: str, monkeypatch):
        """레시피를 등록하고 _execute_action 실행 — execute_recipe kwargs 캡처."""
        store, apscheduler, tmp_path, CronJob = setup
        recipes_dir = tmp_path / "recipes"
        self._write_recipe(recipes_dir, "study-daily", settings_block)

        scheduler = CronScheduler(
            store, apscheduler,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )

        import simpleclaw.recipes.executor as executor_mod
        from simpleclaw.recipes.models import (
            RecipeResult,
            StepResult,
            StepStatus,
        )
        captured: dict = {}

        async def spy_exec(recipe, **kwargs):
            captured.update(kwargs)
            return RecipeResult(
                recipe_name=recipe.name,
                success=True,
                step_results=[
                    StepResult(
                        step_name="run",
                        status=StepStatus.SUCCESS,
                        output="",
                    )
                ],
            )

        monkeypatch.setattr(executor_mod, "execute_recipe", spy_exec)

        job = CronJob(
            name="study-cron",
            cron_expression="0 0 * * *",
            action_type=ActionType.RECIPE,
            action_reference="study-daily",
        )
        await scheduler._execute_action(job)
        return captured

    @pytest.mark.asyncio
    async def test_settings_timeout_forwarded_to_execute_recipe(
        self, setup, monkeypatch,
    ):
        captured = await self._run_and_capture(
            setup, "settings:\n  timeout: 180\n", monkeypatch,
        )
        assert captured["timeout"] == 180

    @pytest.mark.asyncio
    async def test_default_timeout_forwarded_when_settings_missing(
        self, setup, monkeypatch,
    ):
        captured = await self._run_and_capture(setup, "", monkeypatch)
        assert captured["timeout"] == 60


class TestCronDowTranslation:
    """표준 crontab day_of_week(0/7=sun, 1=mon..) → APScheduler(0=mon..6=sun) 매핑."""

    @pytest.mark.parametrize(
        "cron_field, expected",
        [
            ("*", "*"),
            ("1-5", "0,1,2,3,4"),       # mon-fri
            ("0", "6"),                   # sun
            ("7", "6"),                   # sun alias
            ("1", "0"),                   # mon
            ("6", "5"),                   # sat
            ("0,6", "5,6"),              # sun, sat → sat, sun
            ("1,3,5", "0,2,4"),
            ("mon-fri", "mon-fri"),     # 영문 약어는 그대로
            ("*/2", "*/2"),               # 스텝은 그대로
            ("1-3,5", "0,1,2,4"),
        ],
    )
    def test_translate(self, cron_field, expected):
        assert _translate_cron_dow_to_apscheduler(cron_field) == expected

    def test_register_applies_translation(self, tmp_path):
        """표준 cron '1-5' 입력 시 APScheduler 트리거의 day_of_week 가 월~금이 되어야 한다."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        store = DaemonStore(tmp_path / "t.db")
        apscheduler = MagicMock(spec=AsyncIOScheduler)
        added_triggers = []

        def _capture(func, **kw):
            added_triggers.append(kw["trigger"])

        apscheduler.add_job.side_effect = _capture
        sched = CronScheduler(store, apscheduler)
        sched.add_job(
            name="weekday-job",
            cron_expression="0 12 * * 1-5",
            action_type=ActionType.PROMPT,
            action_reference="x",
        )
        assert added_triggers, "APScheduler add_job 가 호출되어야 한다"
        trigger = added_triggers[-1]

        # 월요일(2026-05-18) 11:00 기준 다음 실행이 같은 날 12:00 이어야 함 (변환 전에는 화요일이 됨).
        KST = ZoneInfo("Asia/Seoul")
        monday_11 = datetime(2026, 5, 18, 11, 0, tzinfo=KST)
        nxt = trigger.get_next_fire_time(None, monday_11)
        assert nxt is not None
        assert nxt.strftime("%Y-%m-%d %H:%M") == "2026-05-18 12:00"
        assert nxt.strftime("%A") == "Monday"
