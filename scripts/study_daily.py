#!/usr/bin/env python3
"""Agent Study daily run thin wrapper — code-backed StudyRunner 호출 전용.

live recipe bridge(`~/.simpleclaw-agent/default/recipes/agent-study-daily/scripts/
study_daily.py`)가 자체 수집/갱신 로직을 들고 repo 코드와 diverge 하던 문제
(BIZ-434)를 해소하기 위한 tracked wrapper 다. 이 스크립트는 config/wiki_dir 을
읽고 package 의 :class:`~simpleclaw.study.runner.StudyRunner` 를 호출하는 역할만
한다 — 수집/evolution/노트 렌더링 로직은 전부 테스트된 package 코드에 있다.

Rollout (운영자 승인 후 별도 수행 — 이 PR 은 live 파일을 건드리지 않는다):
1. PR merge 후, live recipe 의 study_daily.py 를 이 wrapper 내용으로 교체하거나
   이 스크립트를 직접 호출하도록 recipe 를 수정한다.
2. 먼저 dry-run 으로 검증한다(live wiki 를 temp 로 복사해 실행, 원본 무변경):
   HOME=/Users/simplist ~/.simpleclaw/.venv/bin/python scripts/study_daily.py \
       --config ~/.simpleclaw-agent/default/config.yaml --dry-run --max-topics 4
3. 출력의 topic evolution 요약과 daily note 내용을 확인한 뒤 non-dry-run 전환.
4. 관심 신호 provider 는 아직 static(빈 신호) 이다 — 실제 대화/메모리 store 콜백
   연결은 안정된 store boundary 확정 후 후속 이슈에서 wiring 한다. 그 전까지도
   pinned/active topic 의 search_queries 분리와 selection 투명성은 동작한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from simpleclaw.config_sections.study import load_study_config
from simpleclaw.study.collector_adapters import GoogleNewsRSSCollector
from simpleclaw.study.collectors import CollectorRegistry
from simpleclaw.study.runner import StudyRunner
from simpleclaw.study.signal_provider import StaticStudySignalProvider
from simpleclaw.study.source_planner import (
    DEFAULT_SOURCE_POLICY,
    CategorySourcePolicy,
    SourcePolicy,
)
from simpleclaw.study.topic_registry import TopicEvolutionPolicy


def _rss_first_policy(base: SourcePolicy = DEFAULT_SOURCE_POLICY) -> SourcePolicy:
    """live bridge 와 동일하게 Google News RSS 를 최우선 collector 로 두는 정책.

    RSS 가 0건이면 후순위 collector 가 fallback 으로 수집하고, runner 가
    "returned zero items; fallback collected N sources" 한계 문구로 표면화한다.
    """

    def _prepend(policy: CategorySourcePolicy) -> CategorySourcePolicy:
        return CategorySourcePolicy(
            collectors=("google-news-rss", *policy.collectors),
            preferred_domains=policy.preferred_domains,
            require_timeline_validation=policy.require_timeline_validation,
        )

    return SourcePolicy(
        categories={name: _prepend(p) for name, p in base.categories.items()},
        fallback=_prepend(base.fallback),
    )


def build_runner(
    wiki_dir: Path, config: dict, *, max_topics: int | None = None
) -> StudyRunner:
    """config 로부터 운영 collector 구성의 StudyRunner 를 만든다."""
    collectors = CollectorRegistry()
    collectors.register(GoogleNewsRSSCollector())
    # web_search 등 오케스트레이터 도구 기반 collector 는 해당 런타임이
    # CallbackWebSearchCollector 에 검색 콜백을 주입해 추가 등록한다.

    daily = config.get("daily", {}) if isinstance(config.get("daily"), dict) else {}
    return StudyRunner(
        wiki_dir=wiki_dir,
        collectors=collectors,
        policy=_rss_first_policy(),
        # 실제 대화/메모리 신호 provider 는 후속 wiring — 그 전까지 빈 신호로도
        # evolution pass(감쇠/selection 투명성)는 동작한다.
        signal_provider=StaticStudySignalProvider(()),
        evolution_policy=TopicEvolutionPolicy.from_config(config),
        max_topics_per_run=(
            max_topics
            if max_topics is not None
            else int(daily.get("max_topics_per_run", 8))
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """daily study run 을 1회 실행하고 요약을 JSON 으로 출력한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml", help="config.yaml 경로")
    parser.add_argument(
        "--wiki-dir", default=None, help="wiki 루트 override (기본: config study.wiki_dir)"
    )
    parser.add_argument(
        "--max-topics", type=int, default=None, help="1회 실행당 최대 topic 수 override"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="wiki 를 temp 로 복사해 실행 — 원본 파일을 변경하지 않는다",
    )
    args = parser.parse_args(argv)

    config = load_study_config(Path(args.config))
    wiki_dir = Path(args.wiki_dir).expanduser() if args.wiki_dir else config["wiki_dir"]

    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="study-daily-dryrun-") as tmp:
            tmp_wiki = Path(tmp) / "wiki"
            if Path(wiki_dir).is_dir():
                shutil.copytree(wiki_dir, tmp_wiki)
            else:
                tmp_wiki.mkdir(parents=True)
            runner = build_runner(tmp_wiki, config, max_topics=args.max_topics)
            summary = runner.run()
            print(json.dumps(asdict(summary), ensure_ascii=False, indent=2, default=str))
            print(f"[dry-run] 원본 wiki({wiki_dir}) 는 변경되지 않았습니다.", file=sys.stderr)
        return 0

    runner = build_runner(Path(wiki_dir), config, max_topics=args.max_topics)
    summary = runner.run()
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
