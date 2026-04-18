"""Telegram User Command Scenarios.

Simulates a real Telegram user sending messages to the bot.
Each scenario verifies:
  1. User Command    — what the user types
  2. Expected Action — what the agent should DO (skill exec, cron create, etc.)
  3. Data Changes    — what changes in DB / files after the command

Architecture under test:
  TelegramBot.handle_message()
    → AgentOrchestrator.process_message()
      → Skill Router (LLM call #1)
      → Skill Execution (real subprocess)
      → Response Generation (LLM call #2)
      → ConversationStore (SQLite write)

Only LLM API calls are mocked. Everything else is real.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.channels.telegram_bot import TelegramBot


# ──────────────────────────────────────────────────────────
# Shared fixture: full agent environment
# ──────────────────────────────────────────────────────────


USER_ID = 123456
CHAT_ID = 789012


@pytest.fixture
def agent_env(tmp_path):
    """Build a complete agent environment and return (bot, orchestrator, paths)."""

    # ── Persona files ──
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "AGENT.md").write_text(
        "# SimpleClaw\n\n"
        "You are SimpleClaw, a personal assistant.\n"
        "Respond in the same language the user writes in.\n"
        "Be concise. Keep responses under 300 chars.\n"
    )
    (agent_dir / "USER.md").write_text(
        "# User\n\nName: 홍길동\nLanguage: Korean\n"
    )
    (agent_dir / "MEMORY.md").write_text(
        "# Memory\n\n- User set up the agent on 2026-04-17.\n"
    )

    # ── Skills with real executable scripts ──
    time_skill = agent_dir / "skills" / "time-skill"
    time_skill.mkdir(parents=True)
    (time_skill / "SKILL.md").write_text(
        "---\nname: time-skill\n"
        "description: Returns the current date and time. Use when user asks what time it is.\n"
        "---\n# Time Skill\n\n## Usage\n\n"
        f"```bash\n{sys.executable} {time_skill}/run.py\n```\n"
    )
    (time_skill / "run.py").write_text(
        "from datetime import datetime\n"
        "now = datetime.now()\n"
        "print(f'현재 시각: {now.strftime(\"%Y-%m-%d %H:%M:%S\")}')\n"
    )

    echo_skill = agent_dir / "skills" / "echo-skill"
    echo_skill.mkdir(parents=True)
    (echo_skill / "SKILL.md").write_text(
        "---\nname: echo-skill\n"
        "description: Echoes the input. Use when user asks to echo or repeat something.\n"
        "---\n# Echo\n\n## Usage\n\n"
        f"```bash\n{sys.executable} {echo_skill}/run.py {{{{text}}}}\n```\n"
    )
    (echo_skill / "run.py").write_text(
        'import sys; print("ECHO:", " ".join(sys.argv[1:]))\n'
    )

    calc_skill = agent_dir / "skills" / "calc-skill"
    calc_skill.mkdir(parents=True)
    (calc_skill / "SKILL.md").write_text(
        "---\nname: calc-skill\n"
        "description: Calculates a math expression. Use when user asks to calculate something.\n"
        "---\n# Calculator\n\n## Usage\n\n"
        f"```bash\n{sys.executable} {calc_skill}/run.py \"{{{{expression}}}}\"\n```\n"
    )
    (calc_skill / "run.py").write_text(
        'import sys\nexpr = " ".join(sys.argv[1:])\n'
        'try:\n    result = eval(expr)\n    print(f"Result: {result}")\n'
        'except Exception as e:\n    print(f"Error: {e}")\n'
    )

    # ── config.yaml ──
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
  db_path: "{agent_dir}/conversations.db"

skills:
  local_dir: "{agent_dir}/skills"
  global_dir: "{tmp_path}/global_skills"
  execution_timeout: 10

persona:
  token_budget: 4096
  local_dir: "{agent_dir}"
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
  pid_file: "{agent_dir}/daemon.pid"
  status_file: "{agent_dir}/HEARTBEAT.md"
  db_path: "{agent_dir}/daemon.db"
  dreaming:
    overnight_hour: 0
    idle_threshold: 1
""")

    (tmp_path / "global_skills").mkdir()
    (tmp_path / "global_persona").mkdir()
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=fake-key\n")

    # ── Build orchestrator and bot ──
    with patch.dict("os.environ", {"GOOGLE_API_KEY": "fake-key"}):
        from simpleclaw.agent import AgentOrchestrator

        orch = AgentOrchestrator(config)

    bot = TelegramBot(
        bot_token="fake-token",
        whitelist_user_ids=[USER_ID],
        message_handler=orch.process_message,
    )

    return bot, orch, tmp_path, agent_dir


