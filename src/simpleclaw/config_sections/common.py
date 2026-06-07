"""config_sections 공통 유틸리티.

시크릿 참조 해소는 여러 subsystem(LLM, channel, admin API)이 공유하므로
순환 import 없이 이 모듈에 둔다.
"""

from __future__ import annotations

import logging

from simpleclaw.security.secrets import SecretReference, resolve_secret

logger = logging.getLogger(__name__)


def _resolve_secret_field(value: object) -> str:
    """config.yaml에서 읽은 시크릿 필드 값을 실제 시크릿으로 해소한다.

    - ``None`` 또는 비문자열 → 빈 문자열
    - 참조 문자열(``"env:..."`` 등) → 백엔드에서 조회
    - 평문 → 그대로 반환하되, 비어있지 않으면 보안 경고 로그를 남긴다.
    """
    if not isinstance(value, str) or not value:
        return ""

    ref = SecretReference.parse(value)
    if ref is None:
        # 평문이 들어있으면 마이그레이션을 권장하는 경고를 남긴다 — 한 번 보고
        # 사용자가 인지할 수 있도록 logger.warning으로 발신.
        logger.warning(
            "config.yaml에 평문 시크릿이 감지되었습니다. "
            "보안을 위해 'env:NAME', 'keyring:NAME', 'file:NAME' 참조로 마이그레이션하세요. "
            "(scripts/migrate_secrets.py 참고)"
        )
        return value
    return resolve_secret(value)
