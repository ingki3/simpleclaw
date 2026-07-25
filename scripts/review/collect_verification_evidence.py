#!/usr/bin/env python3
"""local/CI 검증 결과를 verification evidence JSON 으로 정규화하는 thin wrapper (BIZ-441).

pytest/ruff/gh checks/live smoke 같은 명령을 실행하고, 종료 코드와 출력 꼬리를
:mod:`simpleclaw.review.verification_ledger` 의 evidence 필드 형태로 정규화해
stdout 에 JSON 으로 낸다. ``--record`` 를 주면 config 의
``review.verification_ledger`` 경로에 바로 upsert 저장까지 한다.

수집/판정 로직은 전부 테스트된 package 코드(redaction/길이 제한 포함)에 있고,
이 스크립트는 "명령 실행 → 필드 채우기" 역할만 한다.

사용 예:
    # preset stage (명령 생략 시 stage 별 기본 명령 사용)
    .venv/bin/python scripts/review/collect_verification_evidence.py \
        --issue-id BIZ-441 --stage unit

    # 임의 명령 + ledger 저장
    .venv/bin/python scripts/review/collect_verification_evidence.py \
        --issue-id BIZ-441 --stage pr_ci \
        --command "gh pr checks 123" \
        --pr-number 123 --commit-sha abc123 --source github_actions \
        --record --config config.yaml
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from simpleclaw.config_sections.review import load_review_config
from simpleclaw.review.verification_ledger import (
    MAX_RAW_EXCERPT_CHARS,
    VerificationEvidenceLedger,
    normalize_stage,
    redact_excerpt,
)

# stage 별 기본 검증 명령 — CLAUDE.md 의 표준 로컬 검증 커맨드와 동일하게 유지.
_STAGE_DEFAULT_COMMANDS: dict[str, str] = {
    "unit": ".venv/bin/python -m pytest tests/unit/ -q",
    "lint": ".venv/bin/python -m ruff check src/",
}

_COMMAND_TIMEOUT_SECONDS = 1800


def _run_command(command: str) -> tuple[str, str, int | None]:
    """명령을 실행해 (status, output, returncode) 를 돌려준다.

    timeout/실행 불가도 예외 대신 failed evidence 로 정규화한다 — evidence 는
    "검증이 왜 안 됐는지" 까지 남겨야 한다.
    """
    try:
        proc = subprocess.run(
            command,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return "failed", f"command timed out after {_COMMAND_TIMEOUT_SECONDS}s", None
    except OSError as exc:
        return "failed", f"command failed to start: {exc}", None
    output = (proc.stdout or "") + (proc.stderr or "")
    status = "passed" if proc.returncode == 0 else "failed"
    return status, output, proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="검증 명령 실행 결과를 verification evidence JSON 으로 정규화한다."
    )
    parser.add_argument("--issue-id", required=True, help="대상 issue 식별자 (예: BIZ-441)")
    parser.add_argument(
        "--stage",
        required=True,
        help="검증 stage slug (unit/lint/pr_ci/release_ci/deploy/restart/health_smoke/...)",
    )
    parser.add_argument(
        "--command",
        help="실행할 검증 명령. 생략하면 stage preset(unit/lint) 명령을 사용한다.",
    )
    parser.add_argument("--pr-number", type=int, help="관련 GitHub PR 번호")
    parser.add_argument("--commit-sha", help="검증 대상 commit SHA")
    parser.add_argument("--source", default="local", help="evidence 출처 (기본 local)")
    parser.add_argument("--summary", help="결과 요약 override. 생략 시 종료 코드 기반 자동 생성.")
    parser.add_argument(
        "--record",
        action="store_true",
        help="config 의 review.verification_ledger 에 바로 upsert 저장한다.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="--record 시 ledger 경로를 읽을 config.yaml (기본 ./config.yaml)",
    )
    args = parser.parse_args(argv)

    stage = normalize_stage(args.stage)
    command = args.command or _STAGE_DEFAULT_COMMANDS.get(stage)
    if not command:
        parser.error(
            f"stage '{stage}' 는 preset 명령이 없습니다 — --command 를 지정하세요."
        )

    status, output, returncode = _run_command(command)
    evidence = {
        "issue_id": args.issue_id,
        "stage": stage,
        "status": status,
        "pr_number": args.pr_number,
        "commit_sha": args.commit_sha,
        "command": command,
        "summary": args.summary or f"{command} → exit {returncode} ({status})",
        "raw_excerpt": redact_excerpt(output, max_chars=MAX_RAW_EXCERPT_CHARS),
        "source": args.source,
    }

    if args.record:
        cfg = load_review_config(args.config)["verification_ledger"]
        ledger = VerificationEvidenceLedger(
            cfg["path"], retention_days=cfg["retention_days"]
        )
        record = ledger.record(**evidence)
        print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(evidence, ensure_ascii=False, indent=2))

    # 검증 실패는 exit code 로도 표면화 — CI step 이 이 wrapper 를 그대로
    # gate 로 쓸 수 있게 한다.
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