def _mock_router(orch, routing_response, final_response):
    """Replace orch._router with a mock that returns specified responses.

    Args:
        routing_response: JSON string for skill router (or None to skip routing)
        final_response: text for the final agent response
    """
    call_count = 0

    async def mock_send(request):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.backend_name = "gemini"
        resp.model = "gemini-flash"
        if call_count == 1 and routing_response is not None:
            resp.text = routing_response
        else:
            resp.text = final_response
        return resp

    orch._router = MagicMock()
    orch._router.send = AsyncMock(side_effect=mock_send)
    return orch._router


# ══════════════════════════════════════════════════════════
# SCENARIO 1: 일반 인사
# ══════════════════════════════════════════════════════════


class TestCmd_Greeting:
    """
    사용자 명령: "안녕"
    예상 행동: 스킬 불필요 → LLM이 직접 응답
    데이터 변경: conversations.db에 user/assistant 메시지 2건 저장
    """

    @pytest.mark.asyncio
    async def test_greeting(self, agent_env):
        bot, orch, tmp_path, agent_dir = agent_env

        _mock_router(orch,
            routing_response='{"use_skill": false}',
            final_response="안녕하세요, 홍길동님! 무엇을 도와드릴까요?",
        )

        # ── 사용자 명령 ──
        response = await bot.handle_message("안녕", USER_ID, CHAT_ID)

        # ── 예상 행동 ──
        assert response is not None
        assert "홍길동" in response or "안녕" in response

        # ── 데이터 변경: DB ──
        db_path = agent_dir / "conversations.db"
        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT role, content FROM messages ORDER BY rowid").fetchall()
        conn.close()

        assert len(rows) == 2
        assert rows[0][0] == "user"
        assert rows[0][1] == "안녕"
        assert rows[1][0] == "assistant"
        assert "홍길동" in rows[1][1] or "안녕" in rows[1][1]


# ══════════════════════════════════════════════════════════
# SCENARIO 2: 스킬 실행 (시간 확인)
# ══════════════════════════════════════════════════════════


class TestCmd_TimeSkill:
    """
    사용자 명령: "지금 몇 시야?"
    예상 행동: 스킬 라우터 → time-skill 선택 → run.py 실행 → 결과 기반 응답
    데이터 변경:
      - conversations.db에 메시지 저장
      - LLM의 시스템 프롬프트에 "Skill Execution Result" 포함
    """

    @pytest.mark.asyncio
    async def test_time_skill(self, agent_env):
        bot, orch, tmp_path, agent_dir = agent_env

        time_script = agent_dir / "skills" / "time-skill" / "run.py"
        captured = {}

        call_count = 0
        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:  # Skill router
                resp.text = json.dumps({
                    "use_skill": True,
                    "skill_name": "time-skill",
                    "command": f"{sys.executable} {time_script}",
                })
            else:  # Final response
                captured["system_prompt"] = request.system_prompt
                resp.text = "현재 시각을 알려드리겠습니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        # ── 사용자 명령 ──
        response = await bot.handle_message("지금 몇 시야?", USER_ID, CHAT_ID)

        # ── 예상 행동: 스킬 실제 실행 ──
        assert response is not None
        assert "Skill Execution Result" in captured["system_prompt"]
        assert "현재 시각:" in captured["system_prompt"]

        # ── 데이터 변경: DB ──
        conn = sqlite3.connect(str(agent_dir / "conversations.db"))
        rows = conn.execute("SELECT role, content FROM messages").fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0][1] == "지금 몇 시야?"


# ══════════════════════════════════════════════════════════
# SCENARIO 3: 계산 스킬
# ══════════════════════════════════════════════════════════


class TestCmd_CalcSkill:
    """
    사용자 명령: "123 * 456 계산해줘"
    예상 행동: calc-skill 실행 → "Result: 56088" 반환
    데이터 변경: conversations.db에 저장, 시스템 프롬프트에 계산 결과 포함
    """

    @pytest.mark.asyncio
    async def test_calc(self, agent_env):
        bot, orch, tmp_path, agent_dir = agent_env

        calc_script = agent_dir / "skills" / "calc-skill" / "run.py"
        captured = {}

        call_count = 0
        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:
                resp.text = json.dumps({
                    "use_skill": True,
                    "skill_name": "calc-skill",
                    "command": f'{sys.executable} {calc_script} "123 * 456"',
                })
            else:
                captured["system_prompt"] = request.system_prompt
                resp.text = "123 × 456 = 56,088 입니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        # ── 사용자 명령 ──
        response = await bot.handle_message("123 * 456 계산해줘", USER_ID, CHAT_ID)

        # ── 예상 행동: 계산 결과가 컨텍스트에 포함 ──
        assert "Result: 56088" in captured["system_prompt"]

        # ── 데이터 변경 ──
        conn = sqlite3.connect(str(agent_dir / "conversations.db"))
        rows = conn.execute("SELECT content FROM messages WHERE role='user'").fetchall()
        conn.close()
        assert rows[0][0] == "123 * 456 계산해줘"


