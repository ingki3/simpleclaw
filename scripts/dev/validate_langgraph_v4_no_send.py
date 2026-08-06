#!/usr/bin/env python3
"""Run the contract-fixture no-send scenario through the generic harness."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from simpleclaw.langgraph_v4_shadow_validation import (  # noqa: E402
    ContractIdentity,
    _parser,
    _run,
)
from simpleclaw.llm.models import LLMResponse  # noqa: E402
from simpleclaw.recipes.loader import discover_recipes  # noqa: E402
from simpleclaw.skills.discovery import discover_skills  # noqa: E402

FIXTURE_NAMES = {"contract-fixture-workflow", "contract-fixture-step"}
EXPECTED_CONTRACT_SET = frozenset(
    {
        ContractIdentity(
            "recipe",
            "contract-fixture-workflow",
            "recipe.contract-fixture-workflow.input",
            "1",
            "bd7c4ac7d6dddb0980548e9e734dedd82aa919db89a68f5af9337034f420d951",
        ),
        ContractIdentity(
            "recipe",
            "contract-fixture-workflow",
            "recipe.contract-fixture-workflow.output",
            "1",
            "80b8777f78ea08e5bede7708a3f84d78c38cc2cfb0dd153347034313589c9eae",
        ),
        ContractIdentity(
            "skill",
            "contract-fixture-step",
            "skill.contract-fixture-step.input",
            "1",
            "a742768b209b5455a545f174e2a8b6a9462aebb44a093738e4cb2a30216f62a1",
        ),
        ContractIdentity(
            "skill",
            "contract-fixture-step",
            "skill.contract-fixture-step.output",
            "1",
            "e8167acecea2db65606d53fe918fca0eab4d081148b43ec7e7da21849f977f91",
        ),
    }
)


def definitions():
    """Discover the scenario-owned contract fixtures."""
    recipes = discover_recipes(REPO_ROOT / "tests/fixtures/recipes")
    skills = discover_skills(
        REPO_ROOT / "tests/fixtures/skills",
        REPO_ROOT / "tests/fixtures/global-skills",
    )
    return tuple(
        item for item in (*recipes, *skills) if item.name in FIXTURE_NAMES
    )


def cases() -> tuple[tuple[str, str], ...]:
    return (
        (
            "Use the exact full-coverage recipe contract-fixture-workflow, which "
            "owns all evidence, to verify the current contract validation status. "
            "Use direct_answer as the fallback execution mode.",
            "recipe",
        ),
        (
            "Use the exact full-coverage skill contract-fixture-step, which owns "
            "all evidence, to verify the current contract validation status. Use "
            "answer_with_evidence as the fallback execution mode.",
            "react",
        ),
        (
            "Use the exact full-coverage skill contract-fixture-step, which owns "
            "all evidence, to verify and compare the current contract validation "
            "status. Resolve the explicit dependency graph and conflicting "
            "validation branches with resolve_complex_problem as the fallback "
            "execution mode.",
            "deep_research",
        ),
    )


class HermeticPlannerRouter:
    """Return deterministic plans for the scenario-owned fixtures."""

    async def send(self, request):
        prompt = json.loads(request.user_message)["current_user_message"]
        recipe = "contract-fixture-workflow" in prompt
        complex_case = "verify and compare" in prompt
        fact_required = not recipe
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
            "domains": ["fixture"] if fact_required else [],
            "intents": ["verify"] if fact_required else [],
            "fact_check": {
                "required": fact_required,
                "owner": "asset" if fact_required else "none",
                "domain": "fixture" if fact_required else "none",
                "entities": [],
                "reference_date": "",
                "search_query": prompt if fact_required else "",
                "required_claims": (
                    ["contract validation status"] if fact_required else []
                ),
                "freshness_required": False,
                "reason": "hermetic contract fixture",
            },
            "capability": {
                "coverage": "full_coverage",
                "primary_asset": {
                    "asset_type": "recipe" if recipe else "skill",
                    "asset_name": (
                        "contract-fixture-workflow"
                        if recipe
                        else "contract-fixture-step"
                    ),
                },
                "supporting_assets": [],
                "fallback_modes": [],
                "reason": "hermetic exact fixture",
            },
            "execution": {
                "mode": (
                    "direct_answer"
                    if recipe
                    else (
                        "resolve_complex_problem"
                        if complex_case
                        else "answer_with_evidence"
                    )
                ),
                "allowed_tools": [],
                "requires_confirmation": False,
                "complexity_signals": (
                    ["dependency_graph", "evidence_conflict"]
                    if complex_case
                    else []
                ),
                "reason": "hermetic exact fixture",
            },
            "confidence": 1.0,
            "decision_summary": "deterministic hermetic validation plan",
        }
        return LLMResponse(
            text=json.dumps(payload, ensure_ascii=False),
            backend_name="hermetic",
            model="fixed-contract-fixture",
        )


async def run(args) -> int:
    return await _run(
        args,
        definitions=definitions(),
        cases=cases(),
        router_override=HermeticPlannerRouter() if args.hermetic else None,
        expected_contract_set=EXPECTED_CONTRACT_SET,
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(_parser().parse_args())))
