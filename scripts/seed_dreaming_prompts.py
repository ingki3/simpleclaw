"""BIZ-298 — 패키지 내장 default dreaming 프롬프트 YAML 시드 생성기.

``src/simpleclaw/memory/dreaming.py`` 에 있는 ``_DREAMING_PROMPT`` /
``_CLUSTER_SUMMARY_PROMPT`` 본문을 그대로 읽어 ``_prompts/*.yaml`` 6개로 emit 한다.

용도:
- BIZ-298 머지 시점에 1회 실행해 패키지 default YAML 을 만들기 위함.
- BIZ-299 가 파일별 프롬프트로 분할하면, 분할된 본문은 사람이 직접 YAML 을
  편집하거나 별도 generator 로 갱신한다 — 본 script 의 책임은 *legacy literal
  → YAML* 이전까지.

운영자 export 헬퍼는 BIZ-298 scope 가 아니므로 별도 CLI 화하지 않는다.
"""

from __future__ import annotations

import importlib
from pathlib import Path

_HEADER = """\
# BIZ-298 — 패키지 내장 default dreaming 프롬프트.
#
# 운영자가 ~/.simpleclaw/prompts/dreaming/{name}.yaml 를 만들면 그쪽이 우선한다.
# 본 파일은 source-of-truth 가 아니라 *fallback default* — BIZ-298 시점에는
# dreaming.py 의 단일 _DREAMING_PROMPT (또는 _CLUSTER_SUMMARY_PROMPT) 본문을
# 그대로 옮긴 형태로 시드되어 있다.
#
# BIZ-299 에서 파일별로 본문을 분할하면, memory/user/soul/agent/active_projects
# 각각의 system_prompt 와 user_prompt 를 그 목적에 맞게 좁힌다 (예: memory.yaml
# 은 MEMORY.md 갱신용 필드만 추출하도록).
"""


def _yaml_block_literal(text: str) -> str:
    """주어진 텍스트를 YAML literal block scalar (``|-``) 로 직렬화한다.

    ``|-`` (strip) 정책으로 trailing newline 을 제거 — 원본 Python triple-quoted
    상수는 trailing newline 없이 끝나므로 byte-identical 보존을 위해 strip 한다.
    빈 줄은 indent 없이 진짜 빈 줄로 emit 해 YAML 로더가 정확히 ``\\n`` 으로 해석하게 한다.
    """
    if not text:
        return "|-\n"
    body = text.rstrip("\n")
    lines = body.split("\n")
    rendered = []
    for line in lines:
        if line == "":
            rendered.append("")
        else:
            rendered.append("  " + line)
    return "|-\n" + "\n".join(rendered) + "\n"


def _emit_yaml(
    *,
    name: str,
    description: str,
    system_prompt: str,
    user_prompt: str,
    required_vars: list[str],
) -> str:
    required_block = "\n".join(f"  - {v}" for v in required_vars)
    return (
        _HEADER
        + f"\nversion: 1\n"
        + f"description: {description}\n"
        + f"system_prompt: {_yaml_block_literal(system_prompt)}"
        + f"user_prompt: {_yaml_block_literal(user_prompt)}"
        + f"required_vars:\n{required_block}\n"
    )


def main() -> None:
    dreaming = importlib.import_module("simpleclaw.memory.dreaming")
    legacy_user = dreaming._DREAMING_PROMPT
    cluster_user = dreaming._CLUSTER_SUMMARY_PROMPT

    legacy_system = "You are a conversation analyzer. Respond with valid JSON only."
    cluster_system = (
        "You are a memory clustering assistant. Respond with valid JSON only."
    )

    legacy_required = [
        "language_instruction",
        "date",
        "existing_soul_md",
        "existing_agent_md",
        "existing_user_md",
        "conversations",
    ]
    cluster_required = ["existing_label", "existing_summary", "new_messages"]

    out_dir = Path(__file__).resolve().parent.parent / "src" / "simpleclaw" / "memory" / "_prompts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # BIZ-298: 5개 non-cluster YAML 은 모두 legacy 본문을 그대로 담는다. BIZ-299
    # 가 파일별로 본문을 좁힐 때 각자 분리된다.
    for name, description in [
        ("memory", "MEMORY.md 갱신용 (BIZ-298 시드 — 현재는 legacy _DREAMING_PROMPT 본문)"),
        ("user", "USER.md insights 갱신용 (BIZ-298 시드 — 현재는 legacy _DREAMING_PROMPT 본문)"),
        ("soul", "SOUL.md 성격/말투 변경 추출 (BIZ-298 시드 — 현재는 legacy _DREAMING_PROMPT 본문)"),
        ("agent", "AGENT.md 행동 규칙 추출 (BIZ-298 시드 — 현재는 legacy _DREAMING_PROMPT 본문)"),
        (
            "active_projects",
            "USER.md active-projects 섹션 (BIZ-298 시드 — 현재는 legacy _DREAMING_PROMPT 본문)",
        ),
    ]:
        (out_dir / f"{name}.yaml").write_text(
            _emit_yaml(
                name=name,
                description=description,
                system_prompt=legacy_system,
                user_prompt=legacy_user,
                required_vars=legacy_required,
            ),
            encoding="utf-8",
        )

    (out_dir / "cluster.yaml").write_text(
        _emit_yaml(
            name="cluster",
            description="Phase 3 cluster summary (BIZ-298 시드 — _CLUSTER_SUMMARY_PROMPT 본문)",
            system_prompt=cluster_system,
            user_prompt=cluster_user,
            required_vars=cluster_required,
        ),
        encoding="utf-8",
    )

    print(f"wrote 6 YAML defaults to {out_dir}")


if __name__ == "__main__":
    main()
