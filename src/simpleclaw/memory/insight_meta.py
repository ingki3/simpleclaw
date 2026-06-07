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

def reject_blocklist(self) -> RejectBlocklistStore | None:
    """BIZ-78 — reject 차단 리스트 저장소. ``None`` 이면 비활성.

    Admin Review Loop(H, BIZ-79) 가 같은 store 를 공유해 사용자 거부 신호를
    등록한다.
    """
    return self._reject_store

def apply_insight_meta(
    self,
    meta_items: list[dict],
    source_msg_ids: list[int] | None = None,
) -> tuple[list[InsightMeta], list[InsightMeta]]:
    """이번 회차의 인사이트 메타를 sidecar 와 병합·저장한다.

    BIZ-79 dry-run 모드 (``suggestion_store`` 가 주입된 경우):
    1. 블록리스트에 등록된 토픽 관측은 merge 전에 필터링한다 — 같은 인사이트가
       재추출되는 것을 차단한다(거부 → 차단 루프의 한 끝).
    2. 병합 후, 변경된 각 인사이트를 두 갈래로 라우팅:
       - 자동 적용(auto-promote): ``confidence`` 와 ``evidence_count`` 가
         동시에 임계치를 충족하면 ``promoted`` 로 반환 — 호출자가 USER.md 에
         즉시 append.
       - 큐 적재: 그 외 모든 변경은 pending suggestion 으로 큐에 들어간다.
         기존 pending 행이 있으면 in-place 갱신 (한 토픽당 1행 보장).

    레거시 모드 (``suggestion_store`` 미주입):
    ``promoted`` 는 ``is_promoted`` 기준 — 호출자(``run``)가 별도 로직 없이
    그대로 동작한다.

    Args:
        meta_items: ``[{"topic": ..., "text": ...}, ...]`` 형태의 LLM 추출물.
        source_msg_ids: 이번 회차에 분석한 메시지 rowid 목록 (BIZ-77 source linkage).
            None 이면 빈 리스트로 처리. 신규/강화된 모든 인사이트에 동일 부착.

    Returns:
        (changed, promoted)
        - changed: 이번 회차에 추가/갱신된 인사이트 (블록리스트로 필터된 것 제외).
        - promoted: 자동 적용 대상 — 호출자가 USER.md 본문에 반영해야 하는 항목.
        sidecar 저장소가 비활성이거나 입력이 비어있으면 (빈 리스트, 빈 리스트).
    """
    # BIZ-81: 사이클당 차단된 관측 수 카운터를 0으로 리셋. run() 이 메트릭으로 읽는다.
    self._last_rejected_count = 0
    if not self._insights_store or not meta_items:
        return [], []

    now = datetime.now()
    ids = list(source_msg_ids or [])
    # BIZ-78 — reject 차단 리스트에 있는 topic 은 추출 자체를 무효화한다.
    # load() 는 만료된 항목을 자동 제외하므로 여기서 추가 필터 없이 사용해도 OK.
    blocklist = (
        self._reject_store.load(now=now) if self._reject_store else {}
    )
    blocked_topics_seen: list[str] = []
    observations: list[InsightMeta] = []
    # BIZ-79 — blocklist 사전 필터. 이미 reject 된 topic 은 sidecar 에 진입하지 않는다.
    # blocklist 가 비활성(None) 이면 모든 관측을 통과시킨다.
    for item in meta_items:
        topic = (item.get("topic") or "").strip()
        text = (item.get("text") or "").strip()
        if not topic or not text:
            continue
        # BIZ-78: reject_store 기반 차단 (legacy TTL 기반)
        if blocklist and normalize_topic(topic) in blocklist:
            blocked_topics_seen.append(topic)
            self._last_rejected_count += 1
            continue
        # BIZ-79: 블록리스트 토픽은 merge 이전에 drop — 같은 인사이트 재추출 차단.
        # ``BlocklistStore`` 가 주입되지 않은 경우(레거시) 차단 없이 통과.
        if self._blocklist_store is not None and self._blocklist_store.is_blocked(
            topic
        ):
            logger.info(
                "Skipping blocklisted insight topic: %r",
                normalize_topic(topic),
            )
            self._last_rejected_count += 1
            continue
        observations.append(
            InsightMeta(
                topic=topic,
                text=text,
                evidence_count=1,
                confidence=0.0,  # merge_insights 가 재계산
                first_seen=now,
                last_seen=now,
                source_msg_ids=list(ids),
            )
        )

    if blocked_topics_seen:
        logger.info(
            "Dropped %d blocklisted topic observation(s): %s",
            len(blocked_topics_seen),
            blocked_topics_seen,
        )

    if not observations:
        return [], []

    existing = self._insights_store.load()
    merged, changed = merge_insights(
        existing, observations, self._insight_promotion_threshold
    )
    self._insights_store.save_all(merged)

    # BIZ-79: 변경 항목을 자동 적용 vs 큐 적재로 라우팅.
    promoted: list[InsightMeta] = []
    if self._suggestion_store is not None:
        queued = 0
        for meta in changed:
            if self._meets_auto_promote(meta):
                promoted.append(meta)
                # auto-promote 가 발동된 토픽이 이전 사이클에서 큐에 남아 있었다면
                # 자동으로 accepted 처리한다 — USER.md 에 이미 들어갈 내용을
                # 운영자가 다시 보지 않게 정리.
                existing = self._suggestion_store.find_pending_by_topic(
                    meta.topic
                )
                if existing is not None:
                    self._suggestion_store.update_status(
                        existing.id, "accepted"
                    )
                continue
            # pending 큐에 적재 (한 토픽당 1행 보장 — 같은 토픽 반복 강화는
            # in-place 갱신).
            self._suggestion_store.upsert_pending(meta)
            queued += 1
        logger.info(
            "Insights updated: %d changed, %d auto-applied, %d queued "
            "(promote_conf>=%.2f & ev>=%d, sidecar_threshold=%d)",
            len(changed), len(promoted), queued,
            self._auto_promote_confidence,
            self._auto_promote_evidence_count,
            self._insight_promotion_threshold,
        )
    else:
        # 레거시 모드: ``is_promoted`` 기준 (BIZ-73 호환).
        promoted = [
            m for m in changed
            if is_promoted(m, self._insight_promotion_threshold)
        ]
        logger.info(
            "Insights updated: %d changed, %d promoted (threshold=%d)",
            len(changed), len(promoted), self._insight_promotion_threshold,
        )
    return changed, promoted

