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
from simpleclaw.security import filter_env
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
_PROVENANCE_PREFIX = "__SIMPLECLAW_HELPER_PROVENANCE__="


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
    requested_limit: int
    item_count: int
    answer_chars: int
    argv_sha256: str
    helper_source: str
    helper_source_sha256: str


@dataclass(frozen=True)
class ProductionAssetGateResult:
    """검증된 typed payload와 공개 가능한 bounded 증적이다."""

    payload: dict[str, Any]
    evidence: ProductionAssetEvidence


@dataclass(frozen=True)
class _HelperSourceProvenance:
    """격리 subprocess가 실제 import한 canonical helper identity다."""

    path: Path
    sha256: str


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
                            {"ranking": 4, "teamName": "KIA", "winGameCount": 53},
                            {"ranking": 5, "teamName": "SSG", "winGameCount": 51},
                            {"ranking": 6, "teamName": "KT", "winGameCount": 49},
                            {"ranking": 7, "teamName": "NC", "winGameCount": 47},
                            {"ranking": 8, "teamName": "삼성", "winGameCount": 45},
                            {"ranking": 9, "teamName": "두산", "winGameCount": 43},
                            {"ranking": 10, "teamName": "키움", "winGameCount": 40},
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


def _expected_helper_source() -> tuple[Path, str]:
    """이 validator checkout이 소유한 canonical helper path/hash를 반환한다."""
    source_root = (REPO_ROOT / "src").resolve(strict=True)
    source = (source_root / "simpleclaw" / "skills" / "naver_sports.py").resolve(
        strict=True
    )
    try:
        source.relative_to(source_root)
    except ValueError:
        _fail("helper_source_path_invalid")
    return source, hashlib.sha256(source.read_bytes()).hexdigest()


def _assert_inprocess_helper_source() -> _HelperSourceProvenance:
    """deterministic gate도 ambient import가 아닌 exact checkout module만 수용한다."""
    expected_path, expected_sha256 = _expected_helper_source()
    try:
        actual_path = Path(naver_sports.__file__).resolve(strict=True)
    except (AttributeError, FileNotFoundError, TypeError):
        _fail("helper_source_provenance_missing")
    actual_sha256 = hashlib.sha256(actual_path.read_bytes()).hexdigest()
    if actual_path != expected_path or actual_sha256 != expected_sha256:
        _fail("helper_source_provenance_mismatch")
    return _HelperSourceProvenance(actual_path, actual_sha256)


def _isolated_subprocess_env() -> dict[str, str]:
    """credential과 ambient Python import 제어값을 제거한 자식 환경을 만든다."""
    env = filter_env()
    for key in tuple(env):
        if key.casefold().startswith("python"):
            env.pop(key)
    return env


def _isolated_helper_launcher(helper: Path, argv: tuple[str, ...]) -> str:
    """-I/-S 환경에서 exact source를 선로딩하고 installed wrapper를 실행한다."""
    source, _sha256 = _expected_helper_source()
    simpleclaw_dir = source.parents[1]
    skills_dir = source.parent
    return f"""
import hashlib
import importlib.util
import json
import pathlib
import runpy
import sys
import types

source = pathlib.Path({str(source)!r}).resolve(strict=True)
simpleclaw_package = types.ModuleType("simpleclaw")
simpleclaw_package.__path__ = [{str(simpleclaw_dir)!r}]
skills_package = types.ModuleType("simpleclaw.skills")
skills_package.__path__ = [{str(skills_dir)!r}]
sys.modules["simpleclaw"] = simpleclaw_package
sys.modules["simpleclaw.skills"] = skills_package
spec = importlib.util.spec_from_file_location(
    "simpleclaw.skills.naver_sports",
    source,
)
if spec is None or spec.loader is None:
    raise RuntimeError("canonical helper loader unavailable")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
provenance = {{
    "path": str(pathlib.Path(module.__file__).resolve(strict=True)),
    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
}}
sys.stderr.write({_PROVENANCE_PREFIX!r} + json.dumps(provenance) + "\\n")
sys.argv = [{str(helper)!r}, *{list(argv)!r}]
runpy.run_path({str(helper)!r}, run_name="__main__")
"""


