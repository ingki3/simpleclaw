"""성공한 tool trace를 recipe(반복 실행 절차) 후보로 추상화하는 학습 보조 모듈.

``SkillSuggestion`` 이 "새 능력/도구 사용법" 후보라면, ``RecipeSuggestion`` 은
"반복 실행 절차/워크플로" 후보다. 산출물(``SKILL.md`` vs ``recipe.yaml``),
검증 방식(loader/render smoke), 승인 질문, cron/반복 실행 리스크가 다르므로
skill learning 큐와 분리된 별도 pending 큐로 관리한다 (BIZ-428).

설계 결정:
- v1은 ``instructions`` 기반 recipe 후보만 지원한다. ``steps`` 기반 후보는
  command allowlist/보안 설계가 필요하므로 validation error로 남긴다.
- 후보 LLM 출력은 BIZ-427 structured output(``response_schema``)으로 강제한다.
- ``cron_hint`` 는 metadata로만 저장한다 — 실제 cron job 생성은 별도 승인 범위.
- trace snapshot/redaction/복잡도 판정은 ``skills.learning`` 의 공용 helper를
  재사용하되, 모델/검증/승인 UX는 recipe 전용으로 유지한다.
- 이 모듈은 후보 생성/저장만 담당한다. live ``recipes.dir`` 설치는 operator
  승인 후 ``recipe_learning`` tool의 materialize 경로에서만 수행된다.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# skill learning과 공유하는 trace snapshot/redaction helper — 후보 큐는 분리해도
# 원본 민감값을 저장하지 않는 redaction 정책은 한 소스로 유지한다.
from simpleclaw.skills.learning import (
    SkillTraceStepSnapshot,
    _contains_secret_like,
    _redact_text,
    detect_risk_flags,
)

logger = logging.getLogger(__name__)

RecipeSuggestionStatus = str
VALID_RECIPE_SUGGESTION_STATUSES = ("pending", "accepted", "rejected", "materialized")

# recipe_generate와 동일한 name 규칙 — materialize가 같은 install policy를
# 재사용하므로 후보 단계에서 미리 같은 규칙으로 정규화/검증한다.
_RECIPE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# BIZ-427 — required/propertyOrdering 을 한 소스로 유지하기 위한 필드 순서.
# propertyOrdering 은 Gemini 2.0 계열에서 structured output 안정성에 필요하다.
_RECIPE_SUGGESTION_FIELDS = [
    "title",
    "rationale",
    "recipe_name",
    "description",
    "trigger",
    "instructions",
    "required_skills",
    "parameters",
    "cron_hint",
    "risk_flags",
]

_RECIPE_PARAMETER_FIELDS = ["name", "description", "required", "default"]

# BIZ-428 — recipe 후보 LLM 출력용 JSON Schema. 스키마는 문법(shape)만 보장하며
# name 정규화/v1 instructions-only 같은 semantic 검증은
# validate_recipe_suggestion_plan() 이 담당한다.
RECIPE_SUGGESTION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "description": (
        "A reusable SimpleClaw recipe (repeatable workflow) candidate drafted "
        "from one successful tool trace."
    ),
    "properties": {
        "title": {
            "type": "string",
            "description": "Short human-readable title of the workflow.",
        },
        "rationale": {
            "type": "string",
            "description": "Why this trace is worth saving as a repeatable recipe.",
        },
        "recipe_name": {
            "type": "string",
            "description": (
                "kebab-case recipe name matching ^[a-z0-9][a-z0-9_-]{0,63}$. "
                "No path separators."
            ),
        },
        "description": {
            "type": "string",
            "description": "One-line recipe description shown to the operator.",
        },
        "trigger": {
            "type": "string",
            "description": (
                "Comma-separated user phrasings that should trigger this recipe. "
                "Empty string if unclear."
            ),
        },
        "instructions": {
            "type": "string",
            "description": (
                "Recipe instructions body describing the repeatable procedure. "
                "Reference parameters as {{ name }}. Must not contain secrets."
            ),
        },
        "required_skills": {
            "type": "array",
            "description": "Runtime skill names the recipe relies on; empty if none.",
            "items": {"type": "string"},
            "maxItems": 8,
        },
        "parameters": {
            "type": "array",
            "description": (
                "Inputs that vary between runs (query, date, target...). Empty "
                "if the workflow takes no inputs."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Parameter name referenced as {{ name }}.",
                    },
                    "description": {
                        "type": "string",
                        "description": "What the parameter controls.",
                    },
                    "required": {
                        "type": "boolean",
                        "description": "Whether the caller must provide it.",
                    },
                    "default": {
                        "type": "string",
                        "description": "Default value; empty string if none.",
                    },
                },
                "required": _RECIPE_PARAMETER_FIELDS,
                "propertyOrdering": _RECIPE_PARAMETER_FIELDS,
            },
            "maxItems": 8,
        },
        "cron_hint": {
            "type": "string",
            "description": (
                "Optional 5-field cron expression if the workflow looks periodic "
                "(e.g. '0 8 * * *'). Empty string if not periodic. This is "
                "metadata only; no cron job is created automatically."
            ),
        },
        "risk_flags": {
            "type": "array",
            "description": (
                "Risk labels such as network, external_api, subprocess, "
                "file_write; empty if none."
            ),
            "items": {"type": "string"},
            "maxItems": 8,
        },
    },
    "required": _RECIPE_SUGGESTION_FIELDS,
    "propertyOrdering": _RECIPE_SUGGESTION_FIELDS,
}


@dataclass
class RecipeSuggestion:
    """운영자 검토를 기다리는 recipe(workflow) 후보."""

    id: str
    title: str
    rationale: str
    trace_fingerprint: str
    recipe_name: str
    recipe_yaml: str
    required_skills: list[str] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)
    cron_hint: str | None = None
    source_msg_ids: list[int] = field(default_factory=list)
    trace: list[SkillTraceStepSnapshot] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    status: RecipeSuggestionStatus = "pending"
    materialized_path: str | None = None
    reject_reason: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def new_pending(
        cls,
        *,
        title: str,
        rationale: str,
        trace_fingerprint: str,
        recipe_name: str,
        recipe_yaml: str,
        required_skills: list[str] | None = None,
        parameters: list[dict[str, Any]] | None = None,
        cron_hint: str | None = None,
        source_msg_ids: list[int] | None = None,
        trace: list[SkillTraceStepSnapshot] | None = None,
        risk_flags: list[str] | None = None,
        validation_errors: list[str] | None = None,
    ) -> "RecipeSuggestion":
        now = datetime.now()
        return cls(
            uuid.uuid4().hex,
            title,
            rationale,
            trace_fingerprint,
            normalize_recipe_name(recipe_name),
            recipe_yaml,
            list(required_skills or []),
            [dict(p) for p in (parameters or []) if isinstance(p, dict)],
            (cron_hint or "").strip() or None,
            list(source_msg_ids or []),
            list(trace or []),
            sorted(set(risk_flags or [])),
            list(validation_errors or []),
            "pending",
            None,
            None,
            now,
            now,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trace"] = [s.to_dict() for s in self.trace]
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RecipeSuggestion":
        created, updated = raw.get("created_at"), raw.get("updated_at")
        trace_raw = raw.get("trace") if isinstance(raw.get("trace"), list) else []
        params_raw = (
            raw.get("parameters") if isinstance(raw.get("parameters"), list) else []
        )
        cron_hint = raw.get("cron_hint")
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            title=str(raw.get("title") or "Untitled recipe suggestion"),
            rationale=str(raw.get("rationale") or ""),
            trace_fingerprint=str(raw.get("trace_fingerprint") or ""),
            recipe_name=normalize_recipe_name(
                str(raw.get("recipe_name") or "recipe-suggestion")
            ),
            recipe_yaml=str(raw.get("recipe_yaml") or ""),
            required_skills=[str(v) for v in (raw.get("required_skills") or [])],
            parameters=[dict(p) for p in params_raw if isinstance(p, dict)],
            cron_hint=str(cron_hint).strip() or None if cron_hint else None,
            source_msg_ids=[int(v) for v in (raw.get("source_msg_ids") or [])],
            trace=[
                SkillTraceStepSnapshot.from_dict(v)
                for v in trace_raw
                if isinstance(v, dict)
            ],
            risk_flags=list(raw.get("risk_flags") or []),
            validation_errors=list(raw.get("validation_errors") or []),
            status=str(raw.get("status") or "pending"),
            materialized_path=raw.get("materialized_path"),
            reject_reason=raw.get("reject_reason"),
            created_at=datetime.fromisoformat(created)
            if isinstance(created, str)
            else datetime.now(),
            updated_at=datetime.fromisoformat(updated)
            if isinstance(updated, str)
            else datetime.now(),
        )


def normalize_recipe_name(value: str) -> str:
    """recipe name을 recipe_generate와 동일한 규칙의 kebab-case로 정규화한다."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", (value or "").strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-_")[:64]
    if not _RECIPE_NAME_RE.fullmatch(slug):
        return "recipe-suggestion"
    return slug


