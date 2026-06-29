"""운영자용 Agent Study Wiki 조회/강제 업데이트 도구 (BIZ-395).

Agent Study Wiki 는 SimpleClaw 가 사용자의 관심사·Dreaming 산출물·중요 뉴스를
매일 공부해 쌓아 두는 외부 세계 배경지식 저장소이다(사용자 메모리와는 분리됨,
전체 설계는 `.hermes/plans/2026-06-29_095400-agent-study-wiki.md` 참고).

이 모듈은 그 wiki 를 **운영자만** 들여다보고 조작할 수 있게 하는 관찰성 계층이다.

- 운영자는 최근 study run, active/stale topic, low-confidence item 을 한 번에 본다.
- 잘못 공부한 topic 을 즉시 `archive` 하거나, 다음 daily run 에서 다시 공부하도록
  `refresh` 요청을 걸 수 있다.

## 설계 결정

- **저장 포맷에 직접 붙되 패키지 의존은 피한다.** Study 수집/정리 파이프라인
  (`simpleclaw.study`) 은 별도 이슈에서 구축된다. 이 도구는 그 파이프라인이
  쓰는 on-disk 레이아웃(topics.yaml + daily/*.md + index.sqlite)을 직접 읽어,
  파이프라인이 아직 없거나 비어 있어도 graceful 하게 "not configured" 로 응답한다.
  덕분에 운영자 도구가 수집 파이프라인보다 먼저 머지돼도 깨지지 않는다.
- **read-mostly.** 조회는 부수효과가 없고, `archive`/`refresh` 만 topics.yaml 의
  status/플래그를 갱신한다. 실제 재수집 실행은 daily runner 의 몫이므로 여기서는
  요청 플래그(`refresh_requested_at`)만 남긴다.
- **operator scope 강제.** tool registry 에서 OPERATOR scope + operator_gate 로
  노출하고, dispatch 에서도 operator_tools 가 아니면 차단한다. 일반 사용자 runtime
  tool 표면에는 절대 노출하지 않는다.

## On-disk 레이아웃 (기대값)

```text
<wiki_dir>/
├── topics.yaml            # topic registry (list 또는 {topics: [...]})
├── daily/YYYY-MM-DD.md    # daily study digest
├── topics/<id>/overview.md
└── index.sqlite           # study_items 인덱스 (있으면 low-confidence 산출에 사용)
```
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

# wiki_dir 가 config 에 없을 때의 기본 경로 — 설계 문서의 runtime data 위치.
_DEFAULT_WIKI_DIR = "~/.simpleclaw-agent/default/agent_wiki"

# stale 판정 기본 임계값(시간). active topic 이 이 시간보다 오래 공부되지 않았으면
# "stale" 로 본다. config 의 study.status.stale_after_hours 로 덮어쓸 수 있다.
_DEFAULT_STALE_AFTER_HOURS = 72.0

# low-confidence 판정 기본 임계값. config 의 study.status.low_confidence_threshold
# 또는 tool arg 로 덮어쓸 수 있다.
_DEFAULT_LOW_CONFIDENCE = 0.5

# 응답 폭주 방지를 위한 상한.
_MAX_TOPICS = 100
_MAX_LOW_CONFIDENCE_ITEMS = 50
_MAX_RUN_EXCERPT_CHARS = 1500
_MAX_PAGE_CHARS = 8000

NowFn = Callable[[], datetime]

# active 로 간주할 status 값(아카이브/삭제가 아닌 모든 상태).
_ARCHIVED_STATUSES = frozenset({"archived", "deleted", "retired"})


class StudyStatusError(Exception):
    """Study wiki 조회/조작 중 발생한 운영 도구 오류."""


# ----------------------------------------------------------------------
# 데이터 모델
# ----------------------------------------------------------------------


@dataclass
class StudyTopicView:
    """topics.yaml 한 항목을 운영자 응답용으로 정규화한 뷰."""

    id: str
    title: str
    status: str
    confidence: float | None
    interest_score: float | None
    created_at: str | None
    updated_at: str | None
    last_studied_at: str | None
    source_count: int | None
    summary: str | None
    refresh_requested_at: str | None
    age_hours: float | None
    is_stale: bool
    is_archived: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "confidence": self.confidence,
            "interest_score": self.interest_score,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_studied_at": self.last_studied_at,
            "source_count": self.source_count,
            "summary": self.summary,
            "refresh_requested_at": self.refresh_requested_at,
            "age_hours": self.age_hours,
            "is_stale": self.is_stale,
            "is_archived": self.is_archived,
        }


@dataclass
class StudyRunView:
    """가장 최근 daily study run(=daily/YYYY-MM-DD.md) 요약."""

    date: str
    path: str
    excerpt: str
    total_runs: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "path": self.path,
            "excerpt": self.excerpt,
            "total_runs": self.total_runs,
        }


@dataclass
class StudyLowConfidenceItem:
    """답변 근거로 쓰기엔 신뢰도가 낮은 study item/topic."""

    topic_id: str | None
    title: str
    confidence: float | None
    source: str | None
    kind: str  # "item" | "topic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "title": self.title,
            "confidence": self.confidence,
            "source": self.source,
            "kind": self.kind,
        }


@dataclass
class StudyStatusReport:
    """`/study status` 한 화면에 필요한 모든 정보."""

    configured: bool
    wiki_dir: str
    last_run: StudyRunView | None
    total_topics: int
    active_topics: list[StudyTopicView] = field(default_factory=list)
    stale_topics: list[StudyTopicView] = field(default_factory=list)
    low_confidence_items: list[StudyLowConfidenceItem] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "wiki_dir": self.wiki_dir,
            "last_run": self.last_run.to_dict() if self.last_run else None,
            "total_topics": self.total_topics,
            "active_topics": [t.to_dict() for t in self.active_topics],
            "stale_topics": [t.to_dict() for t in self.stale_topics],
            "low_confidence_items": [i.to_dict() for i in self.low_confidence_items],
            "note": self.note,
        }


# ----------------------------------------------------------------------
# 시간 파싱 헬퍼
# ----------------------------------------------------------------------


def _utcnow() -> datetime:
    """기본 now 제공자 — 테스트는 store 에 now 콜백을 주입해 고정한다."""
    return datetime.now(timezone.utc)


def _parse_iso(value: object) -> datetime | None:
    """ISO8601 문자열을 timezone-aware datetime 으로 파싱한다.

    naive 값은 UTC 로 간주하고, 흔한 ``Z`` 접미사도 허용한다. 파싱 실패는
    조용히 ``None`` — 운영 조회를 깨뜨리지 않는 것이 우선이다.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _coerce_float(value: object) -> float | None:
    """confidence/score 같은 수치를 float 로 정규화한다 (실패 시 None)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    """source_count 같은 정수 필드를 정규화한다 (실패 시 None)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# wiki_dir 해석