def _meets_auto_promote(self, meta: InsightMeta) -> bool:
    """자동 적용 조건 — confidence/evidence_count 가 **동시에** 임계치 이상.

    한쪽만 만족하는 경우(예: 단발 고신뢰)는 큐로 보내 운영자 검수에 맡긴다 —
    BIZ-79 DoD §1 "confidence ≥ X AND evidence_count ≥ Y simultaneously".
    """
    return (
        meta.confidence >= self._auto_promote_confidence
        and meta.evidence_count >= self._auto_promote_evidence_count
    )

def _format_auto_applied_bullets(items: list[InsightMeta]) -> str:
    """자동 적용 인사이트들의 ``text`` 를 USER.md 용 bullet 텍스트로 합친다.

    각 ``text`` 는 보통 이미 "- " 접두 없이 한 줄. 빈 줄/중복은 제거하여
    깔끔한 bullet 블록을 만든다. 빈 입력이면 빈 문자열 반환.
    """
    seen: set[str] = set()
    bullets: list[str] = []
    for meta in items:
        text = (meta.text or "").strip()
        if not text:
            continue
        # 이미 사용자가 적은 prefix 가 있는 경우 그대로 유지.
        line = text if text.startswith(("-", "*")) else f"- {text}"
        if line in seen:
            continue
        seen.add(line)
        bullets.append(line)
    return "\n".join(bullets)

def append_insight_to_user_file(self, text: str) -> None:
    """단일 인사이트(또는 사용자 편집본)를 USER.md insights 섹션에 append.

    Admin API 의 accept / edit 핸들러가 사용한다. 빈 텍스트는 무시.
    """
    text = (text or "").strip()
    if not text or not self._user_file:
        return
    line = text if text.startswith(("-", "*")) else f"- {text}"
    block = self._format_dated_block("Dreaming Insights", line)
    self._safe_append_in_section(self._user_file, self._user_section, block)

