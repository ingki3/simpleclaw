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
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.channels.admin_api_setup import (
    AdminAPIBootError,
    build_admin_api_server,
)
from simpleclaw.channels.telegram_bot import TelegramBot
from simpleclaw.config import (
    load_agent_config,
    load_daemon_config,
    load_persona_config,
    load_telegram_config,
)
from simpleclaw.daemon.dreaming_trigger import LAST_DREAMING_KEY, DreamingTrigger
from simpleclaw.daemon.scheduler import CronScheduler
from simpleclaw.daemon.store import DaemonStore
from simpleclaw.logging.dashboard import DashboardServer
from simpleclaw.logging.metrics import MetricsCollector
from simpleclaw.logging.structured_logger import StructuredLogger
from simpleclaw.memory.clustering import IncrementalClusterer
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.language_policy import LanguagePolicy

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

    # Cron scheduler — notifier is the only external wiring.
    # BIZ-133: daemon.db_path 도 운영 디렉터리(`~/.simpleclaw/`) 기본 — `~` 확장과
    # 부모 디렉토리 생성을 호출 측에서 보장한다.
    daemon_config = load_daemon_config(CONFIG_PATH)
    daemon_db_path = Path(daemon_config["db_path"]).expanduser()
    daemon_db_path.parent.mkdir(parents=True, exist_ok=True)
    daemon_store = DaemonStore(daemon_db_path)
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
    agent_config = load_agent_config(CONFIG_PATH)
    conv_store = ConversationStore(Path(agent_config["db_path"]).expanduser())
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

    # BIZ-74 / BIZ-133 — Active Projects: 기본 활성. 운영자가 끄려면 config 에서
    # enabled=false. sidecar_path 는 운영 디렉터리(``~/.simpleclaw/``) 기본값을 따른다.
    # config 로더가 항상 enabled/window_days/sidecar_path 세 키를 채워주므로 fallback 불필요.
    active_projects_cfg = dreaming_config.get("active_projects", {}) or {}
    active_projects_enabled = bool(active_projects_cfg.get("enabled", True))
    active_projects_file = (
        active_projects_cfg.get("sidecar_path") if active_projects_enabled else None
    )
    active_projects_window_days = int(
        active_projects_cfg.get("window_days", 7)
    )

    # BIZ-80: 1차 언어 정책. config.dreaming.language 가 없거나 primary=null 이면
    # enforcement 가 꺼져 있어 LLM 출력이 그대로 통과한다(레거시 호환). 기본값은
    # 한국어 — 영어 입력에서도 USER/MEMORY/AGENT dreaming 산출물이 한국어로 통일.
    language_cfg = dreaming_config.get("language", {}) or {}
    language_policy = LanguagePolicy(
        primary=language_cfg.get("primary"),
        min_ratio=float(language_cfg.get("min_ratio", 0.3)),
        per_file=dict(language_cfg.get("per_file", {}) or {}),
    )

    # BIZ-133: 페르소나 라이브 파일은 persona.local_dir (기본 `~/.simpleclaw/`) 하위에
    # 위치한다. dreaming sidecar 경로는 daemon.dreaming.* 키에서 직접 읽는다 — 코드에
    # `.agent/...` 같은 하드코드를 두지 않아 운영 디렉터리 이전이 config 한 곳에서
    # 끝나도록 한다.
    persona_config = load_persona_config(CONFIG_PATH)
    persona_local_dir = Path(persona_config["local_dir"]).expanduser()
    persona_local_dir.mkdir(parents=True, exist_ok=True)

    def _expand(path_str: str | None) -> str | None:
        """config 의 raw 경로를 ``~/`` 확장된 절대 경로로 풀어준다.

        DreamingPipeline 와 sidecar store 들은 내부에서 ``Path(...)`` 만 호출하므로
        ``~`` 가 그대로 남으면 working tree 의 리터럴 ``~`` 디렉터리에 떨어지는
        사고가 난다. 이 헬퍼가 wiring 한 곳에서 일괄적으로 풀어준다.
        """
        if path_str is None:
            return None
        return str(Path(path_str).expanduser())

    dreaming_pipeline = DreamingPipeline(
        conversation_store=conv_store,
        memory_file=str(persona_local_dir / "MEMORY.md"),
        user_file=str(persona_local_dir / "USER.md"),
        soul_file=str(persona_local_dir / "SOUL.md"),
        agent_file=str(persona_local_dir / "AGENT.md"),
        llm_router=llm_router,
        dreaming_model=dreaming_config.get("model", ""),
        clusterer=clusterer,
        enable_clusters=enable_clusters,
        # BIZ-73 / BIZ-133: 인사이트 메타 sidecar — config.yaml 의 daemon.dreaming.insights_file
        # 키로 노출되며 기본값은 ``~/.simpleclaw/insights.jsonl``.
        insights_file=_expand(dreaming_config["insights_file"]),
        insight_promotion_threshold=dreaming_config.get(
            "insight_promotion_threshold", 3
        ),
        # BIZ-78: decay/reject 차단 리스트. config 가 None 이면 decay 비활성, reject TTL 영구.
        decay_archive_after_days=dreaming_config.get("decay", {}).get(
            "archive_after_days", 30
        ),
        reject_default_ttl_days=dreaming_config.get(
            "reject_blocklist", {}
        ).get("default_ttl_days"),
        # BIZ-79: dry-run + admin review 모드. 운영 환경에서는 항상 켜져 있어
        # 추출된 인사이트가 USER.md 에 즉시 쓰이지 않고 review 큐로 들어간다.
        # auto_promote 임계치를 동시에 충족한 항목만 큐 우회.
        suggestions_file=_expand(dreaming_config["suggestions_file"]),
        blocklist_file=_expand(dreaming_config["blocklist_file"]),
        auto_promote_confidence=dreaming_config.get(
            "auto_promote_confidence", 0.7
        ),
        auto_promote_evidence_count=dreaming_config.get(
            "auto_promote_evidence_count", 3
        ),
        # BIZ-74: Active Projects
        active_projects_file=_expand(active_projects_file),
        active_projects_window_days=active_projects_window_days,
        # BIZ-81 / BIZ-133: 사이클 메트릭 sidecar — Admin UI Memory 화면의 KPI/진단 입력원.
        # 운영 디렉터리 외부 이전 후에도 grep/diff 친화적인 단일 JSONL 로 유지.
        runs_file=_expand(dreaming_config["runs_file"]),
        # BIZ-80: 1차 언어 정책 (USER/MEMORY/AGENT/SOUL = ko 기본).
        language_policy=language_policy,
    )
    overnight_hour_cfg = int(dreaming_config.get("overnight_hour", 3))
    idle_threshold_cfg = int(dreaming_config.get("idle_threshold", 7200))
    dreaming_trigger = DreamingTrigger(
        conversation_store=conv_store,
        dreaming_pipeline=dreaming_pipeline,
        daemon_store=daemon_store,
        overnight_hour=overnight_hour_cfg,
        idle_threshold=idle_threshold_cfg,
    )

    def _dreaming_status_provider() -> dict:
        """Admin API ``/memory/dreaming/status`` 가 호출하는 상태 컨텍스트 프로바이더 (BIZ-81).

        반환 dict 키:
        - ``next_run``: 다음 트리거 예상 시각(ISO). 오늘 이미 실행했으면 내일 overnight_hour,
          아니면 오늘 overnight_hour. 정확한 idle 조건은 사용자 활동에 의존하므로 *최선 추정*.
        - ``overnight_hour``, ``idle_threshold_seconds``: 운영자 진단용 현재 설정값.
        - ``trigger_blockers``: 지금 트리거가 막혀 있는 사유 (오늘 이미 실행/야간 시간 미도래/
          사용자가 활성/메시지 없음).
        - ``trigger_message``: 사람이 읽을 수 있는 한 줄 진단.

        예외는 흡수해 호출 측이 status 응답 자체를 잃지 않게 한다 — provider 가 실패하면
        admin_api 가 빈 dict 로 폴백한다.
        """
        from datetime import datetime, timedelta

        now = datetime.now()
        last_iso = daemon_store.get_state(LAST_DREAMING_KEY)
        last_dt: datetime | None = None
        if last_iso:
            try:
                last_dt = datetime.fromisoformat(last_iso)
            except ValueError:
                last_dt = None

        # 다음 시각 추정: 오늘 이미 실행했으면 내일 overnight_hour, 아니면 오늘 overnight_hour
        # (이미 그 시간이 지났다면 다음 날). 실제 트리거는 idle 조건도 봐야 하지만, "언제부터
        # 실행 가능 시점인지" 만 알려도 운영자에게 충분한 정보가 된다.
        target = now.replace(
            hour=overnight_hour_cfg, minute=0, second=0, microsecond=0
        )
        if last_dt and last_dt.date() == now.date():
            next_dt = (target + timedelta(days=1))
        elif now < target:
            next_dt = target
        else:
            # 오늘 야간 시간은 이미 지났는데 아직 실행 안 됨 → 곧 다음 5분 체크에서 시도.
            next_dt = now

        # 진단 메시지 — 왜 지금/곧 실행될지 한 줄로.
        blockers: list[str] = []
        if last_dt and last_dt.date() == now.date():
            blockers.append(f"오늘({now:%Y-%m-%d}) 이미 1회 실행됨")
        if now.hour < overnight_hour_cfg:
            blockers.append(
                f"야간 시간({overnight_hour_cfg:02d}:00) 미도래"
            )
        # 메시지가 한 건도 없으면 트리거 자체가 안 돈다 — 운영자가 즉시 인지하도록.
        try:
            recent = conv_store.get_recent(limit=1)
            if not recent:
                blockers.append("처리 가능한 메시지가 없음")
            else:
                last_input = recent[-1].timestamp
                if last_input is not None:
                    idle = (now - last_input).total_seconds()
                    if idle < idle_threshold_cfg:
                        blockers.append(
                            f"사용자 활동 후 {int(idle)}초 경과 (필요: {idle_threshold_cfg}초)"
                        )
        except Exception:  # noqa: BLE001 — 진단용이므로 어떤 실패도 응답을 막지 않는다.
            pass

        msg = (
            f"다음 시도 예정: {next_dt:%Y-%m-%d %H:%M}"
            if not blockers
            else " / ".join(blockers)
        )

        return {
            "next_run": next_dt.isoformat(),
            "overnight_hour": overnight_hour_cfg,
            "idle_threshold_seconds": idle_threshold_cfg,
            "trigger_blockers": blockers,
            "trigger_message": msg,
        }

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
            # BIZ-79 — pending suggestion 큐 + reject 블록리스트 + USER.md writer.
            # accept/edit 핸들러가 dreaming_pipeline 의 ``append_insight_to_user_file``
            # 을 호출해 single-bullet 을 USER.md insights 섹션에 안전하게 append.
            suggestion_store=dreaming_pipeline.suggestion_store,
            blocklist_store=dreaming_pipeline.blocklist_store,
            suggestion_writer=dreaming_pipeline.append_insight_to_user_file,
            # BIZ-81 — 드리밍 사이클 메트릭 sidecar + 진단 컨텍스트 프로바이더.
            # runs_store 가 None 이면 ``/memory/dreaming/runs`` 가 503,
            # status_provider 가 None 이면 ``/memory/dreaming/status`` 가
            # last_run/KPI만 반환한다. dreaming_pipeline 와 동일한 sidecar 를 공유.
            dreaming_run_store=dreaming_pipeline.runs_store,
            dreaming_status_provider=_dreaming_status_provider,
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