# ══════════════════════════════════════════════════════════
# SCENARIO 4: 멀티턴 대화 (맥락 유지)
# ══════════════════════════════════════════════════════════


class TestCmd_MultiTurn:
    """
    사용자 명령 1: "내 이름은 김철수야"
    사용자 명령 2: "내 이름이 뭐였지?"
    예상 행동: 두 번째 요청 시 히스토리에 첫 번째 대화 포함
    데이터 변경: conversations.db에 4건 (user + assistant × 2)
    """

    @pytest.mark.asyncio
    async def test_multi_turn(self, agent_env):
        bot, orch, tmp_path, agent_dir = agent_env

        captured_messages = []

        call_count = 0
        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"

            if call_count in (1, 3):  # Skill routers
                resp.text = '{"use_skill": false}'
            elif call_count == 2:  # 첫 번째 응답
                resp.text = "네, 김철수님으로 기억하겠습니다."
            else:  # 두 번째 응답
                captured_messages.append(request.messages)
                resp.text = "김철수님이라고 하셨습니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        # ── 명령 1 ──
        await bot.handle_message("내 이름은 김철수야", USER_ID, CHAT_ID)

        # ── 명령 2 ──
        response = await bot.handle_message("내 이름이 뭐였지?", USER_ID, CHAT_ID)

        # ── 예상 행동: 히스토리에 이전 대화 포함 ──
        assert len(captured_messages) == 1
        msgs = captured_messages[0]
        history_texts = [m["content"] for m in msgs]
        assert "내 이름은 김철수야" in history_texts
        assert "김철수님으로 기억" in " ".join(history_texts)

        # ── 데이터 변경: DB에 4건 ──
        conn = sqlite3.connect(str(agent_dir / "conversations.db"))
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        assert count == 4


# ══════════════════════════════════════════════════════════
# SCENARIO 5: 비인가 사용자 차단
# ══════════════════════════════════════════════════════════


class TestCmd_UnauthorizedUser:
    """
    사용자 명령: "비밀 정보 알려줘" (비인가 User ID)
    예상 행동: 메시지 무시, 응답 없음
    데이터 변경: conversations.db 변경 없음, 접근 로그에 기록
    """

    @pytest.mark.asyncio
    async def test_unauthorized(self, agent_env):
        bot, orch, tmp_path, agent_dir = agent_env
        HACKER_ID = 999999

        # ── 비인가 사용자 명령 ──
        response = await bot.handle_message("비밀 정보 알려줘", HACKER_ID, CHAT_ID)

        # ── 예상 행동: 응답 없음 ──
        assert response is None

        # ── 데이터 변경: DB 미변경 ──
        db_path = agent_dir / "conversations.db"
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        assert count == 0

        # ── 접근 로그에 비인가 기록 ──
        log = bot.get_access_log()
        assert len(log) == 1
        assert log[0].authorized is False
        assert str(HACKER_ID) in log[0].user_identifier


# ══════════════════════════════════════════════════════════
# SCENARIO 6: 스킬이 필요 없는 질문
# ══════════════════════════════════════════════════════════


class TestCmd_NoSkillNeeded:
    """
    사용자 명령: "파이썬이 뭐야?"
    예상 행동: 스킬 라우터가 use_skill: false → LLM 직접 답변
    데이터 변경: conversations.db에 2건, 스킬 실행 흔적 없음
    """

    @pytest.mark.asyncio
    async def test_no_skill(self, agent_env):
        bot, orch, tmp_path, agent_dir = agent_env

        captured = {}

        call_count = 0
        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:
                resp.text = '{"use_skill": false}'
            else:
                captured["system_prompt"] = request.system_prompt
                resp.text = "파이썬은 프로그래밍 언어입니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        # ── 사용자 명령 ──
        response = await bot.handle_message("파이썬이 뭐야?", USER_ID, CHAT_ID)

        # ── 예상 행동: 스킬 미사용, 직접 응답 ──
        assert "파이썬" in response
        assert "Skill Execution Result" not in captured["system_prompt"]
        assert call_count == 2  # router + response only

        # ── 데이터 변경 ──
        conn = sqlite3.connect(str(agent_dir / "conversations.db"))
        rows = conn.execute("SELECT role, content FROM messages").fetchall()
        conn.close()
        assert len(rows) == 2


