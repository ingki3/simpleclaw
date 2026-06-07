# ruff: noqa: F401,F403,F405
"""DreamingPipeline에서 분리한 단계별 service 함수.

이 모듈의 함수들은 ``DreamingPipeline`` 인스턴스 메서드로 바인딩된다.
기존 public surface와 사용자 데이터 schema를 유지하기 위해 동작 코드는 원본에서
보수적으로 이동만 하고, 의존성은 dreaming 모듈의 기존 전역을 재사용한다.
"""

from __future__ import annotations

from simpleclaw.memory.dreaming import *  # noqa: F403
from simpleclaw.memory import dreaming as _dreaming

AUTO_TRIGGER_MODE_DOWNWEIGHT = _dreaming.AUTO_TRIGGER_MODE_DOWNWEIGHT
AUTO_TRIGGER_MODE_EXCLUDE = _dreaming.AUTO_TRIGGER_MODE_EXCLUDE
_CLUSTER_MARKER_END = _dreaming._CLUSTER_MARKER_END
_CLUSTER_MARKER_START = _dreaming._CLUSTER_MARKER_START
_CLUSTER_SECTION_RE = _dreaming._CLUSTER_SECTION_RE
_VALID_AUTO_TRIGGER_MODES = _dreaming._VALID_AUTO_TRIGGER_MODES
_coerce_meta_items = _dreaming._coerce_meta_items
logger = _dreaming.logger
json = _dreaming.json
re = _dreaming.re
shutil = _dreaming.shutil
time = _dreaming.time
datetime = _dreaming.datetime
timedelta = _dreaming.timedelta

