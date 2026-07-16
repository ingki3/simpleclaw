"""Agent Study daily run 용 runtime collector adapter.

live recipe bridge(`agent-study-daily/scripts/study_daily.py`)에만 있던 수집
로직을 테스트 가능한 package collector 로 옮긴다(BIZ-434). bridge 는 이 adapter
들을 등록한 :class:`~simpleclaw.study.collectors.CollectorRegistry` 로
:class:`~simpleclaw.study.runner.StudyRunner` 를 호출하는 thin wrapper 가 된다.

설계 결정:
- **외부 프로세스/도구 호출은 주입식.** Google News RSS 는 skill 스크립트를
  subprocess 로 부르지만 ``run_json`` 콜백으로 추상화해 테스트가 네트워크 없이
  결정적으로 검증한다. web_search 는 Hermes 도구라 package 가 직접 호출할 수
  없으므로 검색 콜백 주입만 받는다.
- **0건은 조용한 빈 결과.** collector 가 0건을 반환하면 runner 가 후순위
  collector 의 성공 여부와 함께 "fallback 이 수집" 한계 문구로 표면화한다 —
  collector 계층에서 예외를 던져 run 을 죽이지 않는다.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from simpleclaw.security import filter_env
from simpleclaw.study.collectors import StudyFetchRequest, StudyFetchResult

logger = logging.getLogger(__name__)

# skill 스크립트 호출 결과(JSON dict)를 돌려주는 콜백 형태.
RunJson = Callable[[Sequence[str]], dict]

# 검색 콜백 형태: (query, max_results) → 결과 dict 시퀀스.
WebSearchFn = Callable[[str, int], Sequence[dict]]

# 기본 skill/인터프리터 위치 — 운영 배치 규약(~/.agents/skills, ~/.simpleclaw).
_DEFAULT_SCRIPT_PATH = (
    Path.home() / ".agents/skills/google-news-search-skill/scripts/google_news_search.py"
)
_DEFAULT_PYTHON_PATH = Path.home() / ".simpleclaw/.venv/bin/python"


def _subprocess_json(argv: Sequence[str]) -> dict:
    """argv 를 실행해 stdout JSON 을 파싱한다(실패 시 ``{"ok": False, ...}``)."""
    try:
        proc = subprocess.run(  # noqa: S603 — 고정된 skill 스크립트 호출
            list(argv),
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
            # BIZ-443: skill 스크립트는 외부 코드 — provider/admin secret 상속 차단
            env=filter_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip() or proc.stdout.strip()}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"invalid json: {exc}"}


@dataclass
class GoogleNewsRSSCollector:
    """google-news-search-skill 을 StudyCollector 로 감싸는 adapter.

    live bridge 가 하던 RSS 우선 수집을 package 로 옮긴 것. 결과 0건이면 빈
    목록을 반환하고, 실패 사유는 로그로만 남긴다(runner 의 fallback wording 이
    사용자-가시 한계 문구를 책임진다).
    """

    script_path: Path = _DEFAULT_SCRIPT_PATH
    python_path: Path = _DEFAULT_PYTHON_PATH
    locale: str = "ko-KR"
    name: str = "google-news-rss"
    run_json: RunJson = _subprocess_json

    def fetch(self, request: StudyFetchRequest) -> list[StudyFetchResult]:
        """skill 스크립트를 호출해 요청 쿼리의 뉴스 항목을 수집한다."""
        payload = self.run_json(
            [
                str(self.python_path),
                str(self.script_path),
                "--query",
                request.query,
                "--lookback-days",
                str(max(1, request.freshness_hours // 24)),
                "--locale",
                self.locale,
                "--sort",
                "published",
                "--max-results",
                str(request.max_sources),
                "--format",
                "json",
            ]
        )
        if not payload.get("ok"):
            logger.warning(
                "google-news-rss collector 실패 (query=%r): %s",
                request.query,
                payload.get("error"),
            )
            return []
        results: list[StudyFetchResult] = []
        items = payload.get("items")
        for item in (items if isinstance(items, list) else [])[: request.max_sources]:
            if not isinstance(item, dict):
                continue
            results.append(
                StudyFetchResult(
                    request=request,
                    title=str(item.get("title") or ""),
                    text=str(item.get("snippet") or item.get("summary") or ""),
                    url=str(item.get("url") or ""),
                    source="google-news-rss",
                    published_at=item.get("published_at"),
                    confidence=0.75,
                )
            )
        return results


@dataclass
class CallbackWebSearchCollector:
    """주입된 검색 콜백을 StudyCollector 로 감싸는 adapter.

    web_search 는 Hermes/오케스트레이터 계층의 도구라 package 코드가 직접 호출할
    수 없다. runtime bridge 가 자기 환경의 검색 함수를 콜백으로 주입하고, 테스트는
    fake 콜백을 쓴다. 검색 결과의 스니펫만 수집하므로 RSS 보다 신뢰도를 낮게 준다.
    """

    search: WebSearchFn
    name: str = "web_search"
    confidence: float = 0.6

    def fetch(self, request: StudyFetchRequest) -> list[StudyFetchResult]:
        """검색 콜백을 호출해 결과 dict 를 StudyFetchResult 로 정규화한다."""
        try:
            rows = self.search(request.query, request.max_sources)
        except Exception as exc:  # noqa: BLE001 — collector 실패가 run 을 죽이지 않게
            logger.warning(
                "web_search collector 실패 (query=%r): %s", request.query, exc
            )
            return []
        results: list[StudyFetchResult] = []
        for row in list(rows or [])[: request.max_sources]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "")
            text = str(row.get("snippet") or row.get("text") or "")
            if not (title or text):
                continue
            results.append(
                StudyFetchResult(
                    request=request,
                    title=title,
                    text=text,
                    url=str(row.get("url") or ""),
                    source="web_search",
                    published_at=row.get("published_at"),
                    confidence=self.confidence,
                )
            )
        return results


__all__ = [
    "CallbackWebSearchCollector",
    "GoogleNewsRSSCollector",
    "RunJson",
    "WebSearchFn",
]