# ══════════════════════════════════════════════════════════
# SCENARIO 7: 데몬에서 Cron Job 생성 및 실행
# ══════════════════════════════════════════════════════════


class TestCmd_CronJobLifecycle:
    """
    시뮬레이션: 사용자가 Cron Job을 생성하고 실행하는 전체 흐름.
    (텔레그램에서 직접 cron 생성 UI는 아직 없으므로 API 레벨에서 테스트)

    행동:
      1. 데몬 시작
      2. "매일 아침 9시 알림" cron job 생성
      3. job 수동 실행
      4. 실행 결과 DB에 기록 확인
      5. 데몬 정지 후 재시작 → job 복원 확인
    """

    @pytest.mark.asyncio
    async def test_cron_lifecycle(self, agent_env):
        bot, orch, tmp_path, agent_dir = agent_env
        from simpleclaw.daemon.daemon import AgentDaemon
        from simpleclaw.daemon.models import ActionType

        config = tmp_path / "config.yaml"
        daemon = AgentDaemon(config)
        await daemon.start()

        # ── 1. Cron Job 생성 ──
        cs = daemon.cron_scheduler
        cs.add_job(
            name="morning-alarm",
            cron_expression="0 9 * * *",
            action_type=ActionType.PROMPT,
            action_reference="좋은 아침입니다! 오늘의 할 일을 알려드릴게요.",
        )

        # ── 데이터 변경: daemon.db cron_jobs 테이블 ──
        daemon_db = str(agent_dir / "daemon.db")
        conn = sqlite3.connect(daemon_db)
        jobs = conn.execute("SELECT name, cron_expression, action_type, enabled FROM cron_jobs").fetchall()
        conn.close()

        assert len(jobs) == 1
        assert jobs[0][0] == "morning-alarm"
        assert jobs[0][1] == "0 9 * * *"
        assert jobs[0][2] == "prompt"
        assert jobs[0][3] == 1  # enabled

        # ── 2. 수동 실행 ──
        execution = await cs.execute_job("morning-alarm")
        assert execution.status.value == "success"

        # ── 데이터 변경: cron_executions 테이블 ──
        conn = sqlite3.connect(daemon_db)
        execs = conn.execute(
            "SELECT job_name, status, result_summary FROM cron_executions"
        ).fetchall()
        conn.close()

        assert len(execs) == 1
        assert execs[0][0] == "morning-alarm"
        assert execs[0][1] == "success"

        # ── 3. 데몬 정지 후 재시작 → Job 복원 ──
        await daemon.stop()

        daemon2 = AgentDaemon(config)
        await daemon2.start()

        restored_job = daemon2.cron_scheduler.get_job("morning-alarm")
        assert restored_job is not None
        assert restored_job.action_reference == "좋은 아침입니다! 오늘의 할 일을 알려드릴게요."

        await daemon2.stop()


# ══════════════════════════════════════════════════════════
# SCENARIO 8: Heartbeat → HEARTBEAT.md 파일 변경
# ══════════════════════════════════════════════════════════


class TestCmd_Heartbeat:
    """
    시뮬레이션: 데몬 시작 후 Heartbeat 틱이 HEARTBEAT.md를 업데이트.

    데이터 변경:
      - .agent/HEARTBEAT.md 파일 생성/갱신
      - PID 락 파일 생성/삭제
    """

    @pytest.mark.asyncio
    async def test_heartbeat_file_changes(self, agent_env):
        bot, orch, tmp_path, agent_dir = agent_env
        from simpleclaw.daemon.daemon import AgentDaemon

        config = tmp_path / "config.yaml"
        daemon = AgentDaemon(config)

        pid_file = agent_dir / "daemon.pid"
        hb_file = agent_dir / "HEARTBEAT.md"

        # ── 시작 전: 파일 없음 ──
        assert not pid_file.exists()

        await daemon.start()

        # ── 데이터 변경: PID 파일 생성 ──
        assert pid_file.exists()
        pid_content = pid_file.read_text().strip()
        assert pid_content.isdigit()

        # ── 데이터 변경: HEARTBEAT.md 생성 ──
        assert hb_file.exists()
        hb = hb_file.read_text()
        assert "**Status**: running" in hb
        assert "**Last Tick**:" in hb
        assert "**Dirty State**: false" in hb
        assert "**Cron Jobs Active**: 0" in hb

        # ── 추가 틱 후 파일 갱신 ──
        tick1_time = hb_file.stat().st_mtime
        await daemon.heartbeat.tick()
        tick2_time = hb_file.stat().st_mtime
        assert tick2_time >= tick1_time  # 파일이 갱신됨

        # ── 정지 후: PID 파일 삭제 ──
        await daemon.stop()
        assert not pid_file.exists()


