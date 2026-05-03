#!/usr/bin/env python
"""BIZ-80 — 기존 .agent/*.md 의 dreaming-managed 섹션을 1차 언어로 정규화.

이 스크립트는 1회성 도구다. BIZ-80 머지 직후 운영자가 한 번 실행해 USER.md /
MEMORY.md / AGENT.md / SOUL.md 의 ``managed:dreaming:*`` 섹션 안에 남아 있는
영어/혼용 bullet 을 한국어로 통일한다.

동작:

1. 대상 파일 각각의 dreaming-managed 섹션을 추출.
2. 각 bullet 줄을 1차 언어 휴리스틱으로 검사.
3. 비-1차 언어 bullet 을 발견하면 두 가지 모드 중 하나로 처리:
   - ``--strategy translate`` (기본 — LLM 라우터가 활성일 때): LLM 한 호출로 영어
     문장을 한국어로 번역해 자리 교체. 실패하거나 LLM 미설정이면 자동으로
     ``drop`` 으로 폴백.
   - ``--strategy drop``: 비-1차 언어 bullet 을 줄 단위로 제거.
   - ``--strategy report``: 어떤 변경도 가하지 않고 후보만 출력 (검수용).
4. 변경 전 원본은 같은 디렉토리의 ``memory-backup/`` 에 타임스탬프 .bak 으로 저장.
5. ``--dry-run`` 이면 변경 사항을 stdout 에 보여주고 파일은 그대로 둔다.

사용 예:

    # 실제 .agent/ 에 대해 한국어로 통일 (LLM 번역).
    python scripts/migrate_dreaming_language.py --agent-dir .agent

    # 어떤 bullet 이 후보인지만 보고 싶을 때.
    python scripts/migrate_dreaming_language.py --agent-dir .agent --strategy report

    # LLM 없이 단순 드롭 모드.
    python scripts/migrate_dreaming_language.py --agent-dir .agent --strategy drop

설계 결정:
- 비파괴: 백업이 항상 먼저 만들어지고, dry-run 옵션이 있다.
- LLM 호출은 옵션 — 환경에 LLM 키가 없는 운영자도 ``--strategy drop|report`` 로
  안전하게 쓸 수 있다.
- bullet 단위로만 동작 — 헤더 (``## 2026-04-28``), 빈 줄, 마커, 일반 텍스트는
  건드리지 않는다(bullet 외 영역은 사용자가 손댄 영역일 가능성이 높음).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from simpleclaw.memory.language_policy import (  # noqa: E402
    LANG_KOREAN,
    is_primary_language,
    split_bullets,
)
from simpleclaw.memory.protected_section import (  # noqa: E402
    ProtectedSectionError,
    get_section_body,
    has_managed_section,
    replace_section_body,
)

logger = logging.getLogger("migrate_dreaming_language")


# (file_kind, default_filename, sections-to-walk) — BIZ-80 표준 매핑.
# managed 섹션 이름은 dreaming.py 의 DEFAULT_*_SECTION 상수와 동일 — 여기서
# 다시 정의하지 않고 직접 나열해 스크립트 단독 실행 시에도 의존을 최소화.
_TARGETS: list[tuple[str, str, list[str]]] = [
    ("memory", "MEMORY.md", ["journal"]),
    ("user", "USER.md", ["insights", "active-projects", "archive"]),
    ("soul", "SOUL.md", ["dreaming-updates"]),
    ("agent", "AGENT.md", ["dreaming-updates"]),
]


def _backup(path: Path) -> Path | None:
    """파일을 ``memory-backup/<stem>.<ts>.bak`` 으로 복사."""
    if not path.is_file():
        return None
    backup_dir = path.parent / "memory-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = backup_dir / f"{path.stem}.{ts}.biz80.bak"
    shutil.copy2(path, out)
    return out


def _translate_bullets_with_llm(
    bodies: list[str], lang: str, llm_router, model: str | None
) -> list[str]:
    """LLM 한 호출로 bullet 본문 묶음을 1차 언어로 번역. 실패 시 빈 결과."""
    from simpleclaw.llm.models import LLMRequest

    numbered = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(bodies))
    prompt = (
        f"다음은 SimpleClaw 의 dreaming 출력 bullet 들입니다. 각 항목을 자연스러운 "
        f"{'한국어' if lang == LANG_KOREAN else lang} 로 번역하세요. 의미는 그대로,"
        f" 길이는 짧게 유지하세요. 고유명사(SimpleClaw, BIZ-XX 등)는 원어 유지.\n\n"
        f"입력 bullet (번호 매겨짐):\n{numbered}\n\n"
        f"출력 형식: 각 줄을 \"<번호>. <번역문>\" 형태로, 입력과 같은 개수만큼.\n"
    )
    request = LLMRequest(
        system_prompt="You are a faithful translator. Output only numbered lines.",
        user_message=prompt,
        backend_name=model or "",
    )
    try:
        response = asyncio.run(llm_router.send(request))
    except Exception:
        logger.exception("LLM translation failed")
        return []

    # 단순 파서: "<n>. <text>" 줄을 모은다.
    out: list[str] = [""] * len(bodies)
    for line in (response.text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "." not in line:
            continue
        head, _, tail = line.partition(".")
        try:
            idx = int(head.strip()) - 1
        except ValueError:
            continue
        if 0 <= idx < len(bodies):
            out[idx] = tail.strip()
    # 누락이 있으면 원본 유지(나중 단계에서 drop 폴백 처리).
    return out


def _process_section_body(
    body: str,
    lang: str,
    *,
    strategy: str,
    min_ratio: float,
    llm_router,
    model: str | None,
) -> tuple[str, list[str], list[str]]:
    """섹션 본문 한 덩어리를 처리해 새 본문, 변경 후보 목록, 변경 결과를 반환.

    Returns:
        (new_body, candidates, results) — ``candidates`` 는 비-1차 bullet 본문,
        ``results`` 는 strategy 별 처리 결과를 사람이 읽는 한 줄로 요약.
    """
    pairs = split_bullets(body)
    indices: list[int] = []  # 비-1차 bullet 의 split_bullets 인덱스
    candidates: list[str] = []
    for i, (prefix, content) in enumerate(pairs):
        if not prefix:
            continue
        if is_primary_language(content, lang, min_ratio=min_ratio):
            continue
        indices.append(i)
        candidates.append(content.strip())

    if not indices:
        return body, [], []

    results: list[str] = []
    if strategy == "report":
        # 변경 없이 후보만 보고.
        for c in candidates:
            results.append(f"  - candidate: {c}")
        return body, candidates, results

    if strategy == "drop":
        kept = [pair for i, pair in enumerate(pairs) if i not in set(indices)]
        new_body = "\n".join(prefix + content for prefix, content in kept)
        for c in candidates:
            results.append(f"  - dropped: {c}")
        return new_body, candidates, results

    # translate — LLM 라우터가 없으면 drop 으로 폴백.
    if llm_router is None:
        kept = [pair for i, pair in enumerate(pairs) if i not in set(indices)]
        new_body = "\n".join(prefix + content for prefix, content in kept)
        for c in candidates:
            results.append(f"  - dropped (no LLM router): {c}")
        return new_body, candidates, results

    translations = _translate_bullets_with_llm(
        candidates, lang, llm_router, model
    )
    if not translations or len(translations) != len(candidates):
        # 부분 실패 — 결정성을 위해 모두 drop 으로 폴백.
        kept = [pair for i, pair in enumerate(pairs) if i not in set(indices)]
        new_body = "\n".join(prefix + content for prefix, content in kept)
        for c in candidates:
            results.append(f"  - dropped (LLM partial failure): {c}")
        return new_body, candidates, results

    new_pairs = list(pairs)
    for idx, translation in zip(indices, translations):
        if not translation:
            # 빈 번역 — drop 처리.
            new_pairs[idx] = ("", "")  # 빈 줄로 둔다 → strip 후 사라짐
            results.append(f"  - dropped (empty translation): {pairs[idx][1].strip()}")
            continue
        prefix, _content = pairs[idx]
        # bullet 본문을 번역으로 교체. 1차 언어 비율 검사를 한 번 더 통과해야 보존.
        if not is_primary_language(translation, lang, min_ratio=min_ratio):
            new_pairs[idx] = ("", "")
            results.append(
                f"  - dropped (translation still non-primary): "
                f"{pairs[idx][1].strip()} -> {translation}"
            )
            continue
        new_pairs[idx] = (prefix, translation)
        results.append(f"  - translated: {pairs[idx][1].strip()} -> {translation}")

    # 빈 줄 정리 — strategy 가 drop 폴백한 자리(빈 prefix+빈 content) 는 줄 자체를 제거.
    rebuilt: list[str] = []
    for prefix, content in new_pairs:
        if not prefix and not content.strip():
            # 원래 본문에 있던 빈 줄/공백은 보존하되 우리가 "" 로 만든 자리는 줄을
            # 통째로 빼야 한다. split_bullets 는 빈 줄도 ("", "") 로 둔다 — 즉
            # 원본의 빈 줄과 우리가 만든 빈 줄을 구분할 수 없다. 안전 측 — 빈 줄은
            # 한 번만 보존(연속 공백 제거).
            if rebuilt and rebuilt[-1] == "":
                continue
            rebuilt.append("")
        else:
            rebuilt.append(prefix + content)
    new_body = "\n".join(rebuilt)
    return new_body, candidates, results


def _process_file(
    path: Path,
    sections: list[str],
    lang: str,
    *,
    strategy: str,
    min_ratio: float,
    dry_run: bool,
    llm_router,
    model: str | None,
) -> tuple[int, list[str]]:
    """파일 한 개를 처리. (변경 bullet 수, 결과 한 줄 요약 리스트) 반환."""
    if not path.is_file():
        return 0, [f"  ! skipped: {path} (does not exist)"]
    text = path.read_text(encoding="utf-8")
    total_changed = 0
    file_results: list[str] = []
    for section in sections:
        if not has_managed_section(text, section):
            continue
        try:
            body = get_section_body(text, section)
        except ProtectedSectionError as exc:
            file_results.append(f"  ! section '{section}' malformed: {exc}")
            continue
        new_body, candidates, results = _process_section_body(
            body,
            lang,
            strategy=strategy,
            min_ratio=min_ratio,
            llm_router=llm_router,
            model=model,
        )
        if not candidates:
            continue
        total_changed += len(candidates)
        file_results.append(f"  section '{section}': {len(candidates)} candidate(s)")
        file_results.extend(results)
        if strategy != "report" and not dry_run:
            text = replace_section_body(text, section, new_body)

    if total_changed > 0 and strategy != "report" and not dry_run:
        backup = _backup(path)
        if backup:
            file_results.append(f"  backup: {backup}")
        path.write_text(text, encoding="utf-8")
        file_results.append(f"  wrote: {path}")
    return total_changed, file_results


def _build_llm_router(config_path: Path):
    """LLM 라우터를 lazy 로 만든다. 실패하면 None 반환(스크립트가 drop 으로 폴백)."""
    try:
        from simpleclaw.config import load_llm_config
        from simpleclaw.llm.router import LLMRouter
    except Exception:
        logger.exception("Failed to import LLM modules; running without translation")
        return None
    try:
        cfg = load_llm_config(config_path)
        return LLMRouter(cfg)
    except Exception:
        logger.exception("Failed to construct LLM router; running without translation")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--agent-dir", default=".agent", help="대상 디렉토리 (기본 .agent)")
    parser.add_argument("--lang", default=LANG_KOREAN, help="1차 언어 코드 (기본 ko)")
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=0.3,
        help="1차 언어 비율 임계치 (0.0~1.0, 기본 0.3)",
    )
    parser.add_argument(
        "--strategy",
        choices=["translate", "drop", "report"],
        default="translate",
        help="비-1차 언어 bullet 처리 전략 (기본 translate)",
    )
    parser.add_argument("--dry-run", action="store_true", help="변경 사항만 출력")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="LLM 라우터 빌드용 config.yaml 경로 (translate 전략에서만 사용)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="번역에 사용할 LLM 모델 이름. 기본은 라우터의 default backend.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="DEBUG 로그 활성화"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    agent_dir = Path(args.agent_dir)
    if not agent_dir.is_dir():
        print(f"agent dir not found: {agent_dir}", file=sys.stderr)
        return 2

    llm_router = None
    if args.strategy == "translate":
        llm_router = _build_llm_router(Path(args.config))
        if llm_router is None:
            print(
                "WARN: LLM router unavailable; translate will fall back to drop",
                file=sys.stderr,
            )

    grand_total = 0
    print(
        f"BIZ-80 dreaming language migration\n"
        f"  dir={agent_dir}, lang={args.lang}, min_ratio={args.min_ratio}, "
        f"strategy={args.strategy}, dry_run={args.dry_run}"
    )
    for kind, fname, sections in _TARGETS:
        path = agent_dir / fname
        n, results = _process_file(
            path,
            sections,
            args.lang,
            strategy=args.strategy,
            min_ratio=args.min_ratio,
            dry_run=args.dry_run,
            llm_router=llm_router,
            model=args.model or None,
        )
        if n == 0 and not results:
            continue
        print(f"\n{fname} ({kind}):")
        for line in results:
            print(line)
        grand_total += n

    print(f"\nTotal candidate bullet(s): {grand_total}")
    if args.dry_run:
        print("(dry-run — no files were modified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
