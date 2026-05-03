"""Telegram bot launcher.

Thin wrapper that wires together:
- AgentOrchestrator (core agent logic)
- TelegramBot (message handling)
- CronScheduler (scheduled tasks + notification)
- DreamingTrigger (야간 자동 대화 요약 → MEMORY.md/USER.md 갱신)

All business logic lives in core modules. This script only does setup.

Usage:
    .venv/bin/python scripts/run_bot.py
"""

import asyncio
import logging
import os
import signal
import subprocess

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.channels.admin_api_setup import (
    AdminAPIBootError,
    build_admin_api_server,
)
from simpleclaw.channels.telegram_bot import TelegramBot
from simpleclaw.config import load_daemon_config, load_llm_config, load_telegram_config
from simpleclaw.daemon.dreaming_trigger import DreamingTrigger
from simpleclaw.daemon.scheduler import CronScheduler
from simpleclaw.daemon.store import DaemonStore
from simpleclaw.logging.dashboard import DashboardServer
from simpleclaw.logging.metrics import MetricsCollector
from simpleclaw.logging.structured_logger import StructuredLogger
from simpleclaw.memory.clustering import IncrementalClusterer
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CONFIG_PATH = "config.yaml"


def _kill_existing_bots():
    """Kill any other run_bot.py processes to prevent 409 Conflict."""
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ["pgrep", "-f", "run_bot.py"],
            capture_output=True, text=True,
        )
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            pid = int(line.strip())
            if pid != my_pid:
                logger.warning("Killing existing bot process PID %d", pid)
                os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _create_telegram_notifier(bot_token: str, chat_id: int):
    """Create an async notifier that sends cron results to Telegram."""
    async def notifier(job_name: str, text: str) -> None:
        from telegram import Bot
        tg_bot = Bot(token=bot_token)
        async with tg_bot:
            await tg_bot.send_message(chat_id=chat_id, text=text[:4096])

    return notifier


