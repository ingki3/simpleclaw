"""시스템 프롬프트 어셈블러 — 토큰 버짓 관리.

여러 페르소나 파일(AGENT, USER, MEMORY)을 하나의 시스템 프롬프트로 조합한다.
토큰 예산을 초과하면 우선순위가 낮은 파일(MEMORY → USER)부터 잘라낸다.

설계 결정:
- 파일 순서는 AGENT → USER → MEMORY 고정 (AGENT가 가장 중요).
- tiktoken의 cl100k_base 인코딩으로 토큰 수를 계산한다.
- 절삭(truncation)은 토큰 단위로 수행하여 정확도를 보장한다.
"""

from __future__ import annotations

import logging
import re

import tiktoken

from simpleclaw.persona.models import FileType, PersonaFile, PromptAssembly

logger = logging.getLogger(__name__)

# 섹션 간 구분자 — 마크다운 수평선으로 시각적 분리
_SECTION_SEPARATOR = "\n\n---\n\n"
# 조합 우선순위 순서: AGENT(핵심 지시) → USER(사용자 설정) → MEMORY(기억)
_FILE_ORDER = [FileType.AGENT, FileType.USER, FileType.MEMORY]
_DREAMING_HEADING_RE = re.compile(
    r"^(#{1,6})\s+.*Dreaming (?:Updates|Insights|Journal|Clusters?)\b.*$",
    re.IGNORECASE,
)
_ANY_HEADING_RE = re.compile(r"^(#{1,6})\s+")
_MANAGED_DREAMING_START = "<!-- managed:dreaming:"
_MANAGED_DREAMING_END = "<!-- /managed:dreaming:"
_MANAGED_DREAMING_COMMENT_DOC_RE = re.compile(
    r"<!--\s*\n[\s\S]*?managed:dreaming:[\s\S]*?-->",
    re.IGNORECASE,
)
_NON_MANAGED_HTML_COMMENT_RE = re.compile(r"<!--(?!\s*/?managed:dreaming:)[\s\S]*?-->")
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_DREAMING_OMITTED_MARKER = ""
_DREAMING_DOC_ARTIFACT_PHRASES = (
    "드리밍 사이클이",
    "드리밍 사이클 설명",
    "마커 안쪽에서만 dreaming",
    "dreaming의 시간순 append",
)
_LEGACY_UNDERSTANDING_RULE_RE = re.compile(
    r"-\s*형님으로\s*부터\s*질문을\s*받았을\s*때,\s*"
    r"우선\s*이해한\s*내용을\s*먼저\s*말(?:한\s*후|하고,?)?\s*"
    r"(?:작업을\s*시작한다|한다)\."
)
_LEGACY_UNDERSTANDING_RULE_REPLACEMENT = (
    "- 복잡하거나 모호한 작업에서만 이해한 내용을 짧게 먼저 확인하고, "
    "간단한 대화에는 이해 요약을 붙이지 않는다."
)


def _strip_managed_dreaming_comment_docs(text: str) -> tuple[str, bool]:
    """managed:dreaming 마커를 설명하는 HTML 주석 블록도 제거한다.

    실제 managed section 마커는 한 줄 HTML 주석이므로 여기서는 여러 줄 주석만
    대상으로 삼는다. MEMORY.md/AGENT.md 상단 설명 주석이 system prompt에 들어가며
    marker 문자열 자체를 노출하는 것을 막기 위한 렌더링 전용 필터다.
    """
    lines = text.splitlines()
    kept: list[str] = []
    comment_buffer: list[str] | None = None
    removed = False

    for line in lines:
        stripped = line.strip()
        if comment_buffer is not None:
            comment_buffer.append(line)
            if stripped == "-->":
                block = "\n".join(comment_buffer)
                if "managed:dreaming:" in block:
                    if _DREAMING_OMITTED_MARKER and (
                        not kept or kept[-1] != _DREAMING_OMITTED_MARKER
                    ):
                        kept.append(_DREAMING_OMITTED_MARKER)
                    removed = True
                else:
                    kept.extend(comment_buffer)
                comment_buffer = None
            continue

        if stripped == "<!--":
            comment_buffer = [line]
            continue

        kept.append(line)

    if comment_buffer is not None:
        kept.extend(comment_buffer)
    return "\n".join(kept), removed


def _strip_html_comments(text: str) -> str:
    """원본 persona 파일의 운영 설명용 HTML comment를 prompt 렌더링에서 제거한다."""
    return _HTML_COMMENT_RE.sub("", text)


