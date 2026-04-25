"""Real Skill Command Scenarios.

High-difficulty test scenarios simulating real user commands that require
skill execution. Uses the actual installed skills from ~/.agents/skills/.

Each test:
  1. User Command     - natural language request via Telegram
  2. Expected Action  - which skill is selected, what command is built
  3. Data Changes     - DB writes, system prompt content, file changes

Architecture (ReAct pattern):
  TelegramBot.handle_message()
    -> AgentOrchestrator.process_message()
      -> _react_loop()
        -> _react_step()       - LLM decides Action or Answer (MOCKED)
        -> _execute_command()   - REAL subprocess execution
        -> _react_step()       - LLM reads Observation (MOCKED)
      -> ConversationStore     - REAL SQLite write

Note: Skill scripts that require API keys (Gmail OAuth, Google Calendar,
Naver API, etc.) will fail at runtime. Tests verify the PIPELINE works
correctly by checking:
  - Correct skill was selected
  - Correct command was constructed
  - Command was actually executed (subprocess ran)
  - Output (or error) was captured and passed to response LLM
  - Conversation was stored in DB
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.channels.telegram_bot import TelegramBot


USER_ID = 123456
CHAT_ID = 789012

# Paths to real installed skills
SKILLS_DIR = Path.home() / ".agents" / "skills"


@pytest.fixture
def real_env(tmp_path):
    """Build agent environment with REAL global skills from ~/.agents/skills/."""

    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "AGENT.md").write_text(
        "# SimpleClaw\n\nYou are SimpleClaw, a personal assistant.\n"
        "Respond in Korean. Be concise.\n"
    )
    (agent_dir / "USER.md").write_text("# User\n\nName: 홍길동\n")

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
  global_dir: "{SKILLS_DIR}"
  execution_timeout: 15
persona:
  token_budget: 4096
  local_dir: "{agent_dir}"
  global_dir: "{tmp_path}/gp"
  files:
    - name: AGENT.md
      type: agent
    - name: USER.md
      type: user
""")
    (agent_dir / "skills").mkdir()
    (tmp_path / "gp").mkdir()
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=fake\n")

    with patch.dict("os.environ", {"GOOGLE_API_KEY": "fake"}):
        from simpleclaw.agent import AgentOrchestrator
        orch = AgentOrchestrator(config)

    bot = TelegramBot(
        bot_token="fake",
        whitelist_user_ids=[USER_ID],
        message_handler=orch.process_message,
    )

    return bot, orch, agent_dir


def _skill_exists(name: str) -> bool:
    return (SKILLS_DIR / name / "SKILL.md").is_file()


# ======================================================
# SCENARIO 1: "여의도 근처 맛집 찾아줘"
# Expected Skill: local-route-skill (search command)
# ======================================================


@pytest.mark.skipif(not _skill_exists("local-route-skill"),
                    reason="local-route-skill not installed")
class TestCmd_FindRestaurant:

    @pytest.mark.asyncio
    async def test_find_restaurant(self, real_env):
        bot, orch, agent_dir = real_env

        skill_path = SKILLS_DIR / "local-route-skill"
        script = f"{skill_path}/scripts/venv/bin/python {skill_path}/scripts/search_and_route.py"

        captured = {}
        call_count = 0

        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:
                # ReAct: Action
                resp.text = (
                    'Thought: 사용자가 맛집을 찾고 싶어합니다.\n'
                    f'Action: {{"skill_name": "local-route-skill", "command": "{script} search --query \\"여의도 맛집\\""}}'
                )
            else:
                # ReAct: Answer (with Observation in trace)
                captured["system_prompt"] = request.system_prompt
                captured["user_message"] = request.user_message
                resp.text = "Thought: 검색 결과를 확인했습니다.\nAnswer: 여의도 맛집을 검색했습니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        response = await bot.handle_message("여의도 근처 맛집 찾아줘", USER_ID, CHAT_ID)

        assert response is not None
        # Observation should be in the trace (user_message of second call)
        assert "Observation:" in captured["user_message"]
        assert "local-route-skill" in captured["user_message"]

        # DB storage
        conn = sqlite3.connect(str(agent_dir / "conversations.db"))
        rows = conn.execute("SELECT role, content FROM messages").fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0][1] == "여의도 근처 맛집 찾아줘"


# ======================================================
# SCENARIO 2: "내 일정 확인해봐"
# Expected Skill: google-calendar-skill (list command)
# ======================================================


@pytest.mark.skipif(not _skill_exists("google-calendar-skill"),
                    reason="google-calendar-skill not installed")
