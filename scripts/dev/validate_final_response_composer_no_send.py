#!/usr/bin/env python3
"""Production default provider의 중앙 composer를 고정 시나리오로 검증한다."""

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


def _single_scalar_input() -> CompositionInputV1:
    return CompositionInputV1(
        request_id="biz-640-single-scalar-no-send-probe",
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


def _top_three_input() -> CompositionInputV1:
    return CompositionInputV1(
        request_id="biz-640-top-three-no-send-probe",
        question=(
            "In one sentence without numbering, repeat only the top 3 projected "
            "labels and values in order."
        ),
        locale="en-US",
        selected_route="react",
        asset_ref=AssetRefV1(type="skill", name="citation-activation-fixture"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="biz-640-top-three-synthetic-payload",
        public_facts={
            "items": [
                {"label": "Alpha", "value": "One"},
                {"label": "Beta", "value": "Two"},
                {"label": "Gamma", "value": "Three"},
            ]
        },
        resolved_claims=(
            "items[0].label",
            "items[0].value",
            "items[1].label",
            "items[1].value",
            "items[2].label",
            "items[2].value",
        ),
    )


_SCENARIOS = (
    ("single_scalar", _single_scalar_input),
    ("top_three_items", _top_three_input),
)


async def _run(args: argparse.Namespace) -> int:
    config_path = args.config.expanduser()
    if not config_path.is_file():
        print(
            json.dumps(
                {
                    "delivery_sinks_configured": False,
                    "error_type": "ConfigNotFound",
                    "expected_provider_calls": len(_SCENARIOS),
                    "provider_calls": 0,
                    "retry_calls": 0,
                    "scenario_count": len(_SCENARIOS),
                },
                sort_keys=True,
            )
        )
        return 1

    logging.disable(logging.CRITICAL)
    router = create_router(config_path)
    backend = router.get_default_backend()
    scenario_results: list[dict[str, object]] = []
    provider_calls = 0
    for scenario_name, input_factory in _SCENARIOS:
        counted_send = _OneCallSend(router.send)
        composer = FinalResponseComposer(
            send=counted_send,
            persona_prompt="Use concise English and repeat supplied literals exactly.",
            max_tokens=args.max_tokens,
            backend_name=backend,
        )
        value = input_factory()
        try:
            draft = await asyncio.wait_for(
                composer.compose(value), timeout=args.timeout
            )
        except (FinalResponseComposerError, TimeoutError) as exc:
            scenario_results.append(
                {
                    "error_type": type(exc).__name__,
                    "name": scenario_name,
                    "provider_calls": counted_send.calls,
                }
            )
            provider_calls += counted_send.calls
            continue
        provider_calls += counted_send.calls
        guard = guard_final_response(value, draft)
        scenario_results.append(
            {
                "citations": list(draft.cited_paths),
                "content_length": len(draft.content),
                "content_sha256": hashlib.sha256(
                    draft.content.encode("utf-8")
                ).hexdigest(),
                "guard_accepted": guard.accepted,
                "guard_code": guard.code,
                "name": scenario_name,
                "provider_calls": counted_send.calls,
            }
        )

    passed = provider_calls == len(_SCENARIOS) and all(
        scenario.get("guard_accepted") is True
        and scenario.get("provider_calls") == 1
        for scenario in scenario_results
    )
    result = {
        "backend": backend,
        "delivery_sinks_configured": False,
        "expected_provider_calls": len(_SCENARIOS),
        "passed": passed,
        "provider_calls": provider_calls,
        "retry_calls": 0,
        "scenario_count": len(_SCENARIOS),
        "scenarios": scenario_results,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 1


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