# ----------------------------------------------------------------------


def resolve_wiki_dir(config_path: str | Path | None) -> Path:
    """config.yaml 의 ``study.wiki_dir`` 을 해석하고 없으면 기본값을 쓴다.

    `simpleclaw.config_sections.study` 가 아직 없을 수 있으므로 YAML 을 직접
    읽되, 어떤 파싱 오류도 기본 경로로 폴백한다 — 운영 도구는 config 가
    깨져 있어도 최소한 "비어 있음" 을 답할 수 있어야 한다.
    """
    wiki_dir = _DEFAULT_WIKI_DIR
    if config_path is not None:
        try:
            raw = Path(config_path).read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
            study_cfg = data.get("study") if isinstance(data, dict) else None
            if isinstance(study_cfg, dict):
                candidate = study_cfg.get("wiki_dir")
                if isinstance(candidate, str) and candidate.strip():
                    wiki_dir = candidate.strip()
        except (OSError, yaml.YAMLError):
            wiki_dir = _DEFAULT_WIKI_DIR
    return Path(wiki_dir).expanduser()


def _resolve_status_thresholds(
    config_path: str | Path | None,
) -> tuple[float, float]:
    """config 의 study.status 설정에서 stale/low-confidence 임계값을 읽는다."""
    stale = _DEFAULT_STALE_AFTER_HOURS
    low_conf = _DEFAULT_LOW_CONFIDENCE
    if config_path is None:
        return stale, low_conf
    try:
        data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return stale, low_conf
    study_cfg = data.get("study") if isinstance(data, dict) else None
    status_cfg = study_cfg.get("status") if isinstance(study_cfg, dict) else None
    if isinstance(status_cfg, dict):
        stale = _coerce_float(status_cfg.get("stale_after_hours")) or stale
        low_conf = _coerce_float(status_cfg.get("low_confidence_threshold")) or low_conf
    return stale, low_conf


