"""Insight 메타 스키마 + sidecar JSONL 저장소 (BIZ-73).

USER.md의 사람이 읽는 요약과 분리된 메타 데이터를 별도 파일로 보존한다.
- topic: 같은 주제의 신규/기존 인사이트를 병합하기 위한 정규화된 키.
- evidence_count: 같은 topic으로 누적 관측된 횟수.
- confidence: 0.0~1.0. 단발 관측은 0.4 이하로 캡. promotion_threshold 회 누적 시 승격.
- first_seen / last_seen: 최초/최근 관측 시각(ISO).
- source_msg_ids: 인사이트가 추출된 conversation 메시지 rowid 목록(빈 리스트 허용).

설계 결정:
- JSONL(한 줄당 한 인사이트) — 사람이 grep/diff로 검수 가능, 마이그레이션도 단순.
- 별도 sqlite 테이블이 아닌 파일 기반 sidecar — `.agent/insights.jsonl` 한 곳에 모이며,
  conversations.db 와 라이프사이클이 분리되어 백업·복원이 쉽다.
- topic 정규화는 lower + 공백/구두점 제거. 동일 주제를 한국어/영어로 표기해도 묶이도록.
- 단순 구현 — 후속 BIZ-77(source linkage 확장), BIZ-78(decay), BIZ-79(dry-run)에서 확장.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 데이터 클래스
# ----------------------------------------------------------------------

@dataclass
class InsightMeta:
    """단일 인사이트의 메타 정보.

    같은 topic 의 인사이트는 하나의 행으로 합쳐진다(병합 시 evidence_count++,
    last_seen 갱신, source_msg_ids 누적, text 는 최신 관측으로 갱신).
    """

    topic: str
    text: str
    evidence_count: int = 1
    confidence: float = 0.0
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    source_msg_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        """JSONL 직렬화용 dict 로 변환 (datetime → ISO 문자열)."""
        d = asdict(self)
        d["first_seen"] = self.first_seen.isoformat()
        d["last_seen"] = self.last_seen.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> InsightMeta:
        """JSONL 역직렬화. 누락 필드는 합리적 기본값으로 보강."""
        first_seen = d.get("first_seen")
        last_seen = d.get("last_seen")
        return cls(
            topic=str(d.get("topic", "")),
            text=str(d.get("text", "")),
            evidence_count=int(d.get("evidence_count", 1)),
            confidence=float(d.get("confidence", 0.0)),
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
            source_msg_ids=list(d.get("source_msg_ids") or []),
        )


# ----------------------------------------------------------------------
# 정규화 / 신뢰도 계산
# ----------------------------------------------------------------------

# topic 정규화에 사용할 패턴: 한·영 단어 문자만 남기고 모두 제거.
# (예: "맥북 에어 구매!" → "맥북에어구매")
_TOPIC_NORMALIZE_RE = re.compile(r"[^\w가-힣]+", re.UNICODE)


def normalize_topic(topic: str) -> str:
    """topic 문자열을 비교 가능한 정규형으로 변환.

    - 양 끝 공백 제거
    - 소문자화 (영문 알파벳)
    - 공백/구두점 제거 (한글·영문·숫자만 남김)

    빈 문자열이 들어오면 빈 문자열 반환.
    """
    if not topic:
        return ""
    cleaned = _TOPIC_NORMALIZE_RE.sub("", topic.strip().lower())
    return cleaned


def compute_confidence(evidence_count: int, promotion_threshold: int) -> float:
    """누적 관측 횟수와 승격 임계치로부터 confidence 를 계산한다.

    규칙(DoD §B):
    - 단일 관측(evidence_count == 1): 0.4 이하로 캡 → 정확히 0.4 부여.
    - 2회 이상: 0.4 → promotion_threshold 회에 도달하면 정확히 0.7 (승격선).
    - promotion_threshold 초과: 1.0 까지 점진 상승.

    공식:
    - 1회: 0.4
    - 2 ~ promotion_threshold: 0.4 + (n-1)/(threshold-1) * 0.3  → threshold 회에 0.7
    - threshold 초과: 0.7 + min(1, (n-threshold)/threshold) * 0.3 → 2*threshold 회에 1.0
    - threshold == 1 인 엣지(승격 즉시 발동) 케이스에서도 1회=0.4 캡 유지.

    Args:
        evidence_count: 누적 관측 수 (>=0).
        promotion_threshold: 승격에 필요한 관측 수 (>=1).

    Returns:
        0.0 ~ 1.0 사이의 confidence.
    """
    if evidence_count <= 0:
        return 0.0
    if promotion_threshold < 1:
        promotion_threshold = 1

    # 단일 관측 — DoD 의 핵심 가드: 1회만 본 인사이트는 절대 0.4 를 넘지 않는다.
    if evidence_count == 1:
        return 0.4

    if promotion_threshold == 1:
        # 임계치 1이면 2회부터 승격 — 0.7 부터 시작.
        return min(1.0, 0.7 + 0.05 * (evidence_count - 2))

    if evidence_count <= promotion_threshold:
        # 0.4 → 0.7 로 선형 보간 (n=threshold 에서 정확히 0.7).
        ratio = (evidence_count - 1) / (promotion_threshold - 1)
        return round(0.4 + ratio * 0.3, 4)

    # 승격선 위 — 0.7 → 1.0 로 추가 보간, 2*threshold 회에 1.0.
    extra = min(1.0, (evidence_count - promotion_threshold) / promotion_threshold)
    return round(min(1.0, 0.7 + extra * 0.3), 4)


def is_promoted(meta: InsightMeta, promotion_threshold: int) -> bool:
    """인사이트가 \"승격\" 상태인지 판단(USER.md 본문 노출 여부 결정에 사용)."""
    return meta.evidence_count >= max(promotion_threshold, 1)