def _strip_dreaming_doc_artifact_lines(text: str) -> str:
    """파서 단계에서 HTML 주석 껍질이 사라진 dreaming 설명 찌꺼기를 제거한다."""
    return "\n".join(
        line
        for line in text.splitlines()
        if not any(phrase in line for phrase in _DREAMING_DOC_ARTIFACT_PHRASES)
    )


def _strip_html_comments(text: str) -> str:
    """렌더링된 시스템 프롬프트에서 일반 HTML comment를 제거한다.

    Persona 파일의 HTML comment는 운영 메모·관리 마커 표현용이며 모델에게 노출될
    지시가 아니다. managed dreaming 블록 제거가 먼저 실행된 뒤 남은 comment만
    걷어내므로, 마커 내부 본문이 실수로 되살아나지 않는다.
    """
    return _HTML_COMMENT_RE.sub("", text)


def _count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    """tiktoken을 사용하여 텍스트의 토큰 수를 계산한다.

    Args:
        text: 토큰 수를 셀 대상 텍스트.
        encoding_name: tiktoken 인코딩 이름 (기본값: cl100k_base).

    Returns:
        토큰 개수.
    """
    enc = tiktoken.get_encoding(encoding_name)
    return len(enc.encode(text))


def _strip_managed_dreaming_blocks(text: str) -> str:
    """Dreaming managed 블록을 렌더링 시점에 제거한다.

    Dreaming은 장기기억 sidecar/RAG로 회수되어야 하므로, 페르소나 파일에 누적된
    ``managed:dreaming:*`` 원문 블록은 시스템 프롬프트에 그대로 싣지 않는다.
    특히 cluster/journal 원문은 과거 응답 형식 지침까지 포함할 수 있어 prompt를
    오염시키므로, 수동 메모와 일반 섹션은 유지하고 제거 사실만 짧은 marker로 남긴다.
    """
    text = _strip_dreaming_doc_artifact_lines(text)
    text, comment_docs_removed = _strip_managed_dreaming_comment_docs(text)
    text = _NON_MANAGED_HTML_COMMENT_RE.sub("", text)
    if comment_docs_removed:
        text = text.strip()

    if (
        "Dreaming Updates" not in text
        and "Dreaming Insights" not in text
        and "Dreaming Journal" not in text
        and "Dreaming Cluster" not in text
        and _MANAGED_DREAMING_START not in text
    ):
        return text.strip() if comment_docs_removed else text

    kept: list[str] = []
    omitted = False
    skipping = False
    skip_level = 0
    marker_added = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_MANAGED_DREAMING_START):
            omitted = True
            skipping = True
            skip_level = 0
            if _DREAMING_OMITTED_MARKER and not marker_added:
                kept.append(_DREAMING_OMITTED_MARKER)
                marker_added = True
            continue
        if skipping and stripped.startswith(_MANAGED_DREAMING_END):
            skipping = False
            skip_level = 0
            continue

        dreaming_match = _DREAMING_HEADING_RE.match(line)
        if dreaming_match:
            omitted = True
            skipping = True
            skip_level = len(dreaming_match.group(1))
            if _DREAMING_OMITTED_MARKER and not marker_added:
                kept.append(_DREAMING_OMITTED_MARKER)
                marker_added = True
            continue

        if skipping:
            heading_match = _ANY_HEADING_RE.match(line)
            if skip_level and heading_match and len(heading_match.group(1)) <= skip_level:
                skipping = False
            else:
                continue

        kept.append(line)

    if not omitted:
        return text
    return "\n".join(kept).strip()


def _normalize_persona_policy_conflicts(text: str) -> str:
    """구 런타임 AGENT.md의 응답 형식 충돌 지시를 최신 guard와 맞춘다.

    런타임 페르소나 파일은 hot-reload 되는 사용자 소유 파일이라 PR 배포만으로 즉시
    내용이 바뀌지 않을 수 있다. 따라서 렌더링 시점에 과거 "항상 이해 요약 먼저"
    문구만 좁게 치환해, 시스템 guard의 "복잡한 작업에서만" 규칙과 충돌하지 않게 한다.
    """
    return _LEGACY_UNDERSTANDING_RULE_RE.sub(
        _LEGACY_UNDERSTANDING_RULE_REPLACEMENT,
        text,
    )


