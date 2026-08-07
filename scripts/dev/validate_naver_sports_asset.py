#!/usr/bin/env python3
"""설치된 Naver Sports helper의 exact CLI를 read-only/no-send로 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import runpy
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from simpleclaw.graph_runtime.side_effect_monitor import capture_shadow_side_effects
from simpleclaw.production_assets import (
    install_runtime_asset,
    resolve_runtime_asset,
)
from simpleclaw.skills import naver_sports

ASSET_REF = "skill:naver-sports-skill"
SourceMode = Literal["deterministic_fixture", "real_read_only"]
KBO_SEASON_AUTO_ARGV = (
    "--mode",
    "standings",
    "--category",
    "kbo",
    "--date",
    "today",
    "--season",
    "auto",
    "--limit",
    "10",
    "--json",
)
DOCUMENTED_SENTINEL = (
    "--mode standings --category kbo --date today --season auto --limit 10 --json"
)


class ProductionAssetValidationError(RuntimeError):
    """원본 helper 출력 없이 stable code만 노출하는 gate 실패다."""

    def __init__(self, code: str) -> None:
        super().__init__(f"production asset validation failed: {code}")
        self.code = code


@dataclass(frozen=True)
class ProductionAssetEvidence:
    """contract stub과 구분되는 actual helper CLI 실행 증적이다."""

    source_mode: SourceMode
    helper_cli_executed: bool
    telegram_send_count: int
    notifier_count: int
    conversation_write_count: int
    external_write_count: int
    item_count: int
    answer_chars: int
    argv_sha256: str


@dataclass(frozen=True)
class ProductionAssetGateResult:
    """검증된 typed payload와 공개 가능한 bounded 증적이다."""

    payload: dict[str, Any]
    evidence: ProductionAssetEvidence


class _DeterministicKboClient:
    """네트워크 없이 parser 이후 production helper 경로를 실행하는 source fixture다."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self._responses = iter(
            (
                {
                    "code": 200,
                    "success": True,
                    "result": {
                        "seasons": [
                            {
                                "seasonCode": "2025",
                                "title": "2025 KBO",
                                "isEnable": "Y",
                            },
                            {
                                "seasonCode": "2026",
                                "title": "2026 KBO",
                                "startDate": "20260328",
                                "endDate": "20261011",
                                "isEnable": "Y",
                            },
                        ]
                    },
                },
                {
                    "code": 200,
                    "success": True,
                    "result": {
                        "seasonTeamStats": [
                            {"ranking": 1, "teamName": "LG", "winGameCount": 60},
                            {"ranking": 2, "teamName": "한화", "winGameCount": 58},
                            {"ranking": 3, "teamName": "롯데", "winGameCount": 55},
                        ]
                    },
                },
            )
        )

    def get_json(self, url: str) -> Any:
        """호출 URL을 기록하고 고정된 typed provider fixture를 반환한다."""
        self.urls.append(url)
        return next(self._responses)


def _fail(code: str) -> None:
    raise ProductionAssetValidationError(code)


def _assert_installed_asset(skill_dir: Path) -> Path:
    """설치 트리가 현재 manifest bytes와 exact match인지 확인한다."""
    try:
        root = skill_dir.resolve(strict=True)
    except FileNotFoundError:
        _fail("installed_asset_missing")
    resolved = resolve_runtime_asset(ASSET_REF)
    for declared, expected in zip(
        resolved.manifest.files,
        resolved.source_bytes,
        strict=True,
    ):
        path = root.joinpath(*declared.destination.parts)
        try:
            mode = path.lstat().st_mode
            actual = path.read_bytes()
        except (FileNotFoundError, OSError):
            _fail("installed_asset_drift")
        if not stat.S_ISREG(mode) or path.is_symlink() or actual != expected:
            _fail("installed_asset_drift")
        if declared.executable and not mode & stat.S_IXUSR:
            _fail("installed_asset_not_executable")
    skill_text = root.joinpath("SKILL.md").read_text(encoding="utf-8")
    if DOCUMENTED_SENTINEL not in skill_text:
        _fail("season_auto_sentinel_undocumented")
    helper = root / "scripts" / "naver_sports.py"
    try:
        helper.resolve(strict=True).relative_to(root)
    except (FileNotFoundError, ValueError):
        _fail("installed_helper_path_invalid")
    return helper