def apply_decay(
    self, now: datetime | None = None
) -> list[InsightMeta]:
    """``last_seen`` 기준 N일 이상 reinforcement 가 없으면 archive 처리.

    DoD #1 — 30일(기본, ``decay_archive_after_days`` config) 이상 재관측되지
    않은 인사이트는 sidecar 의 ``archived_at`` 이 세팅되고 USER.md 의
    ``managed:dreaming:archive`` 섹션에 dated 흔적이 추가된다(섹션이 없으면
    sidecar 만 갱신).

    반환값은 *이번 호출에서 새로 archive 된* 인사이트 리스트 — 이미 archive 된
    항목은 건드리지 않으므로 결과에 포함되지 않는다.

    Args:
        now: 비교 기준 시각. 기본 현재 시각. 테스트에서 30일 경과를 시뮬레이트할 때
            의도적으로 오버라이드한다.

    Returns:
        새로 archive 된 ``InsightMeta`` 리스트. decay 비활성/비대상이면 빈 리스트.
    """
    if (
        self._insights_store is None
        or self._decay_archive_after_days is None
    ):
        return []

    n = now or datetime.now()
    cutoff = n - timedelta(days=self._decay_archive_after_days)
    loaded = self._insights_store.load()
    if not loaded:
        return []

    newly_archived: list[InsightMeta] = []
    for meta in loaded.values():
        # 이미 archive 되었거나 cutoff 이후 reinforcement 가 있으면 패스.
        if meta.is_archived():
            continue
        if meta.last_seen >= cutoff:
            continue
        meta.archived_at = n
        newly_archived.append(meta)

    if not newly_archived:
        return []

    # sidecar 영속화 — archived_at 만 바뀌고 행 자체는 보존(부활 가능하도록).
    self._insights_store.save_all(loaded)

    # USER.md archive 섹션이 있으면 dated 흔적 추가. 없으면 sidecar 만 갱신하고 끝.
    # (기존 USER.md 가 archive 섹션을 안 갖고 있어도 깨지지 않게 — 호환성 우선)
    if self._user_file and self._user_file.is_file():
        try:
            user_text = self._user_file.read_text(encoding="utf-8")
            if has_managed_section(user_text, self._archive_section):
                block_lines = [
                    f"- [{m.topic}] {m.text} (last_seen="
                    f"{m.last_seen.strftime('%Y-%m-%d')})"
                    for m in newly_archived
                ]
                block = self._format_dated_block(
                    "Archived (decay)", "\n".join(block_lines)
                )
                self._safe_append_in_section(
                    self._user_file, self._archive_section, block
                )
            else:
                logger.info(
                    "Archive section '%s' missing in %s — sidecar only updated. "
                    "Add markers to surface archived insights in USER.md.",
                    self._archive_section, self._user_file,
                )
        except ProtectedSectionError as exc:
            # archive 섹션 마커가 손상된 경우 — sidecar 갱신은 이미 끝났고, 이 흔적
            # 기록만 실패한다. 다음 사이클의 preflight 가 동일 문제를 다시 잡거나
            # 운영자가 수정할 때까지 markdown 흔적은 누락된다(데이터 손실은 없다).
            logger.warning(
                "Failed to write archive markers in %s: %s. Sidecar archive_at "
                "is set; markdown trail is missing until markers are repaired.",
                self._user_file, exc,
            )

    logger.info(
        "Decay archived %d insight(s) (cutoff=%s, days=%d)",
        len(newly_archived),
        cutoff.isoformat(),
        self._decay_archive_after_days,
    )
    return newly_archived

def register_reject(
    self,
    topic: str,
    scope: str = "global",
    ttl_days: int | None = -1,
    reason: str = "",
    now: datetime | None = None,
) -> bool:
    """사용자 reject 신호를 처리한다 — DoD #2.

    1. 인사이트 sidecar 에서 해당 topic 행을 즉시 삭제(archive 가 아니라 *폐기*).
    2. reject 차단 리스트(``rejects.jsonl``)에 등록 — 다음 회차부터 같은 topic 의
       추출이 ``apply_insight_meta`` 단계에서 drop 된다.

    ``ttl_days`` 의미:
        - ``-1`` (기본 sentinel): 생성자에 전달된 ``reject_default_ttl_days`` 사용.
          해당 기본값이 None 이면 영구 차단.
        - ``None``: 영구 차단(가장 흔한 케이스).
        - 양수: 그 일수 이후 자동 해제.

    Args:
        topic: 거부할 인사이트의 topic(원문/정규형 모두 가능).
        scope: 차단 범위. 기본 ``global``.
        ttl_days: 차단 지속 기간(일). 위 의미 참조.
        reason: 자유 텍스트 사유 — Admin UI 에 표시되며 운영자 검수에 활용.
        now: 시각 오버라이드(테스트용).

    Returns:
        차단 리스트에 새 항목이 등록·갱신됐으면 True. 차단 리스트가 비활성이거나
        topic 이 비어있으면 False.
    """
    if self._reject_store is None:
        logger.warning(
            "register_reject called but reject blocklist is disabled "
            "(insights sidecar not configured)"
        )
        return False
    if not topic or not topic.strip():
        return False

    # ttl_days sentinel 해석.
    if ttl_days == -1:
        effective_ttl_days = self._reject_default_ttl_days
    else:
        effective_ttl_days = ttl_days
    if effective_ttl_days is None:
        ttl_seconds: int | None = None
    else:
        try:
            ttl_seconds = int(effective_ttl_days) * 86400
            if ttl_seconds <= 0:
                ttl_seconds = None
        except (TypeError, ValueError):
            ttl_seconds = None

    # 1) sidecar 에서 즉시 삭제 — archive 와 다른 점은 *완전 폐기* 라는 것.
    #    부활 메커니즘은 reject 에서 작동하지 않는다(차단 리스트가 재추출을 막음).
    if self._insights_store is not None:
        insights = self._insights_store.load()
        key = normalize_topic(topic)
        if key and key in insights:
            insights.pop(key, None)
            self._insights_store.save_all(insights)
            logger.info("Reject: removed insight %r from sidecar", topic)

    # 2) 차단 리스트에 등록.
    try:
        self._reject_store.add(
            topic=topic.strip(),
            scope=scope,
            ttl_seconds=ttl_seconds,
            reason=reason,
            now=now,
        )
    except ValueError:
        return False
    logger.info(
        "Reject: blocked topic %r (ttl_seconds=%s, scope=%s)",
        topic.strip(), ttl_seconds, scope,
    )
    return True

def _safe_sync_memory_items(self, label: str, fn, *args, **kwargs):
    """memory_items sync failures must not break the dreaming response flow."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        logger.exception("memory_items sync failed (%s); continuing", label)
        return None

