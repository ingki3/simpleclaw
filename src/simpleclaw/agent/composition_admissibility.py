"""Composer와 Guard가 공유하는 domain-neutral citation 경계 판정."""

from __future__ import annotations

import re
from typing import Literal

ListRootViolation = Literal["mixed_list_roots", "auxiliary_list_root"]
_CONCRETE_INDEX_RE = re.compile(r"\[\d+\]")


def first_concrete_list_root(path: str) -> str | None:
    """첫 concrete index 앞의 list root를 반환한다."""
    match = _CONCRETE_INDEX_RE.search(path)
    return None if match is None else path[: match.start()]


def citation_list_root_violation(
    paths: tuple[str, ...],
    *,
    declared_root: str | None,
) -> ListRootViolation | None:
    """Citation이 하나의 선언된 list root만 사용하는지 판정한다."""
    roots = {
        root
        for path in paths
        if (root := first_concrete_list_root(path)) is not None
    }
    if len(roots) > 1:
        return "mixed_list_roots"
    if roots and (declared_root is None or roots != {declared_root}):
        return "auxiliary_list_root"
    return None
