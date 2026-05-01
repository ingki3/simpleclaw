#!/usr/bin/env python3
"""평문 시크릿(.env / config.yaml)을 시크릿 매니저로 이전하는 마이그레이션 도구.

실행 흐름:
1. ``--env-file``과 ``--config``에서 시크릿 후보를 수집한다.
2. 각 항목을 어디로 옮길지 사용자에게 물어본다 (keyring / file / 건너뛰기).
3. 선택한 백엔드로 시크릿을 저장한다.
4. ``config.yaml``의 평문 값을 참조 문자열로 치환한다 (``--rewrite-config``).
5. ``.env``는 백업본을 남기고 사용자에게 직접 삭제하도록 안내한다 — 자동 삭제는
   복구 불가 위험이 크므로 명시적인 두 단계 절차를 따른다.

기본 시나리오:

    $ python scripts/migrate_secrets.py --backend keyring

    [1/3] llm.providers.claude.api_key (현재: 평문)
      → keyring에 'claude_api_key'로 저장하시겠습니까? [Y/n]:

비대화형 모드(``--non-interactive``)에서는 ``--backend``가 지정한 백엔드로
모든 시크릿을 일괄 이전하며, 이미 같은 키가 있으면 덮어쓴다.
"""

from __future__ import annotations

import argparse
import getpass
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

# scripts/는 sys.path에 없으므로 프로젝트 루트를 명시적으로 추가한다.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from simpleclaw.security.secrets import (  # noqa: E402
    SecretReference,
    SecretsError,
    SecretsManager,
)

# config.yaml 안에서 시크릿으로 취급할 (path, label) 쌍.
# path는 점 표기법, label은 keyring 키 이름의 기본값으로 쓰인다.
_SECRET_FIELDS: list[tuple[tuple[str, ...], str]] = [
    (("telegram", "bot_token"), "telegram_bot_token"),
    (("webhook", "auth_token"), "webhook_auth_token"),
]


def _walk_secret_paths(config: dict) -> list[tuple[tuple[str, ...], str, str]]:
    """config.yaml에서 시크릿 후보 (path, suggested_key, current_value)를 수집한다."""
    found: list[tuple[tuple[str, ...], str, str]] = []

    # llm.providers.<name>.api_key — provider마다 키 이름이 달라 동적으로 처리.
    providers = (
        (config.get("llm") or {}).get("providers")
        if isinstance(config.get("llm"), dict)
        else None
    )
    if isinstance(providers, dict):
        for pname, pcfg in providers.items():
            if isinstance(pcfg, dict) and isinstance(pcfg.get("api_key"), str):
                found.append(
                    (
                        ("llm", "providers", pname, "api_key"),
                        f"{pname}_api_key",
                        pcfg["api_key"],
                    )
                )

    # 정적으로 알려진 위치들
    for path, label in _SECRET_FIELDS:
        node: Any = config
        for part in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(part)
        if isinstance(node, str):
            found.append((path, label, node))

    return found


def _set_in_path(config: dict, path: tuple[str, ...], value: str) -> None:
    """점 경로 위치에 새 값을 기록한다 — config.yaml 재작성 시 사용."""
    node = config
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


def _parse_env_file(env_path: Path) -> dict[str, str]:
    """``.env``의 KEY=VALUE 라인을 단순 파싱한다.

    python-dotenv 의존성을 강제하지 않기 위해 자체 파서를 쓴다 — 따옴표/이스케이프
    같은 복잡한 케이스는 처리하지 않으나, 일반적인 ``KEY=value`` 패턴은 충분.
    """
    out: dict[str, str] = {}
    if not env_path.is_file():
        return out
    pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        m = pattern.match(line)
        if not m:
            continue
        key = m.group(1)
        val = m.group(2).strip()
        # 양 끝 따옴표 제거 — 정확한 dotenv 호환은 아니지만 실용 수준.
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        out[key] = val
    return out


def _confirm(prompt: str, default: bool = True) -> bool:
    """yes/no 프롬프트 — 비대화형 환경에서는 기본값을 즉시 반환한다."""
    if not sys.stdin.isatty():
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _prompt_value(prompt: str, *, secret: bool) -> str:
    """현재 값을 직접 입력받는다 — .env에서 읽지 못한 항목 보충용."""
    if secret:
        return getpass.getpass(prompt)
    return input(prompt)