class TestCmd_CheckCalendar:

    @pytest.mark.asyncio
    async def test_check_calendar(self, real_env):
        bot, orch, agent_dir = real_env

        skill_path = SKILLS_DIR / "google-calendar-skill"
        script = f"{skill_path}/scripts/venv/bin/python {skill_path}/scripts/gcal.py"

        captured = {}
        call_count = 0

        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:
                resp.text = (
                    'Thought: 사용자의 일정을 확인해야 합니다.\n'
                    f'Action: {{"skill_name": "google-calendar-skill", "command": "{script} list --days 7 --limit 10"}}'
                )
            else:
                captured["system_prompt"] = request.system_prompt
                captured["user_message"] = request.user_message
                resp.text = "Thought: 일정 정보를 확인했습니다.\nAnswer: 이번 주 일정을 확인했습니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        response = await bot.handle_message("내 일정 확인해봐", USER_ID, CHAT_ID)

        assert response is not None
        assert "Observation:" in captured["user_message"]
        assert "google-calendar-skill" in captured["user_message"]

        conn = sqlite3.connect(str(agent_dir / "conversations.db"))
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        assert count == 2


# ======================================================
# SCENARIO 3: "오늘 미국 주식 주요 뉴스 알려줘"
# Expected Skill: us-stock-skill (news command)
# ======================================================


@pytest.mark.skipif(not _skill_exists("us-stock-skill"),
                    reason="us-stock-skill not installed")
class TestCmd_USStockNews:

    @pytest.mark.asyncio
    async def test_stock_news(self, real_env):
        bot, orch, agent_dir = real_env

        skill_path = SKILLS_DIR / "us-stock-skill"
        script = f"{skill_path}/scripts/venv/bin/python {skill_path}/scripts/us_stock.py"

        captured = {}
        call_count = 0

        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:
                resp.text = (
                    'Thought: 미국 주식 뉴스를 검색해야 합니다.\n'
                    f'Action: {{"skill_name": "us-stock-skill", "command": "{script} news --symbol SPY --limit 5"}}'
                )
            else:
                captured["user_message"] = request.user_message
                resp.text = "Thought: 뉴스를 확인했습니다.\nAnswer: 미국 주식 뉴스를 정리했습니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        response = await bot.handle_message(
            "오늘 미국 주식 주요 뉴스 알려줘", USER_ID, CHAT_ID
        )

        assert response is not None
        assert "Observation:" in captured["user_message"]
        assert "us-stock-skill" in captured["user_message"]

        conn = sqlite3.connect(str(agent_dir / "conversations.db"))
        rows = conn.execute("SELECT content FROM messages WHERE role='user'").fetchall()
        conn.close()
        assert rows[0][0] == "오늘 미국 주식 주요 뉴스 알려줘"


# ======================================================
# SCENARIO 4: "읽지 않은 메일 확인해줘"
# Expected Skill: gmail-skill (search command)
# ======================================================


@pytest.mark.skipif(not _skill_exists("gmail-skill"),
                    reason="gmail-skill not installed")
class TestCmd_CheckEmail:

    @pytest.mark.asyncio
    async def test_check_email(self, real_env):
        bot, orch, agent_dir = real_env

        skill_path = SKILLS_DIR / "gmail-skill"
        script = f"{skill_path}/scripts/venv/bin/python {skill_path}/scripts/gmail.py"

        captured = {}
        call_count = 0

        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:
                resp.text = (
                    'Thought: 읽지 않은 메일을 확인해야 합니다.\n'
                    f'Action: {{"skill_name": "gmail-skill", "command": "{script} search --query \\"category:primary is:unread\\" --limit 5"}}'
                )
            else:
                captured["user_message"] = request.user_message
                resp.text = "Thought: 메일을 확인했습니다.\nAnswer: 읽지 않은 메일을 확인했습니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        response = await bot.handle_message("읽지 않은 메일 확인해줘", USER_ID, CHAT_ID)

        assert response is not None
        assert "Observation:" in captured["user_message"]
        assert "gmail-skill" in captured["user_message"]


# ======================================================
# SCENARIO 5: "최신 AI 뉴스 검색해줘"
# Expected Skill: news-search-skill
# ======================================================


@pytest.mark.skipif(not _skill_exists("news-search-skill"),
                    reason="news-search-skill not installed")
class TestCmd_NewsSearch:

    @pytest.mark.asyncio
    async def test_news_search(self, real_env):
        bot, orch, agent_dir = real_env

        skill_path = SKILLS_DIR / "news-search-skill"
        script = f"{skill_path}/scripts/venv/bin/python {skill_path}/scripts/news_search.py"

        captured = {}
        call_count = 0

        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:
                resp.text = (
                    'Thought: 뉴스를 검색해야 합니다.\n'
                    f'Action: {{"skill_name": "news-search-skill", "command": "{script} --query \\"최신 AI 뉴스\\""}}'
                )
            else:
                captured["user_message"] = request.user_message
                resp.text = "Thought: 뉴스를 찾았습니다.\nAnswer: 최신 AI 뉴스를 찾았습니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        response = await bot.handle_message("최신 AI 뉴스 검색해줘", USER_ID, CHAT_ID)

        assert response is not None
        assert "Observation:" in captured["user_message"]