def _render_persona_file(persona_file: PersonaFile) -> str:
    """PersonaFile의 섹션들을 하나의 텍스트 블록으로 렌더링한다.

    각 섹션의 제목은 마크다운 헤딩 수준에 맞춰 '#'을 붙이고,
    본문과 함께 빈 줄로 구분하여 이어 붙인다.

    Args:
        persona_file: 렌더링할 PersonaFile 객체.

    Returns:
        조합된 마크다운 텍스트. 섹션이 없으면 빈 문자열.
    """
    if not persona_file.sections:
        return ""

    parts = []
    for section in persona_file.sections:
        if section.title:
            prefix = "#" * section.level
            parts.append(f"{prefix} {section.title}")
        if section.content:
            parts.append(section.content)

    text = "\n\n".join(parts)
    text = _normalize_persona_policy_conflicts(text)
    return _strip_html_comments(_strip_managed_dreaming_blocks(text)).strip()


def assemble_prompt(
    persona_files: list[PersonaFile],
    token_budget: int,
) -> PromptAssembly:
    """페르소나 파일들을 토큰 예산 이내의 시스템 프롬프트로 조합한다.

    파일 순서는 AGENT → USER → MEMORY. 조합된 텍스트가
    토큰 예산을 초과하면 MEMORY 내용부터 뒤에서 잘라내고,
    그래도 초과하면 USER 내용을 잘라낸다.

    Args:
        persona_files: 조합할 PersonaFile 목록.
        token_budget: 허용 최대 토큰 수.

    Returns:
        조합 결과를 담은 PromptAssembly 객체.
    """
    if not persona_files:
        return PromptAssembly(token_budget=token_budget)

    # 정규 순서(AGENT → USER → MEMORY)에 따라 파일 정렬
    files_by_type: dict[FileType, PersonaFile] = {}
    for pf in persona_files:
        files_by_type[pf.file_type] = pf

    ordered_files = [files_by_type[ft] for ft in _FILE_ORDER if ft in files_by_type]

    # 각 파일을 텍스트로 렌더링
    rendered: list[tuple[PersonaFile, str]] = []
    for pf in ordered_files:
        text = _render_persona_file(pf)
        if text:
            rendered.append((pf, text))

    if not rendered:
        return PromptAssembly(parts=ordered_files, token_budget=token_budget)

    # 전체 텍스트 조합
    full_text = _SECTION_SEPARATOR.join(text for _, text in rendered)
    total_tokens = _count_tokens(full_text)

    if total_tokens <= token_budget:
        return PromptAssembly(
            parts=ordered_files,
            assembled_text=full_text,
            token_count=total_tokens,
            token_budget=token_budget,
            was_truncated=False,
        )

    # 절삭 필요 — 뒤쪽 파일(MEMORY → USER)부터 내용 제거
    logger.info(
        "Token budget exceeded (%d > %d), truncating.", total_tokens, token_budget
    )
    truncated_texts = [text for _, text in rendered]
    was_truncated = False

    # 마지막 파일부터 역순으로 절삭 시도
    for i in range(len(truncated_texts) - 1, 0, -1):
        assembled = _SECTION_SEPARATOR.join(truncated_texts)
        current_tokens = _count_tokens(assembled)

        if current_tokens <= token_budget:
            break

        # 해당 파일의 텍스트를 점진적으로 축소
        truncated_texts[i] = _truncate_text_to_fit(
            truncated_texts[i],
            current_tokens - token_budget,
        )
        was_truncated = True

    assembled = _SECTION_SEPARATOR.join(t for t in truncated_texts if t)
    final_tokens = _count_tokens(assembled)

    # 선택적 파일 제거 후에도 초과 시, 강제 절삭 (토큰 단위로 자름)
    if final_tokens > token_budget:
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(assembled)[:token_budget]
        assembled = enc.decode(tokens)
        final_tokens = token_budget
        was_truncated = True

    return PromptAssembly(
        parts=ordered_files,
        assembled_text=assembled,
        token_count=final_tokens,
        token_budget=token_budget,
        was_truncated=was_truncated,
    )


def _truncate_text_to_fit(text: str, tokens_to_remove: int) -> str:
    """텍스트 끝에서 약 tokens_to_remove개의 토큰을 제거한다.

    Args:
        text: 절삭 대상 텍스트.
        tokens_to_remove: 제거할 토큰 수.

    Returns:
        절삭된 텍스트. 제거량이 전체 토큰 수 이상이면 빈 문자열.
    """
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if tokens_to_remove >= len(tokens):
        return ""
    kept = tokens[: len(tokens) - tokens_to_remove]
    return enc.decode(kept)