def _decode_payload(stdout: str) -> dict[str, Any]:
    """stdout 전체가 단일 JSON object일 때만 typed payload로 수용한다."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        _fail("helper_output_invalid")
    if not isinstance(payload, dict):
        _fail("helper_output_invalid")
    return payload


def _execute_deterministic_cli(
    helper: Path,
    *,
    client_factory: Callable[[], object],
) -> tuple[dict[str, Any], tuple[int, int, int]]:
    """설치 wrapper→argparse→canonical main을 exact argv로 실행한다."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        capture_shadow_side_effects() as side_effects,
        patch.object(naver_sports, "SportsClient", client_factory),
        patch.object(sys, "argv", [str(helper), *KBO_SEASON_AUTO_ARGV]),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        try:
            runpy.run_path(str(helper), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                _fail("helper_cli_failed")
    return _decode_payload(stdout.getvalue()), (
        side_effects.telegram_send,
        side_effects.notifier,
        side_effects.conversation_write,
    )


def _execute_real_source_cli(helper: Path) -> tuple[dict[str, Any], tuple[int, int, int]]:
    """운영자가 명시한 release preflight에서 실제 read-only source를 조회한다."""
    completed = subprocess.run(
        [sys.executable, str(helper), *KBO_SEASON_AUTO_ARGV],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        _fail("helper_cli_failed")
    # exact manifest와 side_effect=false 계약을 함께 검증하므로 이 subprocess는
    # 조회 외 callback을 갖지 않는다. Telegram/Notifier/ConversationStore는 미구성이다.
    return _decode_payload(completed.stdout), (0, 0, 0)


def _validate_payload(
    payload: dict[str, Any],
    write_counts: tuple[int, int, int],
) -> None:
    """success/no-effect/bounded season-auto 결과를 fail-closed로 검증한다."""
    if payload.get("ok") is not True:
        _fail("helper_not_ok")
    if payload.get("side_effect") is not False:
        _fail("effect_contract_failed")
    if sum(write_counts) != 0:
        _fail("external_write_detected")
    items = payload.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        _fail("items_contract_failed")
    answer = payload.get("answer")
    if (
        not isinstance(answer, str)
        or not answer
        or len(answer) > naver_sports.MAX_PRESENTATION_CHARS
    ):
        _fail("answer_contract_failed")
    season = payload.get("season")
    if not isinstance(season, dict) or str(season.get("code", "")).casefold() in {
        "",
        "auto",
    }:
        _fail("season_auto_not_normalized")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded) > naver_sports.MAX_OUTPUT_CHARS:
        _fail("output_not_bounded")


def validate_production_asset(
    skill_dir: Path | None = None,
    *,
    source_mode: SourceMode = "deterministic_fixture",
    client_factory: Callable[[], object] = _DeterministicKboClient,
) -> ProductionAssetGateResult:
    """현재 installed helper CLI가 production gate를 충족하는지 검증한다."""

    def execute(selected_dir: Path) -> ProductionAssetGateResult:
        helper = _assert_installed_asset(selected_dir)
        if source_mode == "deterministic_fixture":
            payload, write_counts = _execute_deterministic_cli(
                helper,
                client_factory=client_factory,
            )
        elif source_mode == "real_read_only":
            payload, write_counts = _execute_real_source_cli(helper)
        else:
            _fail("source_mode_invalid")
        _validate_payload(payload, write_counts)
        items = payload["items"]
        answer = payload["answer"]
        argv_sha256 = hashlib.sha256(
            "\0".join(KBO_SEASON_AUTO_ARGV).encode("utf-8")
        ).hexdigest()
        return ProductionAssetGateResult(
            payload=payload,
            evidence=ProductionAssetEvidence(
                source_mode=source_mode,
                helper_cli_executed=True,
                telegram_send_count=write_counts[0],
                notifier_count=write_counts[1],
                conversation_write_count=write_counts[2],
                external_write_count=sum(write_counts),
                item_count=len(items),
                answer_chars=len(answer),
                argv_sha256=argv_sha256,
            ),
        )

    if skill_dir is not None:
        return execute(skill_dir)
    with tempfile.TemporaryDirectory(prefix="simpleclaw-naver-sports-gate-") as temp:
        installed, _resolved = install_runtime_asset(
            ASSET_REF,
            destination_parent=Path(temp) / "skills",
        )
        return execute(installed)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-skill-dir", type=Path)
    parser.add_argument(
        "--source-mode",
        choices=("deterministic_fixture", "real_read_only"),
        default="deterministic_fixture",
    )
    return parser


def _print_pass(result: ProductionAssetGateResult) -> None:
    evidence = result.evidence
    print("VALIDATION_SCOPE=production_asset_execution")
    print("PLANNER_MODE=not_run")
    print("RECIPE_EXECUTOR_MODE=not_run")
    print("SKILL_EXECUTOR_MODE=installed_helper_cli")
    print(f"SOURCE_MODE={evidence.source_mode}")
    print(f"HELPER_CLI_EXECUTED={str(evidence.helper_cli_executed).lower()}")
    print("DOCUMENTED_SENTINEL=season_auto")
    print(f"HELPER_CLI_ARGV_SHA256={evidence.argv_sha256}")
    print("SIDE_EFFECT=false")
    print(f"TELEGRAM_SEND_COUNT={evidence.telegram_send_count}")
    print(f"CRON_NOTIFIER_COUNT={evidence.notifier_count}")
    print(f"CONVERSATION_WRITE_COUNT={evidence.conversation_write_count}")
    print(f"EXTERNAL_WRITE_COUNT={evidence.external_write_count}")
    print(f"ITEM_COUNT={evidence.item_count}")
    print(f"ANSWER_CHARS={evidence.answer_chars}")
    print("PRODUCTION_ASSET_EXECUTION=PASS")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = validate_production_asset(
            args.installed_skill_dir,
            source_mode=args.source_mode,
        )
    except ProductionAssetValidationError as exc:
        print(
            f"PRODUCTION_ASSET_EXECUTION=FAIL code={exc.code}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "PRODUCTION_ASSET_EXECUTION=FAIL code=unexpected_failure",
            file=sys.stderr,
        )
        return 1
    _print_pass(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
