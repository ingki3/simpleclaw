#!/usr/bin/env python3
"""Production default provider의 중앙 composer를 sink 없이 한 번 검증한다."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from simpleclaw.agent.composition_contracts import CompositionInputV1
from simpleclaw.agent.final_response_composer import (
    FinalResponseComposer,
    FinalResponseComposerError,
)
from simpleclaw.agent.final_response_guard import guard_final_response
from simpleclaw.graph_runtime.contracts import AssetRefV1
from simpleclaw.graph_runtime.status import (
    AssetResultStatus,
    EffectStatus,
)
from simpleclaw.llm.router import create_router


class _OneCallSend:
    """Provider 호출 수를 계측하고 두 번째 호출을 구조적으로 차단한다."""

    def __init__(self, send) -> None:
        self._send = send
        self.calls = 0

    async def __call__(self, request):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("composer provider call cap exceeded")
        return await self._send(request)


def _input() -> CompositionInputV1:
    return CompositionInputV1(
        request_id="biz-639-no-send-probe",
        question="Repeat the first projected status exactly.",
        locale="en-US",
        selected_route="react",
        asset_ref=AssetRefV1(type="skill", name="citation-activation-fixture"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="biz-639-synthetic-payload",
        public_facts={
            "data": {"items": [{"status": "The activation gate is READY."}]}
        },
        resolved_claims=("data.items[0].status",),
    )


async def _run(args: argparse.Namespace) -> int:
    config_path = args.config.expanduser()
    if not config_path.is_file():
        print(
            json.dumps(
                {"error_type": "ConfigNotFound", "provider_calls": 0},
                sort_keys=True,
            )
        )
        return 1

    logging.disable(logging.CRITICAL)
    router = create_router(config_path)
    backend = router.get_default_backend()
    counted_send = _OneCallSend(router.send)
    composer = FinalResponseComposer(
        send=counted_send,
        persona_prompt="Use concise English and repeat supplied literals exactly.",
        max_tokens=args.max_tokens,
        backend_name=backend,
    )
    value = _input()
    try:
        draft = await asyncio.wait_for(
            composer.compose(value), timeout=args.timeout
        )
    except (FinalResponseComposerError, TimeoutError) as exc:
        result = {
            "error_type": type(exc).__name__,
            "provider_calls": counted_send.calls,
            "telegram_writes": 0,
            "notifier_writes": 0,
            "conversation_store_writes": 0,
            "external_sink_writes": 0,
        }
        print(json.dumps(result, sort_keys=True))
        return 1
    guard = guard_final_response(value, draft)
    result = {
        "backend": backend,
        "provider_calls": counted_send.calls,
        "guard_accepted": guard.accepted,
        "guard_code": guard.code,
        "content_sha256": hashlib.sha256(draft.content.encode("utf-8")).hexdigest(),
        "content_length": len(draft.content),
        "citations": list(draft.cited_paths),
        "telegram_writes": 0,
        "notifier_writes": 0,
        "conversation_store_writes": 0,
        "external_sink_writes": 0,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if guard.accepted and counted_send.calls == 1 else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".simpleclaw" / "config.yaml",
    )
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
