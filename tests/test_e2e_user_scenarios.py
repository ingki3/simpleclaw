"""End-to-End User Scenario Tests.

Simulates real user environments with actual components.
Only the LLM API is mocked — everything else runs for real:
- Real SQLite databases
- Real file I/O (persona, HEARTBEAT.md, logs)
- Real APScheduler
- Real subprocess execution (skills, sub-agents)
- Real config.yaml parsing
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────
# Fixtures: Build a realistic workspace
# ──────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path):
    """Build a realistic agent workspace with config, persona, skills, recipes."""

    # config.yaml
    config = tmp_path / "config.yaml"
    config.write_text(f"""\
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-flash
      api_key_env: GOOGLE_API_KEY

agent:
  history_limit: 20
  db_path: "{tmp_path}/.agent/conversations.db"

skills:
  local_dir: "{tmp_path}/.agent/skills"
  global_dir: "{tmp_path}/global_skills"
  execution_timeout: 10

persona:
  token_budget: 4096
  local_dir: "{tmp_path}/.agent"
  global_dir: "{tmp_path}/global_persona"
  files:
    - name: AGENT.md
      type: agent
    - name: USER.md
      type: user
    - name: MEMORY.md
      type: memory

daemon:
  heartbeat_interval: 1
  pid_file: "{tmp_path}/.agent/daemon.pid"
  status_file: "{tmp_path}/.agent/HEARTBEAT.md"
  db_path: "{tmp_path}/.agent/daemon.db"
  dreaming:
    overnight_hour: 0
    idle_threshold: 1
  wait_state:
    default_timeout: 5

telegram:
  bot_token_env: TELEGRAM_BOT_TOKEN
  whitelist:
    user_ids: [123456]
    chat_ids: []

webhook:
  enabled: true
  host: "127.0.0.1"
  port: 0
  auth_token_env: WEBHOOK_AUTH_TOKEN
