#!/usr/bin/env python
"""USER.md 내 기존 \"Dreaming Insights\" 섹션을 BIZ-73 sidecar(insights.jsonl)로 마이그레이션.

이 스크립트는 단발/일회성 도구이며, BIZ-73 머지 직후 한 번 실행하여 다음을 수행한다:

1. ``USER.md`` 의 ``## Dreaming Insights (YYYY-MM-DD)`` 섹션을 모두 파싱.
2. 각 bullet 라인을 한 인사이트로 간주하고, 첫 5~12자를 topic 슬러그로 자동 도출.
3. 단발 관측(즉, 마이그레이션 시점의 첫 등록)으로 처리하여 confidence=0.4, evidence_count=1 로 기록.
4. 같은 USER.md 안에 여러 회차에 걸쳐 같은 topic 이 나타나면 evidence_count 를 가산해 누적 반영.
5. 결과를 ``insights.jsonl`` 에 atomic 쓰기 (이미 존재하면 dry-run 아닌 경우 덮어쓰기).

사용 예:

    python scripts/migrate_insights.py \\
        --user-file .agent/USER.md \\
        --out .agent/insights.jsonl \\
        --promotion-threshold 3

    # 결과만 확인하고 파일은 쓰지 않으려면
    python scripts/migrate_insights.py --user-file .agent/USER.md --dry-run

설계 결정:
- LLM 의존성 없이 결정론적으로 동작하도록 함(첫 실행 환경에 LLM 없을 수도 있음).
- 한 회차 내 같은 토픽 중복 bullet 은 1회로 카운트, 회차 간 같은 토픽 재등장은 evidence_count 가산.
- 파싱이 실패하거나 섹션이 없으면 빈 sidecar 생성(에러 없이 종료) — idempotent.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

# scripts/ 직접 실행 지원: 프로젝트 루트의 src/ 를 sys.path 에 추가.
_HERE = Path(__file__).resolve()
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from simpleclaw.memory.insights import (  # noqa: E402  — 위 sys.path 셋업 후 import
    InsightMeta,
    InsightStore,
    compute_confidence,
    normalize_topic,
)

logger = logging.getLogger("migrate_insights")


# 섹션 헤더 — "## Dreaming Insights (YYYY-MM-DD)" / "## Dreaming Insights"
_SECTION_HEADER_RE = re.compile(
    r"^##\s+Dreaming\s+Insights(?:\s*\((?P<date>[\d\-]+)\))?\s*$",
    re.MULTILINE,
)
# bullet 라인 — "- " 또는 "* " 로 시작.
_BULLET_RE = re.compile(r"^[\-\*]\s+(?P<text>.+?)\s*$", re.MULTILINE)


def parse_user_md(text: str) -> list[tuple[datetime | None, str]]:
    """USER.md 본문에서 (회차 날짜, bullet 텍스트) 튜플 리스트를 추출한다.

    Args:
        text: USER.md 전체 본문.

    Returns:
        ``[(date_or_none, bullet_text), ...]`` — 발견 순서.
        Dreaming Insights 섹션이 없으면 빈 리스트.
    """
    out: list[tuple[datetime | None, str]] = []
    headers = list(_SECTION_HEADER_RE.finditer(text))
    if not headers:
        return out

    for i, header in enumerate(headers):
        section_start = header.end()
        section_end = (
            headers[i + 1].start() if i + 1 < len(headers) else len(text)
        )
        section_body = text[section_start:section_end]

        # 다음 ## 헤더가 더 일찍 나오면 거기까지로 자른다 (다른 섹션 침범 방지).
        next_h2 = re.search(r"^##\s+", section_body, re.MULTILINE)
        if next_h2:
            section_body = section_body[: next_h2.start()]

        date_str = header.group("date")
        date_val: datetime | None = None
        if date_str:
            try:
                date_val = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                date_val = None

        for m in _BULLET_RE.finditer(section_body):
            bullet = m.group("text").strip()
            if bullet:
                out.append((date_val, bullet))

    return out


def derive_topic(bullet_text: str, max_chars: int = 12) -> str:
    """bullet 텍스트에서 topic 키를 결정론적으로 도출.

    LLM 없이 동작해야 하므로 단순 휴리스틱:
    - 앞쪽 max_chars 글자(공백 제외) 를 topic 후보로 사용.
    - 후보가 비어있으면 빈 문자열 반환(호출자가 skip 하도록).

    이 키는 후속 dreaming 사이클에서 같은 주제 재관측 시 재매칭의 시드 역할만 한다.
    LLM 이 같은 주제를 다른 표현으로 추출해도 매칭이 안 될 수 있다 — 그럴 경우 evidence 가
    독립적으로 누적되며, BIZ-78(decay) / BIZ-79(admin review) 사이클에서 운영자가 수기로 병합한다.
    """
    cleaned = re.sub(r"\s+", "", bullet_text)
    return cleaned[:max_chars] if cleaned else ""


def build_insights(
    bullets: list[tuple[datetime | None, str]],
    promotion_threshold: int,
) -> dict[str, InsightMeta]:
    """USER.md 에서 추출한 bullet 리스트를 InsightMeta dict 로 변환.

    같은 topic(정규형 일치) 의 bullet 이 여러 회차에 걸쳐 나타나면
    evidence_count 를 누적하고 first_seen / last_seen 을 회차 날짜로 기록한다.
    같은 회차 내 중복 bullet 은 1회로만 가산(같은 날짜는 1번 관측으로 본다).
    """
    out: dict[str, InsightMeta] = {}
    # 같은 topic + 같은 날짜는 한 번만 카운트하기 위한 가드.
    seen_topic_date: set[tuple[str, str]] = set()
    now = datetime.now()

    for date_val, bullet in bullets:
        topic_raw = derive_topic(bullet)
        key = normalize_topic(topic_raw)
        if not key:
            continue

        observed_at = date_val or now
        date_key = observed_at.strftime("%Y-%m-%d")
        guard = (key, date_key)

        if key in out:
            cur = out[key]
            if guard not in seen_topic_date:
                cur.evidence_count += 1
                seen_topic_date.add(guard)
            # last_seen 은 더 늦은 날짜로 갱신
            if observed_at > cur.last_seen:
                cur.last_seen = observed_at
                cur.text = bullet  # 가장 최근 표현으로 갱신
            # first_seen 은 더 이른 날짜로 갱신
            if observed_at < cur.first_seen:
                cur.first_seen = observed_at
            cur.confidence = compute_confidence(
                cur.evidence_count, promotion_threshold
            )
        else:
            seen_topic_date.add(guard)
            out[key] = InsightMeta(
                topic=topic_raw,
                text=bullet,
                evidence_count=1,
                confidence=compute_confidence(1, promotion_threshold),
                first_seen=observed_at,
                last_seen=observed_at,
                source_msg_ids=[],
            )

    return out


def main(argv: list[str] | None = None) -> int:
    """CLI 엔트리. 0 = 성공, 1 = 입력 오류."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-file",
        type=Path,
        default=Path("~/.simpleclaw/USER.md").expanduser(),
        help="입력 USER.md 경로 (기본: ~/.simpleclaw/USER.md)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="출력 sidecar 경로 (기본: USER.md 옆 insights.jsonl)",
    )
    parser.add_argument(
        "--promotion-threshold",
        type=int,
        default=3,
        help="confidence 승격 임계 관측 수 (기본 3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="파일에 쓰지 않고 결과만 표준출력으로 보여줌",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="상세 로그 출력"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not args.user_file.is_file():
        logger.error("USER.md not found: %s", args.user_file)
        return 1

    out_path = args.out or (args.user_file.parent / "insights.jsonl")

    text = args.user_file.read_text(encoding="utf-8")
    bullets = parse_user_md(text)
    logger.info("Parsed %d bullets from %s", len(bullets), args.user_file)

    insights = build_insights(bullets, args.promotion_threshold)
    logger.info(
        "Derived %d insight topics (threshold=%d)",
        len(insights),
        args.promotion_threshold,
    )

    if args.dry_run:
        for meta in insights.values():
            print(
                f"  [{meta.evidence_count}x conf={meta.confidence:.2f}] "
                f"{meta.topic} :: {meta.text}"
            )
        print(f"\n(dry-run) would write {len(insights)} insights to {out_path}")
        return 0

    InsightStore(out_path).save_all(insights)
    logger.info("Wrote %d insights to %s", len(insights), out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
