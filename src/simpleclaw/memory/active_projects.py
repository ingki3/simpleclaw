"""사용자가 현재 집중 중인 "프로젝트" 엔티티의 식별 · 누적 · 렌더링 (BIZ-74).

배경:
    BIZ-66 §1에서 드러난 문제 — 사용자가 5-01~5-03에 SimpleClaw / Multica 빌드·QA에
    대부분의 시간을 쏟았는데도 USER.md에 "현재 집중 중인 프로젝트" 정보가 전혀
    반영되지 않았다. 새 세션의 첫 응답에서 에이전트가 프로젝트 맥락을 0에서 출발하지
    않도록, 최근 N일 대화에서 프로젝트 엔티티를 자동 도출하고 USER.md의
    ``<!-- managed:dreaming:active-projects -->`` 섹션을 in-place 갱신한다.

설계 결정:
    - JSONL sidecar 한 곳에 모든 프로젝트 메타를 보관한다 (``InsightStore`` 패턴과
      동일). USER.md 본문은 sidecar에서 N일 윈도우로 필터링한 사람이 읽는 요약을
      렌더링한 결과일 뿐 — 진실의 출처는 sidecar 한 곳뿐이라 마이그레이션·검수 단순.
    - sidecar에는 윈도우 밖 프로젝트도 그대로 남겨둔다(decay/archive는 BIZ-78의
      영역). 윈도우 밖 항목은 USER.md 섹션에서 자동으로 사라지지만 sidecar 기록은
      보존되므로 다시 활성화되면 first_seen이 최초값으로 유지된다.
    - "프로젝트"의 정의는 LLM이 내린다 — 모듈은 이름/역할/요약/last_seen 필드만
      받는다. 휴리스틱(키워드 매칭 등)으로 식별하지 않는 이유: 사용자가 새 프로젝트를
      시작할 때마다 모듈을 고치는 것은 비현실적이고, BIZ-66의 평가에서도 "토픽
      탐지는 LLM에 맡기되 메타로 캘리브레이션한다"가 합의된 방향이었다.
    - 정규화는 ``normalize_name`` 한 곳에서. 같은 프로젝트를 한·영 혼용으로 적어도
      (예: "SimpleClaw" vs "심플클로우") 동일 키로 묶이도록.

후속 sub-issue 와의 관계:
    - **BIZ-77 (F, source linkage)**: ``ActiveProject``에 ``source_msg_ids`` 필드는
      두지 않는다. 본 모듈은 "현재 활성 프로젝트 카드"만 책임지며, 메시지 출처
      추적은 BIZ-77의 InsightStore-side 확장에서 통합 관리한다.
    - **BIZ-78 (C, decay)**: 윈도우 필터링은 본 모듈이 담당하지만 archive/forget은
      BIZ-78의 일관된 정책(reject blocklist 등)을 따른다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 데이터 클래스
# ----------------------------------------------------------------------


@dataclass
class ActiveProject:
    """사용자가 현재(또는 최근 N일 동안) 집중 중인 프로젝트 한 건.

    필드:
        name: 사람이 읽기 좋은 프로젝트 이름(예: ``SimpleClaw``). 비교에는
            ``normalize_name(name)`` 정규형이 쓰인다.
        role: 이 프로젝트에서의 사용자 역할/관계(예: "솔로 빌더 / 메모리 파이프라인 개선").
        recent_summary: 최근 활동을 한두 문장으로 요약. 매 사이클 LLM이 갱신한다.
        first_seen: 이 프로젝트가 sidecar에 처음 등재된 시각.
        last_seen: 가장 최근에 관측된 시각. 윈도우 필터링과 정렬에 사용.
    """

    name: str
    role: str
    recent_summary: str
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """JSONL 직렬화용 dict 로 변환 (datetime → ISO 문자열)."""
        d = asdict(self)
        d["first_seen"] = self.first_seen.isoformat()
        d["last_seen"] = self.last_seen.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ActiveProject:
        """JSONL 역직렬화. 누락 필드는 합리적 기본값으로 보강."""
        first_seen = d.get("first_seen")
        last_seen = d.get("last_seen")
        return cls(
            name=str(d.get("name", "")).strip(),
            role=str(d.get("role", "")).strip(),
            recent_summary=str(d.get("recent_summary", "")).strip(),
            first_seen=(
                datetime.fromisoformat(first_seen)
                if isinstance(first_seen, str)
                else datetime.now()
            ),
            last_seen=(
                datetime.fromisoformat(last_seen)
                if isinstance(last_seen, str)
                else datetime.now()
            ),
        )


# ----------------------------------------------------------------------
# 정규화
# ----------------------------------------------------------------------

# 이름 정규화: 공백·구두점 제거 + 소문자화. 한·영 동의어를 묶기 위함이지만
# 실제 한국어→영문 음역 변환까지 하지는 않는다(과처리 위험). LLM이 이름을
# 일관되게 출력하도록 prompt에서 가이드한다.
_NAME_NORMALIZE_RE = re.compile(r"[^\w가-힣]+", re.UNICODE)


def normalize_name(name: str) -> str:
    """프로젝트 이름을 비교 가능한 정규형으로 변환한다.

    - 양 끝 공백 제거
    - 영문 소문자화
    - 공백·구두점 제거 (한글·영문·숫자만 남김)

    빈 문자열이 들어오면 빈 문자열 반환. 호출자는 빈 정규형은 무시해야 한다.
    """
    if not name:
        return ""
    return _NAME_NORMALIZE_RE.sub("", name.strip().lower())


# ----------------------------------------------------------------------
# Sidecar 저장소
# ----------------------------------------------------------------------


class ActiveProjectStore:
    """JSONL 기반 active-projects sidecar 저장소.

    파일 구조: 한 줄당 ``ActiveProject.to_dict()`` JSON. 정규화된 name 별로 한 줄.
    동시 쓰기는 가정하지 않는다 (드리밍은 단일 사이클만 실행됨).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, ActiveProject]:
        """파일에서 모든 프로젝트를 로드한다. 키는 ``normalize_name(name)``.

        파일이 없거나 비어 있으면 빈 dict. 손상된 줄은 skip + WARN.
        """
        out: dict[str, ActiveProject] = {}
        if not self._path.is_file():
            return out

        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Failed to read active-projects sidecar %s: %s", self._path, exc
            )
            return out

        for line_no, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                project = ActiveProject.from_dict(d)
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping malformed active-project line %d in %s: %s",
                    line_no,
                    self._path,
                    exc,
                )
                continue
            key = normalize_name(project.name)
            if not key:
                continue
            # 같은 키가 두 번 나오면 마지막을 유효 — 정상 흐름에서는 발생하지 않지만
            # 수기 편집/마이그레이션 충돌 시의 안전 장치.
            out[key] = project
        return out

    def save_all(self, projects: dict[str, ActiveProject]) -> None:
        """모든 프로젝트를 JSONL 로 원자적으로 다시 쓴다.

        디렉토리 누락 시 자동 생성. 동일 키 1행 보장을 위해 매 호출마다 전량 재기록.
        tmp 파일에 쓰고 rename 하므로 부분 쓰기 손상이 없다.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for project in projects.values():
                f.write(json.dumps(project.to_dict(), ensure_ascii=False))
                f.write("\n")
        tmp_path.replace(self._path)


# ----------------------------------------------------------------------
# 병합
# ----------------------------------------------------------------------


def merge_projects(
    existing: dict[str, ActiveProject],
    new_observations: list[ActiveProject],
    *,
    now: datetime | None = None,
) -> dict[str, ActiveProject]:
    """이번 사이클 관측치를 기존 sidecar 와 병합한다.

    규칙:
    1. 같은 키(``normalize_name`` 일치)가 있으면 in-place 갱신:
       - ``last_seen`` ← 새 관측의 last_seen (없으면 ``now``).
       - ``role`` / ``recent_summary`` ← 새 관측 값 (빈 문자열이면 기존 유지).
       - ``first_seen`` 은 절대 덮어쓰지 않는다.
       - ``name`` 표기는 새 관측 우선 (사용자가 표기를 다듬을 수 있음).
    2. 신규 키면 새 ``ActiveProject`` 로 등록 (first_seen=last_seen=관측 시각).
    3. 이번 회차에 관측되지 않은 기존 항목은 그대로 유지된다 — 윈도우 필터링은
       ``filter_active``에서 분리 처리하므로 sidecar는 영구 보관된다(decay는 BIZ-78).

    Args:
        existing: 현재 sidecar에 적재된 프로젝트 dict.
        new_observations: 이번 사이클 LLM이 추출한 관측치들.
        now: 명시적 시각(테스트용). None이면 ``datetime.now()``.

    Returns:
        병합 결과 dict — ``ActiveProjectStore.save_all`` 로 즉시 저장 가능.
    """
    merged = dict(existing)  # 얕은 복사 — 객체는 in-place 수정.
    fallback_now = now or datetime.now()

    for obs in new_observations:
        key = normalize_name(obs.name)
        if not key:
            # 정규화 후 빈 문자열 — 무의미한 이름은 무시.
            continue

        # 관측의 "시각"은 사이클 시각(``now``)이 권위 있는 값이다 — LLM 응답이 들고
        # 오는 ``last_seen`` 필드는 신뢰하지 않는다(LLM이 임의 시각을 만들어낼 위험).
        observed_at = fallback_now

        if key in merged:
            cur = merged[key]
            cur.last_seen = observed_at
            if obs.name.strip():
                cur.name = obs.name.strip()
            if obs.role.strip():
                cur.role = obs.role.strip()
            if obs.recent_summary.strip():
                cur.recent_summary = obs.recent_summary.strip()
            # first_seen 은 절대 덮어쓰지 않는다 — 첫 등록 시각 영구 보존.
        else:
            merged[key] = ActiveProject(
                name=obs.name.strip(),
                role=obs.role.strip(),
                recent_summary=obs.recent_summary.strip(),
                first_seen=observed_at,
                last_seen=observed_at,
            )

    return merged


# ----------------------------------------------------------------------
# 윈도우 필터링 / 렌더링
# ----------------------------------------------------------------------


def filter_active(
    projects: dict[str, ActiveProject],
    window_days: int,
    *,
    now: datetime | None = None,
) -> list[ActiveProject]:
    """윈도우 내(``last_seen >= now - window_days``) 프로젝트를 ``last_seen`` 내림차순으로 반환.

    Args:
        projects: ``ActiveProjectStore.load()`` 결과.
        window_days: 활성 윈도우(일). 0 이하면 빈 리스트(섹션이 비워짐).
        now: 명시적 시각(테스트용). None이면 ``datetime.now()``.

    Returns:
        윈도우 내 프로젝트 리스트, 최근 활동 순.
    """
    if window_days <= 0:
        return []

    fallback_now = now or datetime.now()
    cutoff = fallback_now - timedelta(days=window_days)

    active = [p for p in projects.values() if p.last_seen >= cutoff]
    active.sort(key=lambda p: p.last_seen, reverse=True)
    return active


def render_section_body(active: list[ActiveProject]) -> str:
    """active-projects managed 섹션의 본문 마크다운을 생성한다.

    포맷:
        ## <name>
        - 역할: <role>
        - 최근 활동: <recent_summary>
        - last_seen: YYYY-MM-DD

    빈 리스트가 들어오면 안내 문구를 반환한다 — 새 세션 첫 응답에서 "지금 집중
    중인 프로젝트 없음"이라는 사실 자체도 의미 있는 신호이기 때문(섹션이 통째로
    사라져 누락처럼 보이는 것보다 명시적으로 비어 있다고 알리는 편이 디버깅에
    유리).
    """
    if not active:
        return "_최근 윈도우에 식별된 활성 프로젝트가 없습니다._"

    lines: list[str] = []
    for project in active:
        last_seen_str = project.last_seen.strftime("%Y-%m-%d")
        lines.append(f"## {project.name}")
        if project.role:
            lines.append(f"- 역할: {project.role}")
        if project.recent_summary:
            lines.append(f"- 최근 활동: {project.recent_summary}")
        lines.append(f"- last_seen: {last_seen_str}")
        lines.append("")  # 단락 구분용 빈 줄

    # 마지막 trailing 빈 줄은 ``replace_section_body``가 정규화하므로 그대로 둬도 무해.
    return "\n".join(lines).rstrip()