async def run(self, last_dreaming: datetime | None = None) -> MemoryEntry | None:
    """전체 드리밍 파이프라인을 실행한다.

    1. 미처리 대화 메시지를 수집한다.
    2. 처리할 내용이 있으면 (a) Protected Section 사전 검증 후 (b) 대상 파일들을 백업한다.
    3. LLM을 통해 요약을 생성한다 (USER/SOUL/AGENT 갱신용).
    4. ``_enable_clusters=True``면 그래프형 드리밍을 추가로 실행하여
       MEMORY.md를 시간순 append 대신 클러스터별 마커 섹션 upsert로 갱신한다.
       False(기본값)면 기존 append 동작을 유지한다.
    5. USER/SOUL/AGENT 갱신은 두 모드 모두 동일하게 수행한다.

    BIZ-72 Fail-closed 시맨틱:
    - 사전 검증 단계(2-a)에서 한 파일이라도 managed 섹션이 누락/오염돼 있으면
      전체 사이클을 즉시 abort하고 ``None``을 반환한다. 어떤 파일도 변경되지 않는다.
    - 쓰기 도중에 ``ProtectedSectionError``가 던져지면(예: 파일이 외부에서 동시
      편집됨) 백업에서 모든 대상 파일을 복원하고 ``None``을 반환한다.

    Args:
        last_dreaming: 마지막 드리밍 시각. None이면 최근 메시지를 대상으로 한다.

    Returns:
        생성된 MemoryEntry 객체. 처리할 메시지가 없거나, fail-closed로 abort됐거나,
        결과가 비어있으면 None.
    """
    # BIZ-81: 사이클 시작 시점에 메트릭 행을 만든다 — 도중에 크래시해도 운영자가
    # "마지막 시도 시각" 을 알 수 있다. ``runs_store`` 가 None 이면 메트릭 기록 비활성.
    run_record = (
        self._runs_store.begin() if self._runs_store is not None else None
    )

    # BIZ-77 — 메시지를 id 와 함께 수집한다. 분석 자체는 message 객체만 쓰지만
    # 인사이트 source 역추적을 위해 rowid 를 sidecar 에 기록해야 하기 때문이다.
    id_pairs = self.collect_unprocessed_with_ids(last_dreaming)
    if not id_pairs:
        logger.info("No new messages to process for dreaming.")
        if run_record is not None and self._runs_store is not None:
            self._runs_store.finish(
                run_record,
                input_msg_count=0,
                skip_reason=SKIP_NO_MESSAGES,
            )
        return None

    source_msg_ids = [mid for mid, _ in id_pairs]
    messages = [msg for _, msg in id_pairs]
    if run_record is not None and self._runs_store is not None:
        # 메시지 수가 확정되는 즉시 행에 반영(중도 크래시 시에도 보이도록).
        run_record.input_msg_count = len(id_pairs)
        self._runs_store.update(run_record)

    # BIZ-132 Phase 1 — 매 사이클의 preflight 직전, 위험 파일 목록을 통째로
    # ``.agent/_safety_backup/{ts}/`` 에 스냅샷한다. 백업 자체의 실패는 사이클을
    # 막지 않고(SafetyBackupManager 내부에서 흡수), preflight 가 라이브 손상의
    # 1차 가드 역할을 그대로 수행. 자가 복원 카운터/메타도 회차 단위로 초기화.
    self._self_restore_count_in_cycle = 0
    self._last_recovered_files = {}
    if self._safety_backup_manager is not None:
        self._safety_backup_manager.snapshot()

    # BIZ-72: 쓰기 시작 전 Protected Section 사전 검증. 실패 시 어떤 파일도
    # 백업조차 만들지 않고 즉시 종료(불필요한 디스크 I/O 방지).
    try:
        self._preflight_protected_sections()
    except ProtectedSectionError as exc:
        logger.error(
            "Dreaming aborted (preflight): %s. 파일은 변경되지 않았습니다.",
            exc,
        )
        if run_record is not None and self._runs_store is not None:
            self._runs_store.finish(
                run_record,
                input_msg_count=len(id_pairs),
                skip_reason=SKIP_PREFLIGHT_FAILED,
                details={"message": str(exc)},
            )
        return None

    # BIZ-132 — preflight 가 자가 복원으로 통과한 경우, 어떤 파일이 어떤 백업으로
    # 부터 복원됐는지 메트릭 행에 메타로 남겨 운영 가시성을 확보한다. preflight
    # 실패 경로는 위에서 이미 SKIP_PREFLIGHT_FAILED 로 종료됐으므로 여기 도달 X.
    if (
        self._last_recovered_files
        and run_record is not None
        and self._runs_store is not None
    ):
        run_record.details = dict(run_record.details or {})
        run_record.details["recovered_from"] = dict(self._last_recovered_files)
        self._runs_store.update(run_record)

    # 처리할 메시지가 있고 사전 검증 통과 — 백업 생성 후 본격 작업.
    # BIZ-81: 이후의 모든 예외는 finally 에서 메트릭 행에 error 로 반영된다.
    try:
        return await self._run_after_preflight(
            run_record=run_record,
            id_pairs=id_pairs,
            source_msg_ids=source_msg_ids,
            messages=messages,
        )
    except Exception as exc:
        # 예측 못한 모든 예외(LLM 라우터 5xx, 디스크 OSError, programming bug)를 행에 남긴다.
        # 호출자(트리거)는 동일하게 None 반환을 받지만, 운영자는 stale 한 last_dreaming
        # 만 보는 게 아니라 *왜* 실패했는지 즉시 확인 가능.
        logger.exception("Dreaming cycle raised unexpected error")
        if run_record is not None and self._runs_store is not None:
            # BIZ-299 — 예외로 abort 된 경우에도 이미 발사된 파일별 호출 메트릭이
            # 있으면 어느 호출에서 실패했는지 행 메타로 남긴다.
            details: dict = {}
            per_file = self._snapshot_per_file_metrics()
            if per_file:
                details["per_file"] = per_file
            self._runs_store.finish(
                run_record,
                input_msg_count=len(id_pairs),
                error=f"{type(exc).__name__}: {exc}",
                details=details or None,
            )
        # 의도적으로 raise 하지 않는다 — 트리거 루프가 다음 사이클을 정상 시도해야 한다.
        return None

