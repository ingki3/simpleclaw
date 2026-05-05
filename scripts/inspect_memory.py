"""메모리 인덱스/RAG 분포 점검 CLI (BIZ-29).

`.agent/conversations.db`의 임베딩 커버리지·클러스터 분포·임베딩 차원 일관성을 한 번에 요약하고,
`.logs/execution_YYYYMMDD.log`에 적재된 ``rag_retrieve`` 액션 로그를 일자별로 집계한다.

사용 예::

    .venv/bin/python scripts/inspect_memory.py
    .venv/bin/python scripts/inspect_memory.py --json
    .venv/bin/python scripts/inspect_memory.py --db .agent/conversations.db --logs .logs --days 7

설계 결정:
- 별도 의존성을 추가하지 않기 위해 argparse만 사용한다.
- 텍스트 모드는 운영 점검 보고용으로 가독성을 우선하고,
  ``--json``은 후속 자동화(메트릭 적재, BIZ-29 후속 코멘트 생성 등)에 사용한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 스크립트를 직접 실행할 때도 simpleclaw 패키지를 임포트할 수 있도록 src/를 sys.path에 추가
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from simpleclaw.memory.conversation_store import ConversationStore  # noqa: E402
from simpleclaw.memory.stats import (  # noqa: E402
    MemoryDistributionStats,
    RagAnalysisResult,
    analyze_rag_logs,
    compute_distribution_stats,
)


def _format_distribution(stats: MemoryDistributionStats) -> str:
    """분포 통계를 사람이 읽기 좋은 텍스트로 포맷한다."""
    lines: list[str] = []
    lines.append("=== 임베딩/클러스터 분포 ===")
    lines.append(f"전체 메시지: {stats.total_messages}")
    lines.append(
        f"임베딩 부착: {stats.messages_with_embedding} "
        f"({stats.coverage_percent:.1f}% 커버리지)"
    )
    lines.append(
        f"클러스터 부착 메시지: {stats.clustered_messages} / "
        f"미부착(임베딩만): {stats.unclustered_with_embedding}"
    )
    lines.append(f"클러스터 수: {stats.cluster_count}")
    if stats.cluster_count > 0:
        lines.append(
            f"클러스터당 멤버: min={stats.members_min} "
            f"median={stats.members_median:g} "
            f"mean={stats.members_mean:.2f} "
            f"max={stats.members_max}"
        )
    if stats.embedding_dimensions:
        dim_summary = ", ".join(
            f"{dim}d×{cnt}" for dim, cnt in sorted(stats.embedding_dimensions.items())
        )
        warn = " ⚠ 차원 혼재" if stats.has_dimension_inconsistency else ""
        lines.append(f"임베딩 차원: {dim_summary}{warn}")
    if stats.cluster_distributions:
        lines.append("")
        lines.append("--- 클러스터 상세 (상위 10개) ---")
        # 실제 멤버 수 내림차순으로 상위 10개만 노출
        top = sorted(
            stats.cluster_distributions,
            key=lambda c: c.actual_member_count,
            reverse=True,
        )[:10]
        for c in top:
            label = c.label or "(unlabeled)"
            drift_note = f" drift={c.drift:+d}" if c.drift != 0 else ""
            lines.append(
                f"  [{c.cluster_id:>4}] {label[:40]:<40} "
                f"members={c.actual_member_count} "
                f"(stored={c.stored_member_count}){drift_note}"
            )
    return "\n".join(lines)


def _format_rag_analysis(result: RagAnalysisResult) -> str:
    """RAG 회상 로그 집계를 텍스트로 포맷한다."""
    lines: list[str] = []
    lines.append("")
    lines.append(f"=== RAG 회상 로그 (최근 {result.days}일) ===")
    if result.total_calls == 0:
        lines.append("기록된 rag_retrieve 액션이 없습니다.")
        lines.append(
            "(RAG가 활성화되어 있고 에이전트가 메시지를 처리했는지 확인하세요.)"
        )
        return "\n".join(lines)

    lines.append(
        f"총 호출: {result.total_calls} "
        f"(hit={result.total_hits}, hit_rate={result.hit_rate * 100:.1f}%)"
    )
    lines.append(
        f"회수 토큰 합계: {result.total_recalled_tokens} "
        f"(호출당 평균 {result.avg_recalled_tokens:.1f} 토큰)"
    )
    if result.daily:
        lines.append("")
        lines.append("--- 일자별 ---")
        lines.append(
            f"  {'date':<10} {'calls':>6} {'hits':>5} {'hit_rate':>9} "
            f"{'recall_msgs':>12} {'recall_tokens':>14}"
        )
        for d in result.daily:
            lines.append(
                f"  {d.date:<10} {d.total_calls:>6} {d.hits:>5} "
                f"{d.hit_rate * 100:>8.1f}% "
                f"{d.recalled_messages_sum:>12} "
                f"{d.recalled_tokens_sum:>14}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """스크립트 진입점. 종료 코드는 항상 0(점검 도구)."""
    parser = argparse.ArgumentParser(
        description="SimpleClaw 메모리 인덱스/RAG 분포 점검",
    )
    parser.add_argument(
        "--db",
        default="~/.simpleclaw/conversations.db",
        help="ConversationStore SQLite 파일 경로 (기본: ~/.simpleclaw/conversations.db)",
    )
    parser.add_argument(
        "--logs",
        default=".logs",
        help="StructuredLogger 디렉터리 (기본: .logs)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="RAG 로그를 거슬러 올라갈 일수 (기본: 7)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="결과를 JSON으로 출력 (자동화/대시보드용)",
    )
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="RAG 로그 분석을 생략하고 분포 통계만 출력",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        # store는 없으면 자동 생성하므로, 점검 의도에 맞게 명확히 알린다
        print(f"⚠ DB 파일이 존재하지 않습니다: {db_path}", file=sys.stderr)
        print("(처음 실행이거나 경로가 잘못되었을 수 있습니다.)", file=sys.stderr)

    store = ConversationStore(db_path)
    distribution = compute_distribution_stats(store)

    rag_result: RagAnalysisResult | None = None
    if not args.no_rag:
        rag_result = analyze_rag_logs(args.logs, days=args.days)

    if args.json:
        payload: dict = {"distribution": distribution.to_dict()}
        if rag_result is not None:
            payload["rag"] = rag_result.to_dict()
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(_format_distribution(distribution))
        if rag_result is not None:
            print(_format_rag_analysis(rag_result))

    return 0


if __name__ == "__main__":
    sys.exit(main())
