#!/usr/bin/env python3
"""SimpleClaw LaunchAgent 를 drain → restart → health smoke 순서로 재시작한다 (BIZ-442).

절차:
1. drain 요청 — bot 프로세스와 공유하는 state 파일(``daemon.drain.state_file``)에
   drain 을 기록해 새 intake(텔레그램/웹훅/cron)를 거절시킨다.
2. quiesce 폴링 — ``/admin/v1/health`` 의 ``drain.active_operations`` 가 0 이
   될 때까지 기다린다. admin health 를 못 쓰는 환경이면 고정 grace 대기로
   대체한다.
3. timeout 결정 — drain 창 안에 active operation 이 안 빠지면
   ``--on-timeout`` 정책(proceed/abort)에 따른다. 기본 proceed — drain 창이
   끝나면 어차피 intake 가 자동 복귀하므로 재시작을 미루는 이득이 없다.
4. restart — ``launchctl kickstart -k gui/<uid>/<label>``.
5. health smoke — admin health 가 다시 200/ok 로 돌아오는지 확인한다.
6. evidence 기록 — ``--issue-id`` 가 주어지면 restart/health_smoke stage 를
   VerificationEvidenceLedger 에 upsert 한다.
7. drain 해제 — 성공/실패와 무관하게 해제한다. 실패 시에도 drain 을 남겨
   두면 운영자가 수동 복구하는 동안 서비스 전체가 침묵하므로, 실패는 exit
   code 와 evidence 로 표면화하고 intake 는 되살린다.

live 재시작은 운영자 승인/명시 요청 하에서만 실행할 것 (CLAUDE.md 정책).

사용 예:
    .venv/bin/python scripts/deploy/drain_restart_simpleclaw.py \
        --issue-id BIZ-442 --reason "deploy BIZ-442"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Callable

from simpleclaw.config_sections.channels import load_admin_api_config
from simpleclaw.config_sections.daemon import load_daemon_config
from simpleclaw.config_sections.review import load_review_config
from simpleclaw.daemon.drain import DrainController
from simpleclaw.review.verification_ledger import VerificationEvidenceLedger

DEFAULT_LABEL = "com.simpleclaw.bot"

_HTTP_TIMEOUT_SECONDS = 5.0


def _http_get_json(
    url: str, token: str | None, timeout: float = _HTTP_TIMEOUT_SECONDS
) -> tuple[int | None, dict | None]:
    """GET url → (status_code, json dict). 네트워크/파싱 실패는 (None, None).

    admin health 폴링 한 번의 실패가 스크립트를 죽이면 안 되므로 예외를
    상태값으로 정규화한다 — 호출자가 fallback(grace 대기)을 결정한다.
    """
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return status, None
    return status, parsed if isinstance(parsed, dict) else None


def _resolve_admin_endpoint(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """(health_url, token) 을 인자 → config 순으로 해소한다.

    admin API 가 비활성이거나 토큰이 없으면 (None, None) — 호출자는 health
    폴링 대신 고정 grace 대기/launchctl 확인으로 대체한다.
    """
    token = args.admin_token or os.environ.get("SIMPLECLAW_ADMIN_TOKEN") or None
    if args.admin_url:
        return args.admin_url.rstrip("/") + "/admin/v1/health", token
    try:
        cfg = load_admin_api_config(args.config)
    except Exception:  # noqa: BLE001 — config 해석 실패는 폴링 불가로만 취급
        return None, token
    if not cfg.get("enabled"):
        return None, token
    host = cfg.get("bind_host") or "127.0.0.1"
    port = int(cfg.get("bind_port") or 8082)
    if token is None:
        token = cfg.get("token_secret") or None
    return f"http://{host}:{port}/admin/v1/health", token


def _active_operations(payload: dict | None) -> int | None:
    """health 응답에서 drain.active_operations 를 관대하게 추출한다."""
    if not isinstance(payload, dict):
        return None
    drain = payload.get("drain")
    if not isinstance(drain, dict):
        return None
    try:
        return int(drain.get("active_operations"))
    except (TypeError, ValueError):
        return None


def run_drain_restart(
    args: argparse.Namespace,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    http_get_json: Callable[..., tuple[int | None, dict | None]] = _http_get_json,
    getuid: Callable[[], int] = os.getuid,
) -> int:
    """drain → poll → kickstart → smoke → evidence 를 실행하고 exit code 를 반환.

    subprocess/HTTP/sleep 을 전부 주입 가능하게 두어 단위 테스트가 launchctl
    없이 sequence 를 검증할 수 있다.
    """
    daemon_cfg = load_daemon_config(args.config)
    drain_cfg = daemon_cfg.get("drain", {}) or {}
    controller = DrainController(
        str(drain_cfg.get("state_file") or "~/.simpleclaw-agent/default/drain_state.json"),
        default_timeout=float(drain_cfg.get("default_timeout", 120)),
    )
    drain_timeout = (
        float(args.drain_timeout)
        if args.drain_timeout is not None
        else float(drain_cfg.get("default_timeout", 120))
    )
    health_url, admin_token = _resolve_admin_endpoint(args)
    steps: list[str] = []

    def note(line: str) -> None:
        steps.append(line)
        print(line, flush=True)

    restart_ok = False
    smoke_ok = False
    kickstart_cmd = ""
    try:
        # 1) drain 요청 — deadline 은 폴링 창과 같게 두어, 스크립트가 여기서
        # 죽어도 그 창이 지나면 intake 가 자동 복귀한다.
        state = controller.request_drain(
            args.reason, timeout=drain_timeout, source="drain_restart_script"
        )
        note(f"[1/5] drain requested: reason={state.reason} deadline={state.deadline}")

        # 2) quiesce 폴링 — active operation 이 0 이 될 때까지.
        deadline = monotonic() + drain_timeout
        quiesced = False
        if health_url:
            while monotonic() < deadline:
                status, payload = http_get_json(health_url, admin_token)
                active = _active_operations(payload)
                if active is None:
                    note(
                        "[2/5] health poll unavailable "
                        f"(status={status}) — falling back to grace wait"
                    )
                    break
                if active <= 0:
                    quiesced = True
                    note("[2/5] quiesced: active_operations=0")
                    break
                note(f"[2/5] waiting: active_operations={active}")
                sleep(args.poll_interval)
            else:
                note("[2/5] drain window elapsed with active operations remaining")
        if health_url is None or (not quiesced and monotonic() < deadline):
            # admin health 를 못 쓰는 환경 — in-flight turn 이 끝나기를 기대할 수
            # 있는 고정 grace 만 준다(관측 불가 시의 차선책).
            note(f"[2/5] grace wait {args.grace_seconds:.0f}s (no health polling)")
            sleep(args.grace_seconds)
            quiesced = True

        # 3) timeout 결정.
        if not quiesced:
            if args.on_timeout == "abort":
                note("[3/5] drain timeout → abort (restart skipped)")
                return 1
            note("[3/5] drain timeout → proceed (in-flight turn may be dropped)")
        else:
            note("[3/5] proceeding to restart")

        # 4) launchctl kickstart -k — 기존 LaunchAgent 운영 방식 그대로.
        target = f"gui/{getuid()}/{args.label}"
        kickstart_cmd = f"launchctl kickstart -k {target}"
        proc = runner(
            ["launchctl", "kickstart", "-k", target],
            capture_output=True,
            text=True,
        )
        restart_ok = proc.returncode == 0
        note(
            f"[4/5] {kickstart_cmd} → exit {proc.returncode}"
            + (f" ({(proc.stderr or '').strip()})" if proc.returncode != 0 else "")
        )

        # 5) health smoke — admin health 가 돌아올 때까지 폴링. health 를 못
        # 쓰면 launchctl print 로 프로세스 기동만 확인한다(약한 검증임을 남김).
        if restart_ok:
            smoke_deadline = monotonic() + args.health_timeout
            if health_url:
                while monotonic() < smoke_deadline:
                    status, payload = http_get_json(health_url, admin_token)
                    if status == 200 and (payload or {}).get("status") == "ok":
                        smoke_ok = True
                        note("[5/5] health smoke passed: /admin/v1/health status=ok")
                        break
                    sleep(args.poll_interval)
                else:
                    note(
                        "[5/5] health smoke FAILED: no ok response within "
                        f"{args.health_timeout:.0f}s"
                    )
            else:
                sleep(min(args.grace_seconds, args.health_timeout))
                probe = runner(
                    ["launchctl", "print", target],
                    capture_output=True,
                    text=True,
                )
                smoke_ok = probe.returncode == 0
                note(
                    f"[5/5] launchctl print {target} → exit {probe.returncode} "
                    "(weak smoke: admin health unavailable)"
                )
        else:
            note("[5/5] health smoke skipped: kickstart failed")
    finally:
        # 실패해도 drain 은 해제한다 — 복구 판단은 운영자 몫이지만 intake 를
        # 계속 막아둘 이유는 없다(어차피 deadline 자동 만료 대상).
        controller.clear_drain()
        print("drain cleared", flush=True)

    if args.issue_id:
        _record_evidence(
            args,
            restart_ok=restart_ok,
            smoke_ok=smoke_ok,
            kickstart_cmd=kickstart_cmd,
            steps=steps,
        )

    return 0 if (restart_ok and smoke_ok) else 1


def _record_evidence(
    args: argparse.Namespace,
    *,
    restart_ok: bool,
    smoke_ok: bool,
    kickstart_cmd: str,
    steps: list[str],
) -> None:
    """restart/health_smoke stage evidence 를 ledger 에 upsert 한다.

    기록 실패는 경고만 — 재시작 자체의 성패 판정(exit code)을 바꾸면 안 된다.
    """
    try:
        cfg = load_review_config(args.config)["verification_ledger"]
        ledger = VerificationEvidenceLedger(
            cfg["path"], retention_days=cfg["retention_days"]
        )
        excerpt = "\n".join(steps)
        restart_record = ledger.record(
            issue_id=args.issue_id,
            stage="restart",
            status="passed" if restart_ok else "failed",
            command=kickstart_cmd or "launchctl kickstart -k (not reached)",
            summary=f"launchctl kickstart {'passed' if restart_ok else 'failed'} ({args.label})",
            raw_excerpt=excerpt,
            source="drain_restart_script",
        )
        smoke_record = ledger.record(
            issue_id=args.issue_id,
            stage="health_smoke",
            status="passed" if smoke_ok else "failed",
            command="GET /admin/v1/health (post-restart poll)",
            summary=f"health smoke {'passed' if smoke_ok else 'failed'} after restart",
            raw_excerpt=excerpt,
            source="drain_restart_script",
        )
        print(
            "evidence recorded: "
            f"restart={restart_record.status.value} "
            f"health_smoke={smoke_record.status.value} → {ledger.path}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: evidence 기록 실패 — {exc}", file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="drain → restart → health smoke 순서로 SimpleClaw LaunchAgent 를 재시작한다."
    )
    parser.add_argument(
        "--label",
        default=os.environ.get("SIMPLECLAW_LAUNCHAGENT_LABEL", DEFAULT_LABEL),
        help=f"LaunchAgent label (기본 {DEFAULT_LABEL}, env SIMPLECLAW_LAUNCHAGENT_LABEL)",
    )
    parser.add_argument("--config", default="config.yaml", help="config.yaml 경로")
    parser.add_argument(
        "--reason", default="deploy restart", help="drain 요청 사유 (health 에 노출)"
    )
    parser.add_argument(
        "--drain-timeout",
        type=float,
        default=None,
        help="drain 창(초). 생략 시 config daemon.drain.default_timeout.",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=2.0, help="폴링 간격(초, 기본 2)"
    )
    parser.add_argument(
        "--health-timeout",
        type=float,
        default=60.0,
        help="재시작 후 health smoke 대기 한도(초, 기본 60)",
    )
    parser.add_argument(
        "--grace-seconds",
        type=float,
        default=10.0,
        help="admin health 폴링 불가 시 고정 대기(초, 기본 10)",
    )
    parser.add_argument(
        "--on-timeout",
        choices=("proceed", "abort"),
        default="proceed",
        help="drain 창 안에 quiesce 실패 시 정책 (기본 proceed)",
    )
    parser.add_argument(
        "--issue-id",
        help="지정 시 restart/health_smoke evidence 를 verification ledger 에 기록",
    )
    parser.add_argument(
        "--admin-url",
        help="admin API base URL override (예: http://127.0.0.1:8082). 생략 시 config.",
    )
    parser.add_argument(
        "--admin-token",
        help="admin API 토큰 override. 생략 시 env SIMPLECLAW_ADMIN_TOKEN → config.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_drain_restart(args)


if __name__ == "__main__":
    sys.exit(main())
