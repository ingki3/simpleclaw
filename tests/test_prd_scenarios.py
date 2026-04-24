"""PRD Feature Verification Test Scenarios.

Maps each PRD section to concrete test scenarios verifying
that the implemented system meets the product requirements.

PRD Sections:
  3.1 Persona & Memory
  3.2 Skill, MCP, Agent
  3.3 Sub-Agent (ACP)
  3.4 Recipes
  3.5 Scheduling & Events
  3.6 Channels, Voice, Multi-LLM
  4.1 Workspace rules
  4.3 Technical specs
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ──────────────────────────────────────────────────────────
# PRD 3.1: Persona & Memory System
# ──────────────────────────────────────────────────────────


class TestPRD_3_1_PersonaMemory:
    """PRD 3.1: AGENT.md, USER.md, MEMORY.md 파싱 및 프롬프트 주입."""

    def test_persona_files_parsed(self, tmp_path):
        """AGENT.md, USER.md, MEMORY.md를 파싱하여 섹션 추출."""
        from simpleclaw.persona.parser import parse_markdown
        from simpleclaw.persona.models import FileType

        agent_md = tmp_path / "AGENT.md"
        agent_md.write_text("# Agent\n\nI am SimpleClaw.\n\n## Tone\n\nFriendly.")
        result = parse_markdown(agent_md, FileType.AGENT)
        assert result.file_type == FileType.AGENT
        assert len(result.sections) >= 2

    def test_persona_assembly_order(self, tmp_path):
        """프롬프트 어셈블리 순서: AGENT → USER → MEMORY."""
        from simpleclaw.persona.assembler import assemble_prompt
        from simpleclaw.persona.models import FileType, PersonaFile, Section

        from simpleclaw.persona.models import SourceScope
        files = [
            PersonaFile(
                file_type=FileType.MEMORY, source_path="", source_scope=SourceScope.LOCAL,
                sections=[Section(title="Memory", content="past events", level=1)],
            ),
            PersonaFile(
                file_type=FileType.AGENT, source_path="", source_scope=SourceScope.LOCAL,
                sections=[Section(title="Agent", content="I am agent", level=1)],
            ),
            PersonaFile(
                file_type=FileType.USER, source_path="", source_scope=SourceScope.LOCAL,
                sections=[Section(title="User", content="user info", level=1)],
            ),
        ]
        assembly = assemble_prompt(files, token_budget=4096)
        text = assembly.assembled_text
        # AGENT should come before USER, USER before MEMORY
        assert text.index("agent") < text.index("user")
        assert text.index("user") < text.index("past events")

    def test_token_budget_truncation(self, tmp_path):
        """토큰 예산 초과 시 MEMORY부터 절삭."""
        from simpleclaw.persona.assembler import assemble_prompt
        from simpleclaw.persona.models import FileType, PersonaFile, Section

        from simpleclaw.persona.models import SourceScope
        files = [
            PersonaFile(
                file_type=FileType.AGENT, source_path="", source_scope=SourceScope.LOCAL,
                sections=[Section(title="A", content="agent " * 100, level=1)],
            ),
            PersonaFile(
                file_type=FileType.MEMORY, source_path="", source_scope=SourceScope.LOCAL,
                sections=[Section(title="M", content="memory " * 500, level=1)],
            ),
        ]
        assembly = assemble_prompt(files, token_budget=200)
        assert assembly.was_truncated is True
        assert assembly.token_count <= 200

    def test_local_overrides_global_persona(self, tmp_path):
        """로컬 페르소나 파일이 전역 파일을 Override."""
        from simpleclaw.persona.resolver import resolve_persona_files

        local = tmp_path / "local"
        local.mkdir()
        (local / "AGENT.md").write_text("# Local Agent")

        global_ = tmp_path / "global"
        global_.mkdir()
        (global_ / "AGENT.md").write_text("# Global Agent")

        files = resolve_persona_files(local, global_)
        agent = [f for f in files if f.file_type.value == "agent"][0]
        assert "Local" in agent.sections[0].title

    def test_conversation_store_sqlite(self, tmp_path):
        """대화 로그 SQLite 저장: add → get_recent → get_since."""
        from simpleclaw.memory.conversation_store import ConversationStore
        from simpleclaw.memory.models import ConversationMessage, MessageRole

        store = ConversationStore(tmp_path / "conv.db")
        store.add_message(ConversationMessage(role=MessageRole.USER, content="Hello"))
        store.add_message(ConversationMessage(role=MessageRole.ASSISTANT, content="Hi"))

        recent = store.get_recent(limit=10)
        assert len(recent) == 2
        assert recent[0].role == MessageRole.USER
        assert recent[1].role == MessageRole.ASSISTANT

    def test_dreaming_backup_before_modify(self, tmp_path):
        """드리밍 실행 전 .bak 백업 파일 생성."""
        from simpleclaw.memory.conversation_store import ConversationStore
        from simpleclaw.memory.dreaming import DreamingPipeline

        memory = tmp_path / "MEMORY.md"
        memory.write_text("# Memory\n\nOld content.")
        store = ConversationStore(tmp_path / "db.sqlite")
        pipeline = DreamingPipeline(store, memory)

        backup = pipeline.create_backup(memory)
        assert backup is not None
        assert backup.exists()
        assert ".bak" in str(backup)
        assert backup.read_text() == memory.read_text()

    @pytest.mark.asyncio
    async def test_dreaming_pipeline_full(self, tmp_path):
        """드리밍 파이프라인: 대화 수집 → 요약 → MEMORY.md 병합."""
        from simpleclaw.memory.conversation_store import ConversationStore
        from simpleclaw.memory.dreaming import DreamingPipeline
        from simpleclaw.memory.models import ConversationMessage, MessageRole

        memory = tmp_path / "MEMORY.md"
        memory.write_text("# Memory\n")
        store = ConversationStore(tmp_path / "db.sqlite")
        store.add_message(ConversationMessage(role=MessageRole.USER, content="Plan my day"))
        store.add_message(ConversationMessage(role=MessageRole.ASSISTANT, content="Here is your plan"))

        pipeline = DreamingPipeline(store, memory)
        result = await pipeline.run()
        assert result is not None
        assert "dreaming" in result.source
        content = memory.read_text()
        assert "##" in content  # date-based header from dreaming summary


# ──────────────────────────────────────────────────────────
# PRD 3.2: Skill, MCP, Agent 호출
# ──────────────────────────────────────────────────────────


class TestPRD_3_2_SkillMCPAgent:
    """PRD 3.2: 스킬 디스커버리, 실행, MCP 클라이언트."""

    def test_skill_discovery_global_local(self, tmp_path):
        """전역/로컬 스킬 디스커버리 및 로컬 우선."""
        from simpleclaw.skills.discovery import discover_skills

        global_dir = tmp_path / "global"
        local_dir = tmp_path / "local"

        # Global skill
        gs = global_dir / "my-skill"
        gs.mkdir(parents=True)
        (gs / "SKILL.md").write_text("# My Skill\n\nGlobal version.")

        # Local override
        ls = local_dir / "my-skill"
        ls.mkdir(parents=True)
        (ls / "SKILL.md").write_text("# My Skill\n\nLocal version.")

        skills = discover_skills(local_dir, global_dir)
        assert len(skills) == 1
        assert "Local" in skills[0].description

    def test_skill_discovery_yaml_frontmatter(self, tmp_path):
        """YAML frontmatter 형식의 SKILL.md 파싱."""
        from simpleclaw.skills.discovery import discover_skills

        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A test skill\n---\n\n# Test Skill\n"
        )

        skills = discover_skills(tmp_path / "none", tmp_path / "skills")
        assert len(skills) == 1
        assert skills[0].name == "test-skill"
        assert skills[0].description == "A test skill"

    def test_skill_discovery_bash_commands(self, tmp_path):
        """SKILL.md에서 bash 코드블록 명령어 추출."""
        from simpleclaw.skills.discovery import discover_skills

        skill_dir = tmp_path / "skills" / "gmail"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: gmail\ndescription: Gmail access\n---\n\n"
            "# Gmail\n\n## Usage\n\n```bash\npython gmail.py search --query test\n```\n"
        )

        skills = discover_skills(tmp_path / "none", tmp_path / "skills")
        assert len(skills[0].commands) == 1
        assert "gmail.py" in skills[0].commands[0]

    @pytest.mark.asyncio
    async def test_skill_execution(self, tmp_path):
        """스킬 스크립트 실행 및 결과 반환."""
        from simpleclaw.skills.executor import execute_skill
        from simpleclaw.skills.models import SkillDefinition

        script = tmp_path / "run.py"
        script.write_text('print("skill output")')

        skill = SkillDefinition(
            name="test",
            script_path=str(script),
            skill_dir=str(tmp_path),
        )
        result = await execute_skill(skill)
        assert result.success is True
        assert "skill output" in result.output

    def test_mcp_client_init(self):
        """MCP 클라이언트 초기화 및 도구 목록."""
        from simpleclaw.skills.mcp_client import MCPManager

        mgr = MCPManager()
        assert mgr.list_tools() == []
        assert mgr.get_connected_servers() == []


# ──────────────────────────────────────────────────────────
# PRD 3.3: Sub-Agent (ACP)
# ──────────────────────────────────────────────────────────


class TestPRD_3_3_SubAgent:
    """PRD 3.3: 서브 에이전트 동적 스폰, 풀 제한, 권한 스코프."""

    @pytest.mark.asyncio
    async def test_spawn_and_json_output(self, tmp_path):
        """서브 에이전트 스폰 → JSON-over-Stdout 결과 수신."""
        from simpleclaw.agents.spawner import SubAgentSpawner

        script = tmp_path / "sub.py"
        script.write_text('import json; print(json.dumps({"status":"success","data":{"key":"val"}}))')

        spawner = SubAgentSpawner({
            "max_concurrent": 3,
            "default_timeout": 10,
            "workspace_dir": str(tmp_path / "ws"),
            "cleanup_workspace": True,
            "default_scope": {"allowed_paths": [], "network": False},
        })
        result = await spawner.spawn([sys.executable, str(script)], "test task")
        assert result.status == "success"
        assert result.data["key"] == "val"

    @pytest.mark.asyncio
    async def test_concurrency_hard_limit(self, tmp_path):
        """최대 3개 서브 에이전트 하드 리밋."""
        from simpleclaw.agents.pool import ConcurrencyPool

        pool = ConcurrencyPool(max_concurrent=3)
        for _ in range(3):
            await pool.acquire()
        assert pool.running_count == 3
        assert pool.available_slots == 0

    @pytest.mark.asyncio
    async def test_permission_scope_injection(self, tmp_path):
        """서브 에이전트에 권한 스코프(allowed_paths, network) 주입."""
        from simpleclaw.agents.spawner import SubAgentSpawner
        from simpleclaw.agents.models import PermissionScope

        script = tmp_path / "scope.py"
        script.write_text(textwrap.dedent('''
            import json, os
            scope = json.loads(os.environ.get("AGENT_SCOPE", "{}"))
            print(json.dumps({"status": "success", "data": scope}))
        '''))

        spawner = SubAgentSpawner({
            "max_concurrent": 3,
            "default_timeout": 10,
            "workspace_dir": str(tmp_path / "ws"),
            "cleanup_workspace": True,
            "default_scope": {"allowed_paths": [], "network": False},
        })
        result = await spawner.spawn(
            [sys.executable, str(script)], "scope test",
            scope=PermissionScope(allowed_paths=["/data"], network=True),
        )
        assert result.data["network"] is True
        assert "/data" in result.data["allowed_paths"]

    @pytest.mark.asyncio
    async def test_workspace_sandbox(self, tmp_path):
        """서브 에이전트별 격리된 워크스페이스 디렉토리."""
        from simpleclaw.agents.workspace import WorkspaceManager

        mgr = WorkspaceManager(tmp_path / "ws")
        ws1 = mgr.create("agent-1")
        ws2 = mgr.create("agent-2")
        assert ws1 != ws2
        assert ws1.exists()
        assert ws2.exists()
        assert ws1.name == "agent-1"


# ──────────────────────────────────────────────────────────
# PRD 3.4: Recipes
# ──────────────────────────────────────────────────────────


class TestPRD_3_4_Recipes:
    """PRD 3.4: YAML 레시피 로더 및 실행 엔진."""

    def test_recipe_yaml_load(self, tmp_path):
        """recipe.yaml 파싱: 이름, 파라미터, 스텝."""
        from simpleclaw.recipes.loader import load_recipe

        recipe_dir = tmp_path / "recipes" / "test"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "recipe.yaml").write_text(
            "name: test-recipe\n"
            "description: A test recipe\n"
            "parameters:\n"
            "  - name: target\n"
            "    required: true\n"
            "steps:\n"
            "  - name: step1\n"
            "    type: command\n"
            "    command: echo ${target}\n"
        )
        recipe = load_recipe(recipe_dir / "recipe.yaml")
        assert recipe.name == "test-recipe"
        assert len(recipe.parameters) == 1
        assert len(recipe.steps) == 1

    @pytest.mark.asyncio
    async def test_recipe_execution(self, tmp_path):
        """레시피 실행: 변수 치환 → 명령어 실행 → 결과 수집."""
        from simpleclaw.recipes.loader import load_recipe
        from simpleclaw.recipes.executor import execute_recipe

        recipe_dir = tmp_path / "recipes" / "test"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "recipe.yaml").write_text(
            "name: greet\n"
            "description: Greeting recipe\n"
            "parameters:\n"
            "  - name: who\n"
            "    default: World\n"
            "steps:\n"
            "  - name: say-hello\n"
            "    type: command\n"
            "    command: echo Hello ${who}\n"
        )
        recipe = load_recipe(recipe_dir / "recipe.yaml")
        result = await execute_recipe(recipe)
        assert result.success is True
        assert len(result.step_results) == 1


# ──────────────────────────────────────────────────────────
# PRD 3.5: Scheduling, Events, Async
# ──────────────────────────────────────────────────────────


class TestPRD_3_5_SchedulingEvents:
    """PRD 3.5: Heartbeat, Cron, 드리밍 트리거, 대기 상태."""

    @pytest.mark.asyncio
    async def test_heartbeat_tick(self, tmp_path):
        """5분 주기 Heartbeat 틱 → HEARTBEAT.md 기록."""
        from simpleclaw.daemon.heartbeat import HeartbeatMonitor
        from simpleclaw.daemon.store import DaemonStore

        store = DaemonStore(tmp_path / "daemon.db")
        status_file = tmp_path / "HEARTBEAT.md"
        monitor = HeartbeatMonitor(store, status_file)

        tick = await monitor.tick()
        assert status_file.exists()
        content = status_file.read_text()
        assert "**Status**: running" in content
        assert tick.timestamp is not None

    @pytest.mark.asyncio
    async def test_heartbeat_dirty_state_flush(self, tmp_path):
        """Dirty state일 때만 DB flush."""
        from simpleclaw.daemon.heartbeat import HeartbeatMonitor
        from simpleclaw.daemon.store import DaemonStore

        store = DaemonStore(tmp_path / "daemon.db")
        monitor = HeartbeatMonitor(store, tmp_path / "HB.md")

        # Clean state → no flush
        tick1 = await monitor.tick()
        assert tick1.flush_performed is False

        # Dirty state → flush
        monitor.mark_dirty()
        tick2 = await monitor.tick()
        assert tick2.flush_performed is True

    def test_cron_job_crud(self, tmp_path):
        """Cron Job CRUD: 생성 → 조회 → 수정 → 삭제."""
        from unittest.mock import MagicMock
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from simpleclaw.daemon.scheduler import CronScheduler
        from simpleclaw.daemon.store import DaemonStore
        from simpleclaw.daemon.models import ActionType

        store = DaemonStore(tmp_path / "d.db")
        sched = CronScheduler(store, MagicMock(spec=AsyncIOScheduler))

        job = sched.add_job("test", "0 9 * * *", ActionType.PROMPT, "Hello")
        assert job.name == "test"
        assert len(sched.list_jobs()) == 1

        sched.update_job("test", cron_expression="30 8 * * *")
        assert sched.get_job("test").cron_expression == "30 8 * * *"

        sched.remove_job("test")
        assert sched.get_job("test") is None

    def test_cron_job_persistence(self, tmp_path):
        """Cron Job이 데몬 재시작 후에도 유지."""
        from unittest.mock import MagicMock
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from simpleclaw.daemon.scheduler import CronScheduler
        from simpleclaw.daemon.store import DaemonStore
        from simpleclaw.daemon.models import ActionType

        db_path = tmp_path / "d.db"
        store1 = DaemonStore(db_path)
        sched1 = CronScheduler(store1, MagicMock(spec=AsyncIOScheduler))
        sched1.add_job("persist", "0 0 * * *", ActionType.RECIPE, "recipe.yaml")
        store1.close()

        # New store instance → same DB → job still there
        store2 = DaemonStore(db_path)
        sched2 = CronScheduler(store2, MagicMock(spec=AsyncIOScheduler))
        assert sched2.get_job("persist") is not None
        store2.close()

    @pytest.mark.asyncio
    async def test_dreaming_trigger_conditions(self, tmp_path):
        """드리밍 트리거: 2시간 idle + 심야 조건 동시 만족."""
        from simpleclaw.daemon.dreaming_trigger import DreamingTrigger
        from simpleclaw.daemon.store import DaemonStore
        from simpleclaw.memory.conversation_store import ConversationStore
        from simpleclaw.memory.dreaming import DreamingPipeline
        from simpleclaw.memory.models import ConversationMessage, MessageRole

        conv_store = ConversationStore(tmp_path / "conv.db")
        memory = tmp_path / "MEMORY.md"
        memory.write_text("# M\n")
        pipeline = DreamingPipeline(conv_store, memory)
        daemon_store = DaemonStore(tmp_path / "daemon.db")

        # Add old message (3 hours ago)
        conv_store.add_message(ConversationMessage(
            role=MessageRole.USER, content="old",
            timestamp=datetime.now() - timedelta(hours=3),
        ))

        trigger = DreamingTrigger(conv_store, pipeline, daemon_store,
                                  overnight_hour=0, idle_threshold=7200)
        assert await trigger.should_run() is True

    def test_wait_state_register_resolve(self, tmp_path):
        """비동기 대기 상태: 등록 → 해제."""
        from simpleclaw.daemon.wait_states import WaitStateManager
        from simpleclaw.daemon.store import DaemonStore

        store = DaemonStore(tmp_path / "d.db")
        mgr = WaitStateManager(store)

        mgr.register_wait("t1", {"step": 1}, "callback")
        assert len(mgr.get_pending()) == 1

        mgr.resolve_wait("t1", "completed")
        assert len(mgr.get_pending()) == 0

    def test_wait_state_timeout(self, tmp_path):
        """대기 상태 타임아웃 감지."""
        from simpleclaw.daemon.wait_states import WaitStateManager
        from simpleclaw.daemon.store import DaemonStore
        from simpleclaw.daemon.models import WaitState

        store = DaemonStore(tmp_path / "d.db")
        mgr = WaitStateManager(store, default_timeout=1)

        # Create expired wait state
        old = WaitState(
            task_id="expired",
            serialized_state="{}",
            condition_type="cb",
            registered_at=datetime.now() - timedelta(hours=1),
            timeout_seconds=1,
        )
        store.save_wait_state(old)

        timed_out = mgr.check_timeouts()
        assert len(timed_out) == 1
        assert timed_out[0].task_id == "expired"


# ──────────────────────────────────────────────────────────
# PRD 3.6: Channels, Voice, Multi-LLM
# ──────────────────────────────────────────────────────────


class TestPRD_3_6_ChannelsVoiceLLM:
    """PRD 3.6: 텔레그램, Webhook, STT/TTS, 다중 LLM."""

    @pytest.mark.asyncio
    async def test_telegram_whitelist_authorized(self):
        """텔레그램 화이트리스트 인가 사용자 허용."""
        from simpleclaw.channels.telegram_bot import TelegramBot

        bot = TelegramBot("token", whitelist_user_ids=[123])
        resp = await bot.handle_message("Hi", 123, 999)
        assert resp is not None

    @pytest.mark.asyncio
    async def test_telegram_whitelist_unauthorized_drop(self):
        """텔레그램 비인가 사용자 즉시 DROP."""
        from simpleclaw.channels.telegram_bot import TelegramBot

        bot = TelegramBot("token", whitelist_user_ids=[123])
        resp = await bot.handle_message("Hi", 999, 999)
        assert resp is None

    @pytest.mark.asyncio
    async def test_telegram_fail_closed(self):
        """화이트리스트 미설정 시 전체 거부 (fail-closed)."""
        from simpleclaw.channels.telegram_bot import TelegramBot

        bot = TelegramBot("token")
        assert bot.is_authorized(123, 456) is False

    @pytest.mark.asyncio
    async def test_webhook_bearer_auth(self, aiohttp_client):
        """Webhook 베어러 토큰 인증."""
        from aiohttp import web
        from simpleclaw.channels.webhook_server import WebhookServer

        server = WebhookServer(auth_token="secret")
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        client = await aiohttp_client(server._app)

        # Valid token
        r1 = await client.post("/webhook",
            json={"event_type": "test"},
            headers={"Authorization": "Bearer secret"})
        assert r1.status == 200

        # Invalid token
        r2 = await client.post("/webhook",
            json={"event_type": "test"},
            headers={"Authorization": "Bearer wrong"})
        assert r2.status == 401

    @pytest.mark.asyncio
    async def test_webhook_event_processing(self, aiohttp_client):
        """Webhook 이벤트 수신 및 처리."""
        from aiohttp import web
        from simpleclaw.channels.webhook_server import WebhookServer

        server = WebhookServer(auth_token="")
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        client = await aiohttp_client(server._app)

        resp = await client.post("/webhook",
            json={"event_type": "trigger", "action_type": "prompt",
                  "action_reference": "Hello", "data": {"key": "val"}})
        assert resp.status == 200
        events = server.get_events()
        assert len(events) == 1
        assert events[0].action_reference == "Hello"

    @pytest.mark.asyncio
    async def test_stt_format_validation(self, tmp_path):
        """STT: 지원 포맷 검증 (WAV, MP3, OGG 지원 / 비지원 거부)."""
        from simpleclaw.voice.stt import STTProcessor
        from simpleclaw.voice.models import UnsupportedFormatError

        stt = STTProcessor()
        bad = tmp_path / "test.xyz"
        bad.write_bytes(b"data")
        with pytest.raises(UnsupportedFormatError):
            await stt.transcribe(bad)

    @pytest.mark.asyncio
    async def test_tts_empty_returns_none(self):
        """TTS: 빈 텍스트 → None 반환."""
        from simpleclaw.voice.tts import TTSProcessor

        tts = TTSProcessor()
        assert await tts.synthesize("") is None

    def test_multi_llm_router_config(self, tmp_path):
        """다중 LLM 라우팅: config.yaml 기반 설정."""
        from simpleclaw.config import load_llm_config

        config = tmp_path / "config.yaml"
        config.write_text(
            "llm:\n  default: gemini\n  providers:\n"
            "    gemini:\n      type: api\n      model: gemini-flash\n"
            "      api_key_env: GOOGLE_API_KEY\n"
        )
        cfg = load_llm_config(config)
        assert cfg["default"] == "gemini"
        assert "gemini" in cfg["providers"]

    def test_cli_wrapper_not_found(self):
        """CLI 래퍼: 존재하지 않는 CLI 도구 에러."""
        from simpleclaw.llm.cli_wrapper import CLIProvider
        from simpleclaw.llm.models import LLMCLINotFoundError

        with pytest.raises(LLMCLINotFoundError):
            CLIProvider(command=None)


# ──────────────────────────────────────────────────────────
# PRD 4.1: Workspace Rules
# ──────────────────────────────────────────────────────────


class TestPRD_4_1_WorkspaceRules:
    """PRD 4.1: 워크스페이스 구조 및 저장 규칙."""

    def test_config_loaded_from_yaml(self, tmp_path):
        """config.yaml에서 모든 설정 로드."""
        from simpleclaw.config import (
            load_persona_config, load_llm_config,
            load_daemon_config, load_sub_agents_config,
        )
        config = tmp_path / "config.yaml"
        config.write_text("persona:\n  token_budget: 2048\n")
        assert load_persona_config(config)["token_budget"] == 2048

    def test_sub_agent_sandbox_isolation(self, tmp_path):
        """서브 에이전트 workspace/sub_agents/{id}/ 격리."""
        from simpleclaw.agents.workspace import WorkspaceManager

        mgr = WorkspaceManager(tmp_path / "workspace" / "sub_agents")
        ws = mgr.create("agent-abc")
        assert "sub_agents" in str(ws)
        assert "agent-abc" in str(ws)
        assert ws.is_dir()


# ──────────────────────────────────────────────────────────
# PRD 4.3: Technical Specs
# ──────────────────────────────────────────────────────────


class TestPRD_4_3_TechnicalSpecs:
    """PRD 4.3: asyncio 데몬, APScheduler, JSON 통신, 로깅."""

    @pytest.mark.asyncio
    async def test_daemon_pid_lock(self, tmp_path):
        """데몬 PID 락 파일 → 중복 실행 방지."""
        from simpleclaw.daemon.daemon import AgentDaemon
        from simpleclaw.daemon.models import DaemonLockError

        config = tmp_path / "config.yaml"
        config.write_text(
            f"daemon:\n  heartbeat_interval: 1\n  pid_file: '{tmp_path}/d.pid'\n"
            f"  status_file: '{tmp_path}/HB.md'\n  db_path: '{tmp_path}/d.db'\n"
        )
        d1 = AgentDaemon(config)
        await d1.start()
        assert d1.is_running()

        d2 = AgentDaemon(config)
        with pytest.raises(DaemonLockError):
            await d2.start()

        await d1.stop()

    def test_structured_logging(self, tmp_path):
        """실행 로그 JSONL 형식 아카이빙."""
        from simpleclaw.logging.structured_logger import StructuredLogger

        logger = StructuredLogger(tmp_path / "logs")
        logger.log(action_type="skill_exec", status="success", duration_ms=42)
        logger.log(action_type="cron_job", status="failure", duration_ms=100)

        entries = logger.get_entries()
        assert len(entries) == 2
        assert entries[0].action_type == "skill_exec"
        assert entries[1].status == "failure"

    def test_metrics_collector(self):
        """메트릭스 수집: 토큰, 에러율, 실행 횟수."""
        from simpleclaw.logging.metrics import MetricsCollector

        mc = MetricsCollector()
        mc.record_execution(success=True, tokens_used=100)
        mc.record_execution(success=True, tokens_used=200)
        mc.record_execution(success=False, tokens_used=50)

        snap = mc.get_snapshot()
        assert snap.total_executions == 3
        assert snap.total_tokens_used == 350
        assert snap.error_rate == pytest.approx(1 / 3, abs=0.01)

    @pytest.mark.asyncio
    async def test_dashboard_metrics_api(self, tmp_path, aiohttp_client):
        """웹 대시보드 /api/metrics 엔드포인트."""
        from aiohttp import web
        from simpleclaw.logging.dashboard import DashboardServer
        from simpleclaw.logging.metrics import MetricsCollector
        from simpleclaw.logging.structured_logger import StructuredLogger

        mc = MetricsCollector()
        mc.record_execution(success=True, tokens_used=42)
        sl = StructuredLogger(tmp_path / "logs")

        dash = DashboardServer(mc, sl)
        dash._app = web.Application()
        dash._app.router.add_get("/api/metrics", dash._handle_metrics)
        client = await aiohttp_client(dash._app)

        resp = await client.get("/api/metrics")
        data = await resp.json()
        assert data["total_executions"] == 1
        assert data["total_tokens_used"] == 42

    def test_env_secret_management(self, tmp_path):
        """API 키를 .env에서 로드 (하드코딩 금지)."""
        from simpleclaw.config import load_llm_config

        env = tmp_path / ".env"
        env.write_text("TEST_API_KEY=sk-secret-123\n")
        config = tmp_path / "config.yaml"
        config.write_text(
            "llm:\n  default: test\n  providers:\n"
            "    test:\n      type: api\n      model: m\n"
            "      api_key_env: TEST_API_KEY\n"
        )
        cfg = load_llm_config(config)
        assert cfg["providers"]["test"]["api_key"] == "sk-secret-123"


# ──────────────────────────────────────────────────────────
# PRD E2E: Agent Orchestrator Pipeline
# ──────────────────────────────────────────────────────────


class TestPRD_E2E_AgentPipeline:
    """E2E: 페르소나 + 스킬 + 메모리 + LLM 통합 파이프라인."""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_orchestrator_persona_injection(self, tmp_path):
        """오케스트레이터가 페르소나를 시스템 프롬프트에 주입."""
        persona_dir = tmp_path / "persona"
        persona_dir.mkdir()
        (persona_dir / "AGENT.md").write_text("# Agent\n\nI am SimpleClaw.")

        config = tmp_path / "config.yaml"
        config.write_text(
            f"llm:\n  default: gemini\n  providers:\n"
            f"    gemini:\n      type: api\n      model: m\n      api_key_env: GOOGLE_API_KEY\n"
            f"agent:\n  history_limit: 5\n  db_path: '{tmp_path}/conv.db'\n"
            f"skills:\n  local_dir: '{tmp_path}/ls'\n  global_dir: '{tmp_path}/gs'\n"
            f"persona:\n  token_budget: 4096\n  local_dir: '{persona_dir}'\n"
            f"  global_dir: '{tmp_path}/gp'\n"
            f"  files:\n    - name: AGENT.md\n      type: agent\n"
        )
        (tmp_path / "ls").mkdir()
        (tmp_path / "gs").mkdir()

        from simpleclaw.agent import AgentOrchestrator
        orch = AgentOrchestrator(config)
        assert "SimpleClaw" in orch._persona_prompt

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_orchestrator_conversation_stored(self, tmp_path):
        """오케스트레이터가 대화를 ConversationStore에 저장."""
        persona_dir = tmp_path / "persona"
        persona_dir.mkdir()
        (persona_dir / "AGENT.md").write_text("# A\n\nAgent.")

        config = tmp_path / "config.yaml"
        config.write_text(
            f"llm:\n  default: gemini\n  providers:\n"
            f"    gemini:\n      type: api\n      model: m\n      api_key_env: GOOGLE_API_KEY\n"
            f"agent:\n  history_limit: 5\n  db_path: '{tmp_path}/conv.db'\n"
            f"skills:\n  local_dir: '{tmp_path}/ls'\n  global_dir: '{tmp_path}/gs'\n"
            f"persona:\n  token_budget: 4096\n  local_dir: '{persona_dir}'\n"
            f"  global_dir: '{tmp_path}/gp'\n"
            f"  files:\n    - name: AGENT.md\n      type: agent\n"
        )
        (tmp_path / "ls").mkdir()
        (tmp_path / "gs").mkdir()

        from simpleclaw.agent import AgentOrchestrator
        orch = AgentOrchestrator(config)

        mock_resp = MagicMock()
        mock_resp.text = "Hello!"
        mock_resp.backend_name = "gemini"
        orch._router = MagicMock()
        orch._router.send = AsyncMock(return_value=mock_resp)

        await orch.process_message("Hi", 123, 456)
        msgs = orch._store.get_recent(limit=10)
        assert len(msgs) == 2
        assert msgs[0].role.value == "user"
        assert msgs[1].role.value == "assistant"
