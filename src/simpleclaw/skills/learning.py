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
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*['\"]?[^'\"\s]{8,}"
    ),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
)
_RISK_KEYWORDS = {
    "network": ("requests.", "httpx.", "urllib", "curl ", "wget "),
    "subprocess": ("subprocess", "os.system", "popen("),
    "file_write": ("open(", "write_text", "write_bytes", ".write("),
    "external_api": ("api_key", "authorization", "bearer "),
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
    def from_dict(cls, raw: dict[str, Any]) -> "SkillTraceStepSnapshot":
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
    ) -> "SkillSuggestion":
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
    def from_dict(cls, raw: dict[str, Any]) -> "SkillSuggestion":
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
    return f"""You are drafting a reusable SimpleClaw runtime skill from a successful tool trace.
Return only valid JSON. Do not include secrets, credentials, tokens, personal data, or exact raw private observations.
Do not invent external side effects. Prefer a documentation-first SKILL.md and optional scripts that are safe to review.

JSON contract:
{{"title":"short human-readable title","rationale":"why this trace is reusable","skill_name":"kebab-case-name","skill_md":"---\\nname: kebab-case-name\\ndescription: ...\\n---\\n# ...","scripts":{{"scripts/main.py":"# optional safe script\\n"}},"references":{{"references/notes.md":"optional context"}},"risk_flags":["network","external_api"]}}

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
    scripts = payload.get("scripts") if isinstance(payload.get("scripts"), dict) else {}
    references = (
        payload.get("references") if isinstance(payload.get("references"), dict) else {}
    )
    skill_md = str(
        payload.get("skill_md")
        or f"---\nname: {skill_name}\ndescription: Generated skill candidate.\n---\n# {skill_name}\n"
    )
    risk_flags = sorted(
        set(payload.get("risk_flags") or []) | set(detect_risk_flags(skill_md, scripts))
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
    lowered_md = skill_md.lower()
    if (
        "description:" not in lowered_md
        and "## when to use" not in lowered_md
        and "## trigger" not in lowered_md
    ):
        errors.append(
            "SKILL.md should include description or when-to-use/trigger guidance."
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


def _frontmatter_name(skill_md: str) -> str | None:
    m = re.match(r"^---\s*\n(.+?)\n---\s*\n", skill_md, re.DOTALL)
    if not m:
        return None
    n = re.search(r"(?m)^name:\s*['\"]?([^'\"\n]+)", m.group(1))
    return n.group(1).strip() if n else None


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
