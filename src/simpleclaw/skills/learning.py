"""성공한 tool trace를 runtime skill 후보로 추상화하는 학습 보조 모듈."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
SkillSuggestionStatus = str
VALID_SKILL_SUGGESTION_STATUSES = ("pending", "accepted", "rejected", "materialized")

# BIZ-429 — Hermes /learn 수준의 authoring standards 상수.
# frontmatter description은 스킬 목록 스캔 시 한 줄 요약으로 쓰이므로 짧게 강제한다.
MAX_SKILL_DESCRIPTION_CHARS = 60
# SKILL.md 권장 섹션 순서(프롬프트 지시용) — 전부 필수는 아니다.
SKILL_MD_SECTION_ORDER = (
    "When to Use",
    "Prerequisites",
    "How to Run",
    "Quick Reference",
    "Procedure",
    "Pitfalls",
    "Verification",
)
# 운영자 승인 판단에 반드시 필요한 최소 섹션 — 없으면 validation error.
REQUIRED_SKILL_MD_SECTIONS = ("When to Use", "Procedure", "Verification")
# 패키지 내 파일 경로 prefix 제한 — SKILL.md 외 파일은 역할별 디렉터리로 강제.
SCRIPT_PATH_PREFIX = "scripts/"
REFERENCE_PATH_PREFIXES = ("references/", "templates/")
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*['\"]?[^'\"\s]{8,}"
    ),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
)
# BIZ-432 — 저장/로그를 허용하는 risk flag 전체 집합. LLM payload 가 임의 문자열
# (secret 포함 가능)을 risk_flags 로 흘려도 이 밖의 값은 어디에도 남지 않는다.
RISK_FLAG_ALLOWLIST = ("network", "subprocess", "file_write", "external_api")
_RISK_KEYWORDS = {
    "network": (
        "requests.",
        "httpx.",
        "aiohttp",
        "urllib",
        "socket.",
        "curl ",
        "wget ",
    ),
    "subprocess": ("subprocess", "os.system", "popen(", "shell=true"),
    "file_write": (
        "open(",
        "write_text",
        "write_bytes",
        ".write(",
        "os.remove",
        "shutil.rmtree",
        "unlink(",
    ),
    "external_api": ("api_key", "x-api-key", "authorization", "bearer "),
}

# BIZ-429 — Gemini structured output 용 SkillSuggestion 후보 JSON Schema.
# 프롬프트-only JSON 이 live 에서 파싱 실패로 fallback 되는 문제를 BIZ-427 의
# schema-constrained 출력으로 차단한다. scripts/references 는 임의 키 매핑을
# structured output 이 표현할 수 없어 {path, content} entry 배열로 받고,
# suggestion_from_candidate_payload() 가 dict 로 변환한다(legacy dict 도 허용).
_SKILL_FILE_ENTRY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Relative file path inside the skill package, e.g. "
                "scripts/main.py or references/notes.md."
            ),
        },
        "content": {"type": "string", "description": "Full UTF-8 file content."},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
    "propertyOrdering": ["path", "content"],
}
_SKILL_SUGGESTION_FIELDS = [
    "title",
    "rationale",
    "skill_name",
    "skill_md",
    "scripts",
    "references",
    "risk_flags",
]
SKILL_SUGGESTION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "description": (
        "One reusable SimpleClaw runtime skill package candidate drafted from "
        "a successful tool trace."
    ),
    "properties": {
        "title": {
            "type": "string",
            "description": "Short human-readable title for operator review.",
        },
        "rationale": {
            "type": "string",
            "description": "Why this trace is reusable as a runtime skill.",
        },
        "skill_name": {
            "type": "string",
            "description": "kebab-case skill package name.",
        },
        "skill_md": {
            "type": "string",
            "description": (
                "Full SKILL.md content with YAML frontmatter (name, "
                "description) followed by the documented procedure."
            ),
        },
        "scripts": {
            "type": "array",
            "description": (
                "Optional helper scripts. Every path must start with scripts/."
            ),
            "items": _SKILL_FILE_ENTRY_SCHEMA,
            "maxItems": 5,
        },
        "references": {
            "type": "array",
            "description": (
                "Optional reference/template files. Every path must start "
                "with references/ or templates/."
            ),
            "items": _SKILL_FILE_ENTRY_SCHEMA,
            "maxItems": 5,
        },
        "risk_flags": {
            "type": "array",
            "description": "Self-declared risk flags from the fixed allowlist.",
            # BIZ-432 — 스키마 단계에서도 allowlist 로 제한해 임의 문자열 유입을 줄인다.
            "items": {"type": "string", "enum": list(RISK_FLAG_ALLOWLIST)},
            "maxItems": 8,
        },
    },
    "required": _SKILL_SUGGESTION_FIELDS,
    "additionalProperties": False,
    "propertyOrdering": _SKILL_SUGGESTION_FIELDS,
}


@dataclass
class SkillTraceStepSnapshot:
    """skill 후보에 저장할 redacted tool trace 한 단계."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    observation_preview: str = ""
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SkillTraceStepSnapshot:
        return cls(
            str(raw.get("tool_name") or raw.get("name") or "unknown"),
            raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {},
            str(raw.get("observation_preview") or ""),
            bool(raw.get("success", True)),
        )


