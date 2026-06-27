"""Evidence retrieval adapters for complex fact workflows.

Phase 1 keeps extraction conservative: search results become candidate
EvidenceItem objects with UNKNOWN/PARTIAL/FINAL coverage inferred from explicit
markers. Later DSPy-style extraction can replace this without changing the
workflow interface.
"""

from __future__ import annotations

import re

from simpleclaw.agent.builtin_tools import _fetch_search_result_body, handle_web_search
from simpleclaw.agent.fact_types import EvidenceCoverage, EvidenceItem

_URL_RE = re.compile(r"https?://[^\s)\],]+")
_TITLE_RE = re.compile(r"^\s*\d+\.\s*(?P<title>.+?)\s*$")
_FINAL_MARKERS = ("final", "confirmed", "최종", "확정", "결과", "updated today", "현재")
_CURRENT_PENDING_MARKERS = ("pending", "남은", "remaining", "진행 중", "현재까지")
_PRE_EVENT_MARKERS = ("예정", "preview", "전망", "scheduled", "will play")
_PARTIAL_MARKERS = ("partial", "일부", "잠정")
_OFFICIAL_MARKERS = ("official", "fifa", "kbo", "naver", "공식", "go.kr", "정부")
_MAJOR_NEWS_MARKERS = ("news", "연합뉴스", "reuters", "apnews", "yonhap", "bbc", "cnn")
_BLOG_MARKERS = ("blog", "tistory", "blogspot", "medium.com")


def _infer_coverage(text: str) -> EvidenceCoverage:
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in _PRE_EVENT_MARKERS):
        return EvidenceCoverage.PRE_EVENT
    if any(marker.lower() in lowered for marker in _CURRENT_PENDING_MARKERS):
        return EvidenceCoverage.CURRENT_PENDING
    if any(marker.lower() in lowered for marker in _PARTIAL_MARKERS):
        return EvidenceCoverage.PARTIAL
    if any(marker.lower() in lowered for marker in _FINAL_MARKERS):
        return EvidenceCoverage.FINAL
    return EvidenceCoverage.UNKNOWN


def _infer_source_type(text: str, url: str) -> str:
    combined = f"{text}\n{url}".lower()
    if any(marker.lower() in combined for marker in _OFFICIAL_MARKERS):
        return "official"
    if any(marker.lower() in combined for marker in _MAJOR_NEWS_MARKERS):
        return "major_news"
    if any(marker.lower() in combined for marker in _BLOG_MARKERS):
        return "blog"
    return "unknown"


class EvidenceRetriever:
    """Small adapter around SimpleClaw's existing web_search handler."""

    def __init__(self, *, max_sources_per_slot: int = 3) -> None:
        self.max_sources_per_slot = max_sources_per_slot

    async def search_for_slot(self, slot_name: str, query: str) -> list[EvidenceItem]:
        raw = await handle_web_search(
            {"query": query, "limit": self.max_sources_per_slot},
            body_fetcher=_fetch_search_result_body,
        )
        return self._items_from_search_output(slot_name, raw)

    def _items_from_search_output(self, slot_name: str, raw: str) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        current_title = ""
        lines = raw.splitlines()
        for index, line in enumerate(lines):
            title_match = _TITLE_RE.match(line)
            if title_match:
                current_title = title_match.group("title")[:160]
            match = _URL_RE.search(line)
            if not match:
                continue
            url = match.group(0).rstrip(".,]")
            title = current_title or line[:160]
            block = "\n".join(lines[max(0, index - 1): min(len(lines), index + 5)])
            coverage = _infer_coverage(block)
            items.append(EvidenceItem(
                source_url=url,
                source_title=title,
                source_type=_infer_source_type(f"{title}\n{block}", url),
                claim=f"{slot_name}: {title}",
                coverage=coverage,
                confidence="medium" if coverage != EvidenceCoverage.UNKNOWN else "low",
                raw_excerpt=raw[:1200],
            ))
            if len(items) >= self.max_sources_per_slot:
                break
        if not items and raw.strip() and not raw.lower().startswith("error:"):
            items.append(EvidenceItem(
                source_url="",
                source_title="web_search",
                source_type="unknown",
                claim=f"{slot_name}: {raw[:500]}",
                coverage=EvidenceCoverage.UNKNOWN,
                confidence="low",
                raw_excerpt=raw[:1200],
            ))
        return items
