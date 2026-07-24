"""DreamingPipeline에서 분리한 단계별 service 함수.

이 모듈의 함수들은 ``DreamingPipeline`` 인스턴스 메서드로 바인딩된다.
기존 public surface와 사용자 데이터 schema를 유지하기 위해 동작 코드는 원본에서
보수적으로 이동만 하고, 의존성은 dreaming 모듈의 기존 전역을 재사용한다.
"""

from __future__ import annotations

from simpleclaw.memory import dreaming as _dreaming
from simpleclaw.memory.dreaming import *

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

def create_backup(self, file_path: Path, max_backups: int = 3) -> Path | None:
    """파일 수정 전 타임스탬프가 포함된 .bak 백업을 생성한다.

    백업은 원본 파일의 부모 디렉토리 하위 memory-backup/ 폴더에 저장된다.
    최근 max_backups개만 유지하고 오래된 백업은 자동 삭제한다.

    Args:
        file_path: 백업할 원본 파일 경로.
        max_backups: 유지할 최대 백업 개수 (기본 3).

    Returns:
        생성된 백업 파일 경로. 원본 파일이 없으면 None.
    """
    if not file_path.is_file():
        return None

    backup_dir = file_path.parent / "memory-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    backup_name = f"{file_path.stem}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    backup_path = backup_dir / backup_name
    shutil.copy2(file_path, backup_path)
    logger.info("Created backup: %s", backup_path)

    # 오래된 백업 정리: 같은 stem의 최근 max_backups개만 유지
    stem = file_path.stem
    existing_backups = sorted(
        backup_dir.glob(f"{stem}.*.bak"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old_backup in existing_backups[max_backups:]:
        old_backup.unlink()
        logger.debug("Removed old backup: %s", old_backup)

    return backup_path

def collect_unprocessed(self, last_dreaming: datetime | None = None) -> list:
    """마지막 드리밍 이후 미처리 대화 메시지를 수집한다.

    Args:
        last_dreaming: 마지막 드리밍 시각. None이면 최근 50개 메시지를 가져온다.

    Returns:
        처리 대상 ConversationMessage 리스트.

    BIZ-76: 자동 트리거(cron/recipe) 메시지는 ``auto_trigger_mode`` 에 따라
    제거 또는 stride sampling 으로 축소된다. 반환 순서는 시간순으로 보존된다.
    """
    if last_dreaming:
        raw = self._store.get_since(last_dreaming)
    else:
        raw = self._store.get_recent(limit=50)
    return self._apply_auto_trigger_filter(raw, key=lambda m: m)

def collect_unprocessed_with_ids(
    self, last_dreaming: datetime | None = None
) -> list[tuple[int, ConversationMessage]]:
    """``collect_unprocessed`` 의 id-bearing 변형 (BIZ-77).

    인사이트 source 역추적을 위해 메시지 rowid 를 함께 수집해야 한다.
    반환 순서는 시간순 (id 오름차순) 으로 일관된다.

    BIZ-76: 자동 트리거 메시지는 ``auto_trigger_mode`` 에 따라 제거 또는
    축소된다. 제거된 메시지의 rowid 는 인사이트 source 에 포함되지 않으므로
    Admin "근거 보기" 가 자동 트리거를 가리키지 않는다 — 사용자에게 보이는
    인사이트와 그 근거가 모두 organic 발화로 일관된다.
    """
    if last_dreaming:
        raw = self._store.get_since_with_ids(last_dreaming)
    else:
        raw = self._store.get_recent_with_ids(limit=50)
    return self._apply_auto_trigger_filter(raw, key=lambda pair: pair[1])

def _apply_auto_trigger_filter(self, items, key):
    """``auto_trigger_mode`` 에 따라 자동 트리거 메시지를 코퍼스에서 분리한다.

    ``items`` 는 ``ConversationMessage`` 리스트 또는
    ``(id, ConversationMessage)`` 튜플 리스트로 둘 다 처리한다 — 단일 진입점에서
    분류 정책을 일관되게 적용해 ``collect_unprocessed`` / ``..._with_ids``
    간 행동 차이가 생기지 않도록 한다.

    Args:
        items: 원본 코퍼스 (시간순).
        key: ``items`` 의 한 원소에서 ``ConversationMessage`` 를 꺼내는 함수.

    Returns:
        필터/샘플링이 적용된 새 리스트(시간순 보존).
    """
    if self._auto_trigger_mode == AUTO_TRIGGER_MODE_INCLUDE:
        # 가공 없이 그대로 — 레거시 호환 / 운영자 명시 옵트아웃.
        return list(items)

    organic = []
    auto = []
    for it in items:
        msg = key(it)
        if is_auto_trigger_channel(msg.channel):
            auto.append(it)
        else:
            organic.append(it)

    if self._auto_trigger_mode == AUTO_TRIGGER_MODE_EXCLUDE or not auto:
        # exclude — auto 전부 버린다. organic 만 시간순으로 그대로.
        # auto 가 비어 있을 때도 동일 경로(불필요한 sampling 계산 회피).
        return organic

    # downweight — stride sampling 으로 일정 비율만 보존.
    # weight=0 이면 결과적으로 exclude 와 동일하게 동작(0벡터 stride 회피).
    if self._auto_trigger_weight <= 0:
        return organic
    if self._auto_trigger_weight >= 1.0:
        # 가드 — 1.0 은 include 와 사실상 같으므로 모두 보존.
        return list(items)

    # stride 가 클수록 적게 남는다. weight=0.3 → stride=3 (1/3 보존).
    # round 가 0 을 만들 수 없도록 max(2, ...). 분수 나눗셈은 round 로 안정화.
    stride = max(2, round(1.0 / self._auto_trigger_weight))
    sampled_auto = auto[::stride]

    # 시간순 보존을 위해 원본 인덱스로 재정렬. items 는 시간순 입력 가정.
    index_map = {id(it): i for i, it in enumerate(items)}
    combined = organic + sampled_auto
    combined.sort(key=lambda it: index_map[id(it)])
    return combined

def insight_store(self) -> InsightStore | None:
    """인사이트 sidecar 저장소 (BIZ-73). Admin API 가 같은 sidecar 를 공유한다.

    ``insights_file`` 인자나 ``user_file`` 옆 자동 결정 경로가 둘 다 없으면
    ``None``. Admin API 라우팅은 None 일 때 503 으로 명시 disabled 응답.
    """
    return self._insights_store

def suggestion_store(self) -> SuggestionStore | None:
    """Pending suggestion 큐 (BIZ-79). Admin API 가 같은 sidecar 를 공유.

    None 이면 dry-run 모드가 꺼져 있어 추출된 인사이트가 즉시 USER.md 에
    반영된다 — Admin API 의 ``/memory/suggestions/...`` 엔드포인트는 503 응답.
    """
    return self._suggestion_store

def blocklist_store(self) -> BlocklistStore | None:
    """Reject 누적 블록리스트 (BIZ-79). Admin API reject 액션이 같은 store 에 add."""
    return self._blocklist_store

def auto_promote_thresholds(self) -> tuple[float, int]:
    """``(confidence_floor, evidence_count_floor)`` 쌍 — 운영 가시성용."""
    return self._auto_promote_confidence, self._auto_promote_evidence_count

def runs_store(self) -> DreamingRunStore | None:
    """드리밍 사이클 메트릭 sidecar (BIZ-81). Admin API 가 KPI 계산에 사용.

    ``runs_file`` 인자가 None 이면 메트릭 기록이 비활성. Admin API 의
    ``/memory/dreaming/runs`` / ``/status`` 는 None 일 때 503 disabled 응답.
    """
    return self._runs_store

def _read_existing(self, file_path: Path | None) -> str:
    """파일이 존재하면 본문을, 없으면 ``"(없음)"`` placeholder 를 반환한다.

    BIZ-299 — 파일별 LLM 호출이 기존 md 본문을 컨텍스트로 받기 위한 공용 헬퍼.
    """
    if file_path and file_path.is_file():
        text = file_path.read_text(encoding="utf-8")
        return text or "(없음)"
    return "(없음)"

def append_to_memory(self, summary: str) -> None:
    """드리밍 요약을 MEMORY.md의 managed:dreaming:journal 섹션에 append한다.

    BIZ-72: 마커 외부 영역은 보존된다. 마커가 없거나 잘못된 경우
    ``ProtectedSectionError``를 던져 호출자가 fail-closed로 응답하게 한다.
    """
    if not summary:
        return
    self._safe_append_in_section(
        self._memory_file, self._memory_section, summary
    )

def _safe_append_in_section(
    self,
    file_path: Path,
    section_name: str,
    content: str,
) -> None:
    """파일의 ``managed:dreaming:<section_name>`` 안쪽에 ``content``를 append한다.

    Protected Section 모델의 1차 진입점. 마커 외부 바이트는 보존되고, 마커 자체도
    그대로 유지된다. 파일이 없거나 마커가 없으면 ``ProtectedSectionError``를 던지므로
    호출자(보통 ``run()``)가 잡아 fail-closed로 처리해야 한다.

    Args:
        file_path: 갱신 대상 파일.
        section_name: 갱신할 managed 섹션 이름.
        content: 섹션 내부에 append할 마크다운 본문.

    Raises:
        ProtectedSectionMissing: 파일이 없거나 해당 섹션이 정의돼 있지 않을 때.
        ProtectedSectionMalformed: 마커 자체가 잘못된 경우.
    """
    if not file_path.is_file():
        raise ProtectedSectionMissing(
            f"managed 파일이 존재하지 않음: {file_path} (section={section_name})"
        )
    existing = file_path.read_text(encoding="utf-8")
    new_text = append_to_section(existing, section_name, content)
    # ``append_to_section``은 변경이 없으면 입력을 그대로 반환 — 불필요한 mtime 변경 방지
    if new_text != existing:
        file_path.write_text(new_text, encoding="utf-8")
        logger.info(
            "Updated managed section '%s' in %s", section_name, file_path
        )

def _format_dated_block(self, header: str, content: str) -> str:
    """``## {header} ({date})`` 헤더를 붙인 dated block을 생성한다.

    managed 섹션 내부에 일자별 dreaming 결과를 append할 때의 표준 포맷.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    return f"## {header} ({date_str})\n{content.strip()}"

def update_user_file(self, insights: str) -> None:
    """새로운 사용자 인사이트를 USER.md의 managed:dreaming:insights 섹션에 추가한다."""
    if not self._user_file or not insights:
        return
    block = self._format_dated_block("Dreaming Insights", insights)
    self._safe_append_in_section(self._user_file, self._user_section, block)

def update_soul_file(self, updates: str) -> None:
    """에이전트 성격/말투 변경을 SOUL.md의 managed:dreaming:dreaming-updates에 추가한다."""
    if not self._soul_file or not updates:
        return
    block = self._format_dated_block("Dreaming Updates", updates)
    self._safe_append_in_section(self._soul_file, self._soul_section, block)

def update_agent_file(self, updates: str) -> None:
    """에이전트 행동 규칙 변경을 AGENT.md의 managed:dreaming:dreaming-updates에 추가한다."""
    if not self._agent_file or not updates:
        return
    block = self._format_dated_block("Dreaming Updates", updates)
    self._safe_append_in_section(self._agent_file, self._agent_section, block)

def _preflight_protected_sections(self) -> None:
    """쓰기 시작 전에 모든 대상 파일이 필요한 managed 섹션을 갖췄는지 검증.

    BIZ-72: "Fail-closed" 보장의 핵심 — 한 파일이라도 마커가 없거나 잘못돼 있으면
    쓰기 자체를 시작하지 않는다. 부분 변경(한 파일만 변경되고 다른 파일은 abort)
    같은 어정쩡한 상태가 절대 만들어지지 않게 한다.

    검증되는 섹션:
    - MEMORY.md: ``memory_section``(레거시 append) 또는 ``cluster_section``
      (Phase 3) — ``enable_clusters`` 여부에 따라 다름.
    - USER.md: ``user_section`` (파일이 설정돼 있을 때).
    - SOUL.md: ``soul_section`` (파일이 설정돼 있을 때).
    - AGENT.md: ``agent_section`` (파일이 설정돼 있을 때).

    BIZ-132 Phase 2 자가 복원:
        라이브 파일이 *부재* 인 경우(파일 자체가 없음 — BIZ-28 사고 클래스),
        ``safety_backup_manager`` 또는 레거시 ``memory-backup/`` 디렉터리에서
        동일 basename 의 최신 백업을 찾아 라이브 경로에 1회 한정으로 복사한 뒤
        검증을 재시도한다. 복원 후의 파일이 여전히 마커를 갖추지 못했다면
        ``ProtectedSectionMissing`` 으로 fail-closed (마커 손상은 좁은 예외에
        포함되지 않음). 마커가 잘못된 경우(``ProtectedSectionMalformed``)도
        자가 복원하지 않고 즉시 abort.

    Raises:
        ProtectedSectionError: 어느 한 파일이라도 검증 실패 시.
    """
    targets: list[tuple[Path, str]] = []
    memory_section_name = (
        self._cluster_section if self._enable_clusters else self._memory_section
    )
    targets.append((self._memory_file, memory_section_name))
    if self._user_file:
        targets.append((self._user_file, self._user_section))
        # BIZ-74: active-projects 가 활성화된 경우 같은 USER.md 안의
        # ``active-projects`` 섹션도 사전 검증한다. 누락 시 전체 사이클 abort.
        if self.is_active_projects_enabled():
            targets.append((self._user_file, self._active_projects_section))
    if self._soul_file:
        targets.append((self._soul_file, self._soul_section))
    if self._agent_file:
        targets.append((self._agent_file, self._agent_section))

    for file_path, section_name in targets:
        if not file_path.is_file():
            # BIZ-132 Phase 2 — 부재 케이스 한정 1회 자가 복원 시도.
            # 복원에 실패하면 그대로 fail-closed 로 떨어진다(BIZ-72 의미 보존).
            restored_from = self._try_self_restore(file_path)
            if restored_from is None:
                raise ProtectedSectionMissing(
                    f"Dreaming preflight 실패: {file_path}가 존재하지 않음 "
                    f"(필요 섹션: {section_name}). 먼저 managed 마커가 포함된 템플릿을 "
                    f"수동 또는 ``protected_section.ensure_initialized``로 생성하세요."
                )
            logger.warning(
                "Dreaming preflight: %s 가 없어 백업에서 복원했습니다 (source=%s).",
                file_path,
                restored_from,
            )
            self._last_recovered_files[file_path.name] = str(restored_from)
        text = file_path.read_text(encoding="utf-8")
        # ``get_section_body``는 섹션이 없으면 ProtectedSectionMissing,
        # 마커가 잘못됐으면 ProtectedSectionMalformed를 던진다.
        # 자가 복원으로 채워진 파일이라도 마커가 없거나 손상돼 있으면 여기서
        # 동일하게 fail-closed — Phase 2 의 좁은 예외는 "부재" 한 케이스만이며,
        # 마커 손상·내용 비정상은 abort.
        get_section_body(text, section_name)

def _try_self_restore(self, file_path: Path) -> Path | None:
    """라이브 파일 부재 시 백업에서 1회 한정 복원 시도 (BIZ-132 Phase 2).

    우선순위:
    1. ``safety_backup_manager`` (사이클 직전 통째 스냅샷) — 가장 최근 사이클의
       동일 basename 사본.
    2. 레거시 ``.agent/memory-backup/{stem}.{ts}.bak`` — ``create_backup`` 이
       만든 파일별 직전 사본.

    한 회차 안에서 ``_self_restore_count_in_cycle`` 가 0 일 때만 진행한다.
    같은 회차 안에 두 번째 부재가 발생하면(예: 운영자가 race 로 다시 삭제) 그건
    백업 한 번으로 풀릴 문제가 아니므로 자가 복원을 멈추고 fail-closed 로 처리.
    """
    if self._self_restore_count_in_cycle > 0:
        return None

    backup_path: Path | None = None
    if self._safety_backup_manager is not None:
        backup_path = self._safety_backup_manager.latest_backup_for(file_path.name)
    if backup_path is None:
        backup_path = find_legacy_memory_backup(
            self._memory_backup_dir, file_path.name
        )
    if backup_path is None or not backup_path.is_file():
        return None

    try:
        # 안전한 atomic 복원: 임시 경로에 쓴 뒤 rename. 라이브 위치에 다른
        # 프로세스가 동시에 만들어둘 수 있는 race window 를 좁힌다.
        file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = file_path.with_suffix(file_path.suffix + ".restore.tmp")
        shutil.copy2(backup_path, tmp_path)
        tmp_path.replace(file_path)
    except OSError:
        logger.exception(
            "Self-restore failed: %s ← %s", file_path, backup_path,
        )
        return None

    self._self_restore_count_in_cycle += 1
    return backup_path

def _snapshot_per_file_metrics(self) -> dict[str, dict]:
    """BIZ-299 — 이번 사이클의 파일별 LLM 호출 메트릭을 dict 로 복사한다.

    ``run_record.details["per_file"]`` 에 그대로 영속되며, Admin UI 가 "어떤 호출이
    느렸나" / "토큰을 어디서 많이 썼나" 를 한 행에서 보여줄 수 있게 한다. 호출이 한
    번도 일어나지 않으면 빈 dict.
    """
    return {k: dict(v) for k, v in self._per_file_metrics.items()}

def _restore_from_backups(backups: list[tuple[Path, Path | None]]) -> None:
    """런타임 abort 시 모든 대상 파일을 백업본으로 되돌린다.

    백업이 없는 항목(파일이 처음부터 없었던 경우 등)은 건너뛴다 — 그런 파일은
    쓰기 시도 자체가 차단되었으므로 손상돼 있을 수 없다.
    """
    for original, backup in backups:
        if backup is None or not backup.is_file():
            continue
        try:
            shutil.copy2(backup, original)
            logger.info("Restored %s from backup %s", original, backup)
        except OSError:
            # 복원조차 실패하면 운영자 개입이 필요 — 로그에 명확히 남긴다.
            logger.exception(
                "Failed to restore %s from backup %s — manual intervention required",
                original,
                backup,
            )

