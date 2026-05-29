#!/usr/bin/env python3
"""Function Gemma/Gemini selector spike for SimpleClaw skills and recipes."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from simpleclaw.agent.asset_selector import (  # noqa: E402
    SelectorAsset,
    normalize_selector_response,
)
from simpleclaw.config import load_llm_config, load_recipes_config  # noqa: E402
from simpleclaw.llm.models import LLMRequest, ToolDefinition  # noqa: E402
from simpleclaw.llm.router import create_router  # noqa: E402
from simpleclaw.recipes.loader import discover_recipes  # noqa: E402
from simpleclaw.skills.discovery import discover_skills  # noqa: E402

DEFAULT_CONFIG = REPO_ROOT / "config.yaml"
LEGACY_RECIPES_DIR = REPO_ROOT / ".agent" / "recipes"


@dataclass(frozen=True)
class Asset:
    type: str
    name: str
    description: str
    source: str
    trigger: str = ""
    commands_count: int = 0
    parameters_count: int = 0
    steps_count: int = 0


@dataclass(frozen=True)
class Sample:
    id: str
    utterance: str
    expected: list[dict[str, str]]
    fallback_expected: bool = False
    note: str = ""


SAMPLES: list[Sample] = [
    Sample("browser", "example.com을 열고 로그인 버튼을 눌러 화면 캡처를 해줘", [{"type": "skill", "name": "agent-browser"}]),
    Sample("context7", "FastAPI lifespan 이벤트 최신 사용법 문서를 찾아 코드 예시를 보여줘", [{"type": "skill", "name": "context7"}]),
    Sample("gmail", "안 읽은 메일을 검색해서 중요한 것만 요약해줘", [{"type": "skill", "name": "gmail-skill"}]),
    Sample("calendar", "내일 오후 2시에 캘린더 일정을 등록해줘", [{"type": "skill", "name": "google-calendar-skill"}]),
    Sample("docs", "Google Docs에 회의록 문서를 새로 만들고 내용을 넣어줘", [{"type": "skill", "name": "google-docs-skill"}]),
    Sample("pptx", "이 내용을 발표용 pptx 5장으로 만들어줘", [{"type": "skill", "name": "pptx"}]),
    Sample("pdf", "이 PDF에서 표를 추출하고 페이지를 합쳐줘", [{"type": "skill", "name": "pdf"}]),
    Sample("xlsx", "CSV 데이터를 정리해서 xlsx 파일로 저장하고 차트를 만들어줘", [{"type": "skill", "name": "xlsx"}]),
    Sample("news", "최신 AI 뉴스 검색해서 사실 확인을 보강해줘", [{"type": "skill", "name": "news-search-skill"}]),
    Sample("us-stock", "애플 주가와 배당, 최근 뉴스를 종합해서 알려줘", [{"type": "skill", "name": "us-stock-skill"}]),
    Sample("shopping", "네이버 쇼핑에서 무선 마우스 가격을 비교해줘", [{"type": "skill", "name": "naver-shopping-skill"}]),
    Sample("route", "강남역 근처 식당을 찾고 대중교통 경로를 계산해줘", [{"type": "skill", "name": "local-route-skill"}]),
    Sample("recipe-ai", "매일 아침 최신 AI 뉴스 브리핑을 보내줘", [{"type": "recipe", "name": "ai-report"}]),
    Sample("recipe-stock", "장 마감 후 한국 증시 시황을 정리해줘", [{"type": "recipe", "name": "krstock"}]),
    Sample("ambiguous", "이거 좀 정리해줘", [], True, "의도적으로 모호한 발화"),
    Sample("no-asset", "오늘 날씨 알려줘", [], True),
]


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_skills_config(config_path: Path) -> dict[str, Any]:
    skills = _read_yaml(config_path).get("skills", {})
    return skills if isinstance(skills, dict) else {}


def _key(item: dict[str, str] | Asset) -> tuple[str, str]:
    return (item.type, item.name) if isinstance(item, Asset) else (item.get("type", ""), item.get("name", ""))


def load_manifest(config_path: Path) -> list[Asset]:
    skills_config = _load_skills_config(config_path)
    skills = discover_skills(
        local_dir=skills_config.get("local_dir", ".agent/skills"),
        global_dir=skills_config.get("global_dir", "~/.agents/skills"),
    )
    recipes_config = load_recipes_config(config_path)
    recipes = discover_recipes(recipes_config["dir"], legacy_dir=LEGACY_RECIPES_DIR)
    assets: list[Asset] = []
    for skill in skills:
        assets.append(Asset("skill", skill.name, skill.description, skill.skill_dir, skill.trigger, len(skill.commands)))
    for recipe in recipes:
        assets.append(Asset("recipe", recipe.name, recipe.description or recipe.instructions, recipe.recipe_dir, parameters_count=len(recipe.parameters), steps_count=len(recipe.steps)))
    return sorted(assets, key=lambda a: (a.type, a.name))


def to_selector_assets(assets: list[Asset]) -> list[SelectorAsset]:
    """스파이크 manifest를 production guardrail 입력 모델로 변환한다."""
    return [
        SelectorAsset(
            type=asset.type,
            name=asset.name,
            description=asset.description,
            source=asset.source,
            trigger=asset.trigger,
            commands_count=asset.commands_count,
            parameters_count=asset.parameters_count,
            steps_count=asset.steps_count,
        )
        for asset in assets
    ]


def build_prompt(assets: list[Asset]) -> str:
    lines = []
    for idx, asset in enumerate(assets, 1):
        desc = " ".join((asset.description or asset.trigger or "").split())[:260]
        lines.append(f"{idx}. [{asset.type}] {asset.name}: {desc}")
    return "\n".join(lines)


def selector_tool() -> ToolDefinition:
    return ToolDefinition(
        name="select_assets",
        description="Select the skills or recipes needed for the user's request.",
        parameters={
            "type": "object",
            "properties": {
                "selected": {"type": "array", "items": {"type": "object", "properties": {
                    "type": {"type": "string", "enum": ["skill", "recipe"]},
                    "name": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                }, "required": ["type", "name", "confidence", "reason"]}},
                "fallback": {"type": "boolean"},
                "fallback_reason": {"type": "string"},
            },
            "required": ["selected", "fallback", "fallback_reason"],
        },
    )


def parse_selection(response_text: str, tool_calls: Any) -> tuple[dict[str, Any], bool]:
    if tool_calls:
        call = tool_calls[0]
        if call.name == "select_assets":
            return dict(call.arguments), True
    try:
        parsed = json.loads(response_text)
        if isinstance(parsed, dict):
            return parsed, False
    except json.JSONDecodeError:
        pass
    return {"selected": [], "fallback": True, "fallback_reason": "parse_failed"}, False


def score(sample: Sample, result: dict[str, Any]) -> dict[str, Any]:
    selected = [s for s in result.get("selected", []) if isinstance(s, dict)]
    selected_keys = [_key(s) for s in selected]
    expected_keys = [_key(e) for e in sample.expected]
    hits = [key for key in expected_keys if key in selected_keys]
    recall = len(hits) / len(expected_keys) if expected_keys else (1.0 if not selected_keys else 0.0)
    precision = len(hits) / len(selected_keys) if selected_keys else (1.0 if not expected_keys else 0.0)
    fallback = bool(result.get("fallback"))
    return {
        "sample_id": sample.id,
        "utterance": sample.utterance,
        "expected": sample.expected,
        "selected": selected,
        "fallback": fallback,
        "fallback_expected": sample.fallback_expected,
        "fallback_ok": fallback == sample.fallback_expected,
        "recall": recall,
        "precision": precision,
        "fallback_reason": result.get("fallback_reason", ""),
    }


async def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config).expanduser().resolve()
    assets = load_manifest(config_path)
    providers = load_llm_config(config_path).get("providers", {})
    backend = args.backend or ("gemini" if "gemini" in providers else load_llm_config(config_path).get("default"))
    system_prompt = (
        "You are a strict selector for a local agent. Choose only assets explicitly useful for the request. "
        "If no candidate fits or the request is ambiguous, return selected=[] and fallback=true. Do not invent names.\n\n"
        f"Candidates:\n{build_prompt(assets)}"
    )
    router = create_router(config_path)
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    parse_success = 0
    tool_success = 0
    errors: list[dict[str, str]] = []
    samples = SAMPLES[: args.limit] if args.limit else SAMPLES
    for sample in samples:
        started = time.perf_counter()
        try:
            response = await router.send(LLMRequest(backend_name=backend, system_prompt=system_prompt, user_message=sample.utterance, tools=[selector_tool()], max_tokens=args.max_tokens))
            latency = (time.perf_counter() - started) * 1000
            latencies.append(latency)
            result, used_tool = parse_selection(response.text, response.tool_calls)
            guarded = normalize_selector_response(
                user_message=sample.utterance,
                known_assets=to_selector_assets(assets),
                response_text=response.text,
                tool_calls=response.tool_calls,
            )
            guarded_result = {
                "selected": [asdict(candidate) for candidate in guarded.selected],
                "fallback": guarded.fallback_required,
                "fallback_reason": guarded.fallback_reason,
            }
            if used_tool or result.get("fallback_reason") != "parse_failed":
                parse_success += 1
            if guarded.used_tool_call:
                tool_success += 1
            row = score(sample, guarded_result)
            row.update({
                "latency_ms": round(latency, 1),
                "used_tool_call": guarded.used_tool_call,
                "raw_selector": result,
                "raw_text": response.text,
                "backend": response.backend_name,
                "model": response.model,
                "usage": response.usage,
            })
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            errors.append({"sample_id": sample.id, "error": err})
            row = score(sample, {"selected": [], "fallback": True, "fallback_reason": err})
            row.update({"latency_ms": None, "used_tool_call": False, "error": err})
        rows.append(row)
    n = len(rows)
    return {
        "config": str(config_path),
        "backend_requested": backend,
        "manifest": {"assets_total": len(assets), "skills": sum(a.type == "skill" for a in assets), "recipes": sum(a.type == "recipe" for a in assets), "sample_asset_names": [f"{a.type}:{a.name}" for a in assets[:30]]},
        "samples": n,
        "parse_success_rate": parse_success / n if n else 0,
        "tool_call_success_rate": tool_success / n if n else 0,
        "top_k_recall": statistics.mean(r["recall"] for r in rows) if rows else 0,
        "top_k_precision": statistics.mean(r["precision"] for r in rows) if rows else 0,
        "fallback_accuracy": statistics.mean(1.0 if r.get("fallback_ok") else 0.0 for r in rows) if rows else 0,
        "latency_ms": {"avg": round(statistics.mean(latencies), 1) if latencies else None, "p95": round(statistics.quantiles(latencies, n=20)[-1], 1) if len(latencies) > 1 else (round(latencies[0], 1) if latencies else None), "min": round(min(latencies), 1) if latencies else None, "max": round(max(latencies), 1) if latencies else None},
        "errors": errors,
        "rows": rows,
    }


def write_md(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Function Gemma/Gemini Selector Evaluation Report", "", "## Summary",
        f"- Config: `{summary['config']}`",
        f"- Backend requested: `{summary['backend_requested']}`",
        f"- Manifest: {summary['manifest']['assets_total']} assets ({summary['manifest']['skills']} skills, {summary['manifest']['recipes']} recipes)",
        f"- Samples: {summary['samples']}",
        f"- Tool-call success: {summary['tool_call_success_rate']:.0%}",
        f"- Parse success: {summary['parse_success_rate']:.0%}",
        f"- Top-k recall: {summary['top_k_recall']:.0%}",
        f"- Top-k precision: {summary['top_k_precision']:.0%}",
        f"- Fallback accuracy: {summary['fallback_accuracy']:.0%}",
        f"- Latency avg/p95: {summary['latency_ms']['avg']} ms / {summary['latency_ms']['p95']} ms", "", "## Per-sample results",
        "| sample | recall | precision | fallback | tool_call | latency_ms | selected | error |",
        "|---|---:|---:|---|---|---:|---|---|",
    ]
    for row in summary["rows"]:
        selected = ", ".join(f"{s.get('type')}:{s.get('name')}" for s in row.get("selected", [])) or "-"
        err = str(row.get("error", "")).replace("|", "/")
        lines.append(f"| {row['sample_id']} | {row['recall']:.0%} | {row['precision']:.0%} | {row.get('fallback')} | {row.get('used_tool_call')} | {row.get('latency_ms')} | {selected} | {err} |")
    if summary.get("errors"):
        lines += ["", "## Errors"]
        lines += [f"- {e['sample_id']}: `{e['error']}`" for e in summary["errors"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--backend", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--output", default="selector_results.json")
    parser.add_argument("--markdown", default="selector_results.md")
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args()
    if args.manifest_only:
        assets = load_manifest(Path(args.config).expanduser().resolve())
        print(json.dumps({"assets_total": len(assets), "skills": sum(a.type == "skill" for a in assets), "recipes": sum(a.type == "recipe" for a in assets), "assets": [asdict(a) for a in assets]}, ensure_ascii=False, indent=2))
        return
    summary = asyncio.run(run_eval(args))
    Path(args.output).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(Path(args.markdown), summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
