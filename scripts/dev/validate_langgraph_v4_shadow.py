#!/usr/bin/env python3
"""설치 asset의 planner/PlanGate/stub dispatch 계약 scenario를 검증한다."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from simpleclaw.langgraph_v4_shadow_validation import _parser, _run
from simpleclaw.llm.models import LLMResponse
from simpleclaw.production_assets import install_runtime_asset
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills

DEFAULT_SCENARIO = (
    REPO_ROOT / "tests/fixtures/langgraph_v4_shadow/kbo_no_send.yaml"
)


class _ScenarioRouter:
    """scenario data가 선언한 planner 응답을 결정적으로 반환한다."""

    def __init__(self, scenario: dict[str, object]) -> None:
        """provider 호출 없이 사용할 scenario payload를 고정한다."""
        self._scenario = scenario

    async def send(self, request):
        """입력 prompt가 fixture와 일치할 때 선언된 plan을 반환한다."""
        prompt = json.loads(request.user_message)["current_user_message"]
        if prompt != self._scenario["prompt"]:
            raise RuntimeError("scenario prompt mismatch")
        planner = self._scenario["planner"]
        if not isinstance(planner, dict):
            raise TypeError("scenario planner must be a mapping")
        payload = {
            "context": {
                "relation": "standalone",
                "use_prior_context": False,
                "selected_turn_ids": [],
                "standalone_question": prompt,
                "unresolved_references": [],
                "ignored_context_reason": "",
            },
            "clarification": {
                "required": False,
                "question": "",
                "options": [],
                "reason": "",
            },
            "domains": planner["domains"],
            "intents": planner["intents"],
            "fact_check": {
                "required": True,
                "owner": "planner",
                "domain": planner["domains"][0],
                "entities": [],
                "reference_date": "",
                "search_query": prompt,
                "required_claims": planner["required_claims"],
                "freshness_required": planner["freshness_required"],
                "reason": "scenario-declared factual request",
            },
            "capability": {
                "coverage": "no_match",
                "primary_asset": {"asset_type": "none", "asset_name": "__none__"},
                "supporting_assets": [],
                "fallback_modes": [],
                "reason": "scenario-declared asset-zero plan",
            },
            "execution": {
                "mode": "direct_answer",
                "allowed_tools": [],
                "requires_confirmation": False,
                "complexity_signals": [],
                "reason": "scenario-declared plan",
            },
            "confidence": 1.0,
            "decision_summary": "deterministic scenario plan",
        }
        return LLMResponse(
            text=json.dumps(payload, ensure_ascii=False),
            backend_name="scenario",
            model="fixture",
        )


def _load_scenario(path: Path) -> dict[str, object]:
    """scenario YAML을 mapping으로 제한해 잘못된 fixture를 조기에 거부한다."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("scenario must be a mapping")
    return raw


async def _main() -> int:
    """격리 설치 asset의 contract-only no-send harness를 실행한다."""
    parser = _parser()
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    args = parser.parse_args()
    scenario = _load_scenario(args.scenario)
    raw_assets = scenario.get("assets")
    expected = scenario.get("expected")
    if not isinstance(raw_assets, list) or not all(
        isinstance(item, str) for item in raw_assets
    ):
        raise ValueError("scenario assets must be refs")
    if not isinstance(expected, dict):
        raise TypeError("scenario expected must be a mapping")

    with tempfile.TemporaryDirectory(prefix="simpleclaw-scenario-assets-") as temp:
        installed = Path(temp)
        for ref in raw_assets:
            asset_type = ref.partition(":")[0]
            install_runtime_asset(
                ref,
                destination_parent=installed / f"{asset_type}s",
            )
        definitions = (
            *discover_recipes(installed / "recipes"),
            *discover_skills(Path("/__missing_local_skills__"), installed / "skills"),
        )
        return await _run(
            args,
            definitions=definitions,
            cases=((str(scenario["prompt"]), str(scenario["route"])),),
            router_override=_ScenarioRouter(scenario),
            expected_contract_set=None,
            expected_effective_assets=(str(expected["effective_asset"]),),
            definitions_label="scenario_installer_output",
            planner_mode_label="scenario_stub",
        )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