def build_recipe_candidate_prompt(
    *, user_text: str, assistant_text: str, trace: list[Any]
) -> str:
    """recipe(반복 워크플로) 후보 초안을 요청하는 프롬프트를 만든다.

    skill 후보 프롬프트가 "새 능력/도구 사용법" 문서화를 요구하는 것과 달리,
    여기서는 반복 실행 가능한 절차/파라미터/트리거/주기(cron hint)를 추출하도록
    recipe semantics 중심으로 지시한다.
    """
    from simpleclaw.skills.learning import snapshots_from_trace

    snapshots = (
        trace
        if (trace and isinstance(trace[0], SkillTraceStepSnapshot))
        else snapshots_from_trace(trace)
    )
    trace_json = json.dumps(
        [s.to_dict() for s in snapshots], ensure_ascii=False, indent=2
    )
    return f"""You are drafting a reusable SimpleClaw recipe (a repeatable workflow) from one successful tool trace.
Return only valid JSON matching the given schema. Do not include secrets, credentials, tokens, or personal data.

A recipe is NOT a new skill: it is a repeatable procedure that orchestrates existing tools/skills.
- instructions: describe the procedure step by step so it can be re-run later, referencing inputs as {{{{ name }}}}.
- parameters: extract only inputs that would vary between runs (query, date, target...). Keep run-specific values out of instructions.
- required_skills: list runtime skill names actually used in the trace; do not invent new skills.
- trigger: short comma-separated user phrasings that should invoke this recipe.
- cron_hint: a 5-field cron expression ONLY if the workflow is clearly periodic (daily report, morning briefing...); otherwise an empty string. It is stored as metadata only.
- Do not include shell commands or executable steps; instructions must be natural-language guidance only.

Original user request:
{_redact_text(user_text)[:2000]}

Assistant final answer:
{_redact_text(assistant_text)[:3000]}

Sanitized tool trace:
{trace_json}"""