# ======================================================
# SCENARIO 6: "AAPL 주가 정보 알려줘"
# Expected Skill: us-stock-skill (info command)
# ======================================================


@pytest.mark.skipif(not _skill_exists("us-stock-skill"),
                    reason="us-stock-skill not installed")
class TestCmd_StockInfo:

    @pytest.mark.asyncio
    async def test_stock_info(self, real_env):
        bot, orch, agent_dir = real_env

        skill_path = SKILLS_DIR / "us-stock-skill"
        script = f"{skill_path}/scripts/venv/bin/python {skill_path}/scripts/us_stock.py"

        captured = {}
        call_count = 0

        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:
                resp.text = (
                    'Thought: AAPL 주가 정보를 조회해야 합니다.\n'
                    f'Action: {{"skill_name": "us-stock-skill", "command": "{script} info --symbol AAPL"}}'
                )
            else:
                captured["user_message"] = request.user_message
                resp.text = "Thought: 주가 데이터를 확인했습니다.\nAnswer: AAPL 주가 정보입니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        response = await bot.handle_message("AAPL 주가 정보 알려줘", USER_ID, CHAT_ID)

        assert response is not None
        assert "Observation:" in captured["user_message"]
        # yfinance data or error should be in the observation
        assert len(captured["user_message"]) > 100


# ======================================================
# SCENARIO 7: Multi-skill conversation
# "AAPL 주가 알려줘" -> "관련 뉴스도 찾아줘"
# ======================================================


@pytest.mark.skipif(not _skill_exists("us-stock-skill"),
                    reason="us-stock-skill not installed")
class TestCmd_MultiSkillConversation:

    @pytest.mark.asyncio
    async def test_multi_skill(self, real_env):
        bot, orch, agent_dir = real_env

        skill_path = SKILLS_DIR / "us-stock-skill"
        script = f"{skill_path}/scripts/venv/bin/python {skill_path}/scripts/us_stock.py"

        captured_messages = []
        call_count = 0

        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"

            if call_count == 1:  # First message: Action
                resp.text = (
                    'Thought: AAPL 주가를 조회합니다.\n'
                    f'Action: {{"skill_name": "us-stock-skill", "command": "{script} info --symbol AAPL"}}'
                )
            elif call_count == 2:  # First message: Answer
                resp.text = "Thought: 주가 정보를 확인했습니다.\nAnswer: AAPL 주가는 현재 $195입니다."
            elif call_count == 3:  # Second message: Action
                resp.text = (
                    'Thought: 관련 뉴스를 검색합니다.\n'
                    f'Action: {{"skill_name": "us-stock-skill", "command": "{script} news --symbol AAPL --limit 3"}}'
                )
            else:  # Second message: Answer
                captured_messages.append(request.messages)
                resp.text = "Thought: 뉴스를 확인했습니다.\nAnswer: AAPL 관련 뉴스를 정리했습니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        # Command 1
        await bot.handle_message("AAPL 주가 알려줘", USER_ID, CHAT_ID)

        # Command 2
        response = await bot.handle_message("관련 뉴스도 찾아줘", USER_ID, CHAT_ID)

        # History should contain first conversation
        assert len(captured_messages) == 1
        history = [m["content"] for m in captured_messages[0]]
        assert "AAPL 주가 알려줘" in history

        # DB should have 4 entries
        conn = sqlite3.connect(str(agent_dir / "conversations.db"))
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        assert count == 4


# ======================================================
# SCENARIO 8: "집에서 강남역까지 얼마나 걸려?"
# Expected: local-route-skill -> find-and-go / route
# ======================================================


@pytest.mark.skipif(not _skill_exists("local-route-skill"),
                    reason="local-route-skill not installed")
class TestCmd_RouteQuery:

    @pytest.mark.asyncio
    async def test_route_query(self, real_env):
        bot, orch, agent_dir = real_env

        skill_path = SKILLS_DIR / "local-route-skill"
        script = f"{skill_path}/scripts/venv/bin/python {skill_path}/scripts/search_and_route.py"

        captured = {}
        call_count = 0

        async def mock_send(request):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.backend_name = "gemini"
            if call_count == 1:
                resp.text = (
                    'Thought: 경로를 검색해야 합니다.\n'
                    f'Action: {{"skill_name": "local-route-skill", "command": "{script} find-and-go --origin \\"집\\" --destination \\"강남역\\" --travel-mode \\"TRANSIT\\""}}'
                )
            else:
                captured["user_message"] = request.user_message
                resp.text = "Thought: 경로 정보를 확인했습니다.\nAnswer: 집에서 강남역까지 약 40분 소요됩니다."
            return resp

        orch._router = MagicMock()
        orch._router.send = AsyncMock(side_effect=mock_send)

        response = await bot.handle_message(
            "집에서 강남역까지 얼마나 걸려?", USER_ID, CHAT_ID
        )

        assert response is not None
        assert "Observation:" in captured["user_message"]
        assert "local-route-skill" in captured["user_message"]
