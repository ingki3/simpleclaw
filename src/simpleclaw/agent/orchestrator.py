"""Agent orchestrator — 페르소나·스킬·메모리·LLM을 하나로 묶는 중앙 조율기.

응답 파이프라인 (Native Function Calling):
1. 사용자 메시지 수신
2. LLM에 도구 정의(tools)와 함께 메시지 전송
3. LLM이 tool_calls를 반환하면 → 도구 실행 → 결과를 메시지에 추가 → 재호출
4. LLM이 텍스트만 반환하면 → 최종 응답으로 반환

Hot-reload 정책:
  AGENT.md, USER.md, MEMORY.md, 스킬/레시피 파일은 매 메시지(process_message /
  process_cron_message) 진입 시 1회 디스크에서 다시 읽는다.
  → 파일 수정 후 봇 리스타트 없이 다음 메시지부터 반영됨.
  → tool loop 내부에서는 캐시된 값을 재사용하여 불필요한 I/O를 방지함.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import os
import random
import shlex
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from simpleclaw.agent import (
    command_dispatch,
    memory_search,
    skill_dispatch,
    tool_dispatch,
)
from simpleclaw.agent.asset_selector import (
    AssetSelectionResult,
    build_selector_assets,
    build_selector_prompt,
    build_selector_tool_definition,
    filter_assets_by_selection,
    normalize_selector_response,
)
from simpleclaw.agent.capability_executor import (
    ASSET_RESULT_RESPONSE_SCHEMA,
    CapabilityExecutor,
    decode_asset_result,
)
from simpleclaw.agent.capability_router import (
    CapabilityDecision,
)
from simpleclaw.agent.clarify import (
    ClarifyRequest,
    clarify_chat_id_var,
    normalize_options,
)
from simpleclaw.agent.commands import (
    parse_goal_command,
    try_cron_command,
    try_recipe_command,
)
from simpleclaw.agent.complex_problem import (
    ComplexProblemController,
    ComplexProblemState,
    ProblemNode,
)
from simpleclaw.agent.context_candidates import (
    ContextCandidateBuilder,
    ContextCandidateSet,
)
from simpleclaw.agent.context_retrieval import (
    ContextRetrievalConfig,
    ContextRetrievalService,
)
from simpleclaw.agent.evidence_investigation import (
    EvidenceInvestigationController,
)
from simpleclaw.agent.evidence_policy import (
    EvidenceFreshness,
    EvidenceRequirement,
    EvidenceSourceType,
    EvidenceState,
    EvidenceStatus,
    no_evidence_requirement,
    requirement_from_turn_plan,
)
from simpleclaw.agent.execution_router import (
    ExecutionCallbacks,
    ExecutionRouter,
)
from simpleclaw.agent.fact_check_controller import FactCheckController
from simpleclaw.agent.file_mutation_tracker import (
    FileMutationTracker,
    TrackedRoot,
)
from simpleclaw.agent.goal_loop import GoalLoopConfig, GoalLoopRunner
from simpleclaw.agent.observation_claims import (
    declared_claim_bindings,
    materialize_validated_claims,
)
from simpleclaw.agent.plan_gate import GateStatus, PlanGate
from simpleclaw.agent.planner_catalog import (
    PlannerCatalog,
    build_planner_catalog,
    connected_contract_complete,
)
from simpleclaw.agent.progress import ProgressCallback
from simpleclaw.agent.resolution_controller import ResolutionController
from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    ComplexitySignal,
    GoalStatus,
    ProblemTransition,
    ResolutionBudget,
)
from simpleclaw.agent.session_state import (
    PendingInteraction,
    SessionIdentity,
    SessionState,
    current_session_key_var,
    current_turn_id_var,
)
from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.agent.tool_gate import (
    ToolExecutionScope,
    TrustedAssetSafety,
    skill_definition_fingerprint,
)
from simpleclaw.agent.tool_loop import (
    ToolLoopResult,
    ToolLoopRunner,
    ToolLoopState,
)
from simpleclaw.agent.tool_schemas import (
    NativeToolSpec,
    ToolScope,
    build_native_tool_registry,
    build_tool_definitions,
    filter_tool_definitions,
    validate_dispatch_tool_names,
)
from simpleclaw.agent.turn_plan import AssetRef, ExecutionMode, UnifiedTurnPlan
from simpleclaw.agent.turn_planner import plan_turn_with_llm
from simpleclaw.agent.turn_planner_telemetry import (
    PlannerUsageCaptureRouter,
    build_turn_planner_shadow_event,
    build_turn_planner_shadow_failure_event,
    emit_turn_planner_shadow_event,
)
from simpleclaw.agent.turn_state import TurnExecutionState, TurnPhase
from simpleclaw.config import (
    load_agent_config,
    load_asset_selection_config,
    load_daemon_config,
    load_mcp_config,
    load_memory_config,
    load_persona_config,
    load_recipe_learning_config,
    load_recipes_config,
    load_security_config,
    load_skills_learning_config,
    load_study_config,
)
from simpleclaw.daemon.drain import (
    DRAIN_CRON_SKIPPED_MESSAGE,
    DRAIN_MAINTENANCE_MESSAGE,
    DrainController,
)
from simpleclaw.daemon.models import CronActionResult, CronFailureKind
from simpleclaw.graph_runtime.checkpoint import resolve_checkpoint_path
from simpleclaw.graph_runtime.idempotency import (
    canonical_artifact_content_hash,
    canonical_artifact_id,
)
from simpleclaw.graph_runtime.runtime import (
    LangGraphV4ExecutionReceiptV1,
    LangGraphV4RolloutFacade,
    LegacyRunTelemetryV1,
    ShadowBudgetUsageV1,
)
from simpleclaw.graph_runtime.shadow import (
    ConnectedExecutionError,
    ConnectedShadowResultV1,
    ConnectedShadowTurnRunner,
    DurableDispatchProvenanceV1,
    load_durable_dispatch_provenance,
)
from simpleclaw.graph_runtime.status import TerminalOutcome
from simpleclaw.llm.models import (
    LLMRequest,
    MultimodalAttachment,
    SystemBlock,
    ToolCall,
)
from simpleclaw.llm.providers.base import TextDeltaCallback
from simpleclaw.llm.router import create_router
from simpleclaw.logging.trace_context import trace_scope
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.embedding_service import EmbeddingService
from simpleclaw.memory.models import (
    CHANNEL_CRON_ADMIN,
    CHANNEL_GOAL_PREFIX,
    CHANNEL_RECIPE_PREFIX,
    ConversationMessage,
    MessageRole,
)
from simpleclaw.outbound_delivery import (
    PrimaryDeliveryCoordinator,
    PrimaryDeliveryMetadataV1,
    PrimaryDeliveryOutcomeV1,
    PrimaryResponseText,
)
from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.persona.resolver import resolve_persona_files
from simpleclaw.proactive.conversation_detector import ConversationEndDetector
from simpleclaw.proactive.store import OpportunityStore
from simpleclaw.recipes.executor import (
    execute_recipe,
    render_exact_recipe_instructions,
)
from simpleclaw.recipes.learning import (
    RECIPE_SUGGESTION_RESPONSE_SCHEMA,
    RecipeSuggestion,
    RecipeSuggestionStore,
    build_recipe_candidate_prompt,
    suggestion_from_recipe_payload,
)
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.security import CommandGuard
from simpleclaw.security.secrets import default_manager
from simpleclaw.security.skill_env import load_skill_env_secret_refs
from simpleclaw.skills.discovery import discover_skills
from simpleclaw.skills.learning import (
    SKILL_SUGGESTION_RESPONSE_SCHEMA,
    SkillSuggestion,
    SkillSuggestionStore,
    build_skill_candidate_prompt,
    is_complex_successful_trace,
    snapshots_from_trace,
    suggestion_from_candidate_payload,
    trace_fingerprint,
)
from simpleclaw.skills.mcp_client import MCPManager
from simpleclaw.skills.models import SkillDefinition
from simpleclaw.skills.realtime_contracts import (
    LookupStatus,
    RealtimeLookupRequest,
    RealtimeLookupResult,
)
from simpleclaw.skills.realtime_lookup import (
    decode_payload as decode_realtime_lookup_payload,
)
from simpleclaw.skills.realtime_lookup import (
    lookup_async as run_realtime_lookup,
)
from simpleclaw.study.retriever import StudyRetrievalConfig, StudyRetriever

if TYPE_CHECKING:
    from simpleclaw.daemon.scheduler import CronScheduler
    from simpleclaw.logging.metrics import MetricsCollector
    from simpleclaw.logging.structured_logger import StructuredLogger

logger = logging.getLogger(__name__)


def _deterministic_rollout_sample(
    *,
    user_id: int,
    chat_id: int,
    sample_rate: float,
) -> bool:
    """user/chat cohort를 프로세스 재시작과 무관한 rollout bucket에 고정한다."""
    bounded_rate = min(max(float(sample_rate), 0.0), 1.0)
    if bounded_rate <= 0.0:
        return False
    if bounded_rate >= 1.0:
        return True
    cohort = f"unified-turn-planner-canary-v1:{user_id}:{chat_id}".encode()
    bucket = int.from_bytes(
        hashlib.blake2s(cohort, digest_size=8).digest(),
        "big",
    )
    return bucket / float(1 << 64) < bounded_rate


def _canary_read_only_eligible(
    plan: UnifiedTurnPlan,
    catalog: PlannerCatalog,
) -> bool:
    """Phase 2 canary에서 부작용 없는 direct/declared asset plan만 허용한다."""
    execution = plan.execution
    if execution.requires_confirmation:
        return False
    if (
        execution.mode is ExecutionMode.DIRECT_ANSWER
        and plan.capability.primary_asset is None
        and not plan.capability.supporting_assets
        and not execution.allowed_tools
    ):
        return (
            not plan.fact_check.required
            and not execution.allowed_assets
        )
    if plan.capability.coverage.value != "full_coverage":
        return False

    refs = set(plan.capability.supporting_assets)
    if plan.capability.primary_asset is not None:
        refs.add(plan.capability.primary_asset)
    refs.update(
        AssetRef("native_tool", tool_name)
        for tool_name in execution.allowed_tools
        if tool_name != "execute_skill"
    )
    if not refs:
        return False
    runtime_assets = {
        (asset.asset_type, asset.name): asset
        for asset in catalog.assets
        if asset.runtime_visible
    }
    for ref in refs:
        asset = runtime_assets.get((ref.asset_type, ref.name))
        if (
            asset is None
            or not asset.declared
            or not asset.read_only
            or asset.side_effects
            or asset.requires_confirmation
        ):
            return False
    return True


def _selected_asset_identity(plan: UnifiedTurnPlan) -> str:
    """원문 없이 primary asset의 owner-qualified identity만 기록한다."""
    selected = plan.capability.primary_asset
    if selected is None:
        return "none"
    return f"{selected.asset_type}:{selected.name}"


def _v4_connected_contract_eligible(
    plan: UnifiedTurnPlan,
    catalog: PlannerCatalog,
) -> bool:
    """PlanGate asset과 connected registry가 공유할 exact identity를 요구한다."""
    selected = plan.capability.primary_asset
    if selected is None:
        return False
    matches = tuple(
        asset
        for asset in catalog.assets
        if (asset.asset_type, asset.name)
        == (selected.asset_type, selected.name)
    )
    if len(matches) != 1:
        return False
    asset = matches[0]
    return connected_contract_complete(asset)


def _allow_v4_legacy_fallback(
    v4: Mapping[str, object],
    execution: LangGraphV4ExecutionReceiptV1 | None,
    provenance: DurableDispatchProvenanceV1 | None = None,
) -> bool:
    """Target pre-dispatch 실패에서만 legacy executor 진입을 허용한다."""
    if str(v4.get("on_failure", "fail_closed")) != "legacy":
        return False
    if provenance is None or not provenance.pre_dispatch_proven:
        return False
    return execution is None or (
        execution.dispatch_trace.attempted == 0
        and execution.dispatch_trace.executed == 0
    )


def _is_direct_without_asset(plan: UnifiedTurnPlan) -> bool:
    """Connected exact-asset 경로 밖의 ordinary direct turn을 식별한다."""
    return (
        plan.execution.mode is ExecutionMode.DIRECT_ANSWER
        and plan.capability.primary_asset is None
        and not plan.capability.supporting_assets
        and not plan.execution.allowed_assets
        and not plan.execution.allowed_tools
    )

_ATTACHMENT_CONTEXT_HEADER = "Attachment context"

_NATIVE_DISPATCH_TOOL_NAMES = frozenset({
    "cli",
    "web_fetch",
    "web_search",
    "browser_handoff",
    "file_read",
    "file_write",
    "file_manage",
    "skill_docs",
    "search_memory",
    "clarify",
    "cron",
    "runtime_status",
    "config_inspect",
    "log_debug",
    "asset_inventory",
    "deploy_status",
    "recipe_validate",
    "recipe_generate",
    "recipe_learning",
    "skill_validate",
    "restart_runtime",
    "skill_learning",
    "study_status",
    "review_subagent_ledger",
    "verification_evidence",
})
validate_dispatch_tool_names(
    _NATIVE_DISPATCH_TOOL_NAMES,
    scopes=(ToolScope.RUNTIME, ToolScope.OPERATOR, ToolScope.DEVELOPMENT),
    operator_gate=True,
    browser_handoff_available=True,
)


def _inject_env_secret_refs(env_secret_refs: object) -> None:
    """config의 시크릿 참조를 스킬 실행용 환경변수로 주입한다.

    런타임 스킬은 기존 CLI 생태계와 호환되도록 API 키를 환경변수로 읽는 경우가
    많다. 평문 config/LaunchAgent 대신 암호화 vault에는 ``file:<name>`` 참조를
    저장하고, 봇 프로세스 시작 시 필요한 키만 ``os.environ``에 복원한다.
    실제 자식 프로세스 전달 여부는 ``security.env_passthrough``가 별도로 제어한다.
    """
    if not isinstance(env_secret_refs, dict):
        return

    manager = default_manager()
    for env_name, ref in env_secret_refs.items():
        if not isinstance(env_name, str) or not env_name:
            continue
        if not isinstance(ref, str) or not ref:
            continue
        value = manager.resolve(ref)
        if not value:
            logger.warning("Configured env secret could not be resolved: %s", env_name)
            continue
        os.environ[env_name] = value

# 시스템 프롬프트에 추가할 도구 사용 안내.
#
# 운영 지침에 따라 하드코딩 대신 ``prompts/system/tool_usage.yaml`` 을
# 단일 Source of Truth 로 사용한다.
_TOOL_USAGE_INSTRUCTION = load_system_prompt("tool_usage").prompt

# BIZ-160 — tool 루프가 max_tool_iterations 를 다 쓰고도 LLM 이 빈 텍스트를 돌려준
# 사고(2026-05-08)에서 사용자에게 아무 메시지도 가지 않아 봇이 죽은 것처럼 보였음.
# 빈 응답 자리에 안내 메시지를 채워, 채널 라우터(`if response:`)가 sendMessage 를
# skip 하지 않도록 한다.
_BUDGET_EXHAUSTED_EMPTY_MESSAGE = (
    "여러 도구를 시도했지만 답을 마무리하지 못했습니다.\n"
    "질문을 짧게 다시 표현해 주시거나, URL/파일 경로를 함께 알려 주시면 도움이 됩니다.\n"
    "(debug: tool loop {iterations}회 반복 후 종료)"
)
_BUDGET_EXHAUSTED_HINT_SUFFIX = (
    "(참고: 도구 호출 한도 {iterations}회에 도달해 추가 정보 수집을 멈췄습니다)"
)
_EMPTY_DIRECT_RESPONSE_MESSAGE = (
    "빈 응답으로 인해 응답을 생성하지 못했습니다. 죄송하지만 한 번 더 말씀해 주세요."
)
_UNDO_USAGE_MESSAGE = "사용법: /undo 또는 /undo N (N은 1 이상의 정수)"
_UNDO_NO_TURNS_MESSAGE = "되돌릴 대화 턴이 없습니다."
_UNDO_SUCCESS_MESSAGE = (
    "최근 {turns}턴을 다음 응답부터 제외했습니다. "
    "원본 메시지는 감사용으로 DB에 남겨 두며, 이 /undo 명령 자체는 대화 이력에 저장하지 않습니다."
)
_TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MESSAGE = (
    "확인해 봤지만 관련 기록을 찾지 못했습니다."
)
_TOOL_RESULT_EMPTY_FINAL_ERROR_MESSAGE = (
    "확인 중 오류가 발생해 답변을 마무리하지 못했습니다: {detail}"
)
_TOOL_RESULT_EMPTY_FINAL_GENERIC_MESSAGE = (
    "확인은 했지만 답변을 마무리하지 못했습니다. 확인한 결과: {detail}"
)
_TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MARKERS = (
    "0 chars",
    "0 rows",
    "0 row",
    "no rows",
    "no row",
    "no results",
    "not found",
    "검색 결과가 없습니다",
    "결과 없음",
    "없음",
    "없습니다",
    "못 찾",
    "찾지 못",
)
_TOOL_RESULT_EMPTY_FINAL_ERROR_PREFIXES = (
    "error",
    "traceback",
    "exception",
    "timeout",
    "failed",
    "command failed",
    "tool error",
    "오류",
    "실패",
)

_REALTIME_LOOKUP_SKILL_NAME = "realtime-lookup-skill"
def _parse_undo_command(text: str) -> tuple[bool, int | None]:
    """/undo 명령 여부와 요청 turn 수를 파싱한다.

    Telegram은 slash command를 일반 텍스트로 전달하므로 LLM/tool loop에 넣기 전
    오케스트레이터에서 선처리한다. ``/undo``의 기본값은 1이고, ``/undo N``만
    허용한다. 그 외 토큰/음수/0은 사용법 안내로 돌린다.
    """
    parts = text.strip().split()
    if not parts or parts[0] != "/undo":
        return False, None
    if len(parts) == 1:
        return True, 1
    if len(parts) != 2:
        return True, None
    try:
        turns = int(parts[1])
    except ValueError:
        return True, None
    if turns < 1:
        return True, None
    return True, turns


def _format_attachment_context_note(
    attachments: list[MultimodalAttachment] | None,
) -> str:
    """현재 turn 첨부 메타데이터와 분석 지시를 provider 입력용 note로 만든다."""
    if not attachments:
        return ""

    lines = [
        f"## {_ATTACHMENT_CONTEXT_HEADER}",
        "첨부 내용을 직접 분석해 주세요. 불가능하면 이유와 필요한 조치를 설명해 주세요.",
        ("현재 turn의 첨부는 `이거`, `몇 알`, `이 제품` 같은 지시어를 해석할 때 "
        "1차 근거입니다. 이전 대화보다 먼저 첨부를 분석해 주세요."),
        ("사용자가 최신 확인이나 검색을 명시적으로 요청하지 않았다면, 이전 대화만을 "
        "근거로 첨부와 무관한 웹 검색이나 현재 사실 조회로 확장하지 마세요."),
    ]
    for index, attachment in enumerate(attachments, start=1):
        name = attachment.name or f"attachment-{index}"
        size_bytes = attachment.size_bytes
        if size_bytes is None:
            size_bytes = len(attachment.data) if attachment.data is not None else None
        parts = [
            f"- Attachment {index}",
            f"File name: {name}",
            f"MIME: {attachment.mime_type}",
        ]
        if size_bytes is not None:
            parts.append(f"Size: {size_bytes} bytes")
        if attachment.path:
            parts.append(f"Sandbox path: {attachment.path}")
        lines.append("; ".join(parts))
    return "\n".join(lines)


def _tool_result_looks_like_explicit_error(content: str) -> bool:
    """도구 결과가 명시적 오류 envelope/header 로 시작하는지 판정한다.

    정상 transcript/요약 본문에는 ``error``/``failed`` 같은 단어가 자연어로 섞일 수
    있다. 그래서 전체 본문 검색 대신 첫 non-empty line 또는 JSON-style envelope 처럼
    도구 실행 실패를 직접 선언하는 초반 헤더만 오류로 본다.
    """
    stripped = content.strip()
    if not stripped:
        return False

    lowered = stripped.lower()
    if lowered.startswith(('{"error"', "{'error'")):
        return True

    for line in stripped.splitlines()[:3]:
        header = line.strip().lower()
        if not header:
            continue
        return any(
            header == prefix or header.startswith((f"{prefix}:", f"{prefix} "))
            for prefix in _TOOL_RESULT_EMPTY_FINAL_ERROR_PREFIXES
        )
    return False

def _fallback_for_empty_final_after_tools(
    tool_results: list[tuple[str, str]],
) -> str:
    """도구 실행 후 LLM final 텍스트가 비었을 때 사용자 가시 fallback을 만든다.

    도구 결과까지 얻은 턴에서 “한 번 더 말해 달라”로 끝내면 이미 확인한
    사실(특히 빈 검색 결과)을 버리게 된다. 마지막 도구 결과를 보수적으로
    해석해, 빈 결과는 “못 찾음”, 오류 결과는 “확인 중 오류”로 분리한다.
    """
    if not tool_results:
        return _EMPTY_DIRECT_RESPONSE_MESSAGE

    name, content = tool_results[-1]
    stripped = content.strip()
    if not stripped:
        return _TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MESSAGE

    lowered = stripped.lower()
    if _tool_result_looks_like_explicit_error(stripped):
        detail = stripped.splitlines()[0][:240]
        return _TOOL_RESULT_EMPTY_FINAL_ERROR_MESSAGE.format(detail=detail)

    if any(
        marker in lowered or marker in stripped
        for marker in _TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MARKERS
    ):
        return _TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MESSAGE

    detail = stripped.replace("\n", " ")[:240]
    return _TOOL_RESULT_EMPTY_FINAL_GENERIC_MESSAGE.format(
        detail=f"{name}: {detail}",
    )



# BIZ-141 — forced final-answer LLM 호출이 provider 측에서 hang 하면 메시지가
# 영구 침묵하는 사고를 막기 위한 hard timeout. 일반 응답 시간(통상 1~3초) 대비
# 충분히 길고, hang 식별엔 충분히 짧은 경험적 컷.
_FORCED_FINAL_ANSWER_TIMEOUT_SECONDS = 30.0
_FORCED_FINAL_ANSWER_TIMEOUT_MESSAGE = (
    "응답이 지연되어 처리를 종료했습니다. 죄송하지만 한 번 더 말씀해 주세요. "
    "(debug: final-answer LLM 호출이 {timeout:.0f}초 안에 응답하지 않음)"
)

# BIZ-190 — ``agent-browser`` composite chain (``open && wait && text|evaluate``)
# 은 BIZ-187 에서 시스템 프롬프트 가드 + 180s 화이트리스트 타임아웃으로 봉합을
# 시도했지만, 작은 모델(gemini-2.5-flash-lite 등)이 가드 문구를 무시하고 첫 시도
# 부터 같은 chain 을 다시 보내는 패턴이 잔존(2026-05-13 20:19~20:36 KST 시드
# 측정 4건). 가드를 텍스트로만 두면 한 번 잘못된 시도를 못 막고 그 결과가 다시
# tool history 에 누적돼 후속 turn 까지 같은 chain 을 유도한다. 실행 직전에
# subprocess 전에서 차단하고 명확한 단일-호출 안내를 tool result 로 돌려 줌으로
# 써 LLM 이 같은 turn 안에서 정정하도록 한다.
_AGENT_BROWSER_COMPOSITE_BLOCKED_MESSAGE = (
    "Error: composite `agent-browser` chains are blocked. Each agent-browser "
    "step must be a SEPARATE tool call (one `execute_skill` per `open`, `wait`, "
    "`get`/`evaluate` step). For plain page text, prefer `web_fetch` — it already "
    "auto-falls back to a headless browser. If `web_fetch` returned a short body "
    "for this URL, the site is blocking automated fetching; do NOT keep trying "
    "the same URL via agent-browser. Reply to the user that the page cannot be "
    "retrieved instead."
)

# BIZ-190 — 같은 URL 에 대해 ``agent-browser open`` 류 호출을 한 turn 안에서
# 반복하는 패턴(시드 측정 seed-2/3/8/9 의 4건 공통) 의 cap. 첫 시도가 daemon
# busy(os error 35) 등으로 실패하면 LLM 이 같은 명령을 재시도하면서 max-iter
# 까지 누적 소진한다. 첫 호출 1회만 허용하고 두 번째부터는 합성 응답으로
# 즉시 종결.
_AGENT_BROWSER_PER_TURN_CALL_CAP = 2

_AGENT_BROWSER_CAP_EXCEEDED_MESSAGE = (
    "Error: `agent-browser` has already been attempted {count} times in this "
    "turn and is being rate-limited to avoid exhausting the tool loop. If the "
    "page text could not be retrieved by `web_fetch` (which already includes a "
    "headless fallback), the site is blocking automated fetching. Reply to the "
    "user that the page cannot be retrieved rather than retrying with "
    "agent-browser, cli, or another skill."
)

# BIZ-251 — verifier footer 가 "변경 없음" 마커를 명시적으로 부착해야 하는
# tool 이름. 이들 도구는 디스크/외부 상태를 바꿀 *수* 있으므로, 호출 직후
# diff 가 비었다는 사실 자체가 LLM 의 silent-fail/환각 인지에 가치가 있다.
# read-only 도구(web_fetch, skill_docs, cron list, file_read) 는 빈 diff 가
# 정상 경로이므로 footer 를 생략해 토큰을 절약한다.
_FILE_MUTATING_TOOLS = frozenset(
    {"file_write", "file_manage", "execute_skill", "cli"}
)
_UNIFIED_PLAN_UNAVAILABLE_MESSAGE = (
    "요청을 안전하게 계획하지 못했습니다. 잠시 후 같은 요청을 다시 시도해 주세요."
)
_UNIFIED_PLAN_REJECTED_MESSAGE = (
    "현재 실행 범위로는 요청을 안전하게 처리할 수 없습니다. "
    "대상이나 원하는 작업을 더 구체적으로 알려 주세요."
)
_UNIFIED_PLAN_CONFIRMATION_MESSAGE = (
    "이 작업은 실행 전에 명시적인 확인이 필요합니다. "
    "변경할 대상과 원하는 동작을 구체적으로 다시 확인해 주세요."
)


def _planner_native_specs(
    *,
    cron_available: bool,
    browser_handoff_available: bool,
) -> tuple[NativeToolSpec, ...]:
    """정적 native 설명의 slash 구분자를 path-free catalog 문장으로 정규화한다."""
    specs = build_native_tool_registry(
        cron_available=cron_available,
        browser_handoff_available=browser_handoff_available,
        scopes=(
            ToolScope.RUNTIME,
            ToolScope.OPERATOR,
            ToolScope.DEVELOPMENT,
        ),
        operator_gate=True,
    )
    return tuple(
        replace(
            spec,
            definition=replace(
                spec.definition,
                description=spec.definition.description.replace("/", "·"),
            ),
        )
        for spec in specs
    )


class AgentOrchestrator:
    """페르소나 + 스킬 + 대화 이력 + LLM을 조합하는 중앙 오케스트레이터.

    응답 파이프라인 (Native Function Calling):
    1. 시스템 프롬프트 조립 (페르소나 + 스킬 개요 + 도구 사용 안내)
    2. 도구 정의를 LLM API의 tools 파라미터로 전달
    3. LLM이 tool_calls 반환 시 → 도구 실행 → 결과를 메시지에 추가 → 재호출
    4. LLM이 텍스트만 반환 시 → 최종 응답
    5. 대화 저장
    """

    @staticmethod
    def _build_execution_router(
        callbacks: ExecutionCallbacks,
    ) -> ExecutionRouter:
        """PlanGate PASS 이후 사용할 mode→callback 경계를 만든다.

        BIZ-495/BIZ-497부터 primary와 eligible canary가 이 경계를 사용하며,
        각 callback은 같은 immutable plan의 context/allowlist를 유지한다.
        """
        return ExecutionRouter(callbacks)

    def __init__(
        self,
        config_path: str | Path = "config.yaml",
        *,
        metrics: MetricsCollector | None = None,
        structured_logger: StructuredLogger | None = None,
        llm_usage_sink: object | None = None,
    ) -> None:
        self._config_path = Path(config_path)
        # 메트릭 수집기 — 서브프로세스 종료 결과를 누적하여 누수 추세를 모니터링.
        # None이면 메트릭이 기록되지 않으며, 기존 동작과 호환된다.
        self._metrics = metrics
        # 구조화 로거 — RAG 회상(action_type="rag_retrieve")과 같은 관찰 가능성 이벤트를 적재.
        # None이면 로그가 비활성화되며, 기존 동작과 호환된다.
        self._structured_logger = structured_logger

        # --- 정적 설정 로드 (리스타트 시에만 갱신) ---
        agent_config = load_agent_config(config_path)
        persona_config = load_persona_config(config_path)
        daemon_config = load_daemon_config(config_path)
        recipes_config = load_recipes_config(config_path)
        self._asset_selection_config = load_asset_selection_config(config_path)
        self._browser_handoff_config = agent_config.get("browser_handoff", {})
        self._goal_loop_config = agent_config.get("goal_loop", {})
        self._complex_fact_config = agent_config.get("complex_fact_workflow", {})
        # BIZ-426 — 일반 turn 앞단 LLM turn analysis 설정. 기본 활성이며,
        # 비활성/분석 실패 시에는 기존 결정적(keyword) 경로가 fallback 이다.
        self._turn_analysis_config = agent_config.get("turn_analysis", {}) or {}
        # BIZ-493/BIZ-497 — default off. shadow는 sampled background 관측만,
        # canary는 결정적 read-only cohort, primary는 전체 검증 plan을 사용한다.
        self._unified_turn_planner_config = (
            agent_config.get("unified_turn_planner", {}) or {}
        )
        self._runtime_paths_prompt = self._format_runtime_paths_for_prompt(
            self._config_path,
            persona_config=persona_config,
            agent_config=agent_config,
            daemon_config=daemon_config,
            recipes_config=recipes_config,
        )

        # BIZ-202/BIZ-313: 봇과 데몬이 같은 configured recipe directory를 보도록
        # config 한 곳에서 결정한다. 기본은 ``~/.simpleclaw-agent/default/recipes``.
        # BIZ-410: 일반 runtime ``cli``/``file_write``가 이 디렉터리에 직접 쓰는
        # 경로는 막고, 설치는 operator-gated ``recipe_generate``만 담당한다.
        self._recipes_dir = str(
            Path(recipes_config["dir"]).expanduser()
        )
        # 디렉터리는 부팅 시 자동 생성 — 없으면 봇이 새 레시피 작성을 시도하기 전에
        # mkdir 도구를 명령 받아야 하는 흐름이 되어 사용자 흐름이 깨진다.
        Path(self._recipes_dir).mkdir(parents=True, exist_ok=True)

        self._history_limit = agent_config["history_limit"]

        # 페르소나·스킬 설정값 보관 — _reload_dynamic_files()에서 참조
        self._persona_config = persona_config
        skills_config = self._load_skills_config()
        self._skills_config = skills_config
        self._skill_learning_config = load_skills_learning_config(config_path)
        # BIZ-428 — recipe learning은 skill learning과 별도 config gate.
        # 기본 disabled이며, 켜도 후보는 pending 큐에만 쌓인다 (approval-only).
        self._recipe_learning_config = load_recipe_learning_config(config_path)

        # BIZ-424: MCP config는 부팅 시 로드하되, 서버 연결(외부 subprocess 실행)은
        # 첫 turn 준비 시 _ensure_mcp_connected()가 lazy one-shot으로 수행한다.
        self._mcp_config = load_mcp_config(config_path)
        self._mcp_manager: MCPManager | None = None
        self._mcp_connected = False

        # Cron scheduler — build_tool_definitions에서 참조하므로 리로드 전에 초기화
        self._cron_scheduler: CronScheduler | None = None

        # BIZ-442 — LaunchAgent restart drain/quiesce. deploy script 가 같은
        # state 파일 경로로 drain 을 요청하면 process_message/process_cron_message
        # 진입 시 새 intake 를 거절한다. 이미 시작된 turn 은 operation 카운터로
        # 추적되어 완료까지 이어진다.
        drain_config = daemon_config.get("drain", {}) or {}
        self._drain_controller = DrainController(
            Path(
                str(
                    drain_config.get("state_file")
                    or "~/.simpleclaw-agent/default/drain_state.json"
                )
            ).expanduser(),
            default_timeout=float(drain_config.get("default_timeout", 120)),
        )

        # 초기 로드: 페르소나·스킬 파일을 디스크에서 읽어 캐시 필드 채움
        self._reload_dynamic_files()

        # LLM router
        self._router = create_router(config_path, usage_sink=llm_usage_sink)
        self._router.validate_backend_name(
            daemon_config.get("dreaming", {}).get("model"),
            field_path="daemon.dreaming.model",
        )

        # Conversation store
        # BIZ-313: db_path 가 ``~/.simpleclaw-agent/default/...`` 형태로 오므로 expanduser 로 풀어준다.
        db_path = Path(agent_config["db_path"]).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conversation_db_path = db_path
        self._store = ConversationStore(db_path)

        # 시맨틱 메모리(RAG, spec 005 Phase 2) 설정 로드
        # enabled=False가 기본 — sentence-transformers 미설치 환경에서도 무난하게 동작
        memory_config = load_memory_config(config_path)
        rag_cfg = memory_config["rag"]
        self._rag_enabled: bool = bool(rag_cfg["enabled"])
        self._rag_model_name: str = str(rag_cfg["model"])
        self._rag_top_k: int = int(rag_cfg["top_k"])
        self._rag_threshold: float = float(rag_cfg["similarity_threshold"])
        long_term_cfg = memory_config.get("long_term", {})
        self._long_term_enabled: bool = bool(long_term_cfg.get("enabled", True))
        self._long_term_top_k: int = int(long_term_cfg.get("top_k", 3))
        self._long_term_min_confidence: float = float(
            long_term_cfg.get("min_confidence", 0.7)
        )
        self._long_term_promotion_threshold: int = int(
            long_term_cfg.get("promotion_threshold", 3)
        )
        self._long_term_context_budget_chars: int = int(
            long_term_cfg.get("context_budget_chars", 1600)
        )
        self._long_term_per_item_chars: int = int(
            long_term_cfg.get("per_item_chars", 400)
        )
        self._long_term_insights_file = Path(
            str(long_term_cfg.get("insights_file", "~/.simpleclaw-agent/default/insights.jsonl"))
        ).expanduser()
        self._long_term_active_projects_file = Path(
            str(long_term_cfg.get("active_projects_file", "~/.simpleclaw-agent/default/active_projects.jsonl"))
        ).expanduser()
        self._long_term_active_projects_window_days: int = int(
            long_term_cfg.get("active_projects_window_days", 7)
        )
        self._embedding_service: EmbeddingService | None = (
            EmbeddingService(
                model_name=self._rag_model_name,
                enabled=self._rag_enabled,
            )
            if self._rag_enabled
            else None
        )
        # BIZ-393: Agent Study Wiki 회수기 — 질문 시 관련 배경지식 블록을 system
        # prompt 에 주입한다. 대화 RAG/장기기억 회수와 독립적으로 실패 격리된다.
        # 기능 플래그(study.enabled + study.retrieval.enabled)가 모두 켜져야 동작.
        study_config = load_study_config(config_path)
        study_retrieval_cfg = study_config.get("retrieval", {}) or {}
        study_wiki_dir = study_config.get("wiki_dir") or "~/.simpleclaw-agent/default/agent_wiki"
        self._study_retriever = StudyRetriever(
            StudyRetrievalConfig(
                enabled=bool(study_config.get("enabled"))
                and bool(study_retrieval_cfg.get("enabled")),
                wiki_dir=Path(str(study_wiki_dir)).expanduser(),
                top_k=int(study_retrieval_cfg.get("top_k", 4)),
                max_context_chars=int(study_retrieval_cfg.get("max_context_chars", 5000)),
            )
        )
        self._context_retrieval = ContextRetrievalService(
            store=self._store,
            embedding_service=self._embedding_service,
            structured_logger=self._structured_logger,
            study_retriever=self._study_retriever,
            config=ContextRetrievalConfig(
                rag_top_k=self._rag_top_k,
                rag_threshold=self._rag_threshold,
                long_term_enabled=self._long_term_enabled,
                long_term_top_k=self._long_term_top_k,
                long_term_min_confidence=self._long_term_min_confidence,
                long_term_promotion_threshold=self._long_term_promotion_threshold,
                long_term_context_budget_chars=self._long_term_context_budget_chars,
                long_term_per_item_chars=self._long_term_per_item_chars,
                long_term_insights_file=self._long_term_insights_file,
                long_term_active_projects_file=self._long_term_active_projects_file,
                long_term_active_projects_window_days=self._long_term_active_projects_window_days,
            ),
        )
        # 백그라운드 임베딩 태스크 강한 참조 — GC로 인한 task drop 방지
        self._background_tasks: set = set()

        # Skill execution timeout
        self._skill_timeout = skills_config.get("execution_timeout", 60)

        # Security: command guard + env filtering
        security_config = load_security_config(self._config_path)
        guard_config = security_config.get("command_guard", {})
        self._command_guard = CommandGuard(
            allowlist=guard_config.get("allowlist", []),
            enabled=guard_config.get("enabled", True),
        )
        self._env_passthrough = security_config.get("env_passthrough", [])
        _inject_env_secret_refs(security_config.get("env_secret_refs", {}))
        self._skill_env_overrides = load_skill_env_secret_refs(
            security_config.get("skill_env_secret_refs", {})
        )

        # Multi-turn tool execution budget
        self._max_tool_iterations = agent_config.get("max_tool_iterations", 15)

        # Workspace directory for skill file output.
        # BIZ-313: 기본 위치는 런타임 디렉터리(`~/.simpleclaw-agent/default/workspace`) — 저장소
        # working tree 안에 임시 파일이 쌓이지 않도록.
        self._workspace_dir = Path(
            agent_config.get("workspace_dir", "~/.simpleclaw-agent/default/workspace")
        ).expanduser()
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

        # BIZ-162: web_fetch 의 헤드리스 폴백이 nohup PATH 축소 환경에서도 동작하도록
        # 운영자 명시 경로를 config 에서 읽어 핸들러에 주입한다. None 이면 builtin_tools
        # 의 ``_resolve_agent_browser`` 가 PATH + 알려진 후보 경로 자동 탐색.
        web_fetch_cfg = agent_config.get("web_fetch", {}) or {}
        self._headless_binary: str | None = web_fetch_cfg.get("headless_binary")

        # BIZ-187: agent-browser composite chain (예: ``agent-browser open ... &&
        # agent-browser wait --load load && agent-browser text``) 은 SPA(wikidocs.net,
        # npmjs.com 등)에서 60s 의 기본 ``skills.execution_timeout`` 을 정기적으로
        # 넘어 ``Skill command timed out`` 으로 죽고, 모델이 tool loop 안에서 같은
        # composite 를 재시도하면서 ``max_tool_iterations`` 까지 누적 소진되는
        # 사고 다발(2026-05-13 BIZ-182 / BIZ-183 시드 측정). composite 한 호출의
        # 실제 wall time 은 보통 60~120s 이므로 ``agent-browser`` 명령에만 별도의
        # 더 긴 타임아웃을 화이트리스트로 적용한다. 기본 180s 는 시드 측정에서
        # 관찰된 최악(SPA 5건) 의 약 1.5배. None 으로 두면 기본 60s 유지.
        self._agent_browser_timeout: int = int(
            web_fetch_cfg.get("agent_browser_command_timeout", 180)
        )

        # BIZ-251: per-turn file mutation verifier footer.
        # 워크스페이스는 재귀 walk, 페르소나 dir 은 명시 파일 화이트리스트
        # (SOUL.md / AGENT.md / USER.md / MEMORY.md) 만 추적해 SQLite/dreaming 부산물이
        # footer 노이즈로 새는 것을 차단한다. ``~/.simpleclaw-agent/default`` 가 persona
        # local_dir 인 BIZ-313 경로 가정 — 화이트리스트면 overlap 도 안전.
        persona_local = Path(
            self._persona_config["local_dir"]
        ).expanduser()
        persona_filenames = tuple(
            f["name"] for f in self._persona_config["files"] if "name" in f
        )
        self._mutation_tracker = FileMutationTracker(
            [
                TrackedRoot(".agent/workspace", self._workspace_dir),
                TrackedRoot(".agent", persona_local, files=persona_filenames),
            ]
        )

        # BIZ-260 — clarify 도구의 pending 요청 레지스트리. chat_id → ClarifyRequest.
        # ``_dispatch_tool_call`` 이 채워 넣고, 채널이 ``pop_pending_clarify`` 로 회수.
        # 동일 chat 안에서는 한 번에 하나만 대기 — 새 clarify 가 호출되면 덮어쓴다.
        self._pending_clarify: dict[int, ClarifyRequest] = {}

        proactive_config = daemon_config.get("proactive", {}) or {}
        conversation_config = (
            proactive_config.get("extractors", {}).get("conversation_end", {})
            if isinstance(proactive_config.get("extractors", {}), dict)
            else {}
        )
        self._conversation_end_detector = ConversationEndDetector(
            store=OpportunityStore(proactive_config.get("store_file", "~/.simpleclaw-agent/default/proactive_opportunities.jsonl")),
            enabled=bool(proactive_config.get("enabled", False))
            and bool(conversation_config.get("enabled", False)),
            max_latency_ms=int(conversation_config.get("max_latency_ms", 50) or 50),
        )

        logger.info(
            "AgentOrchestrator initialized: persona=%d chars, skills=%d, backend=%s",
            len(self._persona_prompt),
            len(self._skills),
            self._router.get_default_backend(),
        )

    def _reload_dynamic_files(self) -> None:
        """페르소나·스킬 파일을 디스크에서 다시 읽어 캐시 필드를 갱신한다 (hot-reload).

        호출 시점: __init__() 초기화 + 매 메시지 진입 시 1회.
        tool loop 내부에서는 호출하지 않아 불필요한 I/O를 방지한다.
        """
        # --- 페르소나 리로드 (SOUL.md, AGENT.md, USER.md, MEMORY.md) ---
        persona_files = resolve_persona_files(
            local_dir=self._persona_config["local_dir"],
            global_dir=self._persona_config["global_dir"],
        )
        assembly = assemble_prompt(
            persona_files, self._persona_config["token_budget"]
        )
        self._persona_prompt = assembly.assembled_text or ""

        # --- 스킬 리로드 (.agent/skills, ~/.agents/skills) ---
        self._skills = discover_skills(
            local_dir=self._skills_config.get("local_dir", ".agent/skills"),
            global_dir=self._skills_config.get("global_dir", "~/.agents/skills"),
        )
        # 이름 기반 조회용 딕셔너리 (fuzzy match에서도 사용)
        # BIZ-383: realtime-lookup-skill 은 오케스트레이터가 LLM 루프 밖에서 직접
        # 실행하는 내부 evidence 스킬이다. _resolve_skill_name 으로 내부 실행은 가능해야
        # 하므로 by-name 매핑에는 남기되, LLM callable 목록/프롬프트에서는 제외한다.
        self._skills_by_name = {s.name: s for s in self._skills}
        # 시스템 프롬프트용 스킬 목록 (내부 evidence 스킬 제외)
        self._skills_prompt = self._format_skills_for_prompt(self._exposable_skills())

        # --- 레시피 리로드 (~/.simpleclaw-agent/default/recipes) ---
        # selector manifest와 선택 레시피 컨텍스트가 운영 recipe 디렉터리 변경을
        # 재시작 없이 반영하도록 매 메시지 진입 시 스캔한다. 실패는 selector 보조
        # 경로만 비우고 main 응답은 기존 스킬 경로로 계속 진행한다.
        try:
            self._recipes = discover_recipes(self._recipes_dir)
        except Exception as exc:
            logger.warning("Recipe discovery failed during dynamic reload: %s", exc)
            self._recipes = []

    def set_cron_scheduler(self, scheduler: CronScheduler) -> None:
        """CronScheduler를 주입하여 cron 도구를 활성화한다."""
        self._cron_scheduler = scheduler
        logger.info("CronScheduler injected into AgentOrchestrator.")

    @property
    def drain_controller(self) -> DrainController:
        """drain 상태/active operation 컨트롤러 — 채널·admin health 가 공유한다."""
        return self._drain_controller

    def set_drain_controller(self, controller: DrainController) -> None:
        """DrainController 를 교체 주입한다 (테스트/커스텀 배선용)."""
        self._drain_controller = controller

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def deferred_primary_delivery_required(self) -> bool:
        """V4 actual-response rollout은 durable send 전 streaming을 금지한다."""
        return (
            self._unified_turn_planner_config.get("architecture")
            == "langgraph_v4"
            and self._unified_turn_planner_config.get("mode")
            in {"primary", "read_only_canary"}
        )

    async def deliver_primary_response(
        self,
        response: PrimaryResponseText,
        destination_ref: str,
        sender,
    ) -> PrimaryDeliveryOutcomeV1:
        """Telegram actual send receipt 뒤 delivered assistant만 저장한다."""
        v4 = self._unified_turn_planner_config.get("langgraph_v4", {})
        if not isinstance(v4, dict):
            raise TypeError("langgraph_v4 delivery configuration is missing")
        checkpoint = v4.get("checkpoint", {})
        if not isinstance(checkpoint, dict):
            raise TypeError("langgraph_v4 checkpoint configuration is missing")
        raw_path = str(checkpoint.get("path") or "")
        checkpoint_path = (
            resolve_checkpoint_path(raw_path)
            if raw_path
            else resolve_checkpoint_path()
        )
        journal_path = checkpoint_path.with_name(
            f"{checkpoint_path.name}.deliveries.sqlite3"
        )
        return await PrimaryDeliveryCoordinator(
            journal_path=journal_path,
            conversation_store=self._store,
        ).deliver_telegram(
            response,
            destination_ref=destination_ref,
            sender=sender,
        )

    async def prewarm_embedding(self) -> bool:
        """Telegram intake 전에 RAG 모델을 worker thread에서 준비한다.

        disabled/load failure/예상 밖 예외는 모두 ``False``로 축약해 startup을
        fail-open으로 유지한다. 구조화 이벤트에는 model/status/duration과
        원문 미포함 표식만 기록한다.
        """
        started_at = time.perf_counter()
        service = self._embedding_service
        model_name = (
            service.model_name if service is not None else self._rag_model_name
        )
        status = "disabled"
        ready = False

        if service is not None:
            try:
                ready = await asyncio.to_thread(service.prewarm)
                status = "success" if ready else "failure"
            except Exception as exc:
                status = "failure"
                logger.warning(
                    "Embedding pre-warm boundary failed: model=%s error_type=%s",
                    model_name,
                    type(exc).__name__,
                )

        duration_ms = (time.perf_counter() - started_at) * 1000.0
        logger.info(
            "Embedding pre-warm startup result: status=%s model=%s duration_ms=%.2f",
            status,
            model_name,
            duration_ms,
        )
        if self._structured_logger is not None:
            try:
                self._structured_logger.log(
                    action_type="embedding_prewarm",
                    status=status,
                    duration_ms=duration_ms,
                    model=model_name,
                    raw_text_included=False,
                )
            except Exception as exc:
                logger.warning(
                    "Embedding pre-warm structured log failed (error_type=%s)",
                    type(exc).__name__,
                )
        return ready

    async def process_cron_action(self, text: str) -> CronActionResult:
        """크론 잡 메시지를 격리 처리하고 의미 상태를 보존해 반환한다.

        대화 이력을 불러오지 않고 공유 대화 DB에 메시지를 저장하지 않는다.
        진입점이므로 trace_id를 새로 발급해 호출 체인 전체로 전파한다.

        BIZ-442: drain 중이면 실행을 건너뛰고 skip 사유를 반환한다 — cron 은
        재시작 후 다음 스케줄에서 다시 실행되므로 큐잉 없이 skip 이 안전하다.
        이미 시작된 실행은 operation 카운터로 추적되어 완료까지 이어진다.
        """
        if self._drain_controller.is_draining():
            logger.info("Drain active — skipping cron message intake.")
            return CronActionResult(text=DRAIN_CRON_SKIPPED_MESSAGE)
        with trace_scope() as trace_id, self._drain_controller.operation("cron_turn"):
            logger.info("Cron message received: trace_id=%s", trace_id)
            self._reload_dynamic_files()
            result = await self._run_tool_loop_result(
                text,
                isolated=True,
                allow_cron_mutation=False,
            )
            failure_kind = None
            if result.failure_kind:
                try:
                    failure_kind = CronFailureKind(result.failure_kind)
                except ValueError:
                    failure_kind = CronFailureKind.ACTION_FAILED
            return CronActionResult(
                text=result.text,
                success=result.success,
                failure_kind=failure_kind,
            )

    async def process_cron_message(self, text: str) -> str:
        """기존 호출자를 위한 cron text API compatibility 경로.

        테스트·플러그인이 ``_tool_loop`` wrapper를 교체하는 기존 확장 지점을
        보존하고, 스케줄러만 ``process_cron_action`` 구조화 API를 사용한다.
        """
        if self._drain_controller.is_draining():
            logger.info("Drain active — skipping cron message intake.")
            return DRAIN_CRON_SKIPPED_MESSAGE
        with trace_scope() as trace_id, self._drain_controller.operation("cron_turn"):
            logger.info("Cron message received: trace_id=%s", trace_id)
            self._reload_dynamic_files()
            return await self._tool_loop(
                text,
                isolated=True,
                allow_cron_mutation=False,
            )

    async def process_message(
        self,
        text: str,
        user_id: int,
        chat_id: int,
        *,
        thread_id: int | str | None = None,
        attachments: list[MultimodalAttachment] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
        on_progress: ProgressCallback | None = None,
        operator_tools: bool = False,
        request_id: str | None = None,
    ) -> str:
        """drain 게이트를 거쳐 메시지를 처리한다 (BIZ-442).

        drain 중이면 새 intake 를 즉시 "점검 중" 응답으로 거절하고 대화 DB 에는
        저장하지 않는다 — 유지보수 자동 응답이 dreaming 코퍼스를 오염시키지
        않게 한다. drain 이 아니면 operation 카운터로 turn 을 추적하며 실제
        파이프라인(``_process_message_impl``)에 위임한다. 게이트는 진입 시
        1회만 평가하므로 drain 요청 이전에 시작된 turn 은 끊기지 않는다.
        """
        if self._drain_controller.is_draining():
            logger.info(
                "Drain active — rejecting new message intake: user=%d chat=%d",
                user_id,
                chat_id,
            )
            return DRAIN_MAINTENANCE_MESSAGE
        session_key = SessionIdentity(
            channel="telegram",
            user_id=str(user_id),
            chat_id=str(chat_id),
            thread_id="" if thread_id is None else str(thread_id),
        ).stable_key()
        turn = TurnExecutionState.create(
            session_key=session_key,
            original_text=text,
            turn_id=request_id,
        )
        session_token = current_session_key_var.set(session_key)
        turn_token = current_turn_id_var.set(turn.turn_id)
        try:
            with self._drain_controller.operation("message_turn"):
                return await self._process_message_impl(
                    text,
                    user_id,
                    chat_id,
                    attachments=attachments,
                    on_text_delta=on_text_delta,
                    on_progress=on_progress,
                    operator_tools=operator_tools,
                    turn=turn,
                )
        finally:
            current_turn_id_var.reset(turn_token)
            current_session_key_var.reset(session_token)

    async def _process_message_impl(
        self,
        text: str,
        user_id: int,
        chat_id: int,
        *,
        attachments: list[MultimodalAttachment] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
        on_progress: ProgressCallback | None = None,
        operator_tools: bool = False,
        turn: TurnExecutionState | None = None,
    ) -> str:
        """수신 메시지를 Native Function Calling 파이프라인으로 처리한다.

        진입점이므로 trace_id를 새로 발급해 ``contextvars``로 호출 체인
        (도구 실행, RAG 회상, 백그라운드 임베딩, 서브에이전트/스킬 등) 전체에
        전파한다. ``trace_scope``는 ``with`` 블록 종료 시 이전 trace_id를
        복원하므로 동일 프로세스에서 후속 메시지가 깨끗한 컨텍스트로 시작된다.

        BIZ-259: ``on_text_delta`` 콜백이 주어지면 ``_tool_loop`` 가 LLM 응답 텍스트
        델타를 콜백으로 흘려보낸다. ``/cron``, ``/recipe-*`` 명령어 분기는 즉답 분기
        이므로 콜백을 무시한다 — 부분 결과로 알림 트리거되는 사고 방지(``final_only``).
        """
        if turn is None:
            turn = TurnExecutionState.create(
                session_key=SessionIdentity(
                    channel="telegram",
                    user_id=str(user_id),
                    chat_id=str(chat_id),
                ).stable_key(),
                original_text=text,
            )
        with trace_scope() as trace_id:
            logger.info(
                "Message received: trace_id=%s user=%d chat=%d",
                trace_id, user_id, chat_id,
            )
            self._reload_dynamic_files()
            self._store.clear_pending_interaction(turn.session_key)

            undo_command, undo_turns = _parse_undo_command(text)
            if undo_command:
                if undo_turns is None:
                    return _UNDO_USAGE_MESSAGE
                hidden_turns = self._store.hide_recent_user_turns(
                    undo_turns,
                    session_key=turn.session_key,
                )
                if hidden_turns == 0:
                    return _UNDO_NO_TURNS_MESSAGE
                return _UNDO_SUCCESS_MESSAGE.format(turns=hidden_turns)

            # BIZ-260 — clarify 도구가 발생시킬 ClarifyRequest 를 chat_id 키로
            # 적재할 수 있도록 contextvar 에 chat_id 를 매단다. tool 핸들러는
            # 자기 시그니처를 바꾸지 않고도 contextvar 로 chat_id 를 얻는다.
            clarify_token = clarify_chat_id_var.set(chat_id)
            try:
                # /goal 명령어 확인 — recipe dispatch 보다 먼저 처리해 `/goal` 레시피 오인 방지.
                goal_command = parse_goal_command(text)
                if goal_command is not None:
                    if goal_command.action in {"help", "unsupported"}:
                        self._save_turn(
                            text,
                            goal_command.message,
                            channel=f"{CHANNEL_GOAL_PREFIX}admin",
                        )
                        return goal_command.message
                    if not self._goal_loop_config.get("enabled", True):
                        disabled = "⚠️ /goal 기능이 현재 비활성화되어 있습니다."
                        self._save_turn(
                            text, disabled, channel=f"{CHANNEL_GOAL_PREFIX}disabled"
                        )
                        return disabled

                    cfg = GoalLoopConfig(
                        max_rounds=int(self._goal_loop_config.get("max_rounds", 3)),
                        judge_max_tokens=int(
                            self._goal_loop_config.get("judge_max_tokens", 768)
                        ),
                        max_answer_chars_for_judge=int(
                            self._goal_loop_config.get(
                                "max_answer_chars_for_judge", 6000
                            )
                        ),
                    )

                    async def run_goal_round(prompt: str, **kwargs):
                        kwargs.pop("allow_cron_mutation", None)
                        kwargs["on_text_delta"] = None
                        return await self._run_tool_loop_result(
                            prompt,
                            isolated=False,
                            attachments=attachments,
                            operator_tools=operator_tools,
                            allow_cron_mutation=False,
                            **kwargs,
                        )

                    runner = GoalLoopRunner(
                        run_round=run_goal_round,
                        judge_send=self._router.send,
                        config=cfg,
                    )
                    goal_result = await runner.run(
                        goal_command.objective,
                        on_progress=on_progress,
                    )
                    self._save_turn(
                        text,
                        goal_result.final_text,
                        channel=f"{CHANNEL_GOAL_PREFIX}{goal_result.status}",
                    )
                    return goal_result.final_text

                # /cron 명령어 확인
                cron_result = try_cron_command(text, self._cron_scheduler)
                if cron_result is not None:
                    # BIZ-76 — cron 관리 명령(/cron list 등) 응답은 자동 트리거
                    # 카테고리로 묶어 dreaming 의 사용자 관심 추론에서 분리한다.
                    self._save_turn(
                        text, cron_result, channel=CHANNEL_CRON_ADMIN,
                    )
                    return cron_result

                # /recipe-name 명령어 확인 (e.g. /ai-report)
                # BIZ-202: 레시피 디렉터리는 config 기반 — 봇/데몬 양쪽이 같은 절대 경로를 본다.
                recipe_outcome = await try_recipe_command(
                    text,
                    self._tool_loop,
                    recipes_dir=self._recipes_dir,
                    on_progress=on_progress,
                )
                if recipe_outcome is not None:
                    recipe_result, recipe_name = recipe_outcome
                    # BIZ-76 — 레시피 산출물은 사용자 발화가 아니라 자동/명령 트리거
                    # 결과이므로 ``recipe:<name>`` 채널로 태깅한다. dreaming 코퍼스
                    # 로더가 이 prefix 를 보고 분리 또는 가중치 다운한다.
                    self._save_turn(
                        text,
                        recipe_result,
                        channel=f"{CHANNEL_RECIPE_PREFIX}{recipe_name}",
                    )
                    return recipe_result

                # BIZ-426 — 일반 turn 의 primary path 는 LLM turn analysis 다.
                # follow-up 정규화/clarify/intents/domains/route 를 LLM structured
                # JSON 판단 하나로 결정한다. 분석 비활성 또는 provider 장애 시에만
                # 기존 결정적(keyword) 경로(BIZ-425 TurnFrame + response_router)로
                # 내려간다. V4 actual-response ingress는 stable request identity가
                # 정해진 이 경계에서 실행보다 먼저 user row를 durable 저장한다.
                # replay된 현재 요청은 planner context에서 제외해 첫 실행과 동일한
                # context 후보를 유지한다.
                planner_candidate_limit = int(
                    self._unified_turn_planner_config.get(
                        "context_candidate_limit",
                        8,
                    )
                )
                recent_rows = self._store.get_recent_with_ids(
                    limit=max(self._history_limit, planner_candidate_limit),
                    session_key=turn.session_key,
                )
                if self.deferred_primary_delivery_required():
                    recent_rows = [
                        row
                        for row in recent_rows
                        if row[1].turn_id != turn.turn_id
                    ]
                    inbound_id, created = self._store.save_inbound_once(
                        ConversationMessage(
                            role=MessageRole.USER,
                            content=text,
                            channel="telegram",
                        ),
                        session_key=turn.session_key,
                        request_id=turn.turn_id,
                    )
                    if created:
                        self._schedule_embedding(inbound_id, text)
                rollout_mode = str(
                    self._unified_turn_planner_config.get("mode", "primary")
                )
                v4_read_only_canary = (
                    self._unified_turn_planner_config.get("architecture")
                    == "langgraph_v4"
                    and rollout_mode == "read_only_canary"
                    and _deterministic_rollout_sample(
                        user_id=user_id,
                        chat_id=chat_id,
                        sample_rate=float(
                            self._unified_turn_planner_config.get(
                                "sample_rate",
                                0.0,
                            )
                        ),
                    )
                )
                tool_loop_result = await self._run_unified_turn_planner_primary(
                    text,
                    recent_rows=recent_rows,
                    attachments=attachments,
                    on_text_delta=on_text_delta,
                    on_progress=on_progress,
                    operator_tools=operator_tools,
                    canary_read_only=v4_read_only_canary,
                    turn=turn,
                )
                response_text = tool_loop_result.text

                if (
                    turn.plan is not None
                    and tool_loop_result.selected_route is not None
                ):
                    self._schedule_unified_turn_planner_shadow(
                        text,
                        recent_rows=recent_rows,
                        plan=turn.plan,
                        legacy=LegacyRunTelemetryV1(
                            selected_route=tool_loop_result.selected_route,
                            terminal_outcome=(
                                TerminalOutcome.COMPLETED
                                if tool_loop_result.success
                                else TerminalOutcome.FAILED
                            ),
                            model_calls=tool_loop_result.model_calls,
                            tokens=tool_loop_result.tokens,
                        ),
                        request_id=turn.turn_id,
                        session_key=turn.session_key,
                    )

                metadata = tool_loop_result.primary_delivery
                if (
                    metadata is None
                    and self.deferred_primary_delivery_required()
                ):
                    metadata = PrimaryDeliveryMetadataV1(
                        request_id=turn.turn_id,
                        artifact_id=canonical_artifact_id(
                            turn.turn_id,
                            response_text,
                        ),
                        artifact_hash=canonical_artifact_content_hash(
                            response_text
                        ),
                        session_key=turn.session_key,
                    )
                if metadata is not None:
                    return PrimaryResponseText(response_text, metadata)

                msg_ids = self._save_turn(text, response_text)
                await self._capture_conversation_end_opportunity(
                    text, response_text, list(msg_ids)
                )
                await self._capture_skill_learning_candidate(
                    text, response_text, tool_loop_result, list(msg_ids)
                )
                await self._capture_recipe_learning_candidate(
                    text, response_text, tool_loop_result, list(msg_ids)
                )
                return response_text

            finally:
                clarify_chat_id_var.reset(clarify_token)

    async def _run_unified_turn_planner_primary(
        self,
        text: str,
        *,
        recent_rows: list[tuple[int, ConversationMessage]],
        attachments: list[MultimodalAttachment] | None,
        on_text_delta: TextDeltaCallback | None,
        on_progress: ProgressCallback | None,
        operator_tools: bool,
        canary_read_only: bool = False,
        turn: TurnExecutionState | None = None,
    ) -> ToolLoopResult:
        """Planner→PlanGate→ExecutionRouter를 ordinary primary turn에 한 번 적용한다.

        Planner와 context 선택은 controller loop 밖에서 고정한다. gate가 실행을
        허용하지 않으면 keyword/legacy semantic fallback으로 넓히지 않고 짧은
        fail-closed 응답을 반환한다.
        """
        config = self._unified_turn_planner_config
        if turn is None:
            turn = TurnExecutionState.create(
                session_key=current_session_key_var.get(),
                original_text=text,
            )
        candidate_limit = int(config.get("context_candidate_limit", 8))
        candidates = ContextCandidateBuilder(
            max_turns=candidate_limit,
            max_chars=int(config.get("context_candidate_max_chars", 6000)),
        ).build(recent_rows[-candidate_limit:])
        planner_skills = tuple(self._exposable_skills())
        planner_recipes = tuple(getattr(self, "_recipes", ()))
        catalog = build_planner_catalog(
            skills=planner_skills,
            recipes=planner_recipes,
            native_specs=_planner_native_specs(
                cron_available=self._cron_scheduler is not None,
                browser_handoff_available=bool(
                    self._browser_handoff_config.get("enabled", False)
                ),
            ),
        )
        usage_router = PlannerUsageCaptureRouter(self._router)
        try:
            plan = await plan_turn_with_llm(
                text,
                candidates=candidates,
                catalog=catalog,
                router=usage_router,
                max_tokens=int(config.get("max_tokens", 2048)),
                reasoning=config.get("reasoning"),
                examples_prompt_name=str(
                    config.get(
                        "examples_prompt",
                        "unified_turn_planner_examples",
                    )
                ),
            )
        except Exception as exc:
            logger.warning(
                "Unified TurnPlanner primary failed (error_type=%s)",
                type(exc).__name__,
            )
            self._record_unified_rollout_path(
                path="fail_closed",
                reason="planner_unavailable",
            )
            turn.transition(TurnPhase.FAILED)
            return ToolLoopResult(
                _UNIFIED_PLAN_UNAVAILABLE_MESSAGE,
                success=False,
            )

        if plan.original_text != text:
            logger.warning(
                "Unified planner returned a mismatched original_text; "
                "restoring the controller-owned request text"
            )
            plan = replace(plan, original_text=text)
        turn.attach_plan(plan)
        gate_result = PlanGate(
            selected_context_max_turns=int(
                config.get("selected_context_max_turns", 3)
            ),
            selected_context_max_chars=int(
                config.get("selected_context_max_chars", 2400)
            ),
        ).evaluate(plan, candidates=candidates, catalog=catalog)
        turn.attach_gate_result(gate_result)
        logger.info(
            "Unified TurnPlanner primary gated: status=%s mode=%s "
            "selected_turns=%d tools=%d assets=%d",
            gate_result.status.value,
            plan.execution.mode.value,
            len(plan.context.selected_turn_ids),
            len(plan.execution.allowed_tools),
            len(plan.execution.allowed_assets),
        )
        if gate_result.status in {GateStatus.REPAIR, GateStatus.REJECT}:
            logger.warning(
                "Unified plan blocked: status=%s violation_codes=%s",
                gate_result.status.value,
                [violation.code for violation in gate_result.violations],
            )
            self._record_unified_rollout_path(
                path="fail_closed",
                reason=f"gate_{gate_result.status.value}",
                execution_mode=plan.execution.mode.value,
                gate_status=gate_result.status.value,
            )
            turn.transition(TurnPhase.REJECTED)
            return ToolLoopResult(_UNIFIED_PLAN_REJECTED_MESSAGE, success=False)
        if gate_result.status is GateStatus.CONFIRMATION_REQUIRED:
            self._record_unified_rollout_path(
                path="fail_closed",
                reason="confirmation_required",
                execution_mode=plan.execution.mode.value,
                gate_status=gate_result.status.value,
            )
            self._store.save_session_state(
                SessionState(
                    key=turn.session_key,
                    pending=PendingInteraction(
                        kind="confirmation",
                        payload={
                            "question": _UNIFIED_PLAN_CONFIRMATION_MESSAGE,
                            "execution_mode": plan.execution.mode.value,
                        },
                    ),
                )
            )
            turn.transition(TurnPhase.WAITING_FOR_USER)
            return ToolLoopResult(_UNIFIED_PLAN_CONFIRMATION_MESSAGE, success=False)
        if gate_result.status is GateStatus.CLARIFY:
            self._record_unified_rollout_path(
                path="primary",
                reason="clarify",
                execution_mode=plan.execution.mode.value,
                gate_status=gate_result.status.value,
            )
            turn.transition(TurnPhase.WAITING_FOR_USER)
            return ToolLoopResult(self._render_unified_clarification(plan))

        effective_plan = gate_result.effective_plan
        if effective_plan is None:
            self._record_unified_rollout_path(
                path="fail_closed",
                reason="missing_effective_plan",
                execution_mode=plan.execution.mode.value,
                gate_status=gate_result.status.value,
            )
            turn.transition(TurnPhase.REJECTED)
            return ToolLoopResult(_UNIFIED_PLAN_REJECTED_MESSAGE, success=False)
        logger.info(
            "Unified TurnPlanner effective plan: request_id=%s "
            "original_mode=%s original_asset=%s original_assets=%d "
            "effective_mode=%s effective_asset=%s effective_assets=%d",
            turn.turn_id,
            plan.execution.mode.value,
            _selected_asset_identity(plan),
            len(plan.execution.allowed_assets),
            effective_plan.execution.mode.value,
            _selected_asset_identity(effective_plan),
            len(effective_plan.execution.allowed_assets),
        )
        architecture = str(config.get("architecture", "legacy_v2"))
        rollout_mode = str(config.get("mode", "primary"))
        run_v4 = architecture == "langgraph_v4" and (
            rollout_mode == "primary" or canary_read_only
        )
        # V4 connected runner는 exact asset executor다. 일반 대화는 기존 direct
        # 경로가 소유하며 primary rollout 때문에 사용자 실패로 바뀌지 않는다.
        if run_v4 and _is_direct_without_asset(effective_plan):
            run_v4 = False
        if run_v4 and not _canary_read_only_eligible(effective_plan, catalog):
            self._record_unified_rollout_path(
                path="fail_closed",
                reason="v4_read_only_ineligible_plan",
                execution_mode=effective_plan.execution.mode.value,
                gate_status=gate_result.status.value,
            )
            turn.transition(TurnPhase.REJECTED)
            return ToolLoopResult(_UNIFIED_PLAN_REJECTED_MESSAGE, success=False)
        if run_v4 and not _v4_connected_contract_eligible(effective_plan, catalog):
            self._record_unified_rollout_path(
                path="fail_closed",
                reason="v4_connected_contract_incomplete",
                execution_mode=effective_plan.execution.mode.value,
                gate_status=gate_result.status.value,
            )
            turn.transition(TurnPhase.REJECTED)
            return ToolLoopResult(_UNIFIED_PLAN_REJECTED_MESSAGE, success=False)

        if run_v4:
            v4 = config.get("langgraph_v4", {})
            if not isinstance(v4, dict) or not bool(v4.get("budget_valid", False)):
                self._record_unified_rollout_path(
                    path="fail_closed",
                    reason="langgraph_v4_budget_unbounded",
                    execution_mode=effective_plan.execution.mode.value,
                    gate_status=gate_result.status.value,
                )
                turn.transition(TurnPhase.REJECTED)
                return ToolLoopResult(_UNIFIED_PLAN_REJECTED_MESSAGE, success=False)
            selected = effective_plan.capability.primary_asset
            if selected is None or selected.asset_type not in {"recipe", "skill"}:
                self._record_unified_rollout_path(
                    path="fail_closed",
                    reason="langgraph_v4_exact_asset_required",
                    execution_mode=effective_plan.execution.mode.value,
                    gate_status=gate_result.status.value,
                )
                turn.transition(TurnPhase.REJECTED)
                return ToolLoopResult(_UNIFIED_PLAN_REJECTED_MESSAGE, success=False)
            execution = None
            dispatch_provenance = None
            failure_reason = ""
            try:
                connected = await self._execute_langgraph_v4_connected(
                    plan=effective_plan,
                    legacy=None,
                    mode=(
                        "read_only_canary"
                        if canary_read_only
                        else "primary"
                    ),
                    request_id=turn.turn_id,
                    session_key=turn.session_key,
                    skills=planner_skills,
                    recipes=planner_recipes,
                    planner_model_calls=usage_router.response_count,
                    planner_tokens=usage_router.output_tokens,
                    on_progress=on_progress,
                )
                execution = connected.execution
                if execution.rollback_required:
                    failure_reason = ",".join(execution.rollback_reasons)
                elif execution.final_content is None:
                    failure_reason = "typed_final_missing"
            except Exception as exc:
                if isinstance(exc, ConnectedExecutionError):
                    diagnostic = exc
                else:
                    diagnostic = ConnectedExecutionError(
                        getattr(exc, "connected_phase", "setup"),
                        exc,
                        selected_asset_identity=_selected_asset_identity(
                            effective_plan
                        ),
                        catalog_fingerprint=catalog.fingerprint,
                    )
                failure_reason = diagnostic.code
                logger.exception(
                    "LangGraph V4 primary isolated: request_id=%s "
                    "original_mode=%s effective_mode=%s original_asset=%s "
                    "effective_asset=%s failure_phase=%s phase=%s code=%s "
                    "error_type=%s "
                    "selected_asset_identity=%s selected_asset_hash=%s "
                    "catalog_fingerprint=%s registry_fingerprint=%s "
                    "owned_input_contract_present=%s "
                    "owned_output_contract_present=%s owned_binding_present=%s "
                    "error_message=%s",
                    turn.turn_id,
                    plan.execution.mode.value,
                    effective_plan.execution.mode.value,
                    _selected_asset_identity(plan),
                    _selected_asset_identity(effective_plan),
                    diagnostic.phase,
                    diagnostic.phase,
                    diagnostic.code,
                    diagnostic.error_type,
                    diagnostic.selected_asset_identity
                    or _selected_asset_identity(effective_plan),
                    diagnostic.selected_asset_hash,
                    diagnostic.catalog_fingerprint or catalog.fingerprint,
                    diagnostic.registry_fingerprint,
                    diagnostic.owned_input_contract_present,
                    diagnostic.owned_output_contract_present,
                    diagnostic.owned_binding_present,
                    diagnostic.safe_message,
                    exc_info=(type(diagnostic), diagnostic, exc.__traceback__),
                )

            checkpoint = v4.get("checkpoint", {})
            if isinstance(checkpoint, dict):
                try:
                    dispatch_provenance = load_durable_dispatch_provenance(
                        str(checkpoint.get("path") or ""),
                        turn.turn_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "LangGraph V4 dispatch provenance unavailable "
                        "(error_type=%s)",
                        type(exc).__name__,
                    )

            if failure_reason:
                if _allow_v4_legacy_fallback(
                    v4,
                    execution,
                    dispatch_provenance,
                ):
                    self._record_unified_rollout_path(
                        path="legacy_fallback",
                        reason=failure_reason,
                        execution_mode=effective_plan.execution.mode.value,
                        gate_status=gate_result.status.value,
                    )
                else:
                    self._record_unified_rollout_path(
                        path="fail_closed",
                        reason=failure_reason,
                        execution_mode=effective_plan.execution.mode.value,
                        gate_status=gate_result.status.value,
                    )
                    turn.transition(TurnPhase.REJECTED)
                    return ToolLoopResult(
                        _UNIFIED_PLAN_REJECTED_MESSAGE,
                        success=False,
                    )
            else:
                assert execution is not None
                assert execution.final_content is not None
                turn.transition(TurnPhase.EXECUTING)
                if effective_plan.fact_check.required:
                    turn.transition(TurnPhase.COLLECTING_EVIDENCE)
                    turn.record_evidence(
                        EvidenceState(
                            required=True,
                            attempted=True,
                            status=EvidenceStatus.FOUND,
                            source_type=EvidenceSourceType.APPROVED_TOOL,
                            freshness=EvidenceFreshness.CURRENT_TURN,
                            evidence_text=execution.provenance,
                            query=effective_plan.context.standalone_question,
                        )
                    )
                    turn.verify_evidence()
                    turn.transition(TurnPhase.EVIDENCE_VERIFIED)
                turn.transition(TurnPhase.FINALIZING)
                turn.set_final_text(execution.final_content)
                turn.transition(TurnPhase.COMPLETED)
                self._record_unified_rollout_path(
                    path="langgraph_v4",
                    reason="typed_primary_result",
                    execution_mode=effective_plan.execution.mode.value,
                    gate_status=gate_result.status.value,
                )
                return ToolLoopResult(
                    execution.final_content,
                    success=True,
                    selected_route=execution.selected_route,
                    model_calls=usage_router.response_count,
                    tokens=usage_router.output_tokens,
                    primary_delivery=PrimaryDeliveryMetadataV1(
                        request_id=execution.request_id,
                        artifact_id=execution.final_artifact.artifact_id,
                        artifact_hash=execution.final_artifact.content_hash,
                        session_key=turn.session_key,
                    ),
                )

        self._record_unified_rollout_path(
            path="primary",
            reason="legacy_primary",
            execution_mode=effective_plan.execution.mode.value,
            gate_status=gate_result.status.value,
        )
        primary_route: str | None = None
        primary_model_calls = 0

        def record_primary_execution(route: str, model_calls: int) -> None:
            nonlocal primary_route, primary_model_calls
            if primary_route is not None and primary_route != route:
                raise RuntimeError("primary execution route changed within one turn")
            primary_route = route
            primary_model_calls += model_calls

        async def run_planned_tool_loop(
            state: TurnExecutionState,
            *,
            route: str = "react",
        ) -> TurnExecutionState:
            """검증된 immutable plan을 같은 ToolLoopState에 고정한다."""
            callback_plan = state.plan
            if callback_plan is None:
                raise ValueError("planned tool loop requires an attached plan")
            state.transition(TurnPhase.EXECUTING)
            result = await self._run_tool_loop_result(
                callback_plan.context.standalone_question,
                attachments=attachments,
                on_text_delta=on_text_delta,
                on_progress=on_progress,
                operator_tools=operator_tools,
                plan=callback_plan,
                candidates=candidates,
                evidence_requirement=requirement_from_turn_plan(
                    callback_plan,
                    catalog=catalog,
                ),
                turn=state,
            )
            record_primary_execution(route, result.iterations)
            state.transition(TurnPhase.FINALIZING)
            state.set_final_text(result.text)
            state.transition(TurnPhase.COMPLETED)
            return state

        async def clarify(state: TurnExecutionState) -> TurnExecutionState:
            """PASS plan의 명시적 clarify mode도 같은 사용자 UX로 수렴시킨다."""
            if state.plan is None:
                raise ValueError("clarify requires an attached plan")
            state.transition(TurnPhase.WAITING_FOR_USER)
            state.final_text = self._render_unified_clarification(state.plan)
            return state

        async def run_planned_fact_check(
            state: TurnExecutionState,
            *,
            route: str = "react",
        ) -> TurnExecutionState:
            """Typed source request를 먼저 실행하고 검증된 근거만 조합한다."""

            fact_model_calls = 0

            async def lookup(
                request: RealtimeLookupRequest,
            ) -> RealtimeLookupResult:
                raw = json.dumps(
                    request.to_payload(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                token = base64.urlsafe_b64encode(raw).decode("ascii")
                output = await self._execute_skill(
                    _REALTIME_LOOKUP_SKILL_NAME,
                    token,
                )
                try:
                    payload = json.loads(output or "")
                except (TypeError, json.JSONDecodeError):
                    payload = {
                        "lookup_status": "unusable",
                        "confidence": "low",
                        "evidence": [],
                        "facts": [],
                        "limitations": ["structured lookup returned invalid JSON"],
                    }
                try:
                    status = LookupStatus(
                        str(payload.get("lookup_status") or "unusable")
                    )
                except ValueError:
                    status = LookupStatus.UNUSABLE
                return RealtimeLookupResult(
                    request=request,
                    status=status,
                    evidence=tuple(payload.get("evidence") or ()),
                    facts=tuple(payload.get("facts") or ()),
                    limitations=tuple(payload.get("limitations") or ()),
                    payload=payload,
                )

            async def compose(
                verified_state: TurnExecutionState,
            ) -> str:
                nonlocal fact_model_calls
                if verified_state.plan is None:
                    raise ValueError("fact composition requires plan")
                result = await self._run_tool_loop_result(
                    verified_state.plan.context.standalone_question,
                    attachments=attachments,
                    on_text_delta=None,
                    on_progress=on_progress,
                    operator_tools=operator_tools,
                    plan=verified_state.plan,
                    candidates=candidates,
                    evidence_requirement=requirement_from_turn_plan(
                        verified_state.plan,
                        catalog=catalog,
                    ),
                    turn=verified_state,
                )
                fact_model_calls += result.iterations
                return result.text

            evidence_max_attempts = int(
                config.get("evidence_max_attempts", 2)
            )
            completed = await FactCheckController(
                lookup=tuple(lookup for _ in range(evidence_max_attempts)),
                compose=compose,
                max_attempts=evidence_max_attempts,
            ).run(state)
            record_primary_execution(route, fact_model_calls)
            return completed

        async def run_planned_complex_fact(
            state: TurnExecutionState,
        ) -> TurnExecutionState:
            """복합 사실도 동일한 typed retrieval/finalization gate를 사용한다."""
            return await run_planned_fact_check(state, route="deep_research")

        async def run_planned_recipe(
            state: TurnExecutionState,
        ) -> TurnExecutionState:
            """선택된 recipe만 노출하고 해당 asset이 근거 수명주기를 소유하게 한다."""
            return await run_planned_tool_loop(state, route="recipe")

        async def run_planned_react(
            state: TurnExecutionState,
        ) -> TurnExecutionState:
            return await run_planned_tool_loop(state, route="react")

        async def run_planned_react_fact(
            state: TurnExecutionState,
        ) -> TurnExecutionState:
            return await run_planned_fact_check(state, route="react")

        factual_handler = (
            run_planned_react_fact
            if turn.plan is not None and turn.plan.fact_check.required
            else run_planned_react
        )
        direct_handler = (
            run_planned_recipe
            if effective_plan.capability.primary_asset is not None
            and effective_plan.capability.primary_asset.asset_type == "recipe"
            else factual_handler
        )
        if architecture == "capability_first_v3":
            if not bool(config.get("resolution_budget_valid", False)):
                self._record_unified_rollout_path(
                    path="fail_closed",
                    reason="capability_first_budget_unbounded",
                    execution_mode=effective_plan.execution.mode.value,
                    gate_status=gate_result.status.value,
                )
                turn.transition(TurnPhase.REJECTED)
                return ToolLoopResult(_UNIFIED_PLAN_REJECTED_MESSAGE, success=False)

            budget_config = config.get("resolution_budget", {})
            if not isinstance(budget_config, dict):
                budget_config = {}
            budget = ResolutionBudget.from_seconds(
                max_seconds=budget_config.get("max_seconds"),
                max_steps=budget_config.get("max_steps"),
                max_tool_calls=budget_config.get("max_tool_calls"),
                token_budget=budget_config.get("max_tokens"),
            )

            async def execute_exact_skill(name: str, question: str) -> object:
                return await self._execute_skill(name, shlex.quote(question))

            async def execute_exact_recipe(
                name: str,
                variables: dict[str, str],
            ) -> object:
                return await self._execute_exact_recipe_asset(
                    name,
                    variables,
                    on_progress=on_progress,
                )

            async def direct_mode(
                callback_plan: UnifiedTurnPlan,
                _transition: object,
                _ledger: object,
                _budget: ResolutionBudget,
            ) -> str:
                result = await self._run_tool_loop_result(
                    callback_plan.context.standalone_question,
                    attachments=attachments,
                    on_text_delta=on_text_delta,
                    on_progress=on_progress,
                    operator_tools=operator_tools,
                    plan=callback_plan,
                    candidates=candidates,
                    evidence_requirement=requirement_from_turn_plan(
                        callback_plan,
                        catalog=catalog,
                    ),
                )
                return result.text

            async def execute_supporting_asset(
                selected: AssetRef,
                question: str,
                _ledger: ResolutionLedger,
            ) -> AssetResult:
                if selected.asset_type == "skill":
                    raw = await execute_exact_skill(selected.name, question)
                elif selected.asset_type == "recipe":
                    raw = await execute_exact_recipe(
                        selected.name,
                        {"query": question},
                    )
                else:
                    return AssetResult(
                        asset_type=selected.asset_type,
                        asset_name=selected.name,
                        status=AssetExecutionStatus.UNSUPPORTED,
                        limitations=("typed_supporting_executor_unavailable",),
                    )
                catalog_asset = next(
                    (
                        item
                        for item in catalog.assets
                        if item.asset_type == selected.asset_type
                        and item.name == selected.name
                    ),
                    None,
                )
                return decode_asset_result(
                    raw,
                    asset_type=selected.asset_type,
                    asset_name=selected.name,
                    side_effect=bool(
                        catalog_asset is not None and catalog_asset.side_effects
                    ),
                )

            def investigation_transition(
                callback_plan: UnifiedTurnPlan,
                transition: ProblemTransition | None,
            ) -> ProblemTransition:
                if transition is not None:
                    return transition
                required = callback_plan.fact_check.required_claims or (
                    "unresolved_goal",
                )
                return ProblemTransition(
                    original_goal=callback_plan.context.standalone_question,
                    previous_question=callback_plan.context.standalone_question,
                    triggering_observation="partial_capability",
                    goal_status=GoalStatus.UNRESOLVED,
                    unresolved_gap=required[0],
                    next_question=callback_plan.context.standalone_question,
                    required_claims=required,
                    recommended_mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
                    transition_reason="partial_capability",
                )

            async def evidence_mode(
                callback_plan: UnifiedTurnPlan,
                transition: ProblemTransition | None,
                ledger: ResolutionLedger,
                callback_budget: ResolutionBudget,
            ) -> AssetResult:
                outcome = await EvidenceInvestigationController(
                    execute_supporting_asset=execute_supporting_asset,
                ).run(
                    investigation_transition(callback_plan, transition),
                    supporting_assets=callback_plan.capability.supporting_assets,
                    budget=callback_budget,
                    ledger=ledger,
                )
                return outcome.last_result or AssetResult(
                    asset_type="controller",
                    asset_name="evidence_investigation",
                    status=AssetExecutionStatus.UNSUPPORTED,
                    unresolved_claims=outcome.goal.unresolved_claims,
                    limitations=(outcome.stop_reason,),
                )

            async def complex_mode(
                callback_plan: UnifiedTurnPlan,
                transition: ProblemTransition | None,
                ledger: ResolutionLedger,
                callback_budget: ResolutionBudget,
            ) -> AssetResult:
                prior_signals = (
                    ledger.asset_results[-1].complexity_signals
                    if ledger.asset_results
                    else ()
                )
                signals = tuple(
                    dict.fromkeys(
                        (*callback_plan.execution.complexity_signals, *prior_signals)
                    )
                )
                if not signals:
                    return AssetResult(
                        asset_type="controller",
                        asset_name="complex_problem",
                        status=AssetExecutionStatus.DENIED,
                        limitations=("complexity_signal_missing",),
                    )
                claims = callback_plan.fact_check.required_claims
                if not claims and transition is not None:
                    claims = transition.required_claims
                claims = claims or ("unresolved_goal",)
                ordered = bool(
                    set(signals)
                    & {
                        ComplexitySignal.DEPENDENCY_GRAPH,
                        ComplexitySignal.ORDERED_CAPABILITY_COMPOSITION,
                    }
                )
                nodes = [
                    ProblemNode(
                        node_id=f"claim-{index}",
                        claim=claim,
                        question=(
                            transition.next_question
                            if transition is not None and index == 0
                            else f"다음 미해결 항목을 확인한다: {claim}"
                        ),
                        dependencies=(f"claim-{index - 1}",)
                        if ordered and index > 0
                        else (),
                        allowed_assets=(
                            (
                                callback_plan.capability.supporting_assets[
                                    index
                                    % len(callback_plan.capability.supporting_assets)
                                ],
                            )
                            if callback_plan.capability.supporting_assets
                            else ()
                        ),
                    )
                    for index, claim in enumerate(claims)
                ]

                async def execute_node(
                    node: ProblemNode,
                    asset: AssetRef,
                    node_ledger: ResolutionLedger,
                ) -> AssetResult:
                    return await execute_supporting_asset(
                        asset,
                        node.question,
                        node_ledger,
                    )

                state = ComplexProblemState(
                    original_goal=callback_plan.context.standalone_question,
                    nodes=nodes,
                    ledger=ledger,
                )
                outcome = await ComplexProblemController(
                    execute_node=execute_node,
                ).run(state, budget=callback_budget)
                resolved = tuple(
                    node.claim
                    for node in nodes
                    if node.node_id in outcome.state.resolved_node_ids
                )
                unresolved = tuple(claim for claim in claims if claim not in resolved)
                last = ledger.asset_results[-1] if ledger.asset_results else None
                return AssetResult(
                    asset_type="controller",
                    asset_name="complex_problem",
                    status=(
                        AssetExecutionStatus.COMPLETED
                        if outcome.success
                        else AssetExecutionStatus.PARTIAL_SUCCESS
                    ),
                    data=dict(last.data) if last is not None else {},
                    resolved_claims=resolved,
                    unresolved_claims=unresolved,
                    limitations=outcome.limitations,
                )

            turn.transition(TurnPhase.EXECUTING)
            outcome = await ResolutionController(
                capability_executor=CapabilityExecutor(
                    catalog=catalog,
                    execute_skill=execute_exact_skill,
                    execute_recipe=execute_exact_recipe,
                ),
                direct_answer=direct_mode,
                answer_with_evidence=evidence_mode,
                resolve_complex_problem=complex_mode,
                complex_escalation_enabled=bool(
                    config.get("complex_escalation", {}).get("enabled", False)
                ),
            ).resolve(effective_plan, budget=budget)
            logger.info(
                "Capability resolution: coverage=%s fast_path=%s asset_status=%s "
                "goal_status=%s mode=%s stop_reason=%s attempts=%d "
                "validator_allow_final=%s",
                effective_plan.capability.coverage.value,
                bool(effective_plan.capability.primary_asset),
                (
                    outcome.asset_result.status.value
                    if outcome.asset_result is not None
                    else "none"
                ),
                outcome.goal.status.value,
                outcome.mode.value,
                outcome.stop_reason,
                len(outcome.ledger.attempted_signatures),
                outcome.validation.allow_final,
            )
            if outcome.mode is ExecutionMode.CLARIFY:
                turn.transition(TurnPhase.LIMITED_FINAL)
                turn.set_final_text(outcome.text, limited=True)
                turn.transition(TurnPhase.COMPLETED)
            elif effective_plan.fact_check.required and outcome.validation.allow_final:
                turn.record_evidence(
                    EvidenceState(
                        required=True,
                        attempted=True,
                        status=EvidenceStatus.FOUND,
                        source_type=EvidenceSourceType.APPROVED_TOOL,
                        freshness=EvidenceFreshness.CURRENT_TURN,
                        evidence_text="\n".join(
                            item.source_url or item.provenance
                            for item in outcome.ledger.evidence
                            if item.usable
                        ),
                        query=effective_plan.context.standalone_question,
                    )
                )
                turn.verify_evidence()
                turn.transition(TurnPhase.FINALIZING)
                turn.set_final_text(outcome.text)
                turn.transition(TurnPhase.COMPLETED)
            elif effective_plan.fact_check.required:
                turn.transition(TurnPhase.LIMITED_FINAL)
                turn.set_final_text(outcome.text, limited=True)
                turn.transition(TurnPhase.COMPLETED)
            else:
                turn.transition(TurnPhase.FINALIZING)
                turn.set_final_text(outcome.text)
                turn.transition(TurnPhase.COMPLETED)
            self._record_unified_rollout_path(
                path="capability_first_v3",
                reason=outcome.stop_reason,
                execution_mode=outcome.mode.value,
                gate_status=gate_result.status.value,
            )
            return ToolLoopResult(
                outcome.text,
                success=turn.phase is TurnPhase.COMPLETED,
            )
        router = self._build_execution_router(
            ExecutionCallbacks(
                direct_answer=direct_handler,
                answer_with_evidence=run_planned_react_fact,
                resolve_complex_problem=run_planned_complex_fact,
                clarify=clarify,
            )
        )
        completed = await router.dispatch(turn)
        return ToolLoopResult(
            completed.final_text or _UNIFIED_PLAN_REJECTED_MESSAGE,
            success=completed.phase is TurnPhase.COMPLETED,
            selected_route=primary_route,
            model_calls=usage_router.response_count + primary_model_calls,
            tokens=usage_router.output_tokens,
        )

    def _record_unified_rollout_path(
        self,
        *,
        path: str,
        reason: str,
        execution_mode: str = "unknown",
        gate_status: str = "not_run",
    ) -> None:
        """원문 없이 primary/legacy/fail-closed 경로 선택을 구조화 기록한다."""
        rollout_mode = str(self._unified_turn_planner_config.get("mode", "off"))
        logger.info(
            "Unified TurnPlanner rollout: mode=%s path=%s reason=%s "
            "execution_mode=%s gate_status=%s",
            rollout_mode,
            path,
            reason,
            execution_mode,
            gate_status,
        )
        telemetry = self._unified_turn_planner_config.get("telemetry", {})
        if (
            self._structured_logger is None
            or not isinstance(telemetry, dict)
            or not telemetry.get("enabled", True)
        ):
            return
        self._structured_logger.log(
            action_type="unified_turn_planner_rollout",
            status="failure" if path == "fail_closed" else "success",
            trace_id="",
            rollout_mode=rollout_mode,
            selected_path=path,
            reason=reason,
            execution_mode=execution_mode,
            gate_status=gate_status,
            raw_text_included=False,
        )

    def _render_unified_clarification(self, plan: UnifiedTurnPlan) -> str:
        """Unified clarification plan을 기존 pending/button UX로 변환한다."""
        question = (
            plan.clarification.question.strip()
            or "어느 대상이나 맥락을 뜻하시는지 알려 주세요."
        )
        options = normalize_options(list(plan.clarification.options))
        if len(options) < 2:
            return question
        chat_id = clarify_chat_id_var.get()
        if chat_id is None:
            return ClarifyRequest(
                question=question,
                options=options,
            ).format_user_visible()
        request = ClarifyRequest(question=question, options=options)
        self._pending_clarify[chat_id] = request
        self._save_pending_clarify(current_session_key_var.get(), request)
        return request.format_user_visible()

    def _save_pending_clarify(
        self,
        session_key: str,
        request: ClarifyRequest,
    ) -> None:
        self._store.save_session_state(
            SessionState(
                key=session_key,
                pending=PendingInteraction(
                    kind="clarify",
                    payload={
                        "question": request.question,
                        "options": [
                            {"label": option.label, "body": option.body}
                            for option in request.options
                        ],
                    },
                ),
            )
        )

    def get_pending_clarify(
        self,
        user_id: int,
        chat_id: int,
        thread_id: int | str | None = None,
    ) -> ClarifyRequest | None:
        """Load a durable clarification without consuming it."""
        session_key = SessionIdentity(
            channel="telegram",
            user_id=str(user_id),
            chat_id=str(chat_id),
            thread_id="" if thread_id is None else str(thread_id),
        ).stable_key()
        state = self._store.load_session_state(session_key)
        if state is None or state.pending is None:
            return None
        if state.pending.kind != "clarify":
            return None
        payload = state.pending.payload
        question = str(payload.get("question") or "").strip()
        raw_options = payload.get("options")
        if not question or not isinstance(raw_options, list):
            return None
        try:
            options = normalize_options(raw_options)
        except ValueError:
            return None
        return ClarifyRequest(question=question, options=options)

    def consume_pending_clarify(
        self,
        user_id: int,
        chat_id: int,
        thread_id: int | str | None = None,
    ) -> ClarifyRequest | None:
        """Atomically consume a durable clarification."""
        session_key = SessionIdentity(
            channel="telegram",
            user_id=str(user_id),
            chat_id=str(chat_id),
            thread_id="" if thread_id is None else str(thread_id),
        ).stable_key()
        previous = self._store.clear_pending_interaction(session_key)
        if previous is None or previous.pending is None:
            return None
        payload = previous.pending.payload
        try:
            options = normalize_options(payload.get("options"))
        except (TypeError, ValueError):
            return None
        return ClarifyRequest(
            question=str(payload.get("question") or ""),
            options=options,
        )

    def _schedule_unified_turn_planner_shadow(
        self,
        text: str,
        *,
        recent_rows: list[tuple[int, ConversationMessage]] | None = None,
        plan: UnifiedTurnPlan | None = None,
        legacy: LegacyRunTelemetryV1 | None = None,
        request_id: str = "",
        session_key: str = "",
    ) -> None:
        """설정과 sampling을 통과한 ordinary turn만 background task로 예약한다."""
        config = self._unified_turn_planner_config
        if config.get("mode") != "shadow":
            return
        sample_rate = float(config.get("sample_rate", 0.0))
        if sample_rate <= 0.0:
            return
        if sample_rate < 1.0 and random.random() >= sample_rate:
            return

        candidate_limit = int(config.get("context_candidate_limit", 8))
        if recent_rows is None:
            recent_rows = self._store.get_recent_with_ids(limit=candidate_limit)
        task = asyncio.create_task(
            self._run_unified_turn_planner_shadow(
                text,
                recent_rows=tuple(recent_rows[-candidate_limit:]),
                skills=tuple(self._exposable_skills()),
                recipes=tuple(getattr(self, "_recipes", ())),
                cron_available=self._cron_scheduler is not None,
                browser_handoff_available=bool(
                    self._browser_handoff_config.get("enabled", False)
                ),
                connected_plan=plan,
                legacy=legacy,
                request_id=request_id,
                session_key=session_key,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._finish_unified_turn_planner_shadow)

    def _finish_unified_turn_planner_shadow(self, task: asyncio.Task) -> None:
        """task를 강하게 참조한 set에서 제거하고 예외를 소비해 누출을 막는다."""
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            return
        if error is not None:
            logger.warning(
                "Unified TurnPlanner shadow task isolated "
                "(error_code=shadow_task_failed)"
            )

    async def _run_unified_turn_planner_shadow(
        self,
        text: str,
        *,
        recent_rows: tuple[tuple[int, ConversationMessage], ...],
        skills: tuple[SkillDefinition, ...],
        recipes: tuple[RecipeDefinition, ...],
        cron_available: bool,
        browser_handoff_available: bool,
        connected_plan: UnifiedTurnPlan | None = None,
        legacy: LegacyRunTelemetryV1 | None = None,
        request_id: str = "",
        session_key: str = "",
    ) -> None:
        """Planner→PlanGate를 실행하고 redacted telemetry만 남긴다."""
        config = self._unified_turn_planner_config
        if str(config.get("architecture")) == "langgraph_v4":
            if connected_plan is None or legacy is None or not request_id:
                raise ValueError("langgraph_v4 shadow requires connected primary evidence")
            await self._run_langgraph_v4_connected_shadow(
                plan=connected_plan,
                legacy=legacy,
                request_id=request_id,
                session_key=session_key,
                skills=skills,
                recipes=recipes,
            )
            return
        candidate_limit = int(config.get("context_candidate_limit", 8))
        candidates = ContextCandidateSet((), 0, False)
        catalog_fingerprint = ""
        usage_router = PlannerUsageCaptureRouter(self._router)
        started = time.perf_counter()
        try:
            candidates = ContextCandidateBuilder(
                max_turns=candidate_limit,
                max_chars=int(config.get("context_candidate_max_chars", 6000)),
            ).build(recent_rows)
            catalog = build_planner_catalog(
                skills=skills,
                recipes=recipes,
                native_specs=_planner_native_specs(
                    cron_available=cron_available,
                    browser_handoff_available=browser_handoff_available,
                ),
            )
            catalog_fingerprint = catalog.fingerprint
            plan = await plan_turn_with_llm(
                text,
                candidates=candidates,
                catalog=catalog,
                router=usage_router,
                max_tokens=int(config.get("max_tokens", 2048)),
                reasoning=config.get("reasoning"),
            )
            gate_result = PlanGate(
                selected_context_max_turns=int(
                    config.get("selected_context_max_turns", 3)
                ),
                selected_context_max_chars=int(
                    config.get("selected_context_max_chars", 2400)
                ),
            ).evaluate(plan, candidates=candidates, catalog=catalog)
            event = build_turn_planner_shadow_event(
                plan=plan,
                gate_result=gate_result,
                candidates=candidates,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                input_tokens=usage_router.input_tokens,
                output_tokens=usage_router.output_tokens,
            )
        except Exception:
            event = build_turn_planner_shadow_failure_event(
                candidates=candidates,
                catalog_fingerprint=catalog_fingerprint,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                input_tokens=usage_router.input_tokens,
                output_tokens=usage_router.output_tokens,
            )
            logger.warning(
                "Unified TurnPlanner shadow failed "
                "(error_code=planner_unavailable)"
            )

        telemetry = config.get("telemetry", {})
        if isinstance(telemetry, dict) and telemetry.get("enabled", True):
            emit_turn_planner_shadow_event(
                event,
                structured_logger=self._structured_logger,
            )

    async def _run_langgraph_v4_connected_shadow(
        self,
        *,
        plan: UnifiedTurnPlan,
        legacy: LegacyRunTelemetryV1,
        request_id: str,
        session_key: str,
        skills: tuple[SkillDefinition, ...],
        recipes: tuple[RecipeDefinition, ...],
    ) -> None:
        """Legacy primary와 비교할 background shadow 실행만 예약한다."""
        await self._execute_langgraph_v4_connected(
            plan=plan,
            legacy=legacy,
            mode="shadow",
            request_id=request_id,
            session_key=session_key,
            skills=skills,
            recipes=recipes,
            planner_model_calls=legacy.model_calls,
            planner_tokens=legacy.tokens,
        )

    async def _execute_langgraph_v4_connected(
        self,
        *,
        plan: UnifiedTurnPlan,
        legacy: LegacyRunTelemetryV1 | None,
        mode: str,
        request_id: str,
        session_key: str,
        skills: tuple[SkillDefinition, ...],
        recipes: tuple[RecipeDefinition, ...],
        planner_model_calls: int,
        planner_tokens: int,
        on_progress: ProgressCallback | None = None,
    ) -> ConnectedShadowResultV1:
        """Exact read-only plan을 V4 graph receipt까지 한 번만 실행한다."""
        v4 = self._unified_turn_planner_config.get("langgraph_v4", {})
        if not isinstance(v4, dict):
            raise TypeError("langgraph_v4 configuration is required")
        raw_budget = v4.get("budget", {})
        checkpoint = v4.get("checkpoint", {})
        if not isinstance(raw_budget, dict) or not isinstance(checkpoint, dict):
            raise TypeError("langgraph_v4 budget/checkpoint configuration is required")
        try:
            budget = ShadowBudgetUsageV1(
                max_graph_steps=raw_budget.get("max_graph_steps"),
                max_asset_calls=raw_budget.get("max_asset_calls"),
                max_llm_calls=raw_budget.get("max_llm_calls"),
                max_tokens=raw_budget.get("max_tokens"),
                max_seconds=raw_budget.get("max_seconds"),
                max_parallel_invocations=raw_budget.get("max_parallel_invocations"),
                graph_steps=0,
                asset_calls=0,
                llm_calls=0,
                tokens=0,
                elapsed_seconds=0,
                parallel_peak=0,
                stop_condition="completed",
            )
            facade = LangGraphV4RolloutFacade(
                architecture="langgraph_v4",
                mode=mode,
                shadow_no_send=bool(v4.get("shadow_no_send", False)),
                budget=budget,
                checkpoint_path=str(checkpoint.get("path") or ""),
            )
        except Exception as exc:
            raise ConnectedExecutionError("setup", exc) from exc

        async def execute_skill(definition, argv):
            raw = await self._execute_skill(definition.name, shlex.join(argv))
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                raise TypeError("connected skill output must be a JSON object")
            return decoded

        async def execute_recipe(definition, bound_steps):
            if not bound_steps:
                raise ValueError("connected recipe requires declared binding")
            payload = json.loads(bound_steps[0].source_payload_json)
            if not isinstance(payload, dict):
                raise TypeError("connected recipe payload must be an object")
            raw = await self._execute_exact_recipe_asset(
                definition.name,
                {
                    key: (
                        value
                        if isinstance(value, str)
                        else json.dumps(value, ensure_ascii=False, sort_keys=True)
                    )
                    for key, value in payload.items()
                },
                on_progress=on_progress,
            )
            if not isinstance(raw, dict):
                raise TypeError("connected recipe output must be a JSON object")
            return raw

        result = await ConnectedShadowTurnRunner(
            facade=facade,
            definitions=(*recipes, *skills),
            conversation_store=self._store,
            recipe_executor=execute_recipe,
            skill_executor=execute_skill,
        ).run(
            plan=plan,
            legacy=legacy,
            request_id=request_id,
            session_key=session_key,
            planner_model_calls=planner_model_calls,
            planner_tokens=planner_tokens,
        )
        telemetry = self._unified_turn_planner_config.get("telemetry", {})
        if (
            self._structured_logger is None
            or not isinstance(telemetry, dict)
            or not telemetry.get("enabled", True)
        ):
            return result
        allowed = set(v4.get("telemetry_fields", ()))
        event_fields = {
            key: value
            for key, value in result.telemetry.as_dict().items()
            if key in allowed
        }
        event_fields.update(
            {
                key: value
                for key, value in result.execution.as_dict().items()
                if key in allowed
            }
        )
        event_fields.update(
            side_effect_counts=result.side_effect_counts.as_dict(),
            rollback_required=result.execution.rollback_required,
            rollback_reason=(
                ",".join(result.execution.rollback_reasons) or None
            ),
        )
        if result.canary is not None:
            event_fields["canary_eligible"] = result.canary.eligible
        self._structured_logger.log(
            action_type=f"langgraph_v4_{mode}_rollout",
            status=(
                "failure" if result.execution.rollback_required else "success"
            ),
            trace_id="",
            **event_fields,
        )
        return result

    async def process_operator_message(
        self,
        text: str,
        *,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> str:
        """운영자 context에서만 operator scope native tool을 노출해 메시지를 처리한다."""
        with trace_scope() as trace_id:
            logger.info("Operator message received: trace_id=%s", trace_id)
            self._reload_dynamic_files()
            return await self._tool_loop(
                text,
                isolated=True,
                on_text_delta=on_text_delta,
                operator_tools=True,
            )

    async def _run_complex_fact_workflow(
        self,
        text: str,
        route_decision,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        """Feature-flagged complex fact/scenario workflow entrypoint."""
        from simpleclaw.agent.evidence_retrieval import EvidenceRetriever
        from simpleclaw.agent.fact_answer import compose_fact_answer
        from simpleclaw.agent.fact_workflow import (
            ComplexFactWorkflow,
            ComplexFactWorkflowConfig,
        )

        cfg = self._complex_fact_config or {}
        if str(cfg.get("planner_backend", "simpleclaw")) != "simpleclaw":
            logger.warning(
                "Complex fact planner backend %s is not implemented; falling back to simpleclaw",
                cfg.get("planner_backend"),
            )
        retriever = EvidenceRetriever(
            max_sources_per_slot=int(cfg.get("max_sources_per_slot", 3))
        )

        async def compose(question, plan):
            return await compose_fact_answer(self._router.send, question, plan)

        workflow = ComplexFactWorkflow(
            retriever=retriever,
            compose_answer=compose,
            config=ComplexFactWorkflowConfig(
                max_iterations=int(cfg.get("max_iterations", 4)),
                max_sources_per_slot=int(cfg.get("max_sources_per_slot", 3)),
                enable_claim_verifier=bool(cfg.get("enable_claim_verifier", True)),
                enable_progress_events=bool(cfg.get("enable_progress_events", True)),
            ),
        )
        result = await workflow.run(text, route_decision, on_progress=on_progress)
        return result.text

    async def _run_planned_complex_fact_workflow(
        self,
        plan: UnifiedTurnPlan,
        *,
        on_progress: ProgressCallback | None = None,
    ):
        """의미 재계획 없이 gate를 통과한 복합 계획을 실행한다."""
        from simpleclaw.agent.evidence_retrieval import EvidenceRetriever
        from simpleclaw.agent.fact_answer import compose_fact_answer
        from simpleclaw.agent.fact_workflow import (
            ComplexFactWorkflow,
            ComplexFactWorkflowConfig,
        )

        cfg = self._complex_fact_config or {}
        retriever = EvidenceRetriever.from_turn_plan(
            plan,
            max_sources_per_slot=int(cfg.get("max_sources_per_slot", 3)),
        )

        async def compose(question, fact_plan):
            return await compose_fact_answer(self._router.send, question, fact_plan)

        workflow = ComplexFactWorkflow(
            retriever=retriever,
            compose_answer=compose,
            config=ComplexFactWorkflowConfig(
                max_iterations=int(cfg.get("max_iterations", 4)),
                max_sources_per_slot=int(cfg.get("max_sources_per_slot", 3)),
                enable_claim_verifier=bool(cfg.get("enable_claim_verifier", True)),
                enable_progress_events=bool(cfg.get("enable_progress_events", True)),
            ),
        )
        return await workflow.run_turn_plan(plan, on_progress=on_progress)

    # ------------------------------------------------------------------
    # 대화 저장 + 백그라운드 임베딩 (spec 005 Phase 2)
    # ------------------------------------------------------------------

    def _save_turn(
        self,
        user_text: str,
        assistant_text: str,
        *,
        channel: str | None = None,
    ) -> tuple[int, int]:
        """user/assistant 메시지 한 쌍을 저장하고, RAG가 켜져 있으면 임베딩을 백그라운드 부착한다.

        설계 결정:
        - 임베딩은 fire-and-forget 비동기로 처리하여 응답 레이턴시에 영향을 주지 않는다.
        - 동일 턴 내 user → assistant 순서로 저장(시간순 보존).
        - RAG가 비활성이거나 임베딩 서비스가 None이면 저장만 수행한다.

        BIZ-76: ``channel`` 인자가 주어지면 같은 턴의 user/assistant 두 메시지 모두에
        동일 채널을 부착한다. cron-admin / recipe:<name> 같은 자동·명령 트리거 출처를
        이후 dreaming 코퍼스 로더가 분리하거나 가중치 다운하기 위한 메타이다.
        """
        session_key = current_session_key_var.get()
        turn_id = current_turn_id_var.get() or hashlib.sha256(
            f"{session_key}:{time.time_ns()}".encode()
        ).hexdigest()
        user_id, asst_id = self._store.save_turn(
            ConversationMessage(
                role=MessageRole.USER,
                content=user_text,
                channel=channel,
            ),
            ConversationMessage(
                role=MessageRole.ASSISTANT,
                content=assistant_text,
                channel=channel,
            ),
            session_key=session_key,
            turn_id=turn_id,
        )
        self._schedule_embedding(user_id, user_text)
        self._schedule_embedding(asst_id, assistant_text)
        return user_id, asst_id

    async def _capture_conversation_end_opportunity(
        self, user_text: str, assistant_text: str, source_msg_ids: list[int]
    ) -> None:
        """대화 종료 hook을 best-effort로 실행해 pending 후보만 적재한다."""
        detector = getattr(self, "_conversation_end_detector", None)
        if detector is None:
            return
        try:
            detector.capture(
                user_text=user_text,
                assistant_text=assistant_text,
                source_msg_ids=source_msg_ids,
            )
        except Exception:
            logger.exception("Conversation-end proactive hook failed")

    async def _capture_skill_learning_candidate(
        self,
        user_text: str,
        assistant_text: str,
        result: ToolLoopResult,
        source_msg_ids: list[int],
    ) -> None:
        """성공한 복잡 tool trace를 best-effort pending skill 후보로 적재한다."""
        cfg = getattr(self, "_skill_learning_config", {}) or {}
        if not cfg.get("enabled", False):
            return
        try:
            trace = list(result.trace or [])
            if not result.success or not is_complex_successful_trace(
                trace,
                assistant_text,
                min_tool_calls=int(cfg.get("min_tool_calls", 2)),
                min_distinct_tools=int(cfg.get("min_distinct_tools", 2)),
                min_final_chars=int(cfg.get("min_final_chars", 500)),
            ):
                return
            snapshots = snapshots_from_trace(
                trace,
                max_observation_chars=int(cfg.get("max_trace_observation_chars", 1200)),
            )
            suggestion = await self._draft_skill_suggestion(
                user_text=user_text,
                assistant_text=assistant_text,
                snapshots=snapshots,
                source_msg_ids=source_msg_ids,
            )
            if suggestion is None:
                return
            stored = SkillSuggestionStore(cfg["suggestions_file"]).upsert_pending(
                suggestion
            )
            await self._notify_skill_candidate_pending(stored)
        except Exception:
            logger.exception("Skill-learning candidate hook failed")

    async def _notify_skill_candidate_pending(self, suggestion: SkillSuggestion) -> None:
        """pending skill 후보를 운영자에게 알린다 — 최소한 명확한 이벤트 로그를 남긴다.

        채널(Telegram 등)이 ``_skill_candidate_notifier`` 를 주입하면 그 hook 을
        best-effort 로 호출하고, 없으면 감사 가능한 info 로그만 남긴다. 알림 실패가
        후보 적재를 되돌리면 안 되므로 예외는 여기서 흡수한다.
        """
        cfg = getattr(self, "_skill_learning_config", {}) or {}
        logger.info(
            "Skill suggestion pending operator review: id=%s skill=%s "
            "risk_flags=%s validation_errors=%d",
            suggestion.id,
            suggestion.skill_name,
            suggestion.risk_flags,
            len(suggestion.validation_errors),
        )
        notifier = getattr(self, "_skill_candidate_notifier", None)
        if not cfg.get("notify_on_candidate", True) or notifier is None:
            return
        try:
            result = notifier(suggestion)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Skill candidate notification hook failed")

    async def _draft_skill_suggestion(
        self,
        *,
        user_text: str,
        assistant_text: str,
        snapshots: list,
        source_msg_ids: list[int],
    ) -> SkillSuggestion | None:
        """LLM으로 skill package 후보 JSON을 생성한다.

        BIZ-429 — BIZ-427 의 provider-neutral structured output 으로 후보 JSON 을
        schema-constrained 로 강제한다. 실패 시 raw 전문 대신 raw_len/error class
        같은 안전 진단만 로그하고 ``None`` 을 반환해 후보 적재만 건너뛴다.
        """
        cfg = getattr(self, "_skill_learning_config", {}) or {}
        structured_output = bool(cfg.get("structured_output", True))
        fp = trace_fingerprint(snapshots, user_text=user_text, assistant_text=assistant_text)
        prompt = build_skill_candidate_prompt(
            user_text=user_text,
            assistant_text=assistant_text,
            trace=snapshots,
        )
        response = None
        try:
            request = LLMRequest(user_message=prompt, max_tokens=2048, usage_task="skill_suggestion")
            if structured_output:
                request.response_mime_type = "application/json"
                request.response_schema = SKILL_SUGGESTION_RESPONSE_SCHEMA
                request.require_structured_output = True
            response = await self._router.send(request)
            payload = json.loads((response.text or "{}").strip())
            if not isinstance(payload, dict):
                raise TypeError("Skill candidate response must be a JSON object")
            return suggestion_from_candidate_payload(
                payload,
                trace_fingerprint_value=fp,
                source_msg_ids=source_msg_ids,
                trace=snapshots,
            )
        except Exception as exc:
            # raw 전문에는 사용자 발화/도구 관측이 섞일 수 있어 로그에 남기지 않는다.
            raw_len = len(getattr(response, "text", "") or "")
            logger.warning(
                "Skill suggestion structured output failed; skipping candidate: "
                "%s (error=%s structured=%s raw_len=%d)",
                exc,
                type(exc).__name__,
                structured_output,
                raw_len,
            )
            return None

    async def _capture_recipe_learning_candidate(
        self,
        user_text: str,
        assistant_text: str,
        result: ToolLoopResult,
        source_msg_ids: list[int],
    ) -> None:
        """성공한 복잡 tool trace를 best-effort pending recipe 후보로 적재한다.

        BIZ-428 — skill 후보와 별도 config gate(``recipes.learning``)/큐로 동작한다.
        후보 생성 실패는 사용자 응답을 깨지 않고 warning 로그만 남긴다. live
        ``recipes.dir`` 설치는 여기서 하지 않는다 — operator ``recipe_learning``
        tool의 materialize 승인 경로만 수행한다.
        """
        cfg = getattr(self, "_recipe_learning_config", {}) or {}
        if not cfg.get("enabled", False):
            return
        try:
            trace = list(result.trace or [])
            if not result.success or not is_complex_successful_trace(
                trace,
                assistant_text,
                min_tool_calls=int(cfg.get("min_tool_calls", 2)),
                min_distinct_tools=int(cfg.get("min_distinct_tools", 2)),
                min_final_chars=int(cfg.get("min_final_chars", 500)),
            ):
                return
            snapshots = snapshots_from_trace(
                trace,
                max_observation_chars=int(cfg.get("max_trace_observation_chars", 1200)),
            )
            suggestion = await self._draft_recipe_suggestion(
                user_text=user_text,
                assistant_text=assistant_text,
                snapshots=snapshots,
                source_msg_ids=source_msg_ids,
            )
            if suggestion is None:
                return
            RecipeSuggestionStore(cfg["suggestions_file"]).upsert_pending(suggestion)
        except Exception:
            logger.exception("Recipe-learning candidate hook failed")

    async def _draft_recipe_suggestion(
        self,
        *,
        user_text: str,
        assistant_text: str,
        snapshots: list,
        source_msg_ids: list[int],
    ) -> RecipeSuggestion | None:
        """LLM으로 recipe(반복 워크플로) 후보 JSON을 생성한다.

        BIZ-427 structured output 게이트가 켜져 있으면 provider에
        ``response_schema`` 기반 JSON을 강제한다. BIZ-435 — 실패 시 skill
        learning과 동일하게 raw 전문/프롬프트 대신 exception class·structured
        flag·raw_len 만 로그하고 ``None`` 을 반환해 후보 적재만 건너뛴다.
        """
        cfg = getattr(self, "_recipe_learning_config", {}) or {}
        structured_output = bool(cfg.get("structured_output", True))
        fp = trace_fingerprint(snapshots, user_text=user_text, assistant_text=assistant_text)
        prompt = build_recipe_candidate_prompt(
            user_text=user_text,
            assistant_text=assistant_text,
            trace=snapshots,
        )
        response = None
        try:
            request = LLMRequest(user_message=prompt, max_tokens=2048, usage_task="recipe_suggestion")
            if structured_output:
                request.response_mime_type = "application/json"
                request.response_schema = RECIPE_SUGGESTION_RESPONSE_SCHEMA
                request.require_structured_output = True
            response = await self._router.send(request)
            payload = json.loads((response.text or "{}").strip())
            if not isinstance(payload, dict):
                raise TypeError("Recipe candidate response must be a JSON object")
            return suggestion_from_recipe_payload(
                payload,
                trace_fingerprint_value=fp,
                source_msg_ids=source_msg_ids,
                trace=snapshots,
            )
        except Exception as exc:
            # raw 전문/exception 메시지에는 사용자 발화·도구 관측·provider 응답이
            # 섞일 수 있어 클래스 이름과 길이 진단만 남긴다.
            raw_len = len(getattr(response, "text", "") or "")
            logger.warning(
                "Recipe suggestion drafting failed; skipping candidate "
                "(error=%s structured=%s raw_len=%d)",
                type(exc).__name__,
                structured_output,
                raw_len,
            )
            return None

    def _schedule_embedding(self, message_id: int, content: str) -> None:
        """주어진 메시지의 임베딩을 백그라운드 태스크로 부착한다.

        실패는 조용히 로그만 남긴다(메시지 자체 저장은 이미 완료되었으므로 RAG만 누락).
        sentence-transformers 모델은 동기 API라 ``asyncio.to_thread``로 워커 스레드에 위임한다.
        """
        if self._embedding_service is None or not self._embedding_service.is_enabled:
            return
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 호출 컨텍스트에 이벤트 루프가 없으면 임베딩을 건너뛴다(테스트/동기 호출 보호)
            return

        task = loop.create_task(self._embed_message_async(message_id, content))
        # 강한 참조 유지 — 완료되면 set에서 제거
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _embed_message_async(self, message_id: int, content: str) -> None:
        """메시지를 임베딩하고 ConversationStore에 부착한다(워커 스레드 위임).

        모든 단계는 best-effort이며, 어떤 실패도 호출자로 전파되지 않는다.
        """
        import asyncio
        try:
            assert self._embedding_service is not None  # 호출 전에 확인됨
            vec = await asyncio.to_thread(
                self._embedding_service.encode_passage, content
            )
            if vec is None:
                return
            await asyncio.to_thread(self._store.add_embedding, message_id, vec)
        except Exception as exc:
            logger.warning(
                "Background embedding failed for msg %d: %s", message_id, exc
            )

    # ------------------------------------------------------------------
    # Native Function Calling loop
    # ------------------------------------------------------------------

    def _resolve_mcp_secret(self, value: str) -> str | None:
        """MCP env 값의 secret reference를 해석한다.

        참조 형태(env:/file:/keyring:)만 시크릿 매니저를 통과시키고, 평문 값은
        그대로 전달한다 — MCP 서버 env에 평문 설정값(TZ 등)을 쓰는 경우와 호환.
        """
        if value.startswith(("env:", "file:", "keyring:")):
            resolved = default_manager().resolve(value)
            # 해소 실패(빈 문자열)는 키 자체를 전달하지 않는다 — 빈 시크릿으로
            # 외부 서버가 오동작하는 것보다 명확한 미설정이 낫다.
            return resolved or None
        return value

    async def _ensure_mcp_connected(self) -> None:
        """MCP 서버 discovery를 lazy one-shot으로 수행한다.

        연결은 첫 turn 준비 시 1회만 시도한다 — 매 turn 재연결은 stdio subprocess
        스폰 비용이 크고, 서버 설정 변경은 admin policy상 restart가 필요하다.
        """
        if self._mcp_connected:
            return
        # 실패한 서버가 있어도 재시도하지 않도록 시도 자체를 one-shot으로 마킹한다.
        self._mcp_connected = True
        if not self._mcp_config.get("enabled") or not self._mcp_config.get("servers"):
            return
        if self._mcp_manager is None:
            self._mcp_manager = MCPManager(secret_resolver=self._resolve_mcp_secret)
        try:
            await self._mcp_manager.connect_servers(self._mcp_config)
        except Exception as exc:
            logger.warning("MCP server discovery failed: %s", exc)

    def _mcp_call_available(self, *, operator_tools: bool) -> bool:
        """현재 context에서 mcp_call 노출 여부를 판단한다.

        runtime context는 runtime-scope tool이 하나라도 있어야 노출하고,
        operator context는 연결된 tool이 있으면 scope와 무관하게 노출한다.
        """
        if self._mcp_manager is None:
            return False
        return any(
            operator_tools
            or str(tool.metadata.get("scope") or "operator") == "runtime"
            for tool in self._mcp_manager.list_tools()
        )

    async def _execute_exact_recipe_asset(
        self,
        name: str,
        variables: dict[str, str],
        *,
        on_progress: ProgressCallback | None = None,
    ) -> object:
        """선택된 recipe identity 하나를 strict typed boundary로 실행한다."""
        recipe = next(
            (
                item
                for item in getattr(self, "_recipes", ())
                if item.name == name
            ),
            None,
        )
        if recipe is None:
            return {
                "schema": "asset_result.v1",
                "status": "unsupported",
                "limitations": ["typed_recipe_executor_unavailable"],
            }

        if recipe.instructions:
            try:
                def _typed_string_tuple(name: str) -> tuple[str, ...]:
                    """CapabilityExecutor의 JSON string tuple 변수를 보수 파싱한다."""
                    try:
                        decoded = json.loads(str(variables.get(name) or "[]"))
                    except json.JSONDecodeError:
                        return ()
                    if not isinstance(decoded, list):
                        return ()
                    return tuple(
                        str(item).strip()
                        for item in decoded
                        if str(item).strip()
                    )

                rendered = render_exact_recipe_instructions(
                    recipe,
                    query=str(variables.get("query") or ""),
                    domain=str(variables.get("domain") or ""),
                    intents=_typed_string_tuple("intents"),
                    reference_date=str(variables.get("reference_date") or ""),
                    required_claims=_typed_string_tuple("required_claims"),
                )
                nested_requirement = EvidenceRequirement(
                    required=False,
                    query=str(variables.get("query") or ""),
                    domain=str(variables.get("domain") or ""),
                    allowed_collectors=frozenset({"execute_skill"}),
                    freshness_required=True,
                    origin="exact_recipe",
                    owner="asset",
                    intents=_typed_string_tuple("intents"),
                    reference_date=str(variables.get("reference_date") or ""),
                    required_claims=_typed_string_tuple("required_claims"),
                )
                nested = await self._run_tool_loop_result(
                    rendered,
                    isolated=True,
                    on_text_delta=None,
                    on_progress=on_progress,
                    operator_tools=False,
                    allow_cron_mutation=False,
                    evidence_requirement=nested_requirement,
                    forced_skill_names=frozenset(recipe.skills),
                    forced_tool_names=frozenset({"execute_skill"}),
                    final_response_schema=ASSET_RESULT_RESPONSE_SCHEMA,
                )
            except Exception as exc:
                return {
                    "schema": "asset_result.v1",
                    "status": "failed_terminal",
                    "limitations": [
                        f"typed_recipe_nested_error:{type(exc).__name__}"
                    ],
                }
            delegate_trace = [
                step
                for step in nested.trace
                if step.tool_name == "execute_skill"
                and str(step.arguments.get("skill_name") or "") in recipe.skills
                and step.success
            ]
            if len(nested.trace) != 1 or len(delegate_trace) != 1:
                return {
                    "schema": "asset_result.v1",
                    "status": "failed_terminal",
                    "limitations": ["recipe_requires_one_successful_delegate"],
                }
            if not nested.success:
                return {
                    "schema": "asset_result.v1",
                    "status": "failed_terminal",
                    "limitations": ["typed_recipe_nested_loop_failed"],
                }
            try:
                decoded = json.loads(nested.text)
            except (TypeError, json.JSONDecodeError):
                decoded = None
            if (
                not isinstance(decoded, dict)
                or decoded.get("schema") != "asset_result.v1"
            ):
                return {
                    "schema": "asset_result.v1",
                    "status": "failed_terminal",
                    "limitations": ["recipe_requires_one_typed_envelope"],
                }
            required_claims = _typed_string_tuple("required_claims")
            if required_claims and decoded.get("status") == "completed":
                observation = decoded.get("data")
                bindings = declared_claim_bindings(
                    required_claims=required_claims,
                    declared_resolved_claims=decoded.get("resolved_claims"),
                    declared_evidence=decoded.get("evidence"),
                )
                if not bindings and decoded.get("observation_preserved") is True:
                    bindings = {
                        claim: (claim,)
                        for claim in required_claims
                    }
                resolved, unresolved, evidence = materialize_validated_claims(
                    observation,
                    required_claims=required_claims,
                    claim_bindings=bindings,
                )
                decoded["resolved_claims"] = resolved
                decoded["unresolved_claims"] = unresolved
                decoded["evidence"] = evidence
            return decoded

        if not recipe.steps:
            return {
                "schema": "asset_result.v1",
                "status": "unsupported",
                "limitations": ["typed_recipe_executor_unavailable"],
            }
        result = await execute_recipe(
            recipe,
            variables,
            timeout=recipe.settings.timeout,
            command_guard=self._command_guard,
            metrics=self._metrics,
        )
        if not result.success:
            return {
                "schema": "asset_result.v1",
                "status": "failed_terminal",
                "limitations": [result.error or "recipe_failed"],
            }
        envelopes: list[dict[str, object]] = []
        for step in result.step_results:
            try:
                decoded = json.loads(step.output)
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                isinstance(decoded, dict)
                and decoded.get("schema") == "asset_result.v1"
            ):
                envelopes.append(decoded)
        if len(envelopes) != 1:
            return {
                "schema": "asset_result.v1",
                "status": "failed_terminal",
                "limitations": ["recipe_requires_one_typed_envelope"],
            }
        return envelopes[0]

    async def _prepare_tool_loop_state(
        self,
        text: str,
        isolated: bool,
        *,
        attachments: list[MultimodalAttachment] | None,
        on_text_delta: TextDeltaCallback | None,
        on_progress: ProgressCallback | None,
        operator_tools: bool = False,
        allow_cron_mutation: bool = True,
        capability_hint: CapabilityDecision | None = None,
        plan: UnifiedTurnPlan | None = None,
        candidates: ContextCandidateSet | None = None,
        evidence_requirement: EvidenceRequirement | None = None,
        turn: TurnExecutionState | None = None,
        forced_skill_names: frozenset[str] | None = None,
        forced_tool_names: frozenset[str] | None = None,
        final_response_schema: dict[str, object] | None = None,
    ) -> ToolLoopState:
        """tool loop runner 입력 상태를 조립한다.

        컨텍스트/RAG/자산 선택/실시간 evidence 준비는 오케스트레이터 경계에 남기고,
        실제 반복 lifecycle은 ``ToolLoopRunner``가 담당하도록 상태 객체만 만든다.

        BIZ-425: ``capability_hint`` 가 주어지면 해당 read-only 자산을 그 유형의
        유일한 후보로 강하게 선택하고 selector LLM 호출을 건너뛴다(Option B —
        직접 실행 대신 강한 자산 힌트로 부작용 위험을 줄이고, 최종 자연어 답변
        생성 경로는 기존 tool loop 로 유지).

        BIZ-495: ``plan``이 주어지면 selected turn과 exact asset/tool allowlist를
        그대로 사용한다. 이 경로에서는 legacy Asset Selector/RAG/fuzzy 확대를
        호출하지 않으며 loop iteration도 같은 immutable scope를 재사용한다.

        BIZ-541: instructions exact recipe의 nested loop는 ``forced_*`` scope로
        delegate skill과 실행 도구를 고정한다. planner/capability hint와 혼용하지
        않아 selector/RAG/다른 runtime tool로 scope가 넓어지지 않게 한다.
        """
        if forced_skill_names is not None:
            if not forced_skill_names:
                raise ValueError("forced skill scope must not be empty")
            if plan is not None or capability_hint is not None:
                raise ValueError("forced skill scope cannot be combined with a plan or hint")
            if forced_tool_names is None or not forced_tool_names:
                raise ValueError("forced skill scope requires a non-empty tool scope")
        if plan is not None and candidates is None:
            raise ValueError("planned tool loop requires context candidates")
        if evidence_requirement is None:
            evidence_requirement = (
                requirement_from_turn_plan(plan)
                if plan is not None
                else no_evidence_requirement()
            )
        evidence_state: EvidenceState = (
            turn.evidence
            if turn is not None and turn.evidence.attempted
            else evidence_requirement.initial_state()
        )
        attempted_collectors: set[str] = set()

        # 현재 시각을 KST로 주입
        from datetime import datetime, timedelta, timezone
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        datetime_context = now_kst.strftime(
            "[현재 시각: %Y-%m-%d %H:%M (%A) KST]"
        )
        execution_text = (
            plan.context.standalone_question
            if plan is not None
            else text
        )
        user_content = f"{datetime_context}\n{execution_text}"
        attachment_note = _format_attachment_context_note(attachments)
        if attachment_note:
            user_content = f"{user_content}\n\n{attachment_note}"
        current_user_message: dict = {"role": "user", "content": user_content}
        if attachments:
            # 이미지 bytes는 현재 turn에만 첨부한다. 영속 대화 저장소에는 대용량
            # 바이너리를 넣지 않고 텍스트 발화만 저장해 RAG/히스토리 오염을 피한다.
            current_user_message["attachments"] = attachments

        # 메시지 구성
        if plan is not None:
            by_id = {
                candidate.turn_id: candidate
                for candidate in candidates.candidates
            }
            selected = [
                by_id[turn_id]
                for turn_id in plan.context.selected_turn_ids
                if turn_id in by_id
            ]
            messages = [
                {"role": candidate.role, "content": candidate.content}
                for candidate in selected
            ]
            messages.append(current_user_message)
            # Planner가 선택하지 않은 과거 발화를 semantic retrieval이 다시
            # 주입하면 context allowlist가 무력화되므로 primary에서는 자동 RAG를
            # 비운다. 필요하면 plan이 search_memory를 명시적으로 허용한다.
            rag_context = ""
        elif isolated:
            messages: list[dict] = [current_user_message]
            rag_context = ""
        else:
            recent = self._store.get_recent(
                limit=self._history_limit,
                session_key=current_session_key_var.get(),
            )
            # BIZ-164 — 과거 턴의 ``role=tool`` 메시지와 assistant 메시지의
            # ``tool_calls`` 필드는 다음 턴의 LLM 입력에서 잘라낸다. 5/10 의
            # ``link-git-summarizer`` 같은 실패 도구 호출이 history 에 남아
            # 있으면 작은 모델이 새 사용자 메시지에서도 같은 도구를 다시
            # 시도해 max-iter 까지 낭비하는 사고(2026-05-12 17:46)가 잡힌다.
            # 현재 ``MessageRole`` 은 user/assistant/system 만 정의하므로 실데이터
            # 에선 no-op 이지만, 향후 store 가 tool 역할을 적재하거나 메시지에
            # ``tool_calls`` 속성이 부착되더라도 누설되지 않도록 명시적으로 거른다.
            # 현재 턴 내부(in-flight)의 tool exchange 는 아래 루프에서 그대로
            # 누적되므로 정보 손실 없음.
            messages = []
            for msg in recent:
                role_value = msg.role.value
                if role_value not in ("user", "assistant", "system"):
                    continue
                messages.append({
                    "role": role_value,
                    "content": msg.content,
                })
            messages.append(current_user_message)
            # 시맨틱 회상: 최근 윈도우에 포함되지 않은 과거 메시지를 추가 컨텍스트로 회수
            recent_contents = {msg.content for msg in recent}
            rag_context = await self._retrieve_relevant_context(
                text, exclude_contents=recent_contents,
            )

        # BIZ-383: 내부 evidence 스킬을 LLM callable 후보(asset 선택/프롬프트)에서 제외.
        active_skills = self._exposable_skills()
        active_recipes = getattr(self, "_recipes", [])
        active_skills_prompt = self._skills_prompt
        active_recipes_prompt = self._format_recipes_for_prompt(active_recipes)
        active_recipes_before_skills = False

        planned_allowed_assets: frozenset[tuple[str, str]] | None = None
        if plan is not None:
            planned_asset_refs = {
                (asset.asset_type, asset.name)
                for asset in plan.capability.supporting_assets
            }
            if plan.capability.primary_asset is not None:
                planned_asset_refs.add(
                    (
                        plan.capability.primary_asset.asset_type,
                        plan.capability.primary_asset.name,
                    )
                )
            planned_allowed_assets = frozenset(planned_asset_refs)
            active_skills = [
                skill
                for skill in active_skills
                if ("skill", skill.name) in planned_allowed_assets
            ]
            active_recipes = [
                recipe
                for recipe in active_recipes
                if ("recipe", recipe.name) in planned_allowed_assets
            ]
            primary = plan.capability.primary_asset
            if (
                plan.capability.coverage.value == "full_coverage"
                and primary is not None
            ):
                if primary.asset_type == "skill":
                    active_skills = [
                        skill for skill in active_skills
                        if skill.name == primary.name
                    ]
                    active_recipes = []
                elif primary.asset_type == "recipe":
                    active_recipes = [
                        recipe for recipe in active_recipes
                        if recipe.name == primary.name
                    ]
                    active_skills = []
                    active_recipes_before_skills = True
                planned_allowed_assets = frozenset(
                    {(primary.asset_type, primary.name)}
                )
            active_skills_prompt = self._format_skills_for_prompt(active_skills)
            active_recipes_prompt = self._format_recipes_for_prompt(active_recipes)

        if forced_skill_names is not None:
            trusted_by_name = {
                skill.name: skill
                for skill in active_skills
                if skill.name in forced_skill_names
            }
            execution_by_name = getattr(self, "_skills_by_name", {})
            active_skills = []
            for name in sorted(forced_skill_names):
                trusted = trusted_by_name.get(name)
                actual = execution_by_name.get(name)
                if trusted is None or actual is None:
                    continue
                if (
                    trusted is not actual
                    or skill_definition_fingerprint(trusted)
                    != skill_definition_fingerprint(actual)
                ):
                    raise ValueError(f"forced skill definition drift: {name}")
                active_skills.append(actual)
            loaded_names = frozenset(skill.name for skill in active_skills)
            if loaded_names != forced_skill_names:
                missing = ",".join(sorted(forced_skill_names - loaded_names))
                raise ValueError(f"forced skill scope unavailable: {missing}")
            trusted_asset_safety = tuple(
                TrustedAssetSafety.from_skill(skill) for skill in active_skills
            )
            unsafe = tuple(
                item.asset_name
                for item in trusted_asset_safety
                if not item.safe_for_exact_read_only
            )
            if unsafe:
                names = ",".join(sorted(unsafe))
                raise ValueError(f"forced skill scope is not read-only safe: {names}")
            active_recipes = []
            active_skills_prompt = self._format_skills_for_prompt(active_skills)
            active_recipes_prompt = ""

        # BIZ-425 — capability 힌트가 실제 후보와 매칭되면 그 자산을 유형 내
        # 유일 후보로 좁힌다. 힌트 자산이 목록에 없으면(리로드 경합 등) 조용히
        # 기존 selector 경로로 폴백한다.
        capability_hinted = False
        if (
            plan is None
            and capability_hint is not None
            and capability_hint.safe_to_auto_execute
        ):
            if capability_hint.asset_type == "skill":
                hinted_skills = [
                    s for s in active_skills
                    if s.name == capability_hint.asset_name
                ]
                if hinted_skills:
                    active_skills = hinted_skills
                    capability_hinted = True
            elif capability_hint.asset_type == "recipe":
                hinted_recipes = [
                    r for r in active_recipes
                    if r.name == capability_hint.asset_name
                ]
                if hinted_recipes:
                    active_recipes = hinted_recipes
                    active_recipes_before_skills = True
                    capability_hinted = True
            if capability_hinted:
                active_skills_prompt = self._format_skills_for_prompt(active_skills)
                active_recipes_prompt = self._format_recipes_for_prompt(active_recipes)
                logger.info(
                    "Capability hint applied: %s:%s narrowed candidates "
                    "(skills=%d recipes=%d); selector bypassed",
                    capability_hint.asset_type,
                    capability_hint.asset_name,
                    len(active_skills),
                    len(active_recipes),
                )

        asset_selection = (
            None
            if plan is not None or capability_hinted or forced_skill_names is not None
            else await self._select_assets_for_turn(text, active_skills, active_recipes)
        )
        if asset_selection is not None and not asset_selection.fallback_required:
            selected_skills, selected_recipes = filter_assets_by_selection(
                skills=active_skills,
                recipes=active_recipes,
                selection=asset_selection,
                skill_top_k=int(self._asset_selection_config["skill_top_k"]),
                recipe_top_k=int(self._asset_selection_config["recipe_top_k"]),
            )
            if selected_skills or selected_recipes:
                active_skills = selected_skills
                active_recipes = selected_recipes
                active_skills_prompt = self._format_skills_for_prompt(selected_skills)
                active_recipes_prompt = self._format_recipes_for_prompt(selected_recipes)
                active_recipes_before_skills = bool(selected_recipes)
        elif asset_selection is not None and asset_selection.fallback_required:
            fallback_top_k = int(self._asset_selection_config.get("fallback_top_k", 0))
            if fallback_top_k > 0:
                active_skills = active_skills[:fallback_top_k]
                active_recipes = active_recipes[:fallback_top_k]
                active_skills_prompt = self._format_skills_for_prompt(active_skills)
                active_recipes_prompt = self._format_recipes_for_prompt(active_recipes)

        # 시스템 프롬프트는 페르소나/스킬과 RAG 회상 블록을 합친 결과.
        # BIZ-252 — Claude 의 prompt caching 을 위해 세그먼트 단위로도 함께 보낸다.
        # cache 경계: 페르소나 끝 / 스킬 목록 끝. ReAct 지시문과 RAG 블록은 마커 뒤에 둔다.
        system_blocks = self._build_system_blocks(
            rag_context=rag_context,
            skills_prompt=active_skills_prompt,
            recipes_prompt=active_recipes_prompt,
            recipes_before_skills=active_recipes_before_skills,
        )
        system_prompt = self._flatten_system_blocks(system_blocks)
        scopes = (
            (ToolScope.RUNTIME, ToolScope.OPERATOR, ToolScope.DEVELOPMENT)
            if operator_tools
            else (ToolScope.RUNTIME,)
        )
        # BIZ-424: MCP 서버 연결 보장 후, 호출 가능한 tool이 있을 때만 mcp_call 노출
        await self._ensure_mcp_connected()
        tools = build_tool_definitions(
            active_skills,
            cron_available=self._cron_scheduler is not None,
            scopes=scopes,
            operator_gate=operator_tools,
            browser_handoff_available=bool(
                self._browser_handoff_config.get("enabled", False)
            ),
            mcp_available=self._mcp_call_available(operator_tools=operator_tools),
        )
        execution_scope = None
        if forced_skill_names is not None:
            tools = filter_tool_definitions(
                tools,
                allowed_names=forced_tool_names or (),
            )
            execution_scope = ToolExecutionScope(
                allowed_tools=frozenset(tool.name for tool in tools),
                allowed_assets=frozenset(
                    ("skill", name) for name in forced_skill_names
                ),
                operator_tools=False,
                allow_cron_mutation=False,
                max_tool_calls=1,
                trusted_asset_safety=trusted_asset_safety,
            )
        elif plan is not None:
            tools = filter_tool_definitions(
                tools,
                allowed_names=plan.execution.allowed_tools,
            )
            execution_scope = ToolExecutionScope(
                allowed_tools=frozenset(tool.name for tool in tools),
                allowed_assets=planned_allowed_assets or frozenset(),
                operator_tools=operator_tools,
                allow_cron_mutation=allow_cron_mutation,
            )
            if turn is not None:
                turn.execution_scope = execution_scope
        effective_on_text_delta = on_text_delta
        if plan is not None and (
            plan.fact_check.required
            or plan.execution.mode
            in {
                ExecutionMode.ANSWER_WITH_EVIDENCE,
                ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
            }
        ):
            effective_on_text_delta = None

        return ToolLoopState(
            user_content=user_content,
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            system_blocks=system_blocks,
            execution_scope=execution_scope,
            selected_turn_ids=(
                plan.context.selected_turn_ids if plan is not None else ()
            ),
            previous_mutation_snapshot=self._mutation_tracker.snapshot(),
            on_text_delta=effective_on_text_delta,
            on_progress=on_progress,
            operator_tools=operator_tools,
            allow_cron_mutation=allow_cron_mutation,
            evidence_requirement=evidence_requirement,
            evidence_state=evidence_state,
            attempted_collectors=attempted_collectors,
            turn=turn,
            final_response_schema=final_response_schema,
        )

    async def _run_tool_loop_result(
        self,
        text: str,
        isolated: bool = False,
        *,
        attachments: list[MultimodalAttachment] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
        on_progress: ProgressCallback | None = None,
        operator_tools: bool = False,
        allow_cron_mutation: bool = True,
        capability_hint: CapabilityDecision | None = None,
        plan: UnifiedTurnPlan | None = None,
        candidates: ContextCandidateSet | None = None,
        evidence_requirement: EvidenceRequirement | None = None,
        turn: TurnExecutionState | None = None,
        forced_skill_names: frozenset[str] | None = None,
        forced_tool_names: frozenset[str] | None = None,
        final_response_schema: dict[str, object] | None = None,
    ) -> ToolLoopResult:
        """Native Function Calling 루프를 실행하고 structured result를 반환한다.

        LLM에 도구 정의(tools)와 함께 메시지를 전송하고,
        tool_calls가 반환되면 실행 후 결과를 메시지에 추가하여 재호출한다.
        텍스트만 반환되면 최종 응답으로 반환한다.

        Args:
            text: 사용자 원본 메시지
            isolated: True면 대화 이력 없이 독립 실행 (크론 잡 등). 크론 분기는
                ``final_only_for_cron`` 정책에 따라 호출 측에서 ``on_text_delta`` 를
                None 으로 넘긴다 — 본 함수는 콜백 유무로만 동작 분기.
            on_text_delta: BIZ-259 — 텍스트 델타 콜백. 주어지면 라우터의 ``stream()``
                경로로 전환되어 각 iteration 의 텍스트 델타가 콜백으로 흐른다.
                tool-call iteration 의 ReAct thought 텍스트도 그대로 흐르므로 sink
                측에서 finalize 시 최종 텍스트로 덮어쓰는 패턴을 따른다.
        """
        state = await self._prepare_tool_loop_state(
            text,
            isolated,
            attachments=attachments,
            on_text_delta=on_text_delta,
            on_progress=on_progress,
            operator_tools=operator_tools,
            allow_cron_mutation=allow_cron_mutation,
            capability_hint=capability_hint,
            plan=plan,
            candidates=candidates,
            evidence_requirement=evidence_requirement,
            turn=turn,
            forced_skill_names=forced_skill_names,
            forced_tool_names=forced_tool_names,
            final_response_schema=final_response_schema,
        )
        result = await ToolLoopRunner(self).run(state)
        return result

    async def _tool_loop(
        self,
        text: str,
        isolated: bool = False,
        *,
        attachments: list[MultimodalAttachment] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
        on_progress: ProgressCallback | None = None,
        operator_tools: bool = False,
        allow_cron_mutation: bool = True,
        capability_hint: CapabilityDecision | None = None,
        evidence_requirement: EvidenceRequirement | None = None,
    ) -> str:
        """기존 호출자를 위한 문자열 compatibility wrapper."""
        result = await self._run_tool_loop_result(
            text,
            isolated,
            attachments=attachments,
            on_text_delta=on_text_delta,
            on_progress=on_progress,
            operator_tools=operator_tools,
            allow_cron_mutation=allow_cron_mutation,
            capability_hint=capability_hint,
            evidence_requirement=evidence_requirement,
        )
        return result.text

    # ------------------------------------------------------------------
    # Asset selector
    # ------------------------------------------------------------------

    async def _select_assets_for_turn(
        self,
        text: str,
        skills: list[SkillDefinition],
        recipes: list[RecipeDefinition],
    ) -> AssetSelectionResult | None:
        """설정이 켜진 경우 selector LLM으로 이번 turn의 자산 후보를 축소한다.

        selector는 사용자 응답 경로를 막지 않는 best-effort 보조 호출이다. 설정이
        꺼져 있거나 후보군이 작거나 호출/정규화가 실패하면 None을 반환해 기존 전체
        후보 프롬프트와 도구 스키마를 그대로 사용한다.
        """
        cfg = self._asset_selection_config
        if not cfg.get("enabled", False):
            return None
        known_assets = build_selector_assets(skills, recipes)
        if not known_assets:
            return None
        if len(known_assets) <= int(cfg.get("bypass_below_count", 0)):
            logger.info(
                "Asset selector bypassed: candidates=%d threshold=%d",
                len(known_assets),
                int(cfg.get("bypass_below_count", 0)),
            )
            return None

        prompt = build_selector_prompt(
            user_message=text,
            known_assets=known_assets,
            skill_top_k=int(cfg["skill_top_k"]),
            recipe_top_k=int(cfg["recipe_top_k"]),
        )
        try:
            response = await self._router.send(
                LLMRequest(
                    system_prompt=load_system_prompt("asset_selector").system_prompt,
                    user_message=prompt,
                    backend_name=str(cfg["backend"]),
                    tools=[build_selector_tool_definition()],
                    max_tokens=int(cfg["max_tokens"]),
                    usage_task="asset_selector",
                )
            )
            result = normalize_selector_response(
                user_message=text,
                known_assets=known_assets,
                response_text=response.text or "",
                tool_calls=response.tool_calls,
                top_k=int(cfg["skill_top_k"]) + int(cfg["recipe_top_k"]),
                min_confidence=float(cfg["min_confidence"]),
            )
        except Exception as exc:
            logger.warning("Asset selector failed; falling back to capped assets: %s", exc)
            return AssetSelectionResult(
                fallback_required=True,
                fallback_reason="selector_error",
            )

        if result.fallback_required:
            logger.info(
                "Asset selector fallback: reason=%s selected=%d",
                result.fallback_reason,
                len(result.selected),
            )
        else:
            logger.info("Asset selector selected %d candidate(s)", len(result.selected))
        return result

    # ------------------------------------------------------------------
    # Active Memory tool
    # ------------------------------------------------------------------

    async def _search_memory(self, args: dict) -> str:
        """Active Memory 도구 dispatch 를 전용 모듈에 위임한다."""
        return await memory_search.search_memory(self, args)

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def _dispatch_tool_call(
        self,
        tool_call: ToolCall,
        *,
        operator_tools: bool = False,
        allow_cron_mutation: bool = True,
        execution_scope: ToolExecutionScope | None = None,
    ) -> str:
        """ToolCall 라우팅을 전용 모듈에 위임한다."""
        return await tool_dispatch.dispatch_tool_call(
            self,
            tool_call,
            operator_tools=operator_tools,
            allow_cron_mutation=allow_cron_mutation,
            execution_scope=execution_scope,
        )


    @staticmethod
    def _progress_identity_for_tool_call(tool_call: ToolCall) -> tuple[str, str]:
        """ToolCall 을 사용자 표시용 progress 종류/이름으로 축약한다."""
        name = tool_call.name
        args = tool_call.arguments or {}
        if name == "cli":
            return "command", "cli"
        if name == "execute_skill":
            skill_name = str(args.get("skill_name") or "execute_skill")
            if args.get("command"):
                return "command", skill_name
            return "skill", skill_name
        return "tool", name

    def pop_pending_clarify(self, chat_id: int) -> ClarifyRequest | None:
        """채널이 ``process_message`` 후 호출 — pending clarify 를 회수·제거한다.

        BIZ-260: 한 chat 의 다음 메시지가 도착하기 전까지 ``_pending_clarify[chat_id]``
        에 머무르지만, 채널이 인라인 키보드 렌더에 성공하면 즉시 제거해 다음 호출이
        깨끗한 상태에서 시작되도록 한다. 인라인 키보드를 지원하지 않는 채널
        (webhook 등) 은 이 메서드를 호출하지 않고 ``format_user_visible`` 텍스트를
        그대로 사용자에게 노출한다.
        """
        return self._pending_clarify.pop(chat_id, None)

    async def _dispatch_external_skill(
        self,
        args: dict,
        *,
        allowed_skill_names: frozenset[str] | None = None,
        resolved_skill: SkillDefinition | None = None,
    ) -> str:
        """execute_skill 도구 dispatch 를 전용 모듈에 위임한다."""
        return await skill_dispatch.dispatch_external_skill(
            self,
            args,
            allowed_skill_names=allowed_skill_names,
            resolved_skill=resolved_skill,
        )

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    # BIZ-252 — Anthropic prompt caching 경계.
    # 시스템 프롬프트를 (persona, skills, rag, react) 세그먼트로 쪼개되,
    # 각 세그먼트의 trailing separator 를 텍스트에 포함시켜 단순 합치기(``"".join``)가
    # 기존 ``"\n\n---\n\n".join(parts)`` 와 byte-identical 한 결과를 내도록 한다.
    # 이 덕분에 Claude 가 content blocks 리스트로 받아도, 비-Claude 프로바이더가
    # 평탄화 문자열로 받아도 동일한 prefix 가 노출된다.
    _SYSTEM_BLOCK_SEPARATOR = "\n\n---\n\n"

    @staticmethod
    def _format_runtime_paths_for_prompt(
        config_path: Path,
        *,
        persona_config: dict,
        agent_config: dict,
        daemon_config: dict,
        recipes_config: dict,
    ) -> str:
        """live 배포 repo와 런타임 state 경로를 시스템 프롬프트용으로 요약한다.

        BIZ-313: 모델이 ``~/.simpleclaw``(배포 repo/config)와
        ``~/.simpleclaw-agent/default``(대화 DB·레시피·workspace·페르소나 파일)를
        혼동하면 잘못된 파일을 읽거나 새 레시피를 레거시 위치에 쓰게 된다. 이 블록은
        config 로더가 실제로 반환한 경로만 노출해 운영 설정과 프롬프트를 맞춘다.
        """
        deploy_repo = config_path.expanduser().resolve().parent
        persona_dir = Path(str(persona_config["local_dir"])).expanduser()
        workspace_dir = Path(str(agent_config["workspace_dir"])).expanduser()
        recipes_dir = Path(str(recipes_config["dir"])).expanduser()
        conversation_db = Path(str(agent_config["db_path"])).expanduser()
        daemon_db = Path(str(daemon_config["db_path"])).expanduser()
        return load_system_prompt("runtime_paths").format_field(
            deploy_repo=deploy_repo,
            persona_dir=persona_dir,
            conversation_db=conversation_db,
            daemon_db=daemon_db,
            recipes_dir=recipes_dir,
            workspace_dir=workspace_dir,
        )

    def _build_system_blocks(
        self,
        rag_context: str = "",
        *,
        skills_prompt: str | None = None,
        recipes_prompt: str = "",
        recipes_before_skills: bool = False,
    ) -> list[SystemBlock]:
        """페르소나·스킬·RAG·ReAct 지시문을 세그먼트(SystemBlock)로 반환한다.

        캐시 경계:
          - 1차: 페르소나 끝 (AGENT.md + USER.md + MEMORY.md)
          - 2차: 스킬 목록 끝
        ReAct 지시문과 RAG 블록은 캐시 마커 뒤에 둔다 (RAG 는 요청마다 변하므로
        무효화 회피, ReAct 는 작아 별도 마커가 불필요).

        Args:
            rag_context: ``_retrieve_relevant_context()`` 결과. 빈 문자열이면 블록을 생략한다.
        """
        # (text, cache) 쌍을 모은 뒤 마지막 블록을 제외한 모든 블록 끝에 separator 를 부착한다.
        segments: list[tuple[str, bool]] = []
        if self._persona_prompt:
            segments.append((self._persona_prompt, True))
        if self._runtime_paths_prompt:
            segments.append((self._runtime_paths_prompt, True))
        effective_skills_prompt = self._skills_prompt if skills_prompt is None else skills_prompt
        if recipes_before_skills and recipes_prompt:
            segments.append((recipes_prompt, False))
        if effective_skills_prompt:
            # recipe 우선 노출 시 동적 recipe 블록 뒤의 skill 블록은 더 이상
            # 정적 prefix가 아니므로 cache marker를 붙이지 않는다.
            segments.append((effective_skills_prompt, not recipes_before_skills))
        if not recipes_before_skills and recipes_prompt:
            segments.append((recipes_prompt, False))
        if rag_context:
            segments.append((rag_context, False))
        segments.append((_TOOL_USAGE_INSTRUCTION, False))

        blocks: list[SystemBlock] = []
        last = len(segments) - 1
        for idx, (text, cache) in enumerate(segments):
            suffix = self._SYSTEM_BLOCK_SEPARATOR if idx < last else ""
            blocks.append(SystemBlock(text=text + suffix, cache=cache))
        return blocks

    @staticmethod
    def _flatten_system_blocks(blocks: list[SystemBlock]) -> str:
        """``_build_system_blocks`` 결과를 단일 문자열로 합친다.

        각 블록 텍스트가 자체 separator 를 포함하므로 빈 문자열로 합쳐도
        기존 ``_build_system_prompt`` 와 byte-identical 한 결과를 낸다.
        """
        return "".join(b.text for b in blocks)

    def _build_system_prompt(self, rag_context: str = "") -> str:
        """레거시 단일-문자열 system prompt API.

        ``_build_system_blocks`` + ``_flatten_system_blocks`` 를 합친 얇은 래퍼.
        BIZ-252 이전 호출자(tests, docs) 호환용. 신규 호출 경로는
        ``_build_system_blocks`` 를 사용해 prompt caching 경계를 보존해야 한다.
        """
        return self._flatten_system_blocks(self._build_system_blocks(rag_context=rag_context))

    async def _retrieve_relevant_context(
        self,
        user_text: str,
        exclude_contents: set[str] | None = None,
    ) -> str:
        """과거 대화 RAG와 Dreaming 장기기억 회수를 service에 위임한다."""
        return await self._context_retrieval.retrieve(user_text, exclude_contents)

    # ------------------------------------------------------------------
    # Skill execution
    # ------------------------------------------------------------------

    def _exposable_skills(self) -> list[SkillDefinition]:
        """LLM에 callable로 노출 가능한 스킬만 추린다.

        BIZ-383: ``realtime-lookup-skill`` 은 오케스트레이터가 LLM 루프 밖에서 직접
        실행해 evidence 만 주입하는 내부 스킬이다. LLM이 이를 일반 ``execute_skill``
        대상으로 다시 호출하면 의도와 다른 raw 호출/중복 실행이 생기므로, 프롬프트
        목록과 asset 선택 후보에서 제외한다. 내부 실행은 ``_skills_by_name`` 을 쓰는
        ``_resolve_skill_name`` 으로 그대로 가능하다.
        """
        return [s for s in self._skills if s.name != _REALTIME_LOOKUP_SKILL_NAME]

    def _resolve_skill_name(self, name: str) -> SkillDefinition | None:
        """LLM이 반환한 스킬 이름을 등록된 스킬과 fuzzy-match한다."""
        if name in self._skills_by_name:
            return self._skills_by_name[name]

        lower = name.lower()
        for key, skill in self._skills_by_name.items():
            if key.lower() == lower:
                return skill

        normalized = lower.replace(" ", "-")
        for key, skill in self._skills_by_name.items():
            if key.lower() == normalized:
                return skill

        for key, skill in self._skills_by_name.items():
            if lower.replace("-", "").replace(" ", "") in key.lower().replace("-", ""):
                return skill

        return None

    def _resolve_command_timeout(self, command: str) -> int:
        """명령 timeout 결정을 command_dispatch 에 위임한다."""
        return command_dispatch.resolve_command_timeout(self, command)

    @staticmethod
    def _call_invokes_agent_browser(tool_call: ToolCall) -> bool:
        """agent-browser 호출 판별을 command_dispatch 에 위임한다."""
        return command_dispatch.call_invokes_agent_browser(tool_call)

    @staticmethod
    def _is_agent_browser_command(command: str) -> bool:
        """agent-browser 명령 판별을 command_dispatch 에 위임한다."""
        return command_dispatch.is_agent_browser_command(command)

    @staticmethod
    def _is_composite_agent_browser_chain(command: str) -> bool:
        """agent-browser composite chain 판별을 command_dispatch 에 위임한다."""
        return command_dispatch.is_composite_agent_browser_chain(command)

    @staticmethod
    def _agent_browser_npx_fallback_command(
        command: str, stderr: str,
    ) -> str | None:
        """agent-browser npx fallback 결정을 command_dispatch 에 위임한다."""
        return command_dispatch.agent_browser_npx_fallback_command(command, stderr)

    async def _execute_command(self, skill_name: str, command: str) -> str:
        """셸 명령 실행을 command_dispatch 에 위임한다."""
        return await command_dispatch.execute_command(self, skill_name, command)

    async def _execute_skill(
        self, skill_name: str, args_str: str
    ) -> str | None:
        """등록 스킬 실행. realtime source는 내장 web-fetch 보안 정책을 재사용한다."""
        if skill_name.lower() == _REALTIME_LOOKUP_SKILL_NAME:
            from simpleclaw.agent.builtin_tools import (
                handle_web_fetch,
                resolve_web_page_link,
            )

            payload = decode_realtime_lookup_payload(args_str)

            async def fetch_page(url: str) -> str:
                return await handle_web_fetch(
                    {"url": url},
                    headless_binary=self._headless_binary,
                )

            async def resolve_news_url(candidate) -> str | None:
                title = candidate.title
                suffix = f" - {candidate.source}"
                title = title.removesuffix(suffix)
                if not candidate.source_url:
                    return None
                return await resolve_web_page_link(candidate.source_url, title)

            result = await run_realtime_lookup(
                payload,
                fetch_page=fetch_page,
                resolve_news_url=resolve_news_url,
            )
            return json.dumps(result, ensure_ascii=False)
        return await skill_dispatch.execute_registered_skill(self, skill_name, args_str)

    # ------------------------------------------------------------------
    # Skill formatting
    # ------------------------------------------------------------------

    def _format_recipes_for_prompt(self, recipes: list[RecipeDefinition]) -> str:
        """시스템 프롬프트용 레시피 후보 목록을 생성한다.

        selector가 recipe를 고른 경우 main LLM이 `/recipe-name` 명령 경로와 구분해
        레시피 존재 여부만 참고할 수 있도록 읽기 전용 컨텍스트로 노출한다.
        """
        if not recipes:
            return ""
        lines = [*load_system_prompt("recipe_listing").prompt.splitlines(), ""]
        for recipe in recipes:
            desc = recipe.description or recipe.instructions[:160]
            lines.append(f"- **{recipe.name}**: {desc}")
            if recipe.parameters:
                params = ", ".join(param.name for param in recipe.parameters)
                lines.append(f"  Parameters: {params}")
        return "\n".join(lines)

    def _format_skills_for_prompt(self, skills: list[SkillDefinition]) -> str:
        """시스템 프롬프트용 스킬 개요 생성을 skill_dispatch 에 위임한다."""
        return skill_dispatch.format_skills_for_prompt(skills)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize_skill_command(self, command: str) -> str:
        """스킬 명령 정규화를 skill_dispatch 에 위임한다."""
        return skill_dispatch.normalize_skill_command(self, command)

    @staticmethod
    def _find_venv_python(script_path: Path) -> Path | None:
        """스크립트 인근 venv python 탐색을 skill_dispatch 에 위임한다."""
        return skill_dispatch.find_venv_python(script_path)

    def _load_skills_config(self) -> dict:
        """config.yaml에서 skills 섹션을 로드한다."""
        import yaml
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("skills", {}) if isinstance(data, dict) else {}
        except (yaml.YAMLError, OSError):
            return {}
