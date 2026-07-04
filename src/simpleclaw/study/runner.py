"""Daily study runner — topic registry → source planner → updater → index store.

이 모듈은 Agent Study Wiki 의 *실제 공부*를 수행하는 MVP runner 다. 매일(또는 운영자
강제 실행 시) 다음 흐름을 한 번 돌린다.

1. **topic registry** (``<wiki_dir>/topics.yaml``) 에서 공부할 주제를 읽는다. 아카이브된
   주제는 제외하고, 운영자가 ``refresh`` 를 건 주제를 우선 처리한 뒤 관심도 순으로
   채운다(``max_topics_per_run`` 상한).
2. **source planner** (:mod:`simpleclaw.study.source_planner`) 로 주제별 collector
   fetch 요청을 만들고, :class:`~simpleclaw.study.collectors.CollectorRegistry` 로
   수집한다. collector 는 전부 주입식이라 테스트에서 fake 로 대체된다(실제 도구
   wiring 은 후속 issue). 일반 뉴스 후보는 relevance gate 로 걸러진다.
3. **wiki updater** (:mod:`simpleclaw.study.wiki_updater`) 로 ``topics/<id>/overview.md``
   를 부분 병합한다. 수동 섹션은 보존되고, 저신뢰 항목은 "확인 필요"/open_questions
   로 격리된다.
4. **index store** (``<wiki_dir>/index.sqlite`` 의 ``study_items`` 테이블) 에 이번에
   채택된 항목을 적재한다. 스키마는 운영자 조회 도구
   (:mod:`simpleclaw.agent.study_status`) 가 읽는 컬럼(confidence/title/topic_id/source)
   과 호환된다.

부수적으로 ``daily/YYYY-MM-DD.md`` 데일리 노트(오늘 무엇을 공부했는지 요약만)를 쓰고,
``topics.yaml`` 의 ``last_studied_at``/``source_count`` 를 갱신하며 처리한 refresh 플래그를
지운다.

설계 결정:
- **모든 외부 의존은 주입식.** collector·시간·run_id 생성기를 주입받아, 네트워크/시계
  없이 결정적으로 테스트한다.
- **on-disk 레이아웃을 SoT 로 따른다.** 별도 store 추상 패키지를 만들지 않고
  study_status 가 정의한 레이아웃(topics.yaml + topics/<id>/overview.md + daily/*.md +
  index.sqlite)에 직접 쓴다 — 운영자 조회 도구와 같은 디스크를 공유하기 위함.
- **LLM 프롬프트는 YAML SoT.** topic_update/daily_digest 프롬프트는
  ``prompts/study/*.yaml`` 에 두고, 본 MVP 는 결정적 렌더링을 쓰되 후속 LLM 요약이
  곧바로 같은 spec 을 ``format()`` 할 수 있도록 로더만 노출한다.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from simpleclaw.study.collectors import CollectorRegistry, StudyFetchResult
from simpleclaw.study.source_planner import (
    DEFAULT_RELEVANCE_THRESHOLD,
    DEFAULT_SOURCE_POLICY,
    RelevanceScorer,
    SourcePolicy,
    StudyPromptSpec,
    TopicKind,
    _load_study_prompt,
    plan_fetch_requests,
    select_wiki_worthy,
)
from simpleclaw.study.wiki_updater import merge_open_questions, merge_study_update

logger = logging.getLogger(__name__)

# 아카이브/삭제된 topic 의 status 값 — 공부 대상에서 제외(study_status 와 동일 집합).
_ARCHIVED_STATUSES = frozenset({"archived", "deleted", "retired"})

# confidence(0~1) → 등급 경계. wiki_updater 가 low 일 때 "확인 필요"로 우회한다.
_HIGH_CONFIDENCE = 0.7
_MEDIUM_CONFIDENCE = 0.4


@dataclass(frozen=True)
class StudyRunSummary:
    """한 번의 daily study run 결과 요약.

    runner 호출자(daily job/운영자 강제 실행)가 로깅·알림에 쓰는 불변 결과.
    """

    run_id: str
    started_at: str
    finished_at: str
    topics_considered: int
    topics_updated: int
    items_added: int
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class StudyTopicRecord:
    """topics.yaml 한 항목을 runner 가 소비할 형태로 정규화한 레코드.

    :class:`~simpleclaw.study.source_planner.StudyTopic` Protocol(topic_id/label/
    category/kind/max_sources/freshness_hours)을 만족하므로 source planner 에 그대로
    넘길 수 있다. ``raw`` 는 write-back(last_studied_at 등) 을 위해 원본 dict 참조를
    보관한다.
    """

    topic_id: str
    label: str
    category: str
    kind: TopicKind
    max_sources: int
    freshness_hours: int
    title: str
    interest_score: float
    refresh_requested: bool
    raw: dict = field(default_factory=dict, compare=False, repr=False)


def _utcnow() -> datetime:
    """기본 now 제공자 — 테스트는 runner 에 now 콜백을 주입해 고정한다."""
    return datetime.now(timezone.utc)


def _confidence_grade(score: float) -> str:
    """수집 신뢰도(0~1)를 wiki_updater 가 쓰는 등급 문자열로 변환한다."""
    if score >= _HIGH_CONFIDENCE:
        return "high"
    if score >= _MEDIUM_CONFIDENCE:
        return "medium"
    return "low"


@dataclass
class StudyRunner:
    """Agent Study Wiki 의 daily study 파이프라인을 한 번 실행하는 runner.

    Args:
        wiki_dir: study wiki 루트(``topics.yaml`` 등이 있는 디렉터리).
        collectors: source 수집을 위임할 collector 레지스트리. 비어 있으면
            placeholder 로 폴백해 안전한 no-op 으로 흐른다.
        policy: source planner 에 쓸 정책.
        scorer: 일반 뉴스 relevance scorer(None 이면 휴리스틱 기본).
        relevance_threshold: general_news 채택 임계값.
        max_topics_per_run: 한 번에 공부할 최대 topic 수.
        now: 시간 제공자(테스트 주입용).
        run_id_factory: run_id 생성기(테스트 주입용). 기본은 started_at 기반.
    """

    wiki_dir: Path
    collectors: CollectorRegistry = field(default_factory=CollectorRegistry)
    policy: SourcePolicy = DEFAULT_SOURCE_POLICY
    scorer: RelevanceScorer | None = None
    relevance_threshold: float = DEFAULT_RELEVANCE_THRESHOLD
    max_topics_per_run: int = 8
    now: Callable[[], datetime] = _utcnow
    run_id_factory: Callable[[datetime], str] | None = None

    def __post_init__(self) -> None:
        self.wiki_dir = Path(self.wiki_dir).expanduser()

    # -- 경로 헬퍼 ----------------------------------------------------

    @property
    def topics_path(self) -> Path:
        return self.wiki_dir / "topics.yaml"

    @property
    def daily_dir(self) -> Path:
        return self.wiki_dir / "daily"

    @property
    def index_path(self) -> Path:
        return self.wiki_dir / "index.sqlite"

    def _topic_page_path(self, topic_id: str) -> Path:
        return self.wiki_dir / "topics" / topic_id / "overview.md"

    @property
    def open_questions_path(self) -> Path:
        return self.wiki_dir / "open_questions.md"

    # -- public API ---------------------------------------------------

    def run(self) -> StudyRunSummary:
        """study 파이프라인을 한 번 실행하고 요약을 반환한다."""
        started = self.now()
        run_id = self._make_run_id(started)

        raw_topics, records = self._load_topics()
        selected_topics = self._select_topics(records)
        topics_considered = len(selected_topics)

        limitations: set[str] = set()
        items_added = 0
        updated_topic_ids: set[str] = set()
        index_rows: list[dict] = []
        by_topic: dict[str, list[StudyFetchResult]] = {}

        if selected_topics:
            topic_by_id = {t.topic_id: t for t in selected_topics}
            requests = plan_fetch_requests(selected_topics, policy=self.policy)
            results = self.collectors.fetch_all(requests)
            selection = select_wiki_worthy(
                results,
                topics=topic_by_id,
                scorer=self.scorer,
                threshold=self.relevance_threshold,
            )

            # 채택된 결과를 topic 별로 묶는다.
            for result in selection.selected:
                by_topic.setdefault(result.request.topic_id, []).append(result)

            now_iso = started.isoformat()
            for topic_id, topic in topic_by_id.items():
                topic_results = by_topic.get(topic_id, [])
                if not topic_results:
                    continue
                added = self._update_topic_page(topic, topic_results, now_iso)
                updated_topic_ids.add(topic_id)
                items_added += added
                for result in topic_results:
                    index_rows.append(self._index_row(topic, result, run_id, now_iso))
                    for lim in result.limitations:
                        limitations.add(lim)

        finished = self.now()
        finished_iso = finished.isoformat()

        # 인덱스 적재.
        if index_rows:
            self._append_index(index_rows)

        # topics.yaml write-back: 공부한 topic 의 메타 갱신.
        if updated_topic_ids:
            self._write_back_topics(
                raw_topics, records, updated_topic_ids, by_topic, finished_iso
            )

        # 데일리 노트 — "오늘 무엇을 공부했는지"만 요약.
        if topics_considered:
            self._write_daily_note(
                run_id=run_id,
                date=finished,
                considered=selected_topics,
                updated_ids=updated_topic_ids,
                items_added=items_added,
                limitations=sorted(limitations),
            )

        if topics_considered and not updated_topic_ids:
            # 주제는 골랐으나 채택된 source 가 0건 — collector 미연결의 전형적 신호.
            limitations.add("수집된 source 없음 (collector 미연결 가능성)")

        return StudyRunSummary(
            run_id=run_id,
            started_at=started.isoformat(),
            finished_at=finished_iso,
            topics_considered=topics_considered,
            topics_updated=len(updated_topic_ids),
            items_added=items_added,
            limitations=tuple(sorted(limitations)),
        )

    # -- topic registry ----------------------------------------------

    def _load_topics(self) -> tuple[list[dict], list[StudyTopicRecord]]:
        """topics.yaml 을 읽어 (raw list, 정규화 레코드 list) 를 반환한다.

        파일이 없으면 빈 결과. 형식은 top-level list 와 ``{topics: [...]}`` 둘 다
        허용한다(study_status 와 동일). 깨진 YAML 은 빈 결과로 폴백해 daily job 을
        죽이지 않는다(운영자 조회 도구가 별도로 파싱 오류를 표면화한다).
        """
        if not self.topics_path.is_file():
            return [], []
        try:
            data = yaml.safe_load(self.topics_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            logger.warning("study runner: topics.yaml 읽기 실패 — 빈 topic 으로 진행")
            return [], []
        if isinstance(data, dict):
            data = data.get("topics", [])
        if not isinstance(data, list):
            return [], []

        raw_topics = [t for t in data if isinstance(t, dict)]
        records: list[StudyTopicRecord] = []
        for raw in raw_topics:
            record = self._to_record(raw)
            if record is not None:
                records.append(record)
        return raw_topics, records

    def _to_record(self, raw: dict) -> StudyTopicRecord | None:
        """raw topic dict 를 :class:`StudyTopicRecord` 로 정규화한다(id 없으면 None)."""
        topic_id = str(raw.get("id") or raw.get("topic_id") or "").strip()
        if not topic_id:
            return None
        title = str(raw.get("title") or raw.get("name") or topic_id)
        category = str(raw.get("category") or "").strip()
        kind_raw = str(raw.get("kind") or TopicKind.USER_INTEREST.value).strip().lower()
        try:
            kind = TopicKind(kind_raw)
        except ValueError:
            kind = TopicKind.USER_INTEREST
        return StudyTopicRecord(
            topic_id=topic_id,
            label=str(raw.get("label") or title),
            category=category,
            kind=kind,
            max_sources=_coerce_int(raw.get("max_sources"), default=3),
            freshness_hours=_coerce_int(raw.get("freshness_hours"), default=24),
            title=title,
            interest_score=_coerce_float(
                raw.get("interest_score") or raw.get("score"), default=0.0
            ),
            refresh_requested=bool(raw.get("refresh_requested_at")),
            raw=raw,
        )

    def _select_topics(
        self, records: Sequence[StudyTopicRecord]
    ) -> list[StudyTopicRecord]:
        """이번 run 에서 공부할 topic 을 고른다.

        - 아카이브된 topic 제외.
        - 운영자 refresh 요청이 걸린 topic 을 우선, 그다음 관심도(interest_score)
          내림차순. 동률은 원래 순서 유지(stable sort).
        - ``max_topics_per_run`` 으로 상한.
        """
        active = [
            r
            for r in records
            if str(r.raw.get("status") or "active").strip().lower()
            not in _ARCHIVED_STATUSES
        ]
        # 우선순위: refresh 요청 먼저(True=0), 그다음 관심도 높은 순.
        ordered = sorted(
            active,
            key=lambda r: (0 if r.refresh_requested else 1, -r.interest_score),
        )
        return ordered[: max(0, self.max_topics_per_run)]

    def _write_back_topics(
        self,
        raw_topics: list[dict],
        records: Sequence[StudyTopicRecord],
        updated_ids: set[str],
        by_topic: dict[str, list[StudyFetchResult]],
        finished_iso: str,
    ) -> None:
        """공부한 topic 의 last_studied_at/source_count 를 갱신하고 refresh 를 지운다."""
        for raw in raw_topics:
            tid = str(raw.get("id") or raw.get("topic_id") or "").strip()
            if tid not in updated_ids:
                continue
            raw["last_studied_at"] = finished_iso
            raw["updated_at"] = finished_iso
            added = len(by_topic.get(tid, []))
            prev = _coerce_int(raw.get("source_count"), default=0)
            raw["source_count"] = prev + added
            # 처리한 refresh 요청은 비운다.
            raw.pop("refresh_requested_at", None)
        self._save_topics(raw_topics)

    def _save_topics(self, topics: list[dict]) -> None:
        """topics.yaml 을 ``{topics: [...]}`` 형태로 atomic 하게 다시 쓴다."""
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.topics_path.with_suffix(".yaml.tmp")
        payload = yaml.safe_dump(
            {"topics": topics}, allow_unicode=True, sort_keys=False
        )
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.topics_path)

    # -- wiki page 갱신 -----------------------------------------------

    def _update_topic_page(
        self,
        topic: StudyTopicRecord,
        results: Sequence[StudyFetchResult],
        timestamp: str,
    ) -> int:
        """한 topic 의 overview.md 를 부분 병합하고 추가 항목 수를 반환한다."""
        page_path = self._topic_page_path(topic.topic_id)
        existing = page_path.read_text(encoding="utf-8") if page_path.is_file() else ""

        updates: list[str] = []
        sources: list[str] = []
        cautions: list[str] = []
        min_confidence = 1.0
        for result in results:
            note = result.title.strip() or "(제목 없음)"
            snippet = " ".join(result.text.split())[:200]
            if snippet:
                note = f"{note} — {snippet}"
            updates.append(note)
            url = result.url.strip() or result.source.strip()
            if url:
                sources.append(url)
            for lim in result.limitations:
                cautions.append(lim)
            min_confidence = min(min_confidence, result.confidence)

        merged = merge_study_update(
            existing,
            topic_title=topic.title,
            updates=updates,
            sources=sources,
            cautions=_dedup(cautions),
            confidence=_confidence_grade(min_confidence),
            timestamp=timestamp,
        )
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(merged, encoding="utf-8")

        # 저신뢰면 open_questions.md 에도 미검증 항목을 남긴다(설계 정책).
        if _confidence_grade(min_confidence) == "low":
            self._append_open_questions(
                [f"[{topic.title}] {u}" for u in updates]
            )
        return len(results)

    def _append_open_questions(self, questions: Sequence[str]) -> None:
        """open_questions.md 에 미검증 항목을 dedup 누적한다."""
        existing = (
            self.open_questions_path.read_text(encoding="utf-8")
            if self.open_questions_path.is_file()
            else ""
        )
        merged = merge_open_questions(existing, questions)
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        self.open_questions_path.write_text(merged, encoding="utf-8")

    # -- index store --------------------------------------------------

    def _index_row(
        self,
        topic: StudyTopicRecord,
        result: StudyFetchResult,
        run_id: str,
        collected_at: str,
    ) -> dict:
        """채택된 한 결과를 study_items row dict 로 변환한다."""
        return {
            "topic_id": topic.topic_id,
            "title": result.title.strip() or topic.title,
            "summary": " ".join(result.text.split())[:500],
            "source": result.source.strip() or result.url.strip(),
            "url": result.url.strip(),
            "confidence": float(result.confidence),
            "collected_at": result.retrieved_at or collected_at,
            "run_id": run_id,
        }

    def _append_index(self, rows: Sequence[dict]) -> None:
        """index.sqlite 의 study_items 테이블에 row 들을 적재한다.

        스키마는 운영자 조회 도구(study_status)가 읽는 컬럼과 호환된다. 테이블이
        없으면 생성한다.
        """
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.index_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS study_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id TEXT,
                    title TEXT,
                    summary TEXT,
                    source TEXT,
                    url TEXT,
                    confidence REAL,
                    collected_at TEXT,
                    run_id TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO study_items
                    (topic_id, title, summary, source, url, confidence,
                     collected_at, run_id)
                VALUES
                    (:topic_id, :title, :summary, :source, :url, :confidence,
                     :collected_at, :run_id)
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    # -- daily note ---------------------------------------------------

    def _write_daily_note(
        self,
        *,
        run_id: str,
        date: datetime,
        considered: Sequence[StudyTopicRecord],
        updated_ids: set[str],
        items_added: int,
        limitations: Sequence[str],
    ) -> None:
        """daily/YYYY-MM-DD.md 에 "오늘 무엇을 공부했는지" 요약만 쓴다.

        같은 날 재실행되면 덮어쓴다(하루 1 노트). 상세 사실은 topic 페이지/인덱스에
        있으므로 데일리 노트는 의도적으로 요약만 담는다(설계 정책).
        """
        day = date.strftime("%Y-%m-%d")
        lines = [
            f"# {day} 학습 노트",
            "",
            f"- run_id: {run_id}",
            f"- 검토한 주제: {len(considered)}개",
            f"- 갱신한 주제: {len(updated_ids)}개",
            f"- 추가한 항목: {items_added}개",
            "",
            "## 오늘 공부한 주제",
        ]
        studied = [t for t in considered if t.topic_id in updated_ids]
        if studied:
            for topic in studied:
                lines.append(f"- {topic.title}")
        else:
            lines.append("- (새로 갱신된 주제 없음)")

        if limitations:
            lines += ["", "## 한계 / 주의"]
            lines += [f"- {lim}" for lim in limitations]

        self.daily_dir.mkdir(parents=True, exist_ok=True)
        note_path = self.daily_dir / f"{day}.md"
        note_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    # -- run_id -------------------------------------------------------

    def _make_run_id(self, started: datetime) -> str:
        """run_id 를 만든다(주입된 factory 우선, 없으면 시각 기반)."""
        if self.run_id_factory is not None:
            return self.run_id_factory(started)
        return f"study-{started.strftime('%Y%m%dT%H%M%S')}"


# ----------------------------------------------------------------------
# 보조 헬퍼
# ----------------------------------------------------------------------


def _coerce_int(value: object, *, default: int) -> int:
    """정수 필드 정규화(실패 시 default)."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: object, *, default: float) -> float:
    """실수 필드 정규화(실패 시 default)."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedup(items: Iterable[str]) -> list[str]:
    """순서를 보존하며 중복 제거(빈 항목 제외)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


# ----------------------------------------------------------------------
# study 프롬프트 로더 (YAML SoT)
#
# MVP runner 는 결정적 렌더링을 쓰지만, 후속 LLM 요약 단계가 곧바로 같은 spec 을
# format() 할 수 있도록 prompts/study/*.yaml 로더를 노출한다. 검증 로직은
# source_planner 의 공통 로더를 재사용한다(같은 패키지 내부 재사용).
# ----------------------------------------------------------------------


def load_topic_update_prompt(
    *, repo_root: str | Path | None = None
) -> StudyPromptSpec:
    """``prompts/study/topic_update.yaml`` 을 로드/검증한다."""
    return _load_study_prompt("topic_update", repo_root=repo_root)


def load_daily_digest_prompt(
    *, repo_root: str | Path | None = None
) -> StudyPromptSpec:
    """``prompts/study/daily_digest.yaml`` 을 로드/검증한다."""
    return _load_study_prompt("daily_digest", repo_root=repo_root)
