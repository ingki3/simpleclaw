"""BIZ-644 — Final Composer runtime persona projection 경계."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from simpleclaw.persona.composition_projection import (
    COMPOSITION_PERSONA_POLICY_VERSION,
    build_composition_persona_projection,
)
from simpleclaw.persona.models import FileType, SourceScope
from simpleclaw.persona.parser import parse_markdown
from simpleclaw.persona.resolver import resolve_persona_files


def _resolved(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "SOUL.md").write_text(
        "# Identity\n\nSimpleClaw\n\n"
        "# Personality\n\n차분함\n\n"
        "# Speaking Style\n\n간결한 한국어 존댓말\n\n"
        "# Core Values\n\n정확성과 정직성\n\n"
        "# Secrets\n\nsoul-private",
        encoding="utf-8",
    )
    (runtime / "AGENT.md").write_text(
        "# Identity\n\n신뢰할 수 있는 조력자\n\n"
        "# Language\n\n한국어 사용\n\n"
        "# Tool Usage Rules\n\nrun-dangerous-tool\n\n"
        "# Directories\n\n/private/runtime/path\n\n"
        "# Integrations\n\nprivate-integration\n\n"
        "# Dreaming Updates\n\nagent-dream",
        encoding="utf-8",
    )
    (runtime / "USER.md").write_text(
        "# Preferences\n\n결론 먼저\n"
        "api_key: sk-fixture-secret-value\n"
        "<!-- hidden-user-comment -->\n\n"
        "# Corrections\n\n불확실성 명시\n\n"
        "# Preferences and Corrections\n\n표는 요청할 때만 사용\n\n"
        "# Stale Memory Guards\n\n오래된 선호는 확인\n\n"
        "# Dreaming Journal\n\nuser-dream\n\n"
        "# Private Profile\n\nprivate-user-id",
        encoding="utf-8",
    )
    (runtime / "MEMORY.md").write_text(
        "# Preferences\n\nraw-memory-secret",
        encoding="utf-8",
    )
    return resolve_persona_files(runtime, tmp_path / "missing")


def test_projection_includes_only_allowlisted_soul_agent_user_sections(tmp_path) -> None:
    projection = build_composition_persona_projection(
        _resolved(tmp_path), token_budget=2048
    )

    assert projection.source_types == (
        FileType.SOUL,
        FileType.AGENT,
        FileType.USER,
    )
    for allowed in (
        "SimpleClaw",
        "차분함",
        "간결한 한국어 존댓말",
        "정확성과 정직성",
        "신뢰할 수 있는 조력자",
        "한국어 사용",
        "결론 먼저",
        "불확실성 명시",
        "표는 요청할 때만 사용",
        "오래된 선호는 확인",
    ):
        assert allowed in projection.instruction_text
    for forbidden in (
        "soul-private",
        "run-dangerous-tool",
        "/private/runtime/path",
        "private-integration",
        "agent-dream",
        "user-dream",
        "private-user-id",
        "raw-memory-secret",
        "hidden-user-comment",
        "sk-fixture-secret-value",
        "managed:dreaming:",
    ):
        assert forbidden not in projection.instruction_text
    assert projection.policy_version == COMPOSITION_PERSONA_POLICY_VERSION
    assert projection.token_count <= projection.token_budget


def test_projection_is_deterministic_budgeted_immutable_and_content_sensitive(
    tmp_path,
) -> None:
    persona_files = _resolved(tmp_path)
    first = build_composition_persona_projection(persona_files, token_budget=80)
    second = build_composition_persona_projection(persona_files, token_budget=80)

    assert first == second
    assert first.token_count <= 80
    with pytest.raises(FrozenInstanceError):
        first.instruction_text = "changed"  # type: ignore[misc]

    runtime = tmp_path / "runtime"
    soul = runtime / "SOUL.md"
    soul.write_text(
        soul.read_text(encoding="utf-8").replace("차분함", "활기참"),
        encoding="utf-8",
    )
    reloaded = build_composition_persona_projection(
        resolve_persona_files(runtime, tmp_path / "missing"),
        token_budget=80,
    )
    assert reloaded.fingerprint != first.fingerprint
    assert reloaded.instruction_text != first.instruction_text


def test_repository_agents_is_never_a_runtime_persona_source(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "# Identity\n\nrepo-agent-secret", encoding="utf-8"
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "AGENT.md").write_text(
        "# Identity\n\nruntime-agent", encoding="utf-8"
    )

    projection = build_composition_persona_projection(
        resolve_persona_files(runtime, tmp_path / "missing"),
        token_budget=2048,
    )

    assert "runtime-agent" in projection.instruction_text
    assert "repo-agent-secret" not in projection.instruction_text


def test_secret_line_is_removed_even_in_an_allowlisted_section(tmp_path) -> None:
    path = tmp_path / "USER.md"
    path.write_text(
        "# Preferences\n\n목록 선호\npassword = fixture-password\n존댓말 선호",
        encoding="utf-8",
    )
    persona = parse_markdown(path, FileType.USER, SourceScope.LOCAL)

    projection = build_composition_persona_projection(
        [persona], token_budget=2048
    )

    assert "목록 선호" in projection.instruction_text
    assert "존댓말 선호" in projection.instruction_text
    assert "fixture-password" not in projection.instruction_text


@pytest.mark.parametrize(
    ("credential_type", "line"),
    (
        pytest.param(
            "authorization_bearer",
            "Authorization: Bearer fixture-token-value",
            id="authorization-bearer",
        ),
        pytest.param(
            "database_uri_userinfo",
            "database_url = postgresql://fixture-user:fixture-password@db.invalid/app",
            id="database-uri-userinfo",
        ),
        pytest.param(
            "aws_access_key",
            "AWS access key: AKIAIOSFODNN7EXAMPLE",
            id="aws-access-key",
        ),
        pytest.param(
            "aws_secret_access_key",
            "aws_secret_access_key = fixture-secret-value",
            id="aws-secret-access-key",
        ),
        pytest.param(
            "google_api_key",
            "Google credential: AIzaFixtureKeyMaterial0123456789",
            id="google-api-key",
        ),
        pytest.param(
            "github_token",
            "GitHub credential: github_pat_fixtureTokenMaterial",
            id="github-token",
        ),
        pytest.param(
            "gitlab_token",
            "GitLab credential: glpat-fixtureTokenMaterial",
            id="gitlab-token",
        ),
        pytest.param(
            "stripe_key",
            "Stripe credential: sk_live_fixtureKeyMaterial",
            id="stripe-key",
        ),
    ),
)
def test_credential_line_types_are_removed_fail_closed(
    tmp_path, credential_type: str, line: str
) -> None:
    path = tmp_path / "USER.md"
    path.write_text(
        f"# Preferences\n\n결론 먼저\n{line}\n존댓말 선호",
        encoding="utf-8",
    )
    persona = parse_markdown(path, FileType.USER, SourceScope.LOCAL)

    projection = build_composition_persona_projection(
        [persona], token_budget=2048
    )

    assert credential_type
    assert "결론 먼저" in projection.instruction_text
    assert "존댓말 선호" in projection.instruction_text
    assert line not in projection.instruction_text


def test_direct_policy_override_cannot_expand_the_maximum_allowlist(tmp_path) -> None:
    path = tmp_path / "AGENT.md"
    path.write_text(
        "# Language\n\n한국어\n\n# Tool Usage Rules\n\nrun-private-tool",
        encoding="utf-8",
    )
    persona = parse_markdown(path, FileType.AGENT, SourceScope.LOCAL)

    projection = build_composition_persona_projection(
        [persona],
        token_budget=2048,
        section_policy={
            "agent": ["Language", "Tool Usage Rules"],
            "soul": [],
            "user": [],
        },
    )

    assert "한국어" in projection.instruction_text
    assert "run-private-tool" not in projection.instruction_text