@dataclass
class SkillSuggestion:
    """운영자 검토를 기다리는 skill package 후보."""

    id: str
    title: str
    rationale: str
    trace_fingerprint: str
    skill_name: str
    skill_md: str
    scripts: dict[str, str] = field(default_factory=dict)
    references: dict[str, str] = field(default_factory=dict)
    source_msg_ids: list[int] = field(default_factory=list)
    trace: list[SkillTraceStepSnapshot] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    status: SkillSuggestionStatus = "pending"
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
        skill_name: str,
        skill_md: str,
        scripts: dict[str, str] | None = None,
        references: dict[str, str] | None = None,
        source_msg_ids: list[int] | None = None,
        trace: list[SkillTraceStepSnapshot] | None = None,
        risk_flags: list[str] | None = None,
        validation_errors: list[str] | None = None,
    ) -> SkillSuggestion:
        now = datetime.now()
        return cls(
            uuid.uuid4().hex,
            title,
            rationale,
            trace_fingerprint,
            normalize_skill_name(skill_name),
            skill_md,
            dict(scripts or {}),
            dict(references or {}),
            list(source_msg_ids or []),
            list(trace or []),
            normalize_risk_flags(risk_flags),
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
    def from_dict(cls, raw: dict[str, Any]) -> SkillSuggestion:
        created, updated = raw.get("created_at"), raw.get("updated_at")
        trace_raw = raw.get("trace") if isinstance(raw.get("trace"), list) else []
        scripts_raw = raw.get("scripts") if isinstance(raw.get("scripts"), dict) else {}
        refs_raw = (
            raw.get("references") if isinstance(raw.get("references"), dict) else {}
        )
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            title=str(raw.get("title") or "Untitled skill suggestion"),
            rationale=str(raw.get("rationale") or ""),
            trace_fingerprint=str(raw.get("trace_fingerprint") or ""),
            skill_name=normalize_skill_name(
                str(raw.get("skill_name") or "skill-suggestion")
            ),
            skill_md=str(raw.get("skill_md") or ""),
            scripts={str(k): str(v) for k, v in scripts_raw.items()},
            references={str(k): str(v) for k, v in refs_raw.items()},
            source_msg_ids=[int(v) for v in (raw.get("source_msg_ids") or [])],
            trace=[
                SkillTraceStepSnapshot.from_dict(v)
                for v in trace_raw
                if isinstance(v, dict)
            ],
            # legacy 저장분에 임의 문자열이 남아 있어도 로드 시점에 정화한다.
            risk_flags=normalize_risk_flags(raw.get("risk_flags")),
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


def normalize_risk_flags(
    values: Any, *, allowlist: tuple[str, ...] = RISK_FLAG_ALLOWLIST
) -> list[str]:
    """risk flag 목록을 allowlist 값만 남긴 정렬된 리스트로 정규화한다 (BIZ-432).

    LLM payload 나 legacy 저장 데이터에서 온 risk_flags 는 임의 문자열일 수
    있고 secret-like 값이 섞일 수 있으므로, 대소문자/구분자만 보정한 뒤
    allowlist 밖 값은 전부 버린다. 버린 값 자체는 secret 일 수 있어 로그에도
    남기지 않고 개수만 경고한다. recipe learning 처럼 도메인 전용 플래그가
    추가로 필요한 호출자는 ``allowlist`` 로 자체 상수를 넘긴다 (BIZ-435).
    """
    if not isinstance(values, (list, tuple, set)):
        return []
    kept: set[str] = set()
    dropped = 0
    for value in values:
        normalized = str(value).strip().lower().replace("-", "_")
        if normalized in allowlist:
            kept.add(normalized)
        else:
            dropped += 1
    if dropped:
        logger.warning(
            "Dropped %d non-allowlisted risk flag value(s); values are not logged.",
            dropped,
        )
    return sorted(kept)


def normalize_skill_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "skill-suggestion"


def trace_fingerprint(
    trace: list[Any], *, user_text: str = "", assistant_text: str = ""
) -> str:
    payload = {
        "user": user_text[:500],
        "assistant": assistant_text[:500],
        "trace": [
            {
                "tool_name": getattr(s, "tool_name", ""),
                "arguments": getattr(s, "arguments", {}),
                "success": bool(getattr(s, "success", True)),
            }
            for s in trace
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def snapshots_from_trace(
    trace: list[Any], *, max_observation_chars: int = 1200
) -> list[SkillTraceStepSnapshot]:
    return [
        SkillTraceStepSnapshot(
            str(getattr(s, "tool_name", "unknown")),
            _redact_mapping(getattr(s, "arguments", {}) or {}),
            _redact_text(str(getattr(s, "observation_preview", "")))[
                :max_observation_chars
            ],
            bool(getattr(s, "success", True)),
        )
        for s in trace
    ]


def is_complex_successful_trace(
    trace: list[Any],
    final_text: str,
    *,
    min_tool_calls: int,
    min_distinct_tools: int,
    min_final_chars: int = 500,
) -> bool:
    if (
        len(trace) < min_tool_calls
        or len({str(getattr(s, "tool_name", "")) for s in trace}) < min_distinct_tools
        or len((final_text or "").strip()) < min_final_chars
    ):
        return False
    for s in trace:
        preview = str(getattr(s, "observation_preview", "")).strip().lower()
        if not bool(getattr(s, "success", True)) or preview.startswith(
            ("error", "traceback", "exception", "failed", "도구 실행 실패")
        ):
            return False
    return True


def build_skill_candidate_prompt(
    *, user_text: str, assistant_text: str, trace: list[Any]
) -> str:
    snapshots = (
        trace
        if (trace and isinstance(trace[0], SkillTraceStepSnapshot))
        else snapshots_from_trace(trace)
    )
    trace_json = json.dumps(
        [s.to_dict() for s in snapshots], ensure_ascii=False, indent=2
    )
    section_order = ", ".join(SKILL_MD_SECTION_ORDER)
    return f"""You are drafting a reusable SimpleClaw runtime skill from a successful tool trace.
Return only valid JSON matching the response schema. Do not include secrets, credentials, tokens, personal data, or exact raw private observations.
Do not invent external side effects. Prefer a documentation-first SKILL.md and optional scripts that are safe to review.

Authoring standards (mandatory):
- SKILL.md starts with YAML frontmatter containing `name` (kebab-case, must equal skill_name) and `description`.
- The frontmatter description must be at most {MAX_SKILL_DESCRIPTION_CHARS} characters.
- Order SKILL.md body sections as: {section_order}. "When to Use", "Procedure", and "Verification" are required.
- Describe SimpleClaw native or wrapped tools by their tool name (e.g. web_search, file_manage); do not over-document generic shell utilities.
- Never invent commands, APIs, endpoints, or file paths that do not appear in the trace. Only document what the trace proves works.
- Place helper scripts under scripts/, reference notes under references/, and reusable templates under templates/.
- If a step relies on an OS-specific primitive, gate it explicitly by platform (e.g. "macOS only:").

JSON contract:
{{"title":"short human-readable title","rationale":"why this trace is reusable","skill_name":"kebab-case-name","skill_md":"---\\nname: kebab-case-name\\ndescription: ...\\n---\\n# ...","scripts":[{{"path":"scripts/main.py","content":"# optional safe script\\n"}}],"references":[{{"path":"references/notes.md","content":"optional context"}}],"risk_flags":["network","external_api"]}}

Original user request:
{_redact_text(user_text)[:2000]}

Assistant final answer:
{_redact_text(assistant_text)[:3000]}

Sanitized tool trace:
{trace_json}"""


def suggestion_from_candidate_payload(
    payload: dict[str, Any],
    *,
    trace_fingerprint_value: str,
    source_msg_ids: list[int],
    trace: list[SkillTraceStepSnapshot],
) -> SkillSuggestion:
    skill_name = normalize_skill_name(
        str(payload.get("skill_name") or payload.get("title") or "skill-suggestion")
    )
    scripts = _files_mapping_from_payload(payload.get("scripts"))
    references = _files_mapping_from_payload(payload.get("references"))
    skill_md = str(
        payload.get("skill_md")
        or f"---\nname: {skill_name}\ndescription: Generated skill candidate.\n---\n# {skill_name}\n"
    )
    # LLM 이 선언한 risk_flags 는 allowlist 로 정화한 뒤에만 자체 감지 결과와 합친다.
    risk_flags = sorted(
        set(normalize_risk_flags(payload.get("risk_flags")))
        | set(detect_risk_flags(skill_md, scripts))
    )
    validation_errors = validate_skill_package_plan(
        skill_name=skill_name, skill_md=skill_md, scripts=scripts, references=references
    )
    return SkillSuggestion.new_pending(
        title=str(payload.get("title") or skill_name),
        rationale=str(
            payload.get("rationale")
            or "Generated from a successful complex tool trace."
        ),
        trace_fingerprint=trace_fingerprint_value,
        skill_name=skill_name,
        skill_md=skill_md,
        scripts={str(k): str(v) for k, v in scripts.items()},
        references={str(k): str(v) for k, v in references.items()},
        source_msg_ids=source_msg_ids,
        trace=trace,
        risk_flags=risk_flags,
        validation_errors=validation_errors,
    )


def validate_skill_package_plan(
    *,
    skill_md: str,
    scripts: dict[str, str] | None = None,
    references: dict[str, str] | None = None,
    skill_name: str | None = None,
) -> list[str]:
    errors: list[str] = []
    scripts = scripts or {}
    references = references or {}
    normalized = normalize_skill_name(
        skill_name or _frontmatter_name(skill_md) or "skill-suggestion"
    )
    fm_name = _frontmatter_name(skill_md)
    if fm_name and normalize_skill_name(fm_name) != normalized:
        errors.append("SKILL.md frontmatter name does not match target skill_name.")
    if not fm_name:
        errors.append("SKILL.md must include YAML frontmatter with a name field.")
    description = _frontmatter_description(skill_md)
    if not description:
        errors.append(
            "SKILL.md frontmatter must include a non-empty description field."
        )
    elif len(description) > MAX_SKILL_DESCRIPTION_CHARS:
        errors.append(
            "SKILL.md frontmatter description exceeds "
            f"{MAX_SKILL_DESCRIPTION_CHARS} characters ({len(description)})."
        )
    # 운영자가 승인 판단에 쓰는 핵심 섹션은 heading 으로 존재해야 한다.
    lowered_md = skill_md.lower()
    for section in REQUIRED_SKILL_MD_SECTIONS:
        if f"## {section.lower()}" not in lowered_md:
            errors.append(f"SKILL.md is missing required section: {section}")
    for rel_path in scripts:
        if not rel_path.startswith(SCRIPT_PATH_PREFIX):
            errors.append(f"Script path must start with scripts/: {rel_path}")
    for rel_path in references:
        if not rel_path.startswith(REFERENCE_PATH_PREFIXES):
            errors.append(
                f"Reference path must start with references/ or templates/: {rel_path}"
            )
    for rel_path, body in {"SKILL.md": skill_md, **scripts, **references}.items():
        if _path_has_traversal(rel_path):
            errors.append(f"Unsafe relative path: {rel_path}")
        if _contains_secret_like(body):
            errors.append(f"Secret-like content detected in {rel_path}.")
    return errors


def detect_risk_flags(
    skill_md: str, scripts: dict[str, str] | None = None
) -> list[str]:
    haystack = "\n".join([skill_md, *(scripts or {}).values()]).lower()
    return [
        flag
        for flag, words in _RISK_KEYWORDS.items()
        if any(w in haystack for w in words)
    ]


class SkillSuggestionStore:
    """JSONL skill suggestion sidecar 저장소."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[SkillSuggestion]:
        if not self._path.is_file():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read skill suggestions %s: %s", self._path, exc)
            return []
        out = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    out.append(SkillSuggestion.from_dict(item))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed skill suggestion line %d: %s", line_no, exc
                )
        return out

    def save_all(self, items: list[SkillSuggestion]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        tmp.replace(self._path)

    def list_all(self) -> list[SkillSuggestion]:
        items = self.load()
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return items

    def list_pending(self) -> list[SkillSuggestion]:
        return [i for i in self.list_all() if i.status == "pending"]

    def get(self, suggestion_id: str) -> SkillSuggestion | None:
        return next((i for i in self.load() if i.id == suggestion_id), None)

    def upsert_pending(self, suggestion: SkillSuggestion) -> SkillSuggestion:
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
        status: SkillSuggestionStatus,
        *,
        reject_reason: str | None = None,
        materialized_path: str | None = None,
    ) -> SkillSuggestion | None:
        if status not in VALID_SKILL_SUGGESTION_STATUSES:
            raise ValueError(f"Invalid skill suggestion status: {status}")
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


def _files_mapping_from_payload(value: Any) -> dict[str, str]:
    """structured output 의 {path, content} entry 배열 또는 legacy dict 를 dict 로 정규화한다."""
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    out: dict[str, str] = {}
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict) and entry.get("path"):
                out[str(entry["path"])] = str(entry.get("content") or "")
    return out


def _frontmatter_name(skill_md: str) -> str | None:
    m = re.match(r"^---\s*\n(.+?)\n---\s*\n", skill_md, re.DOTALL)
    if not m:
        return None
    n = re.search(r"(?m)^name:\s*['\"]?([^'\"\n]+)", m.group(1))
    return n.group(1).strip() if n else None


def _frontmatter_description(skill_md: str) -> str | None:
    m = re.match(r"^---\s*\n(.+?)\n---\s*\n", skill_md, re.DOTALL)
    if not m:
        return None
    d = re.search(r"(?m)^description:\s*['\"]?([^'\"\n]+)", m.group(1))
    return d.group(1).strip() if d else None


def _path_has_traversal(rel_path: str) -> bool:
    p = Path(rel_path)
    return p.is_absolute() or ".." in p.parts or rel_path.startswith("~")


def _contains_secret_like(text: str) -> bool:
    return any(p.search(text or "") for p in _SECRET_PATTERNS)


def _redact_text(text: str) -> str:
    out = text or ""
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED_SECRET]", out)
    return out


def _redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, raw in value.items():
        if re.search(r"(?i)(token|secret|password|api[_-]?key)", str(k)):
            out[str(k)] = "[REDACTED_SECRET]"
        elif isinstance(raw, dict):
            out[str(k)] = _redact_mapping(raw)
        elif isinstance(raw, str):
            out[str(k)] = _redact_text(raw)[:500]
        else:
            out[str(k)] = raw
    return out