async def _run_after_preflight(
    self,
    *,
    run_record,
    id_pairs,
    source_msg_ids,
    messages,
):
    """preflight 통과 이후의 사이클 본문. ``run`` 에서 호출되며 메트릭은 호출자에서 처리.

    반환값/None 의미는 ``run`` 과 동일. 메트릭 finish 는 정상 종료 경로(이 함수 내부)
    와 예외 경로(``run`` 의 try/except)에서 각자 책임진다.
    """
    backups: list[tuple[Path, Path | None]] = []
    backups.append((self._memory_file, self.create_backup(self._memory_file)))
    if self._user_file:
        backups.append((self._user_file, self.create_backup(self._user_file)))
    if self._soul_file:
        backups.append((self._soul_file, self.create_backup(self._soul_file)))
    if self._agent_file:
        backups.append((self._agent_file, self.create_backup(self._agent_file)))

    result = await self.summarize(messages)
    memory_summary = result.get("memory", "")
    user_insights = result.get("user_insights", "")
    user_insights_meta = result.get("user_insights_meta", []) or []
    soul_updates = result.get("soul_updates", "")
    agent_updates = result.get("agent_updates", "")
    if agent_updates:
        memory_for_agent_filter = "\n".join(
            part
            for part in (
                memory_summary,
                self._read_existing(self._memory_file),
            )
            if part
        )
        agent_filter = filter_agent_updates_with_stats(
            agent_updates,
            memory_text=memory_for_agent_filter,
        )
        if agent_filter.dropped:
            logger.info(
                "Dreaming agent update filter: kept=%d dropped=%d "
                "event=%d duplicate=%d non_policy=%d",
                agent_filter.kept,
                agent_filter.dropped,
                agent_filter.dropped_event,
                agent_filter.dropped_duplicate,
                agent_filter.dropped_non_policy,
            )
        agent_updates = agent_filter.text
    # BIZ-74: 관측치는 빈 리스트일 수 있다(LLM이 식별 못함). 빈 리스트여도
    # update_active_projects를 호출해 윈도우 외 항목이 USER.md에서 사라지도록 한다.
    active_project_obs = result.get("active_projects", []) or []

    # BIZ-78: decay 적용을 *meta 갱신 이전* 에 수행한다. 같은 회차에 reinforcement
    # 가 들어오는 topic 은 archive 가 됐다가 즉시 부활하는 비효율을 피하기 위해 —
    # 아니, 사실 그 *순서가 의도된* 것이기도 하다: archive 흔적이 남고 그 회차에
    # 부활이 명시적으로 기록되어 운영자에게 "한 번 archive 됐다가 부활됨" 을 보여
    # 준다. 결정 근거: archive 와 reinforcement 가 같은 회차에 동시 발생하는 건
    # 매우 드물고, 두 경로가 모두 sidecar 에서 보이는 게 진단에 유리.
    if self._insights_store is not None:
        try:
            self.apply_decay()
        except Exception:
            # decay 실패는 사이클 자체를 중단시키지 않는다 — 다음 회차에 다시 시도.
            logger.exception("apply_decay failed; continuing dreaming cycle")

    # BIZ-73 + BIZ-77: 인사이트 메타 sidecar 갱신 — USER.md 본문 append 보다 먼저
    # 실행하여 어떤 항목이 "승격" 됐는지(USER.md에 high-confidence 표시 가능)
    # 사전 판단할 수 있게 한다. BIZ-77 부터는 이번 회차에 분석된 모든 메시지의
    # rowid 를 신규/강화된 모든 인사이트에 부착한다 — Admin "근거 보기" 의 입력.
    # 주의: sidecar 갱신은 try 바깥에서 수행하지만, fail-closed 의미를 깨지 않기 위해
    # 어떤 markdown 파일도 아직 변경되지 않았다(preflight 통과 직후). sidecar 자체는
    # JSONL atomic-rename 으로 항상 일관된 상태가 보장된다.
    promoted_meta: list[InsightMeta] = []
    changed_meta: list[InsightMeta] = []
    if user_insights_meta and self._insights_store:
        changed_meta, promoted_meta = self.apply_insight_meta(
            user_insights_meta, source_msg_ids=source_msg_ids
        )

    if self._insights_store is not None:
        self._safe_sync_memory_items(
            "insights",
            sync_insights_to_memory_items,
            self._store,
            self._insights_store.load().values(),
            promotion_threshold=self._insight_promotion_threshold,
        )

    cluster_summary_text = ""
    active_projects_rendered: list[ActiveProject] = []
    try:
        # USER/SOUL/AGENT는 두 모드 공통으로 갱신.
        # BIZ-79: dry-run 모드(suggestion_store 활성)에서는 LLM 의 user_insights
        # 블록을 통째로 USER.md 에 쓰지 않는다 — 큐를 우회하면 review 의미가
        # 사라지기 때문이다. 대신 ``apply_insight_meta`` 가 자동 적용 대상으로
        # 분류한 ``promoted_meta`` 만 한 줄씩 bullet 으로 append 한다.
        # 레거시 모드(suggestion_store 미주입)에서는 기존 동작 유지.
        if self._suggestion_store is not None:
            auto_text = self._format_auto_applied_bullets(promoted_meta)
            if auto_text:
                self.update_user_file(auto_text)
        elif user_insights:
            self.update_user_file(user_insights)
        if soul_updates:
            self.update_soul_file(soul_updates)
        if agent_updates:
            self.update_agent_file(agent_updates)

        # BIZ-74: USER.md active-projects 섹션 in-place 갱신.
        # 활성화돼 있을 때만 호출되며, 빈 관측이어도 실행해 윈도우 외 항목이
        # 자연스럽게 섹션에서 사라지도록 한다.
        if self.is_active_projects_enabled():
            active_projects_rendered = self.update_active_projects(
                active_project_obs
            )
            project_store = ActiveProjectStore(self._active_projects_file)
            self._safe_sync_memory_items(
                "active_projects",
                sync_active_projects_to_memory_items,
                self._store,
                project_store.load().values(),
                window_days=self._active_projects_window_days,
            )

        # MEMORY.md 갱신은 클러스터 모드 여부에 따라 분기
        if self._enable_clusters:
            cluster_summary_text = await self._run_cluster_pipeline()
        elif memory_summary:
            # 레거시 모드: 시간순 append (managed:dreaming:journal 안쪽으로)
            self.append_to_memory(memory_summary)
    except ProtectedSectionError as exc:
        # 동시 편집·외부 손상 등으로 쓰기 도중 예외. 부분 변경된 파일이 있을 수
        # 있으므로 모든 대상 파일을 백업으로 복원해 트랜잭션 의미를 보존한다.
        logger.error(
            "Dreaming aborted (mid-write): %s. 백업에서 복원합니다.", exc
        )
        self._restore_from_backups(backups)
        if run_record is not None and self._runs_store is not None:
            details = {"message": str(exc)}
            per_file = self._snapshot_per_file_metrics()
            if per_file:
                details["per_file"] = per_file
            self._runs_store.finish(
                run_record,
                input_msg_count=len(source_msg_ids),
                generated_insight_count=len(changed_meta),
                rejected_count=self._last_rejected_count,
                skip_reason=SKIP_MIDWRITE_ABORTED,
                details=details,
            )
        return None

    proactive_opportunities = self._extract_and_store_proactive_opportunities(id_pairs)

    # 결과 산출물이 전혀 없으면 None 반환 (테스트/호출자가 빈 회차를 식별할 수 있도록).
    # active-projects/proactive 후보만 갱신된 경우(다른 산출물이 모두 비어 있음)에도
    # None을 반환하지 않는다 — sidecar/queue 에 의미 있는 변경이 일어났음을 호출자가
    # 인지해야 한다.
    if not any(
        [memory_summary, user_insights, soul_updates, agent_updates,
         cluster_summary_text, active_projects_rendered, proactive_opportunities]
    ):
        if run_record is not None and self._runs_store is not None:
            details: dict = {}
            per_file = self._snapshot_per_file_metrics()
            if per_file:
                details["per_file"] = per_file
            self._runs_store.finish(
                run_record,
                input_msg_count=len(source_msg_ids),
                generated_insight_count=len(changed_meta),
                rejected_count=self._last_rejected_count,
                skip_reason=SKIP_EMPTY_RESULTS,
                details=details or None,
            )
        return None

    # MemoryEntry.summary는 호환을 위해 LLM이 만든 memory_summary를 우선 사용하고,
    # 클러스터 모드에서 memory_summary가 비어있다면 클러스터 요약 통합본을 담는다.
    entry_summary = memory_summary or cluster_summary_text
    if run_record is not None and self._runs_store is not None:
        # 정상 종료 — 메트릭 행을 success 로 마감. BIZ-299: 파일별 호출 메트릭을
        # ``details["per_file"]`` 로 영속한다.
        details: dict = {}
        per_file = self._snapshot_per_file_metrics()
        if per_file:
            details["per_file"] = per_file
        if proactive_opportunities:
            details["proactive_opportunity_count"] = len(proactive_opportunities)
        self._runs_store.finish(
            run_record,
            input_msg_count=len(source_msg_ids),
            generated_insight_count=len(changed_meta),
            rejected_count=self._last_rejected_count,
            details=details or None,
        )
    return MemoryEntry(
        summary=entry_summary,
        source=f"dreaming_{datetime.now().strftime('%Y-%m-%d')}",
    )

def _extract_and_store_proactive_opportunities(
    self, id_pairs: list[tuple[int, ConversationMessage]]
) -> list[ProactiveOpportunity]:
    """BIZ-333 — Dreaming 코퍼스에서 proactive 후보를 만들고 pending queue에 저장한다.

    hook 실패는 memory/user write 성공을 되돌릴 이유가 아니므로 logging 후 skip 한다.
    반환값은 성공적으로 upsert 된 opportunity 목록이며, 어떤 경로에서도 Telegram 발송이나
    cron 생성 같은 외부 부작용은 수행하지 않는다.
    """
    if self._proactive_extractor is None or self._opportunity_store is None:
        return []
    try:
        opportunities = self._proactive_extractor.extract(id_pairs)
    except Exception:
        logger.exception("Dreaming proactive extractor failed; skipping opportunities")
        return []

    stored: list[ProactiveOpportunity] = []
    for opportunity in opportunities:
        try:
            stored.append(
                self._opportunity_store.upsert_pending_by_cooldown_key(opportunity)
            )
        except Exception:
            logger.exception(
                "Failed to store proactive opportunity cooldown_key=%s",
                getattr(opportunity, "cooldown_key", ""),
            )
    return stored