# ----------------------------------------------------------------------
# Store
# ----------------------------------------------------------------------


class StudyWikiStore:
    """Agent Study Wiki on-disk 레이아웃에 대한 얇은 read/write 어댑터.

    수집 파이프라인이 아직 없거나 wiki 디렉터리가 비어 있어도 모든 조회는
    빈 결과로 정상 응답한다. 쓰기(archive/refresh)는 topics.yaml 만 갱신한다.
    """

    def __init__(
        self,
        wiki_dir: str | Path,
        *,
        stale_after_hours: float = _DEFAULT_STALE_AFTER_HOURS,
        low_confidence_threshold: float = _DEFAULT_LOW_CONFIDENCE,
        now: NowFn | None = None,
    ) -> None:
        self._wiki_dir = Path(wiki_dir).expanduser()
        self._stale_after_hours = float(stale_after_hours)
        self._low_confidence_threshold = float(low_confidence_threshold)
        self._now = now or _utcnow

    # -- 경로 헬퍼 ----------------------------------------------------

    @property
    def wiki_dir(self) -> Path:
        return self._wiki_dir

    @property
    def topics_path(self) -> Path:
        return self._wiki_dir / "topics.yaml"

    @property
    def daily_dir(self) -> Path:
        return self._wiki_dir / "daily"

    @property
    def index_path(self) -> Path:
        return self._wiki_dir / "index.sqlite"

    def is_configured(self) -> bool:
        """wiki 디렉터리와 topic registry 가 존재하는지 여부."""
        return self._wiki_dir.is_dir() and self.topics_path.is_file()

    # -- topic 읽기 ---------------------------------------------------

    def _load_raw_topics(self) -> list[dict[str, Any]]:
        """topics.yaml 을 list[dict] 로 정규화해서 읽는다.

        top-level list 와 ``{topics: [...]}`` 두 형태를 모두 허용한다. 파일이
        없으면 빈 리스트, 깨졌으면 명시적 오류 — 조용히 빈 값으로 두면 운영자가
        "공부한 게 없다" 와 "파일이 깨졌다" 를 구분 못 한다.
        """
        if not self.topics_path.is_file():
            return []
        try:
            data = yaml.safe_load(self.topics_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise StudyStatusError(f"topics.yaml 파싱 실패: {exc}") from exc
        if data is None:
            return []
        if isinstance(data, dict):
            data = data.get("topics", [])
        if not isinstance(data, list):
            raise StudyStatusError(
                "topics.yaml 형식이 올바르지 않습니다 (list 또는 {topics: [...]} 기대)."
            )
        return [t for t in data if isinstance(t, dict)]

    def _topic_view(self, raw: dict[str, Any]) -> StudyTopicView:
        """raw topic dict 한 개를 정규화하고 staleness 를 계산한다."""
        topic_id = str(raw.get("id") or raw.get("topic_id") or "").strip()
        title = str(raw.get("title") or raw.get("name") or topic_id or "(untitled)")
        status = str(raw.get("status") or "active").strip().lower()
        confidence = _coerce_float(raw.get("confidence"))
        last_studied_at = raw.get("last_studied_at") or raw.get("updated_at")
        studied_dt = _parse_iso(last_studied_at)
        age_hours: float | None = None
        if studied_dt is not None:
            age_hours = (self._now() - studied_dt).total_seconds() / 3600.0

        is_archived = status in _ARCHIVED_STATUSES
        # active topic 만 stale 판정 대상 — 아카이브된 topic 은 stale 이 의미 없다.
        is_stale = (not is_archived) and (
            studied_dt is None or (age_hours is not None and age_hours > self._stale_after_hours)
        )

        return StudyTopicView(
            id=topic_id,
            title=title,
            status=status,
            confidence=confidence,
            interest_score=_coerce_float(
                raw.get("interest_score") or raw.get("score")
            ),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
            last_studied_at=last_studied_at,
            source_count=_coerce_int(raw.get("source_count")),
            summary=(str(raw["summary"]) if raw.get("summary") else None),
            refresh_requested_at=raw.get("refresh_requested_at"),
            age_hours=(round(age_hours, 1) if age_hours is not None else None),
            is_stale=is_stale,
            is_archived=is_archived,
        )

    def load_topics(self, *, include_archived: bool = True) -> list[StudyTopicView]:
        """모든 topic 을 뷰로 변환해 반환한다 (id 누락 항목은 건너뜀)."""
        views: list[StudyTopicView] = []
        for raw in self._load_raw_topics():
            view = self._topic_view(raw)
            if not view.id:
                continue
            if not include_archived and view.is_archived:
                continue
            views.append(view)
            if len(views) >= _MAX_TOPICS:
                break
        return views

    def get_topic(self, topic_id: str) -> StudyTopicView | None:
        """id 로 단일 topic 을 찾는다 (없으면 None)."""
        target = topic_id.strip()
        for view in self.load_topics():
            if view.id == target:
                return view
        return None

    # -- daily run 읽기 -----------------------------------------------

    def last_run(self) -> StudyRunView | None:
        """가장 최근 daily/YYYY-MM-DD.md 를 요약해 반환한다."""
        if not self.daily_dir.is_dir():
            return None
        notes = sorted(
            (p for p in self.daily_dir.glob("*.md") if p.is_file()),
            key=lambda p: p.name,
        )
        if not notes:
            return None
        latest = notes[-1]
        try:
            text = latest.read_text(encoding="utf-8")
        except OSError as exc:
            raise StudyStatusError(f"daily note 읽기 실패: {latest.name}: {exc}") from exc
        excerpt = text.strip()
        if len(excerpt) > _MAX_RUN_EXCERPT_CHARS:
            excerpt = excerpt[:_MAX_RUN_EXCERPT_CHARS] + "…"
        return StudyRunView(
            date=latest.stem,
            path=str(latest),
            excerpt=excerpt,
            total_runs=len(notes),
        )

    # -- topic page 읽기 ----------------------------------------------

    def topic_page(self, topic_id: str) -> str | None:
        """topics/<id>/overview.md 본문을 반환한다 (없으면 None)."""
        page = self._wiki_dir / "topics" / topic_id / "overview.md"
        if not page.is_file():
            return None
        try:
            text = page.read_text(encoding="utf-8")
        except OSError:
            return None
        if len(text) > _MAX_PAGE_CHARS:
            text = text[:_MAX_PAGE_CHARS] + "\n…(truncated)"
        return text

    # -- low-confidence 산출 -----------------------------------------

    def low_confidence_items(
        self, *, threshold: float | None = None
    ) -> list[StudyLowConfidenceItem]:
        """신뢰도가 임계값 미만인 study item/topic 을 모은다.

        index.sqlite 에 study_items 테이블이 있으면 item 단위로 집계하고,
        없으면 topics.yaml 의 confidence 로 폴백한다. 인덱스 스키마는 후속
        이슈에서 확정되므로, 컬럼이 없거나 테이블이 없으면 조용히 폴백한다.
        """
        limit = self._low_confidence_threshold if threshold is None else float(threshold)
        items = self._low_confidence_from_index(limit)
        if items is not None:
            return items[:_MAX_LOW_CONFIDENCE_ITEMS]
        # 인덱스가 없으면 topic confidence 로 폴백.
        fallback: list[StudyLowConfidenceItem] = []
        for view in self.load_topics(include_archived=False):
            if view.confidence is not None and view.confidence < limit:
                fallback.append(
                    StudyLowConfidenceItem(
                        topic_id=view.id,
                        title=view.title,
                        confidence=view.confidence,
                        source=None,
                        kind="topic",
                    )
                )
        fallback.sort(key=lambda i: (i.confidence if i.confidence is not None else 1.0))
        return fallback[:_MAX_LOW_CONFIDENCE_ITEMS]

    def _low_confidence_from_index(
        self, threshold: float
    ) -> list[StudyLowConfidenceItem] | None:
        """index.sqlite 의 study_items 에서 low-confidence item 을 읽는다.

        Returns:
            인덱스/테이블/컬럼이 갖춰져 있으면 item 리스트, 아니면 ``None``
            (호출자가 topic 폴백을 쓰도록).
        """
        if not self.index_path.is_file():
            return None
        try:
            conn = sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='study_items'"
            )
            if cur.fetchone() is None:
                return None
            cols = {row[1] for row in conn.execute("PRAGMA table_info(study_items)")}
            if "confidence" not in cols:
                return None
            title_col = "title" if "title" in cols else (
                "summary" if "summary" in cols else None
            )
            topic_col = "topic_id" if "topic_id" in cols else None
            source_col = "source" if "source" in cols else (
                "url" if "url" in cols else None
            )
            select_cols = ["confidence"]
            if title_col:
                select_cols.append(title_col)
            if topic_col:
                select_cols.append(topic_col)
            if source_col:
                select_cols.append(source_col)
            query = (
                f"SELECT {', '.join(select_cols)} FROM study_items "
                "WHERE confidence IS NOT NULL AND confidence < ? "
                "ORDER BY confidence ASC LIMIT ?"
            )
            rows = conn.execute(
                query, (threshold, _MAX_LOW_CONFIDENCE_ITEMS)
            ).fetchall()
        except sqlite3.Error:
            return None
        finally:
            conn.close()

        items: list[StudyLowConfidenceItem] = []
        for row in rows:
            keys = row.keys()
            items.append(
                StudyLowConfidenceItem(
                    topic_id=(str(row[topic_col]) if topic_col and topic_col in keys else None),
                    title=(
                        str(row[title_col])
                        if title_col and title_col in keys and row[title_col]
                        else "(untitled item)"
                    ),
                    confidence=_coerce_float(row["confidence"]),
                    source=(
                        str(row[source_col])
                        if source_col and source_col in keys and row[source_col]
                        else None
                    ),
                    kind="item",
                )
            )
        return items

    # -- 종합 report --------------------------------------------------

    def status_report(self, *, max_per_section: int = 20) -> StudyStatusReport:
        """`/study status` 응답용 종합 리포트를 만든다."""
        if not self.is_configured():
            return StudyStatusReport(
                configured=False,
                wiki_dir=str(self._wiki_dir),
                last_run=None,
                total_topics=0,
                note=(
                    "Agent Study Wiki 가 아직 구성되지 않았습니다 "
                    f"({self.topics_path} 없음). daily study run 이 한 번 이상 "
                    "실행되면 채워집니다."
                ),
            )
        topics = self.load_topics()
        active = [t for t in topics if not t.is_archived]
        stale = [t for t in active if t.is_stale]
        # 가장 오래된 것부터 보여 줘야 운영자가 우선순위를 잡는다.
        stale.sort(key=lambda t: (t.age_hours if t.age_hours is not None else 1e9), reverse=True)
        return StudyStatusReport(
            configured=True,
            wiki_dir=str(self._wiki_dir),
            last_run=self.last_run(),
            total_topics=len(topics),
            active_topics=active[:max_per_section],
            stale_topics=stale[:max_per_section],
            low_confidence_items=self.low_confidence_items(),
        )

    # -- 쓰기 (operator action) ---------------------------------------

    def _rewrite_topic(
        self, topic_id: str, mutate: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        """topics.yaml 에서 한 topic 을 찾아 mutate 적용 후 원자적으로 저장한다.

        Raises:
            StudyStatusError: wiki 미구성 또는 topic 미발견.
        """
        if not self.is_configured():
            raise StudyStatusError(
                f"Agent Study Wiki 가 구성되지 않았습니다 ({self.topics_path} 없음)."
            )
        raw_topics = self._load_raw_topics()
        target = topic_id.strip()
        found: dict[str, Any] | None = None
        for raw in raw_topics:
            rid = str(raw.get("id") or raw.get("topic_id") or "").strip()
            if rid == target:
                found = raw
                break
        if found is None:
            raise StudyStatusError(f"topic '{topic_id}' 을 찾을 수 없습니다.")
        mutate(found)
        self._save_topics(raw_topics)
        return found

    def _save_topics(self, topics: list[dict[str, Any]]) -> None:
        """topics.yaml 을 {topics: [...]} 형태로 atomic 하게 다시 쓴다."""
        tmp = self.topics_path.with_suffix(".yaml.tmp")
        payload = yaml.safe_dump(
            {"topics": topics}, allow_unicode=True, sort_keys=False
        )
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.topics_path)

    def archive_topic(self, topic_id: str) -> StudyTopicView:
        """topic 을 archived 로 표시한다 (이후 retrieval/공부 대상에서 제외)."""

        def _mark(raw: dict[str, Any]) -> None:
            raw["status"] = "archived"
            raw["archived_at"] = self._now().isoformat()
            # 아카이브하면 대기 중인 refresh 요청은 무의미하므로 비운다.
            raw.pop("refresh_requested_at", None)

        updated = self._rewrite_topic(topic_id, _mark)
        return self._topic_view(updated)

    def refresh_topic(self, topic_id: str) -> StudyTopicView:
        """다음 daily run 에서 즉시 재수집하도록 refresh 요청 플래그를 건다.

        실제 재수집은 daily runner 의 책임이다. 이 도구는 동기적으로 LLM/네트워크
        수집을 돌리지 않고, runner 가 pick up 할 요청 시각만 남긴다 — 무거운 수집을
        운영자 명령 경로에 묶지 않기 위함.
        """

        def _mark(raw: dict[str, Any]) -> None:
            raw["refresh_requested_at"] = self._now().isoformat()
            # 아카이브돼 있었다면 다시 active 로 되살린다(재공부 의도).
            if str(raw.get("status") or "").lower() in _ARCHIVED_STATUSES:
                raw["status"] = "active"
                raw.pop("archived_at", None)

        updated = self._rewrite_topic(topic_id, _mark)
        return self._topic_view(updated)