def suggestion_from_recipe_payload(
    payload: dict[str, Any],
    *,
    trace_fingerprint_value: str,
    source_msg_ids: list[int],
    trace: list[SkillTraceStepSnapshot],
) -> RecipeSuggestion:
    """LLM 후보 JSON payload를 검증 결과가 포함된 RecipeSuggestion으로 만든다."""
    # 순환 import 방지 — agent.recipe_generate는 recipes.loader를 import한다.
    from simpleclaw.agent.recipe_generate import build_recipe_yaml

    recipe_name = normalize_recipe_name(
        str(payload.get("recipe_name") or payload.get("title") or "recipe-suggestion")
    )
    parameters = [
        dict(p) for p in (payload.get("parameters") or []) if isinstance(p, dict)
    ]
    required_skills = [
        str(v).strip() for v in (payload.get("required_skills") or []) if str(v).strip()
    ]
    cron_hint = str(payload.get("cron_hint") or "").strip() or None
    recipe_yaml = build_recipe_yaml(
        {
            "name": recipe_name,
            "description": str(payload.get("description") or ""),
            "trigger": str(payload.get("trigger") or ""),
            "instructions": str(payload.get("instructions") or ""),
            "skills": required_skills,
            "parameters": parameters,
        }
    )
    risk_flags = set(payload.get("risk_flags") or []) | set(
        detect_risk_flags(recipe_yaml)
    )
    if cron_hint:
        # 반복 실행 후보임을 승인 단계에서 눈에 띄게 남긴다 — cron 생성은 별도 승인.
        risk_flags.add("cron_hint")
    validation_errors = validate_recipe_suggestion_plan(
        recipe_name=recipe_name, recipe_yaml=recipe_yaml
    )
    return RecipeSuggestion.new_pending(
        title=str(payload.get("title") or recipe_name),
        rationale=str(
            payload.get("rationale")
            or "Generated from a successful complex tool trace."
        ),
        trace_fingerprint=trace_fingerprint_value,
        recipe_name=recipe_name,
        recipe_yaml=recipe_yaml,
        required_skills=required_skills,
        parameters=parameters,
        cron_hint=cron_hint,
        source_msg_ids=source_msg_ids,
        trace=trace,
        risk_flags=sorted(risk_flags),
        validation_errors=validation_errors,
    )