def _rewrite_config_yaml(
    config_path: Path,
    updates: list[tuple[tuple[str, ...], str]],
) -> None:
    """config.yaml의 시크릿 필드를 참조 문자열로 치환한다.

    YAML 라이브러리로 파싱→재작성 시 코멘트와 키 순서가 손실된다. 사용자가
    직접 정리한 코멘트를 지키고 싶을 가능성이 높으므로 정규식 기반 in-place 치환을
    시도하고, 실패 시 전체 재작성으로 폴백한다.
    """
    text = config_path.read_text(encoding="utf-8")
    new_text = text
    failed: list[tuple[tuple[str, ...], str]] = []
    for path, new_value in updates:
        leaf = path[-1]
        # ``  api_key: "sk-..."`` 또는 ``api_key: sk-...`` 패턴.
        pattern = re.compile(
            rf"^(?P<indent>\s*){re.escape(leaf)}:\s*(?P<value>.*?)\s*(?:#.*)?$",
            re.MULTILINE,
        )
        # 단순 leaf만으로 충돌(예: 같은 key가 여러 곳)할 수 있으므로 신중히 한 번만 치환.
        matches = list(pattern.finditer(new_text))
        if len(matches) != 1:
            failed.append((path, new_value))
            continue
        m = matches[0]
        replacement = f'{m.group("indent")}{leaf}: "{new_value}"'
        new_text = new_text[: m.start()] + replacement + new_text[m.end() :]

    if failed:
        # 정규식 치환이 모호한 항목은 전체 재작성으로 폴백 — 코멘트 유실 알림.
        print(
            "[경고] 다음 필드는 코멘트를 보존하면서 치환할 수 없어 전체 재작성으로 폴백합니다:"
        )
        for path, _ in failed:
            print(f"  - {'.'.join(path)}")
        loaded = yaml.safe_load(new_text) or {}
        for path, new_value in failed:
            _set_in_path(loaded, path, new_value)
        new_text = yaml.safe_dump(loaded, allow_unicode=True, sort_keys=False)

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup)
    config_path.write_text(new_text, encoding="utf-8")
    print(f"[OK] config.yaml 갱신 완료. 원본은 {backup}에 백업됨.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SimpleClaw 평문 시크릿을 keyring/암호화 파일로 이전합니다."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="대상 config.yaml 경로 (기본: ./config.yaml)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="평문 .env 경로 (기본: ./.env)",
    )
    parser.add_argument(
        "--backend",
        choices=("keyring", "file"),
        default="keyring",
        help="기본 저장 백엔드 (기본: keyring)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="확인 없이 일괄 이전 (CI/스크립트용).",
    )
    parser.add_argument(
        "--rewrite-config",
        action="store_true",
        help="config.yaml의 평문 필드를 참조 문자열로 치환한다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="변경 사항 미리 보기 — 실제 저장/수정은 하지 않는다.",
    )
    args = parser.parse_args()

    if not args.config.is_file():
        print(f"[오류] config.yaml을 찾을 수 없습니다: {args.config}")
        return 1

    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    env_values = _parse_env_file(args.env_file)
    candidates = _walk_secret_paths(config)

    if not candidates:
        print("[정보] 이전할 시크릿 필드를 찾지 못했습니다.")
        return 0

    manager = SecretsManager()
    updates: list[tuple[tuple[str, ...], str]] = []

    print(f"이전 대상: {len(candidates)}개 필드")
    for idx, (path, suggested_key, current_value) in enumerate(candidates, 1):
        path_str = ".".join(path)
        print(f"\n[{idx}/{len(candidates)}] {path_str}")

        # 이미 참조 문자열이면 건너뛴다 — 멱등성 보장.
        ref = SecretReference.parse(current_value) if current_value else None
        if ref is not None:
            print(f"  이미 참조({current_value})로 설정되어 있어 건너뜁니다.")
            continue

        # 현재 값 결정: config.yaml 평문 > .env 매핑 > 사용자 입력
        value = current_value
        if not value:
            # .env에서 흔한 이름으로 추정 — 정확하지 않으면 사용자가 보충.
            for key in (suggested_key.upper(), suggested_key):
                if key in env_values:
                    value = env_values[key]
                    print(f"  .env에서 '{key}' 매핑을 발견했습니다.")
                    break

        if not value and not args.non_interactive:
            value = _prompt_value(
                f"  '{suggested_key}' 값을 입력하세요(빈 줄 = 건너뛰기): ",
                secret=True,
            )

        if not value:
            print("  값이 없어 건너뜁니다.")
            continue

        # 백엔드/키 이름 확인
        if args.non_interactive:
            scheme = args.backend
            key_name = suggested_key
        else:
            print(
                f"  → '{suggested_key}'로 {args.backend} 백엔드에 저장합니다."
            )
            if not _confirm("  진행할까요?", default=True):
                print("  건너뜁니다.")
                continue
            scheme = args.backend
            key_name = suggested_key

        if args.dry_run:
            print(f"  [dry-run] {scheme}:{key_name} ← (값 길이 {len(value)})")
        else:
            try:
                manager.store(scheme, key_name, value)
            except SecretsError as exc:
                print(f"  [오류] 저장 실패: {exc}")
                continue
            print(f"  [OK] {scheme}:{key_name}에 저장됨.")

        updates.append((path, f"{scheme}:{key_name}"))

    if not updates:
        print("\n변경 사항이 없습니다.")
        return 0

    if args.rewrite_config and not args.dry_run:
        _rewrite_config_yaml(args.config, updates)
    else:
        print("\n다음 참조로 config.yaml을 수동 갱신하세요 (또는 --rewrite-config):")
        for path, ref in updates:
            print(f"  {'.'.join(path)}: \"{ref}\"")

    if args.env_file.is_file() and not args.dry_run:
        print(
            f"\n[권장] 시크릿이 keyring으로 이전되었습니다. "
            f"이제 {args.env_file}의 평문 항목을 직접 삭제하거나 파일을 제거하세요."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