# ----------------------------------------------------------------------
# Native tool handler
# ----------------------------------------------------------------------

_VALID_ACTIONS = frozenset({"status", "topics", "show", "refresh", "archive"})


def handle_study_status(
    args: dict[str, Any],
    *,
    config_path: str | Path | None = None,
    store: StudyWikiStore | None = None,
    now: NowFn | None = None,
) -> str:
    """``study_status`` operator native tool 의 Function Calling 핸들러.

    Args:
        args: ``action`` (status|topics|show|refresh|archive) 과 옵션 ``topic_id``,
            ``include_archived``, ``low_confidence_threshold`` 를 담은 tool arguments.
        config_path: wiki_dir/임계값 해석용 config.yaml 경로.
        store: 테스트 주입용 store. 주어지면 config_path 해석을 건너뛴다.
        now: 테스트 주입용 시간 콜백.

    Returns:
        운영자 응답용 JSON 문자열. 실패는 ``{"ok": false, "error": ...}`` 로 축약해
        tool loop 를 죽이지 않는다.
    """
    action = str(args.get("action") or "status").strip().lower()
    if action not in _VALID_ACTIONS:
        return _error_json(
            f"알 수 없는 action '{action}'. "
            f"{sorted(_VALID_ACTIONS)} 중 하나를 사용하세요."
        )

    if store is None:
        wiki_dir = resolve_wiki_dir(config_path)
        stale_hours, low_conf = _resolve_status_thresholds(config_path)
        store = StudyWikiStore(
            wiki_dir,
            stale_after_hours=stale_hours,
            low_confidence_threshold=low_conf,
            now=now,
        )

    try:
        if action == "status":
            report = store.status_report()
            return _ok_json({"action": "status", **report.to_dict()})

        if action == "topics":
            include_archived = bool(args.get("include_archived", False))
            topics = store.load_topics(include_archived=include_archived)
            return _ok_json(
                {
                    "action": "topics",
                    "configured": store.is_configured(),
                    "wiki_dir": str(store.wiki_dir),
                    "count": len(topics),
                    "include_archived": include_archived,
                    "topics": [t.to_dict() for t in topics],
                }
            )

        if action == "show":
            topic_id = _require_topic_id(args)
            view = store.get_topic(topic_id)
            if view is None:
                return _error_json(f"topic '{topic_id}' 을 찾을 수 없습니다.")
            return _ok_json(
                {
                    "action": "show",
                    "topic": view.to_dict(),
                    "page": store.topic_page(topic_id),
                }
            )

        if action == "refresh":
            topic_id = _require_topic_id(args)
            view = store.refresh_topic(topic_id)
            return _ok_json(
                {
                    "action": "refresh",
                    "topic": view.to_dict(),
                    "message": (
                        f"topic '{topic_id}' refresh 요청을 등록했습니다. "
                        "다음 daily study run 에서 재수집됩니다."
                    ),
                }
            )

        if action == "archive":
            topic_id = _require_topic_id(args)
            view = store.archive_topic(topic_id)
            return _ok_json(
                {
                    "action": "archive",
                    "topic": view.to_dict(),
                    "message": f"topic '{topic_id}' 을 archive 했습니다.",
                }
            )
    except StudyStatusError as exc:
        return _error_json(str(exc))
    except Exception as exc:  # noqa: BLE001 — tool loop 보호용 방어적 축약.
        return _error_json(f"study_status 처리 오류: {str(exc)[:200]}")

    # _VALID_ACTIONS 검증을 통과한 이상 도달 불가하지만 안전하게.
    return _error_json(f"처리되지 않은 action '{action}'.")


def _require_topic_id(args: dict[str, Any]) -> str:
    """show/refresh/archive 에 필수인 topic_id 를 추출/검증한다."""
    topic_id = str(args.get("topic_id") or "").strip()
    if not topic_id:
        raise StudyStatusError("이 action 에는 'topic_id' 가 필요합니다.")
    return topic_id


def _ok_json(payload: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, sort_keys=True)


def _error_json(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False, sort_keys=True)


__all__ = [
    "StudyStatusError",
    "StudyTopicView",
    "StudyRunView",
    "StudyLowConfidenceItem",
    "StudyStatusReport",
    "StudyWikiStore",
    "resolve_wiki_dir",
    "handle_study_status",
]
