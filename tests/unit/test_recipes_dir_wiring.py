"""BIZ-202 — 레시피 디렉터리 단일 진실점(`recipes.dir`) 봉합 검증.

검증 포인트:
1. `try_recipe_command` 가 명시된 ``recipes_dir`` 에서 레시피를 발견한다 — 데몬과
   동일한 절대 경로를 봇이 보도록 한다.
2. 봇이 채팅에서 새로 만든 ``<dir>/<name>/recipe.yaml`` 을 다음 명령에서 곧장
   발견한다 (= hot-discovery — 데몬 재기동 불요).
3. 1차 경로에 없으면 레거시 ``.agent/recipes`` 에서 폴백 + deprecation 경고.

여기에 commands/loader 어느 한 쪽의 회귀가 들어와도 즉시 잡힌다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simpleclaw.agent.commands import try_recipe_command


def _write_recipe(recipes_dir: Path, name: str, instructions: str) -> Path:
    """단일 v2(instructions) 레시피를 ``recipes_dir/<name>/recipe.yaml`` 에 쓴다."""
    rdir = recipes_dir / name
    rdir.mkdir(parents=True, exist_ok=True)
    rfile = rdir / "recipe.yaml"
    rfile.write_text(
        f"name: {name}\n"
        f"description: test\n"
        f"steps: []\n"
        f"instructions: |\n  {instructions}\n",
        encoding="utf-8",
    )
    return rfile


class TestTryRecipeCommandUsesConfiguredDir:
    """`/<recipe-name>` 명령이 명시된 디렉터리에서 레시피를 발견하는지."""

    @pytest.mark.asyncio
    async def test_discovers_recipe_from_explicit_dir(self, tmp_path):
        recipes_dir = tmp_path / "home" / "recipes"
        _write_recipe(recipes_dir, "krstock", "fetch korean stock")

        captured = {}

        async def fake_react_loop(rendered):
            captured["rendered"] = rendered
            return "ok"

        result = await try_recipe_command(
            "/krstock",
            fake_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )

        assert result is not None
        assert result[1] == "krstock"
        assert "fetch korean stock" in captured["rendered"]

    @pytest.mark.asyncio
    async def test_does_not_fall_through_to_cwd(self, tmp_path, monkeypatch):
        """CWD 에 ``.agent/recipes/krstock`` 이 있더라도 명시된 디렉터리만 본다 —
        명시된 곳이 비어 있으면 *None* (= '레시피 명령 아님') 으로 끝나야 한다."""
        # CWD 를 tmp 로 옮기고 그 안에 .agent/recipes/krstock 을 만들어 둔다.
        legacy_cwd = tmp_path / "project"
        (legacy_cwd / ".agent" / "recipes").mkdir(parents=True)
        _write_recipe(legacy_cwd / ".agent" / "recipes", "krstock", "WRONG")
        monkeypatch.chdir(legacy_cwd)

        recipes_dir = tmp_path / "home" / "recipes"
        recipes_dir.mkdir(parents=True)

        async def fake_react_loop(_):
            return "ok"

        result = await try_recipe_command(
            "/krstock",
            fake_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )

        # primary 가 비어 있고 legacy 도 비활성 — 명령 미인식.
        assert result is None

    @pytest.mark.asyncio
    async def test_picks_up_new_recipe_without_restart(self, tmp_path):
        """봇이 채팅에서 갓 만든 레시피가 같은 프로세스의 다음 명령에서 곧장
        발견된다 (= hot-discovery — `try_recipe_command` 는 매번 `discover_recipes`
        를 새로 호출한다). 데몬 재시작 없이도 사용자 흐름이 닫힌다."""
        recipes_dir = tmp_path / "home" / "recipes"
        recipes_dir.mkdir(parents=True)

        async def fake_react_loop(_):
            return "ok"

        # 1차 호출: 아직 레시피 없음 → 명령 미인식.
        first = await try_recipe_command(
            "/krstock",
            fake_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )
        assert first is None

        # 봇이 곧이어 같은 디렉터리에 레시피를 작성 (= file_write/cli 시뮬).
        _write_recipe(recipes_dir, "krstock", "fetch korean stock")

        # 2차 호출: 같은 프로세스에서 즉시 발견되어야 한다.
        second = await try_recipe_command(
            "/krstock",
            fake_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )
        assert second is not None
        assert second[1] == "krstock"

    @pytest.mark.asyncio
    async def test_legacy_fallback_finds_pre_migration_recipes(
        self, tmp_path, caplog,
    ):
        """primary 가 비어 있어도 legacy 에서 한 번 폴백 — 마이그레이션 시점의 안전망."""
        recipes_dir = tmp_path / "home" / "recipes"
        recipes_dir.mkdir(parents=True)
        legacy_dir = tmp_path / "project" / ".agent" / "recipes"
        _write_recipe(legacy_dir, "old-recipe", "I am legacy")

        captured = {}

        async def fake_react_loop(rendered):
            captured["rendered"] = rendered
            return "ok"

        with caplog.at_level("WARNING"):
            result = await try_recipe_command(
                "/old-recipe",
                fake_react_loop,
                recipes_dir=recipes_dir,
                legacy_recipes_dir=legacy_dir,
            )

        assert result is not None
        assert result[1] == "old-recipe"
        assert "I am legacy" in captured["rendered"]
        assert any("DEPRECATED" in r.message for r in caplog.records)


class TestRecipeWriteThenLoadIntegration:
    """샌드박스 sandbox-write 시나리오:
    봇 도구가 절대 경로 ``recipes_dir/<name>/recipe.yaml`` 에 직접 작성한 뒤,
    다음 명령 처리에서 발견된다."""

    @pytest.mark.asyncio
    async def test_sandbox_write_then_discover(self, tmp_path):
        # ``recipes_dir`` 는 봇 워크스페이스(`~/.simpleclaw/`) 아래의 절대 경로를
        # 시뮬레이션 — sandbox 가 일반적으로 허용하는 트리.
        sandbox_home = tmp_path / "simpleclaw_home"
        recipes_dir = sandbox_home / "recipes"
        recipes_dir.mkdir(parents=True)

        # === 봇 측 시뮬레이션: file_write 도구가 절대 경로로 recipe.yaml 작성 ===
        target = recipes_dir / "test" / "recipe.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "name: test\n"
            "description: integration\n"
            "steps: []\n"
            "instructions: |\n  run integration test\n",
            encoding="utf-8",
        )

        # === 다음 명령에서 발견되어야 한다 ===
        captured = {}

        async def fake_react_loop(rendered):
            captured["rendered"] = rendered
            return "done"

        result = await try_recipe_command(
            "/test",
            fake_react_loop,
            recipes_dir=recipes_dir,
            legacy_recipes_dir=None,
        )
        assert result is not None
        assert result[1] == "test"
        assert "run integration test" in captured["rendered"]