""")

    # .env
    (tmp_path / ".env").write_text(
        "GOOGLE_API_KEY=fake-key\n"
        "TELEGRAM_BOT_TOKEN=fake-token\n"
        "WEBHOOK_AUTH_TOKEN=secret-123\n"
    )

    # Persona files
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "AGENT.md").write_text(
        "# SimpleClaw\n\n"
        "You are SimpleClaw, a personal assistant.\n"
        "Respond in the same language the user writes in.\n"
        "Be concise and helpful.\n"
    )
    (agent_dir / "USER.md").write_text(
        "# User\n\n"
        "Name: TestUser\n"
        "Language: Korean\n"
        "Preference: Direct and concise answers.\n"
    )
    (agent_dir / "MEMORY.md").write_text(
        "# Memory\n\n"
        "- 2026-04-17: User set up the agent.\n"
    )

    # Local skill: echo-skill (has executable script)
    echo_skill = agent_dir / "skills" / "echo-skill"
    echo_skill.mkdir(parents=True)
    (echo_skill / "SKILL.md").write_text(
        "---\nname: echo-skill\n"
        "description: Echoes back the input text. Use when user asks to echo something.\n"
        "---\n\n# Echo Skill\n\n## Usage\n\n"
        f"```bash\n{sys.executable} {echo_skill}/run.py ${{text}}\n```\n"
    )
    (echo_skill / "run.py").write_text(
        'import sys; print("ECHO:", " ".join(sys.argv[1:]))\n'
    )

    # Local skill: time-skill (has executable script)
    time_skill = agent_dir / "skills" / "time-skill"
    time_skill.mkdir(parents=True)
    (time_skill / "SKILL.md").write_text(
        "---\nname: time-skill\n"
        "description: Returns the current date and time. Use when user asks about the time or date.\n"
        "---\n\n# Time Skill\n\n## Usage\n\n"
        f"```bash\n{sys.executable} {time_skill}/run.py\n```\n"
    )
    (time_skill / "run.py").write_text(
        'from datetime import datetime; print(f"Current time: {datetime.now().strftime(\'%Y-%m-%d %H:%M:%S\')}")\n'
    )

    # Global skills dir (empty)
    (tmp_path / "global_skills").mkdir()
    (tmp_path / "global_persona").mkdir()

    # Recipes
    recipe_dir = agent_dir / "recipes" / "daily-report"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "recipe.yaml").write_text(
        "name: daily-report\n"
        "description: Generate a daily status report\n"
        "parameters:\n"
        "  - name: date\n"
        "    default: today\n"
        "steps:\n"
        "  - name: generate\n"
        "    type: command\n"
        "    command: echo Daily report for ${date}\n"
    )

    return tmp_path, config


def _mock_llm_response(text):
    """Create a mock LLM response."""
    resp = MagicMock()
    resp.text = text
    resp.backend_name = "gemini"
    resp.model = "gemini-flash"
    resp.usage = {"input_tokens": 100, "output_tokens": 50}
    return resp


# ──────────────────────────────────────────────────────────
# Scenario 1: 사용자가 에이전트에게 일반 대화
# ──────────────────────────────────────────────────────────


class TestScenario_NormalConversation:
    """사용자가 텔레그램으로 일반 대화를 하는 시나리오.

    기대:
    - 페르소나가 시스템 프롬프트에 반영됨
    - 대화 히스토리가 저장됨
    - 연속 대화 시 이전 맥락이 유지됨
    """

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "fake"})
    @pytest.mark.asyncio
    async def test_first_message(self, workspace):
        """첫 메시지: 페르소나 로드, LLM 호출, 응답 반환."""
        tmp_path, config = workspace
        from simpleclaw.agent import AgentOrchestrator

        orch = AgentOrchestrator(config)

        # 페르소나가 로드되었는지 확인
        assert "SimpleClaw" in orch._persona_prompt
        assert "TestUser" in orch._persona_prompt

        # LLM Mock: ReAct → Answer directly (no skill needed)
        async def mock_send(request):
            return _mock_llm_response(
                "Thought: 일반 인사입니다.\nAnswer: 안녕하세요! 무엇을 도와드릴까요?"
            )

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        response = await orch.process_message("안녕", 123456, 789)
        assert "안녕하세요" in response

        # 대화가 DB에 저장되었는지 확인
        msgs = orch._store.get_recent(limit=10)
        assert len(msgs) == 2
        assert msgs[0].content == "안녕"
        assert msgs[0].role.value == "user"
        assert msgs[1].role.value == "assistant"

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "fake"})
    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self, workspace):
        """연속 대화: 이전 맥락이 LLM에 전달되는지 확인."""
        tmp_path, config = workspace
        from simpleclaw.agent import AgentOrchestrator

        orch = AgentOrchestrator(config)
        captured_requests = []

        async def mock_send(request):
            captured_requests.append(request)
            return _mock_llm_response(
                "Thought: 대화를 처리합니다.\nAnswer: 응답입니다."
            )

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        await orch.process_message("내 이름은 홍길동이야", 123456, 789)
        await orch.process_message("내 이름 기억해?", 123456, 789)

        # 두 번째 대화의 최종 응답 요청에 히스토리가 포함되어야 함
        final_request = captured_requests[-1]
        assert final_request.messages is not None
        # 히스토리에 이전 대화가 포함
        history_texts = [m["content"] for m in final_request.messages]
        assert "내 이름은 홍길동이야" in history_texts


# ──────────────────────────────────────────────────────────
# Scenario 2: 사용자가 스킬을 사용하는 대화
# ──────────────────────────────────────────────────────────


class TestScenario_SkillExecution:
    """사용자가 스킬 실행이 필요한 요청을 하는 시나리오.

    기대:
    - 스킬 라우터가 적절한 스킬을 선택
    - 스킬 스크립트가 실제로 실행됨
    - 실행 결과가 최종 응답에 반영됨
    """

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "fake"})
    @pytest.mark.asyncio
    async def test_skill_routing_and_execution(self, workspace):
        """스킬 라우팅 → 스크립트 실행 → 결과 기반 응답."""
        tmp_path, config = workspace
        from simpleclaw.agent import AgentOrchestrator

        orch = AgentOrchestrator(config)

        # time-skill의 실제 실행 경로
        time_script = tmp_path / ".agent" / "skills" / "time-skill" / "run.py"

        call_count = 0
        captured_user_message = None

        async def mock_send(request):
            nonlocal call_count, captured_user_message
            call_count += 1
            if call_count == 1:  # ReAct: Action
                action = json.dumps({
                    "skill_name": "time-skill",
                    "command": f"{sys.executable} {time_script}"
                })
                return _mock_llm_response(
                    f"Thought: 시간을 확인합니다.\nAction: {action}"
                )
            else:  # ReAct: Answer (Observation in trace)
                captured_user_message = request.user_message
                return _mock_llm_response(
                    "Thought: 시간을 확인했습니다.\nAnswer: 현재 시간은 2026-04-18 21:30:00 입니다."
                )

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        response = await orch.process_message("지금 몇 시야?", 123456, 789)

        # 스킬이 실제로 실행되어 결과가 Observation에 포함되었는지
        assert captured_user_message is not None
        assert "Observation:" in captured_user_message
        assert "Current time:" in captured_user_message

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "fake"})
    @pytest.mark.asyncio
    async def test_skill_not_needed(self, workspace):
        """일반 질문 → 스킬 라우터가 use_skill: false 반환."""
        tmp_path, config = workspace
        from simpleclaw.agent import AgentOrchestrator

        orch = AgentOrchestrator(config)

        call_count = 0
        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            return _mock_llm_response(
                "Thought: 스킬이 필요 없습니다.\nAnswer: 파이썬은 프로그래밍 언어입니다."
            )

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        response = await orch.process_message("파이썬이 뭐야?", 123456, 789)
        assert "파이썬" in response
        assert call_count == 1  # single ReAct step (Answer directly)


# ──────────────────────────────────────────────────────────
# Scenario 3: 데몬 + Heartbeat + Cron 실제 동작
# ──────────────────────────────────────────────────────────


class TestScenario_DaemonCron:
    """데몬을 실제로 시작하고 Cron Job이 실행되는 시나리오.

    기대:
    - 데몬 시작 → PID 락 → HEARTBEAT.md 생성
    - Cron Job 생성 → 스케줄러 등록 → 실제 실행
    - 데몬 중지 → 정리
    """

    @pytest.mark.asyncio
    async def test_daemon_full_lifecycle(self, workspace):
        """데몬 시작 → Heartbeat 틱 → Cron Job → 정지."""
        tmp_path, config = workspace
        from simpleclaw.daemon.daemon import AgentDaemon
        from simpleclaw.daemon.models import ActionType

        daemon = AgentDaemon(config)
        await daemon.start()

        # 1. 데몬이 실행 중
        assert daemon.is_running()
        pid_file = tmp_path / ".agent" / "daemon.pid"
        assert pid_file.exists()

        # 2. HEARTBEAT.md 생성됨
        hb_file = tmp_path / ".agent" / "HEARTBEAT.md"
        assert hb_file.exists()
        content = hb_file.read_text()
        assert "**Status**: running" in content

        # 3. Cron Job 생성 및 확인
        cs = daemon.cron_scheduler
        cs.add_job("morning", "0 9 * * *", ActionType.PROMPT, "Good morning!")
        cs.add_job("evening", "0 18 * * *", ActionType.RECIPE, ".agent/recipes/daily-report")

        jobs = cs.list_jobs()
        assert len(jobs) == 2
        job_names = {j.name for j in jobs}
        assert "morning" in job_names
        assert "evening" in job_names

        # 4. Cron Job 수정
        cs.update_job("morning", cron_expression="30 8 * * *")
        assert cs.get_job("morning").cron_expression == "30 8 * * *"

        # 5. Cron Job 비활성화/활성화
        cs.disable_job("evening")
        assert cs.get_job("evening").enabled is False
        cs.enable_job("evening")
        assert cs.get_job("evening").enabled is True

        # 6. Cron Job 삭제
        cs.remove_job("evening")
        assert len(cs.list_jobs()) == 1

        # 7. Cron Job 수동 실행
        execution = await cs.execute_job("morning")
        assert execution.status.value == "success"

        # 8. 실행 로그 확인
        execs = daemon.store.get_executions("morning")
        assert len(execs) == 1

        # 9. 정지
        await daemon.stop()
        assert not daemon.is_running()
        assert not pid_file.exists()

    @pytest.mark.asyncio
    async def test_cron_job_survives_restart(self, workspace):
        """Cron Job이 데몬 재시작 후에도 유지."""
        tmp_path, config = workspace
        from simpleclaw.daemon.daemon import AgentDaemon
        from simpleclaw.daemon.models import ActionType

        # 첫 번째 데몬: job 생성
        d1 = AgentDaemon(config)
        await d1.start()
        d1.cron_scheduler.add_job("persist-test", "0 12 * * *", ActionType.PROMPT, "Lunch!")
        await d1.stop()

        # 두 번째 데몬: job 확인
        d2 = AgentDaemon(config)
        await d2.start()
        job = d2.cron_scheduler.get_job("persist-test")
        assert job is not None
        assert job.action_reference == "Lunch!"
        await d2.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_ticks_multiple(self, workspace):
        """여러 번의 Heartbeat 틱이 정상 실행."""
        tmp_path, config = workspace
        from simpleclaw.daemon.daemon import AgentDaemon

        daemon = AgentDaemon(config)
        await daemon.start()

        # 수동으로 여러 번 틱 실행
        for _ in range(3):
            await daemon.heartbeat.tick()

        hb = daemon.heartbeat.get_last_tick()
        assert hb is not None

        await daemon.stop()


# ──────────────────────────────────────────────────────────
# Scenario 4: 드리밍 자동 트리거
# ──────────────────────────────────────────────────────────


class TestScenario_Dreaming:
    """드리밍 파이프라인이 조건 만족 시 자동 트리거되는 시나리오.

    기대:
    - 2시간 idle + 심야 시간 조건 충족 시 드리밍 실행
    - MEMORY.md에 세션 요약 추가
    - .bak 백업 파일 생성
    - 같은 날 중복 실행 방지
    """

    @pytest.mark.asyncio
    async def test_dreaming_auto_trigger(self, workspace):
        """조건 충족 시 드리밍 자동 트리거 + MEMORY.md 업데이트."""
        tmp_path, config = workspace
        from simpleclaw.daemon.dreaming_trigger import DreamingTrigger, LAST_DREAMING_KEY
        from simpleclaw.daemon.store import DaemonStore
        from simpleclaw.memory.conversation_store import ConversationStore
        from simpleclaw.memory.dreaming import DreamingPipeline
        from simpleclaw.memory.models import ConversationMessage, MessageRole

        memory_file = tmp_path / ".agent" / "MEMORY.md"
        original_content = memory_file.read_text()

        conv_store = ConversationStore(tmp_path / ".agent" / "conv_dream.db")
        daemon_store = DaemonStore(tmp_path / ".agent" / "daemon_dream.db")
        pipeline = DreamingPipeline(conv_store, memory_file)

        # 3시간 전 대화 추가
        conv_store.add_message(ConversationMessage(
            role=MessageRole.USER, content="오늘 회의 정리해줘",
            timestamp=datetime.now() - timedelta(hours=3),
        ))
        conv_store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="네, 오늘 회의 내용을 정리하겠습니다.",
            timestamp=datetime.now() - timedelta(hours=3),
        ))

        trigger = DreamingTrigger(
            conv_store, pipeline, daemon_store,
            overnight_hour=0,  # 현재 시간이 항상 이 이후
            idle_threshold=1,  # 1초 idle이면 충분
        )

        # 조건 충족 확인
        assert await trigger.should_run() is True

        # 드리밍 실행
        await trigger.execute()

        # MEMORY.md 업데이트 확인
        updated_content = memory_file.read_text()
        assert len(updated_content) > len(original_content)
        assert "##" in updated_content  # date-based header from dreaming summary

        # 백업 파일 생성 확인
        backups = list(tmp_path.glob(".agent/MEMORY.*.bak"))
        assert len(backups) >= 1

        # 같은 날 재실행 방지
        assert await trigger.should_run() is False

        # 타임스탬프 기록 확인
        last = daemon_store.get_state(LAST_DREAMING_KEY)
        assert last is not None


# ──────────────────────────────────────────────────────────
# Scenario 5: 서브 에이전트 실제 스폰
# ──────────────────────────────────────────────────────────


class TestScenario_SubAgent:
    """서브 에이전트를 실제로 스폰하고 결과를 수신하는 시나리오.

    기대:
    - 서브프로세스 실제 생성 및 JSON 결과 수신
    - 동시 실행 제한 (3개)
    - 타임아웃 시 강제 종료
    - 워크스페이스 격리
    """

    @pytest.mark.asyncio
    async def test_real_subprocess_spawn(self, workspace):
        """실제 Python 서브프로세스 스폰 → JSON 결과."""
        tmp_path, config = workspace
        from simpleclaw.agents.spawner import SubAgentSpawner

        script = tmp_path / "agent_task.py"
        script.write_text(textwrap.dedent('''
            import json, os
            workspace = os.environ.get("AGENT_WORKSPACE", "unknown")
            agent_id = os.environ.get("AGENT_ID", "unknown")
            result = {
                "status": "success",
                "data": {
                    "agent_id": agent_id,
                    "workspace_exists": os.path.isdir(workspace),
                    "computed": 6 * 7,
                }
            }
            print(json.dumps(result))
        '''))

        spawner = SubAgentSpawner({
            "max_concurrent": 3,
            "default_timeout": 10,
            "workspace_dir": str(tmp_path / "workspace" / "sub_agents"),
            "cleanup_workspace": False,
            "default_scope": {"allowed_paths": [], "network": False},
        })

        result = await spawner.spawn([sys.executable, str(script)], "compute task")
        assert result.status == "success"
        assert result.data["computed"] == 42
        assert result.data["workspace_exists"] is True
        assert result.data["agent_id"] != "unknown"

    @pytest.mark.asyncio
    async def test_concurrent_limit_enforced(self, workspace):
        """동시 3개 서브 에이전트 제한 실제 검증."""
        tmp_path, config = workspace
        from simpleclaw.agents.spawner import SubAgentSpawner

        script = tmp_path / "slow_agent.py"
        script.write_text(textwrap.dedent('''
            import json, time
            time.sleep(0.3)
            print(json.dumps({"status": "success", "data": {}}))
        '''))

        spawner = SubAgentSpawner({
            "max_concurrent": 2,
            "default_timeout": 10,
            "workspace_dir": str(tmp_path / "ws"),
            "cleanup_workspace": True,
            "default_scope": {"allowed_paths": [], "network": False},
        })

        # 4개 동시 요청 → 2개씩 처리
        start = time.time()
        results = await asyncio.gather(*[
            spawner.spawn([sys.executable, str(script)], f"task-{i}")
            for i in range(4)
        ])
        elapsed = time.time() - start

        assert all(r.status == "success" for r in results)
        # 4개 task, 2개 동시 → 최소 0.6초 소요 (2 batch * 0.3s)
        assert elapsed >= 0.5

    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess(self, workspace):
        """타임아웃 시 서브프로세스 강제 종료."""
        tmp_path, config = workspace
        from simpleclaw.agents.spawner import SubAgentSpawner

        script = tmp_path / "hang.py"
        script.write_text("import time; time.sleep(60)")

        spawner = SubAgentSpawner({
            "max_concurrent": 3,
            "default_timeout": 1,
            "workspace_dir": str(tmp_path / "ws"),
            "cleanup_workspace": True,
            "default_scope": {"allowed_paths": [], "network": False},
        })

        result = await spawner.spawn([sys.executable, str(script)], "hanging task")
        assert result.status == "error"
        assert "Timeout" in result.error


# ──────────────────────────────────────────────────────────
# Scenario 6: 레시피 실제 실행
# ──────────────────────────────────────────────────────────


class TestScenario_Recipe:
    """레시피를 실제로 로드하고 실행하는 시나리오.

    기대:
    - YAML 파싱, 변수 치환, 다단계 실행
    """

    @pytest.mark.asyncio
    async def test_recipe_with_variables(self, workspace):
        """변수 치환이 포함된 다단계 레시피 실행."""
        tmp_path, config = workspace
        from simpleclaw.recipes.loader import load_recipe
        from simpleclaw.recipes.executor import execute_recipe

        recipe_dir = tmp_path / ".agent" / "recipes" / "multi-step"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "recipe.yaml").write_text(
            "name: multi-step\n"
            "description: Multi-step test\n"
            "parameters:\n"
            "  - name: target\n"
            "    default: world\n"
            "steps:\n"
            "  - name: step1\n"
            "    type: command\n"
            "    content: echo Step1 ${target}\n"
            "  - name: step2\n"
            "    type: command\n"
            "    content: echo Step2 done\n"
        )

        recipe = load_recipe(recipe_dir / "recipe.yaml")
        result = await execute_recipe(recipe, variables={"target": "SimpleClaw"})

        assert result.success is True
        assert len(result.step_results) == 2
        assert "SimpleClaw" in result.step_results[0].output


# ──────────────────────────────────────────────────────────
# Scenario 7: Webhook 이벤트 수신 및 처리
# ──────────────────────────────────────────────────────────


class TestScenario_Webhook:
    """외부 서비스가 Webhook으로 이벤트를 보내는 시나리오.

    기대:
    - 인증 토큰 검증
    - JSON 페이로드 파싱
    - 이벤트 로그 기록
    """

    @pytest.mark.asyncio
    async def test_webhook_full_flow(self, workspace, aiohttp_client):
        """Webhook: 인증 → 이벤트 수신 → 로깅."""
        from aiohttp import web
        from simpleclaw.channels.webhook_server import WebhookServer

        server = WebhookServer(auth_token="secret-123")
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        server._app.router.add_get("/health", server._handle_health)
        client = await aiohttp_client(server._app)

        # Health check
        r = await client.get("/health")
        assert r.status == 200

        # 인증 실패
        r = await client.post("/webhook",
            json={"event_type": "test"},
            headers={"Authorization": "Bearer wrong"})
        assert r.status == 401

        # 정상 이벤트
        r = await client.post("/webhook",
            json={
                "event_type": "external_trigger",
                "action_type": "prompt",
                "action_reference": "뉴스 요약해줘",
                "data": {"source": "zapier"},
            },
            headers={"Authorization": "Bearer secret-123"})
        assert r.status == 200

        events = server.get_events()
        assert len(events) == 1
        assert events[0].event_type == "external_trigger"
        assert events[0].action_reference == "뉴스 요약해줘"

        # 접근 로그
        log = server.get_access_log()
        assert len(log) == 2
        assert log[0].authorized is False  # 실패한 것
        assert log[1].authorized is True   # 성공한 것


# ──────────────────────────────────────────────────────────
# Scenario 8: 텔레그램 보안 (비인가 차단)
# ──────────────────────────────────────────────────────────


class TestScenario_TelegramSecurity:
    """텔레그램 화이트리스트 보안 시나리오.

    기대:
    - 인가된 사용자만 처리
    - 비인가 시도 로그 기록
    - 빈 화이트리스트 → 전체 거부
    """

    @pytest.mark.asyncio
    async def test_authorized_user_gets_response(self):
        from simpleclaw.channels.telegram_bot import TelegramBot

        bot = TelegramBot("token", whitelist_user_ids=[123456])
        resp = await bot.handle_message("안녕하세요", 123456, 999)
        assert resp is not None
        assert len(resp) > 0

    @pytest.mark.asyncio
    async def test_unauthorized_user_silently_dropped(self):
        from simpleclaw.channels.telegram_bot import TelegramBot

        bot = TelegramBot("token", whitelist_user_ids=[123456])
        resp = await bot.handle_message("해킹 시도", 999999, 999)
        assert resp is None

        # 접근 시도가 로그에 기록됨
        log = bot.get_access_log()
        assert len(log) == 1
        assert log[0].authorized is False
        assert "999999" in log[0].user_identifier

    @pytest.mark.asyncio
    async def test_no_whitelist_rejects_all(self):
        from simpleclaw.channels.telegram_bot import TelegramBot

        bot = TelegramBot("token")  # 빈 화이트리스트
        resp = await bot.handle_message("아무 메시지", 123, 456)
        assert resp is None


# ──────────────────────────────────────────────────────────
# Scenario 9: 로깅 및 모니터링
# ──────────────────────────────────────────────────────────


class TestScenario_Logging:
    """실제 로그 파일 생성 및 메트릭스 시나리오.

    기대:
    - .logs/execution_YYYYMMDD.log 파일 생성
    - JSONL 포맷
    - 메트릭스 정확한 집계
    - 대시보드 API 응답
    """

    def test_real_log_file_creation(self, workspace):
        """실제 로그 파일 생성 및 JSONL 검증."""
        tmp_path, _ = workspace
        from simpleclaw.logging.structured_logger import StructuredLogger

        log_dir = tmp_path / ".logs"
        logger = StructuredLogger(log_dir)

        # 여러 이벤트 기록
        logger.log(action_type="skill_exec", input_summary="시간 확인",
                    output_summary="15:30", duration_ms=120, status="success")
        logger.log(action_type="cron_job", input_summary="morning briefing",
                    output_summary="", duration_ms=3500, status="failure",
                    error="API timeout")
        logger.log(action_type="telegram_msg", input_summary="안녕",
                    output_summary="안녕하세요!", duration_ms=250, status="success")

        # 파일 존재 확인
        log_files = list(log_dir.glob("execution_*.log"))
        assert len(log_files) == 1

        # JSONL 포맷 검증
        lines = log_files[0].read_text().strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            data = json.loads(line)
            assert "timestamp" in data
            assert "action_type" in data
            assert "duration_ms" in data

        # 읽기 확인
        entries = logger.get_entries()
        assert len(entries) == 3
        assert entries[1].status == "failure"

    def test_metrics_accuracy(self):
        """메트릭스 정확성: 토큰, 에러율, 실행 횟수."""
        from simpleclaw.logging.metrics import MetricsCollector

        mc = MetricsCollector()
        mc.record_execution(success=True, duration_ms=100, tokens_used=500)
        mc.record_execution(success=True, duration_ms=200, tokens_used=300)
        mc.record_execution(success=False, duration_ms=50, tokens_used=100)
        mc.record_sub_agent_spawn()
        mc.set_active_cron_jobs(3)

        snap = mc.get_snapshot()
        assert snap.total_executions == 3
        assert snap.successful_executions == 2
        assert snap.failed_executions == 1
        assert snap.total_tokens_used == 900
        assert snap.total_duration_ms == 350
        assert snap.sub_agent_spawns == 1
        assert snap.active_cron_jobs == 3
        assert abs(snap.error_rate - 1/3) < 0.01


# ──────────────────────────────────────────────────────────
# Scenario 10: Wait State (비동기 대기)
# ──────────────────────────────────────────────────────────


class TestScenario_WaitState:
    """작업이 외부 응답을 기다리며 일시정지하는 시나리오.

    기대:
    - 대기 등록 → 다른 작업 수행 가능 → 조건 충족 시 재개
    - 타임아웃 초과 시 자동 해제
    """

    def test_full_wait_lifecycle(self, workspace):
        """등록 → 대기 → 해제 전체 수명주기."""
        tmp_path, _ = workspace
        from simpleclaw.daemon.wait_states import WaitStateManager
        from simpleclaw.daemon.store import DaemonStore

        store = DaemonStore(tmp_path / ".agent" / "wait_test.db")
        mgr = WaitStateManager(store, default_timeout=300)

        # 1. 작업 중 외부 API 응답 대기
        wait = mgr.register_wait(
            task_id="email-send-123",
            state={"step": "waiting_for_confirmation", "email_to": "user@example.com"},
            condition_type="callback",
        )
        assert wait.task_id == "email-send-123"

        # 2. 대기 중인 작업 확인
        pending = mgr.get_pending()
        assert len(pending) == 1

        # 3. 상태 데이터 확인
        data = mgr.get_state_data("email-send-123")
        assert data["email_to"] == "user@example.com"

        # 4. 콜백 도착 → 재개
        resolved = mgr.resolve_wait("email-send-123", "completed")
        assert resolved.resolution == "completed"
        assert resolved.resolved_at is not None

        # 5. 대기 목록 비움
        assert len(mgr.get_pending()) == 0

    def test_timeout_auto_cleanup(self, workspace):
        """타임아웃 초과 시 자동 정리."""
        tmp_path, _ = workspace
        from simpleclaw.daemon.wait_states import WaitStateManager
        from simpleclaw.daemon.store import DaemonStore
        from simpleclaw.daemon.models import WaitState

        store = DaemonStore(tmp_path / ".agent" / "wait_to.db")
        mgr = WaitStateManager(store, default_timeout=1)

        # 이미 만료된 상태 생성
        store.save_wait_state(WaitState(
            task_id="old-task",
            serialized_state='{"step": "abandoned"}',
            condition_type="timer",
            registered_at=datetime.now() - timedelta(minutes=10),
            timeout_seconds=1,
        ))

        # 아직 유효한 상태
        mgr.register_wait("new-task", {"step": "active"}, "callback", timeout=9999)

        # 타임아웃 체크
        expired = mgr.check_timeouts()
        assert len(expired) == 1
        assert expired[0].task_id == "old-task"

        # new-task은 여전히 대기 중
        pending = mgr.get_pending()
        assert len(pending) == 1
        assert pending[0].task_id == "new-task"