async def main():
    _kill_existing_bots()

    tg_config = load_telegram_config(CONFIG_PATH)
    if not tg_config["bot_token"]:
        print("ERROR: telegram.bot_token not set in config.yaml")
        return

    # 모니터링: 단일 MetricsCollector 인스턴스를 오케스트레이터·대시보드가 공유.
    # 서브프로세스 종료/좀비 회수 카운터(`process_kills_*`, `process_group_leaks`,
    # `zombies_reaped`)가 운영 환경에서 자동 누적되도록 한다.
    metrics = MetricsCollector()
    structured_logger = StructuredLogger()

    # Core modules
    # structured_logger를 함께 주입하여 RAG 회상(action_type="rag_retrieve") 이벤트를
    # 일별 JSONL 로그로 적재한다(BIZ-29 토큰 절감 추세 분석 입력).
    orchestrator = AgentOrchestrator(
        CONFIG_PATH,
        metrics=metrics,
        structured_logger=structured_logger,
    )

    whitelist = tg_config["whitelist"]
    bot = TelegramBot(
        bot_token=tg_config["bot_token"],
        whitelist_user_ids=whitelist["user_ids"],
        whitelist_chat_ids=whitelist["chat_ids"],
        message_handler=orchestrator.process_message,
    )

    # Cron scheduler — notifier is the only external wiring
    daemon_config = load_daemon_config(CONFIG_PATH)
    daemon_store = DaemonStore(daemon_config["db_path"])
    apscheduler = AsyncIOScheduler()

    notify_chat_id = whitelist["user_ids"][0] if whitelist["user_ids"] else None
    notifier = None
    if notify_chat_id:
        notifier = _create_telegram_notifier(
            tg_config["bot_token"], notify_chat_id
        )
        logger.info("Cron → Telegram notification enabled (chat_id=%d)", notify_chat_id)

    cron = CronScheduler(
        daemon_store, apscheduler,
        agent_orchestrator=orchestrator,
        notifier=notifier,
    )

    # Wire cron scheduler into orchestrator for /cron commands
    orchestrator.set_cron_scheduler(cron)

    # Dreaming — 야간 자동 대화 요약 (5분마다 조건 체크)
    dreaming_config = daemon_config.get("dreaming", {})
    # orchestrator와 동일한 대화 DB를 사용
    from simpleclaw.config import load_agent_config
    agent_config = load_agent_config(CONFIG_PATH)
    conv_store = ConversationStore(agent_config["db_path"])
    llm_router = orchestrator._router  # 오케스트레이터의 LLM 라우터 재사용

    # Phase 3 그래프형 드리밍 — enable_clusters=True일 때만 IncrementalClusterer를 주입한다.
    # threshold는 multilingual-e5-small 경험적 컷(0.75) 기준이며 config로 튜닝 가능.
    enable_clusters = dreaming_config.get("enable_clusters", False)
    cluster_threshold = dreaming_config.get("cluster_threshold", 0.75)
    clusterer = (
        IncrementalClusterer(threshold=cluster_threshold)
        if enable_clusters
        else None
    )

    dreaming_pipeline = DreamingPipeline(
        conversation_store=conv_store,
        memory_file=".agent/MEMORY.md",
        user_file=".agent/USER.md",
        soul_file=".agent/SOUL.md",
        agent_file=".agent/AGENT.md",
        llm_router=llm_router,
        dreaming_model=dreaming_config.get("model", ""),
        clusterer=clusterer,
        enable_clusters=enable_clusters,
        # BIZ-73: 인사이트 메타 sidecar — USER.md 옆에 두어 운영자 검수가 쉽도록.
        insights_file=".agent/insights.jsonl",
        insight_promotion_threshold=dreaming_config.get(
            "insight_promotion_threshold", 3
        ),
        # BIZ-79: Dry-run + Admin Review Loop. 기본은 dry_run=true 로 모든 새 인사이트가
        # 큐(insight_suggestions.jsonl)에 적재되며, auto_promote 두 조건을 동시에 만족하면
        # 자동 승격된다. 큐와 blocklist 도 .agent/ 하위에 둔다 — 운영자 검수 산출물.
        dry_run_enabled=dreaming_config.get("dry_run", True),
        auto_promote_confidence=dreaming_config.get("auto_promote", {}).get(
            "confidence", 0.7
        ),
        auto_promote_evidence_count=dreaming_config.get("auto_promote", {}).get(
            "evidence_count", 3
        ),
        suggestions_file=".agent/insight_suggestions.jsonl",
        blocklist_file=".agent/insight_blocklist.jsonl",
    )
    dreaming_trigger = DreamingTrigger(
        conversation_store=conv_store,
        dreaming_pipeline=dreaming_pipeline,
        daemon_store=daemon_store,
        overnight_hour=dreaming_config.get("overnight_hour", 3),
        idle_threshold=dreaming_config.get("idle_threshold", 7200),
    )

    async def _dreaming_check():
        """APScheduler interval job: 드리밍 조건 체크 및 실행."""
        try:
            if await dreaming_trigger.should_run():
                await dreaming_trigger.execute()
        except Exception:
            logger.exception("Dreaming check failed")

    apscheduler.add_job(
        _dreaming_check, "interval", minutes=5, id="dreaming-check",
    )
    logger.info(
        "Dreaming enabled: overnight_hour=%d, idle_threshold=%ds, check every 5min, "
        "clusters=%s (threshold=%.2f)",
        dreaming_config.get("overnight_hour", 3),
        dreaming_config.get("idle_threshold", 7200),
        "on" if enable_clusters else "off",
        cluster_threshold,
    )

    # 대시보드 — 메트릭 스냅샷을 127.0.0.1:8081에 노출.
    # 외부 노출 없이 로컬 점검 용도로만 바인딩한다.
    dashboard = DashboardServer(
        metrics=metrics,
        structured_logger=structured_logger,
        host="127.0.0.1",
        port=8081,
        conversation_store=conv_store,
    )
    try:
        await dashboard.start()
    except Exception as exc:  # noqa: BLE001 — 대시보드 실패는 봇 동작을 막지 않음.
        logger.warning("Dashboard failed to start: %s", exc)
        dashboard = None

    # Admin API (BIZ-58) — Admin UI 백엔드. 토큰 검증·시크릿/감사 매니저는 모두
    # build_admin_api_server에 위임. ``admin_api.enabled=False``면 None이 반환된다.
    # 토큰이 비어 있으면 부팅 단계에서 RuntimeError가 올라가 봇 시작이 즉시 실패한다 —
    # silent insecure 운용 방지.
    admin_api = None
    try:
        # 헬스 콜백 — 데몬 메인 헬스(텔레그램/대시보드/cron 상태)를
        # ``/admin/v1/health`` 응답에 머지한다.
        def _admin_health() -> dict:
            return {
                "daemon": {
                    "telegram_running": bool(getattr(bot, "is_running", False)),
                    "dashboard_running": dashboard is not None,
                    "cron_jobs_active": len(cron.list_jobs()),
                    "scheduler_running": apscheduler.running,
                }
            }

        admin_api = build_admin_api_server(
            CONFIG_PATH,
            structured_logger=structured_logger,
            health_provider=_admin_health,
            # BIZ-77 — 인사이트 source 역추적 엔드포인트(/memory/insights/{id}/sources)
            # 가 두 의존성을 모두 사용한다. dreaming_pipeline 가 같은 sidecar 경로를
            # 쓰므로 동일 InsightStore 를 공유한다.
            conversation_store=conv_store,
            insight_store=dreaming_pipeline.insight_store,
            # BIZ-79 — Dreaming Dry-run + Admin Review Loop. 큐/blocklist 도 같은
            # 인스턴스를 공유해 admin 액션이 다음 dreaming 사이클에 즉시 반영되도록.
            suggestion_store=dreaming_pipeline.suggestion_store,
            insight_blocklist=dreaming_pipeline.blocklist,
        )
    except AdminAPIBootError as exc:
        # 명시적 부팅 실패 — 토큰 미설정/검증 실패 등을 사유와 함께 stderr에 남기고 종료.
        print(f"ERROR: Admin API 부팅 실패 — {exc}")
        if dashboard is not None:
            try:
                await dashboard.stop()
            except Exception:  # noqa: BLE001
                logger.exception("Dashboard stop failed during admin boot abort")
        return

    if admin_api is not None:
        try:
            await admin_api.start()
        except Exception as exc:  # noqa: BLE001 — 포트 충돌 등은 명시적 에러로 종료.
            print(f"ERROR: Admin API 바인딩 실패 — {exc}")
            if dashboard is not None:
                try:
                    await dashboard.stop()
                except Exception:  # noqa: BLE001
                    logger.exception("Dashboard stop failed during admin bind abort")
            return

    # Start cron + dreaming scheduler
    apscheduler.start()
    cron.load_persisted_jobs()
    jobs = cron.list_jobs()
    logger.info("Cron scheduler started with %d jobs.", len(jobs))
    for j in jobs:
        status = "ON" if j.enabled else "OFF"
        logger.info("  [%s] %s: %s → %s", status, j.name, j.cron_expression, j.action_reference[:60])

    # Start bot
    print("Starting Telegram bot...")
    print(f"Whitelist User IDs: {whitelist['user_ids']}")
    print("Press Ctrl+C to stop.\n")

    await bot.start()
    if not bot.is_running:
        print("Bot failed to start. Check your token.")
        return

    print("Bot is running!\n")

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, stop_event.set)
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)

    await stop_event.wait()
    print("\nStopping...")
    if apscheduler.running:
        apscheduler.shutdown(wait=False)
    if admin_api is not None:
        try:
            # AppRunner.cleanup()이 진행 중 요청을 마무리한다 — graceful shutdown.
            await admin_api.stop()
        except Exception:  # noqa: BLE001 — 종료 경로에서 예외 흡수.
            logger.exception("Admin API stop failed")
    if dashboard is not None:
        try:
            await dashboard.stop()
        except Exception:  # noqa: BLE001 — 종료 경로에서 예외 흡수.
            logger.exception("Dashboard stop failed")
    await bot.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
