"""Admin API 감사 로그 — JSONL 일별 로테이션 저장소.

Admin UI(BIZ-37/41)에서 발생하는 모든 설정 변경/시스템 액션을
``~/.simpleclaw/audit/YYYY-MM-DD.jsonl``에 append-only로 기록한다.

설계 결정:

- **append-only + 일별 로테이션**: 라이브 시스템에서 설정 변경 추적은 사후 감사가
  핵심이므로, 파일 회전·잠금·롤링은 두지 않고 매 호출마다 짧게 열고 닫는다.
- **마스킹은 기록 시점**: 시크릿 패턴(``*api_key``/``*_token``/``*_secret`` 등)에
  해당하는 ``before``/``after`` 값은 저장 직전 ``••••<last4>`` 형태로 변환한다.
  이미 ``env:``/``keyring:``/``file:`` 참조 문자열은 그 자체가 비밀이 아니므로
  원형 보존(undo 가능).
- **검색은 메모리 로드**: 일별 파일은 일반적으로 작아서(수백~수천 줄) 정렬·필터를
  Python 메모리에서 처리한다. 다중 일자 조회는 ``since``로 들어오는 epoch 또는
  ISO 시각 이후 N개 파일을 합친다.
- **ID는 UUID4**: 외부 시스템과의 조인 키로 단조 증가가 필요 없고, undo 시점에
  단일 항목을 식별할 수 있으면 충분하다.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# 시크릿으로 보이는 키 이름 패턴 — 마스킹 대상 식별에 사용.
# CLAUDE.md/admin-requirements.md §2.2의 정의를 그대로 옮겼다.
_SECRET_KEY_PATTERN = re.compile(
    r"(api_key|_token$|password|_secret$|secret_key)",
    re.IGNORECASE,
)

# 시크릿 참조 문자열(env:/keyring:/file:/plain:) — 평문이 아니므로 마스킹하지 않는다.
_REFERENCE_PREFIX = re.compile(r"^(env|keyring|file|plain):", re.IGNORECASE)


def _is_secret_key(key: str) -> bool:
    """주어진 dict 키 이름이 시크릿 패턴에 해당하는지 판단한다.

    ``api_key``, ``bot_token``, ``auth_token``, ``password``, ``client_secret``
    등을 잡아낸다. 검색은 case-insensitive substring + 명시 접미사 매칭이다.
    """
    if not isinstance(key, str):
        return False
    return bool(_SECRET_KEY_PATTERN.search(key))


def _mask_value(value: object) -> object:
    """시크릿 값으로 의심되는 문자열을 ``••••<last4>``로 변환한다.

    참조 문자열(``keyring:foo``)은 비밀이 아니므로 원형 유지. 비문자열, 빈 문자열은
    그대로 둔다.
    """
    if not isinstance(value, str) or not value:
        return value
    if _REFERENCE_PREFIX.match(value):
        return value
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


def _mask_secrets(payload: object) -> object:
    """before/after 페이로드를 재귀 순회하며 시크릿 값을 마스킹한다.

    dict/list를 깊이 우선으로 따라가며 시크릿 키에 해당하는 값만 마스킹한다.
    원본 객체를 변형하지 않도록 새 컨테이너를 반환한다.
    """
    if isinstance(payload, dict):
        masked: dict = {}
        for k, v in payload.items():
            if _is_secret_key(k) and isinstance(v, str):
                masked[k] = _mask_value(v)
            else:
                masked[k] = _mask_secrets(v)
        return masked
    if isinstance(payload, list):
        return [_mask_secrets(item) for item in payload]
    return payload


@dataclass
class AuditEntry:
    """단일 감사 항목.

    ``before``/``after``는 PATCH의 의미를 보존하기 위해 *영역 전체 스냅샷*이
    아니라 *변경된 부분 트리*만 담는다(예: ``{"providers": {"claude": {"model": "..."}}}``).
    이렇게 해야 ``undo``가 정확히 ``before`` 부분 트리를 다시 PATCH하면 원복된다.
    """

    id: str = ""
    ts: str = ""
    actor_id: str = "local"
    trace_id: str = ""
    action: str = "config.update"
    area: str = ""
    target: str = ""
    before: object = None
    after: object = None
    outcome: str = "applied"  # applied | dry_run | pending | rejected
    requires_restart: bool = False
    affected_modules: list = field(default_factory=list)
    undoable: bool = True
    reason: str | None = None

    def to_json(self) -> str:
        """한 줄 JSON으로 직렬화한다 — JSONL 추가용."""
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


class AuditLog:
    """일별 JSONL 파일에 감사 항목을 append하고 검색하는 저장소.

    파일 위치: ``base_dir/YYYY-MM-DD.jsonl``. 디렉토리는 0700, 파일은 0600 권한.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        # 기본값은 시크릿 볼트와 같은 ``~/.simpleclaw/audit/`` — 백업/이전을 한 곳에서.
        if base_dir is None:
            base_dir = Path.home() / ".simpleclaw" / "audit"
        self._base_dir = Path(base_dir)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def _ensure_dir(self) -> bool:
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            return True
        except OSError as exc:
            logger.warning("Cannot create audit dir %s: %s", self._base_dir, exc)
            return False

    @staticmethod
    def _file_for_date(base_dir: Path, dt: datetime) -> Path:
        return base_dir / f"{dt.strftime('%Y-%m-%d')}.jsonl"

    # ------------------------------------------------------------------
    # 쓰기
    # ------------------------------------------------------------------

    def append(
        self,
        *,
        action: str,
        area: str,
        target: str = "",
        before: object = None,
        after: object = None,
        outcome: str = "applied",
        requires_restart: bool = False,
        affected_modules: Iterable[str] | None = None,
        undoable: bool = True,
        actor_id: str = "local",
        trace_id: str = "",
        reason: str | None = None,
    ) -> AuditEntry:
        """새 감사 항목을 기록하고 항목을 반환한다.

        시크릿 패턴에 해당하는 ``before``/``after`` 값은 자동 마스킹된다.
        ``id``는 UUID4로 새로 생성된다.
        """
        entry = AuditEntry(
            id=str(uuid.uuid4()),
            ts=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            actor_id=actor_id,
            trace_id=trace_id,
            action=action,
            area=area,
            target=target,
            before=_mask_secrets(before),
            after=_mask_secrets(after),
            outcome=outcome,
            requires_restart=requires_restart,
            affected_modules=list(affected_modules or []),
            undoable=undoable,
            reason=reason,
        )

        if not self._ensure_dir():
            return entry

        path = self._file_for_date(self._base_dir, datetime.now())
        try:
            # 0600으로 새 파일을 만드는 경로 — 이미 존재하는 파일에는 권한을 변경하지 않음.
            existed = path.exists()
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry.to_json() + "\n")
            if not existed:
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
        except OSError as exc:
            logger.warning("Audit write failed (%s): %s", path, exc)

        return entry

    # ------------------------------------------------------------------
    # 읽기 / 검색
    # ------------------------------------------------------------------

    def _iter_files(self, since_dt: datetime | None) -> list[Path]:
        """검색 대상이 되는 일별 파일 목록을 시간 오름차순으로 돌려준다.

        ``since_dt``가 주어지면 그 날짜 이후 파일만 포함한다.
        """
        if not self._base_dir.is_dir():
            return []
        files: list[tuple[str, Path]] = []
        for p in self._base_dir.iterdir():
            if not p.is_file() or not p.name.endswith(".jsonl"):
                continue
            stem = p.stem
            if since_dt is not None and stem < since_dt.strftime("%Y-%m-%d"):
                continue
            files.append((stem, p))
        files.sort(key=lambda x: x[0])
        return [p for _, p in files]

    def search(
        self,
        *,
        since: str | None = None,
        actor: str | None = None,
        area: str | None = None,
        outcome: str | None = None,
        action: str | None = None,
        limit: int = 200,
    ) -> list[AuditEntry]:
        """필터 조건에 맞는 감사 항목을 최근부터 ``limit``개 반환한다.

        ``since``는 ISO 형식 ('2026-05-03', '2026-05-03T00:00:00') 모두 허용.
        파싱 실패 시 ``None``으로 간주한다(전체 검색).
        """
        since_dt: datetime | None = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
            except ValueError:
                # 날짜만 들어온 경우 — 그날 자정으로 해석.
                try:
                    since_dt = datetime.strptime(since[:10], "%Y-%m-%d")
                except ValueError:
                    since_dt = None

        results: list[AuditEntry] = []
        for path in self._iter_files(since_dt):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if since_dt is not None:
                            try:
                                ts = datetime.fromisoformat(data.get("ts", ""))
                            except ValueError:
                                ts = None
                            if ts is not None:
                                # tz 비교 안전화 — naive vs aware 혼용 시 둘 다 naive로.
                                if since_dt.tzinfo is None and ts.tzinfo is not None:
                                    ts = ts.replace(tzinfo=None)
                                if since_dt.tzinfo is not None and ts.tzinfo is None:
                                    ts = ts.replace(tzinfo=since_dt.tzinfo)
                                if ts < since_dt:
                                    continue
                        if actor and data.get("actor_id") != actor:
                            continue
                        if area and data.get("area") != area:
                            continue
                        if outcome and data.get("outcome") != outcome:
                            continue
                        if action and data.get("action") != action:
                            continue

                        try:
                            entry = AuditEntry(**data)
                        except TypeError:
                            continue
                        results.append(entry)
            except OSError:
                continue

        # 최근 항목 우선 — limit은 뒤에서 자른다.
        return results[-limit:]

    def get(self, entry_id: str) -> AuditEntry | None:
        """ID로 단일 항목을 조회한다 (모든 일별 파일 스캔)."""
        for path in self._iter_files(None):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if data.get("id") == entry_id:
                            try:
                                return AuditEntry(**data)
                            except TypeError:
                                return None
            except OSError:
                continue
        return None


__all__ = [
    "AuditEntry",
    "AuditLog",
    "_is_secret_key",
    "_mask_secrets",
    "_mask_value",
]