def validate_recipe_suggestion_plan(
    *, recipe_name: str, recipe_yaml: str
) -> list[str]:
    """recipe 후보의 정적 오류를 반환한다 (설치 없이 검출 가능한 것만).

    loader/render smoke까지 포함한 전체 검증은 materialize 시점에
    ``validate_recipe_candidate()`` 가 다시 수행한다 — 여기서는 승인 화면에
    보여줄 수 있는 name/YAML/보안 오류를 후보 단계에서 미리 남긴다.
    """
    errors: list[str] = []
    if not _RECIPE_NAME_RE.fullmatch(recipe_name or ""):
        errors.append(
            "recipe_name must match ^[a-z0-9][a-z0-9_-]{0,63}$ and contain no "
            "path separators"
        )
    if Path(recipe_name or "").is_absolute() or ".." in Path(recipe_name or "").parts:
        errors.append(f"Unsafe recipe name: {recipe_name}")
    try:
        data = yaml.safe_load(recipe_yaml or "")
    except yaml.YAMLError as exc:
        errors.append(f"recipe_yaml is not valid YAML: {exc}")
        return errors
    if not isinstance(data, dict):
        errors.append("recipe_yaml must parse to a YAML mapping")
        return errors
    if not str(data.get("instructions") or "").strip():
        errors.append("recipe candidate must include non-empty instructions (v1)")
    if data.get("steps"):
        # v1 범위 제한 — command 실행이 가능한 steps 후보는 별도 보안 설계 필요.
        errors.append(
            "steps-based recipe candidates are out of scope for v1; use "
            "instructions only"
        )
    if _contains_secret_like(recipe_yaml):
        errors.append("Secret-like content detected in recipe_yaml.")
    return errors


class RecipeSuggestionStore:
    """JSONL recipe suggestion sidecar 저장소."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[RecipeSuggestion]:
        if not self._path.is_file():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read recipe suggestions %s: %s", self._path, exc)
            return []
        out = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    out.append(RecipeSuggestion.from_dict(item))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed recipe suggestion line %d: %s", line_no, exc
                )
        return out

    def save_all(self, items: list[RecipeSuggestion]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        tmp.replace(self._path)

    def list_all(self) -> list[RecipeSuggestion]:
        items = self.load()
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return items

    def list_pending(self) -> list[RecipeSuggestion]:
        return [i for i in self.list_all() if i.status == "pending"]

    def get(self, suggestion_id: str) -> RecipeSuggestion | None:
        return next((i for i in self.load() if i.id == suggestion_id), None)

    def upsert_pending(self, suggestion: RecipeSuggestion) -> RecipeSuggestion:
        """같은 trace fingerprint의 pending 후보는 새로 만들지 않고 갱신한다."""
        items = self.load()
        now = datetime.now()
        for idx, existing in enumerate(items):
            if (
                existing.status == "pending"
                and existing.trace_fingerprint == suggestion.trace_fingerprint
            ):
                suggestion.id = existing.id
                suggestion.created_at = existing.created_at
                suggestion.updated_at = now
                items[idx] = suggestion
                self.save_all(items)
                return suggestion
        suggestion.created_at = now
        suggestion.updated_at = now
        items.append(suggestion)
        self.save_all(items)
        return suggestion

    def update_status(
        self,
        suggestion_id: str,
        status: RecipeSuggestionStatus,
        *,
        reject_reason: str | None = None,
        materialized_path: str | None = None,
    ) -> RecipeSuggestion | None:
        if status not in VALID_RECIPE_SUGGESTION_STATUSES:
            raise ValueError(f"Invalid recipe suggestion status: {status}")
        items = self.load()
        for idx, item in enumerate(items):
            if item.id == suggestion_id:
                item.status = status
                item.reject_reason = reject_reason
                item.materialized_path = materialized_path or item.materialized_path
                item.updated_at = datetime.now()
                items[idx] = item
                self.save_all(items)
                return item
        return None


__all__ = [
    "RECIPE_SUGGESTION_RESPONSE_SCHEMA",
    "RecipeSuggestion",
    "RecipeSuggestionStore",
    "VALID_RECIPE_SUGGESTION_STATUSES",
    "build_recipe_candidate_prompt",
    "normalize_recipe_name",
    "suggestion_from_recipe_payload",
    "validate_recipe_suggestion_plan",
]