def _decode_subprocess_provenance(stderr: str) -> _HelperSourceProvenance:
    """자식 provenance를 exact checkout path/hash와 대조한다."""
    encoded = next(
        (
            line.removeprefix(_PROVENANCE_PREFIX)
            for line in stderr.splitlines()
            if line.startswith(_PROVENANCE_PREFIX)
        ),
        None,
    )
    if encoded is None:
        _fail("helper_source_provenance_missing")
    try:
        payload = json.loads(encoded)
        actual_path = Path(payload["path"]).resolve(strict=True)
        actual_sha256 = str(payload["sha256"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        _fail("helper_source_provenance_invalid")
    expected_path, expected_sha256 = _expected_helper_source()
    if actual_path != expected_path or actual_sha256 != expected_sha256:
        _fail("helper_source_provenance_mismatch")
    return _HelperSourceProvenance(actual_path, actual_sha256)


def _execute_deterministic_cli(
    helper: Path,
    *,
    argv: tuple[str, ...],
    client_factory: Callable[[], object],
) -> tuple[dict[str, Any], tuple[int, int, int], _HelperSourceProvenance]:
    """설치 wrapper→argparse→canonical main을 exact argv로 실행한다."""
    provenance = _assert_inprocess_helper_source()
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        capture_shadow_side_effects() as side_effects,
        patch.object(naver_sports, "SportsClient", client_factory),
        patch.object(sys, "argv", [str(helper), *argv]),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        try:
            runpy.run_path(str(helper), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                _fail("helper_cli_failed")
    return (
        _decode_payload(stdout.getvalue()),
        (
            side_effects.telegram_send,
            side_effects.notifier,
            side_effects.conversation_write,
        ),
        provenance,
    )


def _execute_real_source_cli(
    helper: Path,
    *,
    argv: tuple[str, ...] = KBO_SEASON_AUTO_ARGV,
) -> tuple[dict[str, Any], tuple[int, int, int], _HelperSourceProvenance]:
    """운영자가 명시한 release preflight에서 실제 read-only source를 조회한다."""
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            _isolated_helper_launcher(helper, argv),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=REPO_ROOT,
        env=_isolated_subprocess_env(),
    )
    if completed.returncode != 0:
        _fail("helper_cli_failed")
    provenance = _decode_subprocess_provenance(completed.stderr)
    # exact source 선로드와 side_effect=false 계약을 함께 검증하므로 이 subprocess는
    # 조회 외 callback을 갖지 않는다. Telegram/Notifier/ConversationStore는 미구성이다.
    return _decode_payload(completed.stdout), (0, 0, 0), provenance


def _validate_payload(
    payload: dict[str, Any],
    write_counts: tuple[int, int, int],
    *,
    requested_limit: int,
) -> None:
    """success/no-effect/bounded season-auto 결과를 fail-closed로 검증한다."""
    if payload.get("ok") is not True:
        _fail("helper_not_ok")
    if payload.get("side_effect") is not False:
        _fail("effect_contract_failed")
    if sum(write_counts) != 0:
        _fail("external_write_detected")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != requested_limit:
        _fail("items_contract_failed")
    answer = payload.get("answer")
    if (
        not isinstance(answer, str)
        or not answer
        or len(answer) > naver_sports.MAX_PRESENTATION_CHARS
    ):
        _fail("answer_contract_failed")
    if answer.count("\n- ") != requested_limit:
        _fail("answer_limit_drift")
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("rank"), int)
        or item["rank"] > requested_limit
        for item in items
    ):
        _fail("rank_limit_drift")
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
    argv: tuple[str, ...] = KBO_SEASON_AUTO_ARGV,
    client_factory: Callable[[], object] = _DeterministicKboClient,
    expected_result_limit: int | None = None,
) -> ProductionAssetGateResult:
    """현재 installed helper CLI가 production gate를 충족하는지 검증한다."""

    def execute(selected_dir: Path) -> ProductionAssetGateResult:
        try:
            positions = [index for index, value in enumerate(argv) if value == "--limit"]
            if len(positions) != 1 or positions[0] + 1 >= len(argv):
                _fail("result_limit_missing")
            requested_limit = int(argv[positions[0] + 1])
        except ValueError:
            _fail("result_limit_invalid")
        if (
            expected_result_limit is not None
            and requested_limit != expected_result_limit
        ):
            _fail("result_limit_drift")
        helper = _assert_installed_asset(selected_dir)
        if source_mode == "deterministic_fixture":
            payload, write_counts, provenance = _execute_deterministic_cli(
                helper,
                argv=argv,
                client_factory=client_factory,
            )
        elif source_mode == "real_read_only":
            payload, write_counts, provenance = _execute_real_source_cli(
                helper,
                argv=argv,
            )
        else:
            _fail("source_mode_invalid")
        _validate_payload(
            payload,
            write_counts,
            requested_limit=requested_limit,
        )
        items = payload["items"]
        answer = payload["answer"]
        argv_sha256 = hashlib.sha256(
            "\0".join(argv).encode("utf-8")
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
                requested_limit=requested_limit,
                item_count=len(items),
                answer_chars=len(answer),
                argv_sha256=argv_sha256,
                helper_source="exact_checkout",
                helper_source_sha256=provenance.sha256,
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
    parser.add_argument(
        "--result-limit",
        type=int,
        default=10,
        help="Expected KBO standings item/answer count (1..20).",
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
    print(f"HELPER_SOURCE={evidence.helper_source}")
    print(f"HELPER_SOURCE_SHA256={evidence.helper_source_sha256}")
    print("SIDE_EFFECT=false")
    print(f"TELEGRAM_SEND_COUNT={evidence.telegram_send_count}")
    print(f"CRON_NOTIFIER_COUNT={evidence.notifier_count}")
    print(f"CONVERSATION_WRITE_COUNT={evidence.conversation_write_count}")
    print(f"EXTERNAL_WRITE_COUNT={evidence.external_write_count}")
    print(f"REQUESTED_LIMIT={evidence.requested_limit}")
    print(f"ITEM_COUNT={evidence.item_count}")
    print(f"ANSWER_CHARS={evidence.answer_chars}")
    print("PRODUCTION_ASSET_EXECUTION=PASS")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    helper_argv = list(KBO_SEASON_AUTO_ARGV)
    helper_argv[helper_argv.index("--limit") + 1] = str(args.result_limit)
    try:
        result = validate_production_asset(
            args.installed_skill_dir,
            source_mode=args.source_mode,
            argv=tuple(helper_argv),
            expected_result_limit=args.result_limit,
        )
    except ProductionAssetValidationError as exc:
        print(
            f"PRODUCTION_ASSET_EXECUTION=FAIL code={exc.code}",
            file=sys.stderr,
        )
        return 1
    # CLI boundary는 helper/installer의 예기치 않은 원문 예외도 redaction해야 한다.
    except Exception:  # noqa: BLE001
        print(
            "PRODUCTION_ASSET_EXECUTION=FAIL code=unexpected_failure",
            file=sys.stderr,
        )
        return 1
    _print_pass(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
