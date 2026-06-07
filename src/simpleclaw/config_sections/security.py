"""Security config loader.

시크릿 볼트/마스터키 경로를 expanduser 가능한 절대 문자열로 정규화한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

def load_security_config(config_path: str | Path) -> dict:
    """config.yaml에서 security 섹션을 로드한다.

    BIZ-302 후속 — ``vault_path`` / ``master_key_path`` 키가 있으면 ``~`` 확장 후
    절대경로로 반환한다. 두 키는 ``EncryptedFileBackend`` 의 시크릿 볼트와 마스터
    키 파일 위치를 가리키며, 부트스트랩(``configure_default_manager``)에 전달된다.
    """
    config_path = Path(config_path)
    if not config_path.is_file():
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    sec = data.get("security", {})
    if not isinstance(sec, dict):
        return {}

    for key in ("vault_path", "master_key_path"):
        value = sec.get(key)
        if isinstance(value, str) and value:
            sec[key] = str(Path(value).expanduser())
        elif value is not None:
            sec[key] = None
    return sec