# ----------------------------------------------------------------------
# 저장소
# ----------------------------------------------------------------------

class InsightStore:
    """JSONL 기반 인사이트 sidecar 저장소.

    파일 구조: 한 줄당 ``InsightMeta.to_dict()`` JSON. topic 별로 한 줄.
    동시 쓰기는 가정하지 않는다 (드리밍은 단일 사이클만 실행됨).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, InsightMeta]:
        """파일에서 모든 인사이트를 로드. topic 정규형을 키로 한다.

        파일이 없거나 비어 있으면 빈 dict. 손상된 줄은 skip + WARN.
        """
        out: dict[str, InsightMeta] = {}
        if not self._path.is_file():
            return out

        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read insights sidecar %s: %s", self._path, exc)
            return out

        for line_no, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                meta = InsightMeta.from_dict(d)
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping malformed insight line %d in %s: %s",
                    line_no, self._path, exc,
                )
                continue
            key = normalize_topic(meta.topic)
            if not key:
                continue
            # 중복 라인은 마지막 항목이 유효 — 정상 저장 흐름에선 발생하지 않지만
            # 수기 편집/마이그레이션 충돌 시의 안전 장치.
            out[key] = meta
        return out

    def save_all(self, insights: dict[str, InsightMeta]) -> None:
        """모든 인사이트를 JSONL 로 원자적으로 다시 쓴다.

        디렉토리 누락 시 자동 생성. 동일 토픽 1행 보장을 위해 저장 시점마다 전량 재기록.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # tmp 파일에 쓰고 rename — 부분 쓰기 중 크래시로 손상되는 것을 방지.
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for meta in insights.values():
                f.write(json.dumps(meta.to_dict(), ensure_ascii=False))
                f.write("\n")
        tmp_path.replace(self._path)


# ----------------------------------------------------------------------
# 병합 로직
# ----------------------------------------------------------------------

def merge_insights(
    existing: dict[str, InsightMeta],
    new_observations: list[InsightMeta],
    promotion_threshold: int,
) -> tuple[dict[str, InsightMeta], list[InsightMeta]]:
    """새 관측을 기존 sidecar 와 병합하고 갱신된 dict + 변경된 항목 리스트를 반환.

    병합 규칙 (DoD §B):
    1. 같은 topic(정규형 일치) 의 관측이 있으면 evidence_count += 1, last_seen 갱신,
       source_msg_ids 누적(중복 제거), text 는 새 관측 텍스트로 갱신
       (사람이 읽는 표현은 최신 관측이 더 정확하다고 가정).
    2. 신규 topic 이면 dict 에 새로 등록 (evidence_count=1).
    3. 모든 변경/신규 항목의 confidence 를 ``compute_confidence`` 로 재계산.
       기존 항목 중 이번 회차에 reinforcement 가 없는 것은 그대로 유지(C: decay는 별도 이슈).

    Args:
        existing: 현재 sidecar 에 적재된 인사이트 dict (topic 정규형 → InsightMeta).
        new_observations: 이번 사이클에 LLM 이 추출한 관측들 (topic 미정규형 OK).
        promotion_threshold: 승격 임계치 (config 로 노출).

    Returns:
        (merged_dict, changed_list)
        - merged_dict: 갱신 반영된 전체 인사이트 dict — InsightStore.save_all 로 즉시 쓸 수 있음.
        - changed_list: 이번 회차에 신규/갱신된 항목 — 다운스트림 로직(USER.md 갱신, 알림)이 사용.
    """
    merged = dict(existing)  # 얕은 복사 — 인사이트 객체는 in-place 수정.
    changed: list[InsightMeta] = []
    now = datetime.now()

    for obs in new_observations:
        key = normalize_topic(obs.topic)
        if not key:
            # 정규화 후 빈 문자열 — 무의미한 토픽으로 간주, 무시.
            continue

        if key in merged:
            # reinforcement — 같은 topic 의 누적 관측.
            cur = merged[key]
            cur.evidence_count += 1
            cur.last_seen = obs.last_seen or now
            cur.text = obs.text or cur.text  # 최신 관측이 비어있다면 기존 유지.
            # source_msg_ids 누적 (중복 제거, 안정 정렬)
            seen = set(cur.source_msg_ids)
            for mid in obs.source_msg_ids:
                if mid not in seen:
                    cur.source_msg_ids.append(mid)
                    seen.add(mid)
            cur.confidence = compute_confidence(
                cur.evidence_count, promotion_threshold
            )
            changed.append(cur)
        else:
            # 신규 topic — 단발 관측이므로 confidence 는 0.4 캡.
            new_meta = InsightMeta(
                topic=obs.topic.strip(),
                text=obs.text.strip(),
                evidence_count=1,
                confidence=compute_confidence(1, promotion_threshold),
                first_seen=obs.first_seen or now,
                last_seen=obs.last_seen or now,
                source_msg_ids=list(obs.source_msg_ids),
            )
            merged[key] = new_meta
            changed.append(new_meta)

    return merged, changed