# ══════════════════════════════════════════════════════════
# SCENARIO 9: 드리밍 → MEMORY.md 갱신 + .bak 백업
# ══════════════════════════════════════════════════════════


class TestCmd_DreamingMemoryUpdate:
    """
    시뮬레이션: 대화 후 드리밍 조건 충족 → MEMORY.md 자동 업데이트.

    데이터 변경:
      - .agent/MEMORY.md 내용 추가
      - .agent/MEMORY.*.bak 백업 파일 생성
      - daemon.db에 last_dreaming_timestamp 기록
    """

    @pytest.mark.asyncio
    async def test_dreaming_updates_memory(self, agent_env):
        bot, orch, tmp_path, agent_dir = agent_env
        from simpleclaw.daemon.dreaming_trigger import DreamingTrigger, LAST_DREAMING_KEY
        from simpleclaw.daemon.store import DaemonStore
        from simpleclaw.memory.conversation_store import ConversationStore
        from simpleclaw.memory.dreaming import DreamingPipeline
        from simpleclaw.memory.models import ConversationMessage, MessageRole

        memory_file = agent_dir / "MEMORY.md"
        original = memory_file.read_text()

        # 대화 DB에 오래된 대화 추가 (3시간 전)
        conv_store = ConversationStore(agent_dir / "dream_conv.db")
        conv_store.add_message(ConversationMessage(
            role=MessageRole.USER,
            content="내일 회의 준비해줘",
            timestamp=datetime.now() - timedelta(hours=3),
        ))
        conv_store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT,
            content="내일 회의 자료를 준비하겠습니다.",
            timestamp=datetime.now() - timedelta(hours=3),
        ))

        daemon_store = DaemonStore(agent_dir / "dream_daemon.db")
        pipeline = DreamingPipeline(conv_store, memory_file)
        trigger = DreamingTrigger(
            conv_store, pipeline, daemon_store,
            overnight_hour=0, idle_threshold=1,
        )

        # ── 드리밍 실행 ──
        assert await trigger.should_run() is True
        await trigger.execute()

        # ── 데이터 변경 1: MEMORY.md 내용 추가 ──
        updated = memory_file.read_text()
        assert len(updated) > len(original)
        assert "Session" in updated

        # ── 데이터 변경 2: .bak 백업 생성 ──
        backups = list(agent_dir.glob("MEMORY.*.bak"))
        assert len(backups) >= 1
        backup_content = backups[0].read_text()
        assert backup_content == original  # 백업 = 변경 전 내용

        # ── 데이터 변경 3: daemon_state에 타임스탬프 ──
        last = daemon_store.get_state(LAST_DREAMING_KEY)
        assert last is not None
        ts = datetime.fromisoformat(last)
        assert (datetime.now() - ts).total_seconds() < 10

        # ── 같은 날 재실행 방지 ──
        assert await trigger.should_run() is False


# ══════════════════════════════════════════════════════════
# SCENARIO 10: 페르소나가 응답에 반영되는지
# ══════════════════════════════════════════════════════════


class TestCmd_PersonaReflection:
    """
    사용자 명령: "너는 누구야?"
    예상 행동: 시스템 프롬프트에 AGENT.md + USER.md 내용 포함
    데이터 변경: conversations.db에 저장
    """

    @pytest.mark.asyncio
    async def test_persona_in_prompt(self, agent_env):
        bot, orch, tmp_path, agent_dir = agent_env

        captured = {}

        call_count = 0
        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:
                resp.text = '{"use_skill": false}'
            else:
                captured["system_prompt"] = request.system_prompt
                captured["messages"] = request.messages
                resp.text = "저는 SimpleClaw입니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        await bot.handle_message("너는 누구야?", USER_ID, CHAT_ID)

        # ── 시스템 프롬프트에 페르소나 반영 ──
        sp = captured["system_prompt"]
        assert "SimpleClaw" in sp        # AGENT.md
        assert "홍길동" in sp            # USER.md
        assert "2026-04-17" in sp        # MEMORY.md

        # ── 스킬 목록도 포함 ──
        assert "time-skill" in sp
        assert "calc-skill" in sp
        assert "echo-skill" in sp
