"""Tests for recipe loader."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from simpleclaw.recipes.loader import discover_recipes, load_recipe
from simpleclaw.recipes.models import OnErrorPolicy, RecipeParseError, StepType

FIXTURES = Path(__file__).parent.parent / "fixtures" / "recipes"


class TestRecipeLoader:
    def test_loader_import_does_not_trigger_operator_tool_circular_import(self):
        """BIZ-410 — recipes.loader import 순서가 operator tool circular import로 깨지지 않는다."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from simpleclaw.recipes.loader import load_recipe; print('ok')",
            ],
            check=False,
            capture_output=True,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
            },
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"

    def test_load_valid_recipe(self):
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        assert recipe.name == "daily-report"
        assert recipe.description != ""
        assert len(recipe.parameters) == 2
        assert len(recipe.steps) == 2

    def test_parameters_parsed(self):
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        date_param = next(p for p in recipe.parameters if p.name == "date")
        assert date_param.required is True
        format_param = next(p for p in recipe.parameters if p.name == "format")
        assert format_param.required is False
        assert format_param.default == "markdown"

    def test_steps_parsed(self):
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        assert recipe.steps[0].step_type == StepType.COMMAND
        assert recipe.steps[1].step_type == StepType.PROMPT
        assert "${date}" in recipe.steps[0].content

    def test_discover_recipes(self):
        recipes = discover_recipes(FIXTURES)
        assert len(recipes) == 2
        names = {r.name for r in recipes}
        assert "daily-report" in names

    def test_discover_empty_dir(self, tmp_path):
        result = discover_recipes(tmp_path / "nonexistent")
        assert result == []

    def test_missing_name_raises(self, tmp_path):
        recipe_dir = tmp_path / "bad"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text("description: no name")
        with pytest.raises(RecipeParseError, match="missing 'name'"):
            load_recipe(recipe_dir / "recipe.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        recipe_dir = tmp_path / "bad"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(": : invalid yaml [[")
        with pytest.raises(RecipeParseError):
            load_recipe(recipe_dir / "recipe.yaml")

    def test_default_on_error_policy_is_abort(self):
        """on_error 미지정 레시피는 ABORT 정책을 기본값으로 가진다."""
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        assert recipe.on_error == OnErrorPolicy.ABORT

    def test_on_error_and_rollback_parsed(self, tmp_path):
        recipe_dir = tmp_path / "with-policy"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: with-policy\n"
            "on_error: rollback\n"
            "steps:\n"
            "  - type: command\n"
            "    name: A\n"
            "    content: echo a\n"
            "    rollback: echo undo-a\n"
            "  - type: command\n"
            "    name: B\n"
            "    content: echo b\n"
            "    on_error: continue\n"
        )
        recipe = load_recipe(recipe_dir / "recipe.yaml")
        assert recipe.on_error == OnErrorPolicy.ROLLBACK
        assert recipe.steps[0].rollback == "echo undo-a"
        assert recipe.steps[0].on_error is None  # 레시피 기본값을 따름
        assert recipe.steps[1].on_error == OnErrorPolicy.CONTINUE

    def test_invalid_on_error_raises(self, tmp_path):
        recipe_dir = tmp_path / "bad-policy"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: bad-policy\non_error: maybe\nsteps: []\n"
        )
        with pytest.raises(RecipeParseError, match="Invalid on_error"):
            load_recipe(recipe_dir / "recipe.yaml")


class TestStrictStepValidation:
    """BIZ-243 — 미지원 키 무성 폴백과 빈 PROMPT content 가 silent no-op 으로
    이어지지 않도록 로더가 명시적으로 실패한다."""

    def test_unsupported_step_key_raises(self, tmp_path):
        """`prompt:`/`tool:`/`args:` 같이 비슷한 이름의 미지원 키는 로드 시 즉시 실패.

        2026-05-18 cron-krstock-auto 사고 — `tool:`/`args:`/`prompt:` 키가 무성으로
        무시되어 빈 content PROMPT 스텝이 생성, LLM 호출 없이 SUCCESS 종료된 회귀.
        """
        recipe_dir = tmp_path / "krstock-like"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: krstock-like\n"
            "steps:\n"
            "  - name: search_market\n"
            "    tool: news-search-skill\n"
            "    args: \"오늘 한국 증시 시황\"\n"
        )
        with pytest.raises(RecipeParseError) as excinfo:
            load_recipe(recipe_dir / "recipe.yaml")
        msg = str(excinfo.value)
        assert "search_market" in msg
        # 미지원 키 명단을 노출해야 운영자가 즉시 원인을 알 수 있다.
        assert "tool" in msg
        assert "args" in msg

    def test_unsupported_prompt_alias_raises(self, tmp_path):
        """`content:` 대신 `prompt:` 로 작성한 PROMPT 스텝은 명시적으로 거부."""
        recipe_dir = tmp_path / "prompt-alias"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: prompt-alias\n"
            "steps:\n"
            "  - name: summarize\n"
            "    type: prompt\n"
            "    prompt: \"오늘 시황 요약해줘\"\n"
        )
        with pytest.raises(RecipeParseError) as excinfo:
            load_recipe(recipe_dir / "recipe.yaml")
        assert "prompt" in str(excinfo.value)

    def test_empty_prompt_content_raises(self, tmp_path):
        """PROMPT 스텝의 content 가 비면 LLM 입력이 사라져 호출이 스킵된다 — 즉시 실패."""
        recipe_dir = tmp_path / "empty-prompt"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: empty-prompt\n"
            "steps:\n"
            "  - name: ghost\n"
            "    type: prompt\n"
            "    content: \"\"\n"
        )
        with pytest.raises(RecipeParseError, match="empty"):
            load_recipe(recipe_dir / "recipe.yaml")

    def test_missing_prompt_content_raises(self, tmp_path):
        """content 키 자체가 없어도 빈 PROMPT 로 폴백되지 않고 명시적 실패."""
        recipe_dir = tmp_path / "missing-content"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: missing-content\n"
            "steps:\n"
            "  - name: ghost\n"
            "    type: prompt\n"
        )
        with pytest.raises(RecipeParseError, match="empty"):
            load_recipe(recipe_dir / "recipe.yaml")

    def test_whitespace_only_prompt_content_raises(self, tmp_path):
        """공백/줄바꿈만 있는 content 도 LLM 입력으로는 무의미하므로 거부."""
        recipe_dir = tmp_path / "whitespace-prompt"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: whitespace-prompt\n"
            "steps:\n"
            "  - name: ghost\n"
            "    type: prompt\n"
            "    content: \"   \\n\\t  \"\n"
        )
        with pytest.raises(RecipeParseError, match="empty"):
            load_recipe(recipe_dir / "recipe.yaml")

    def test_command_empty_content_still_allowed(self, tmp_path):
        """COMMAND 스텝의 빈 content 는 운영적으로 의미가 있을 수 있어
        검증 대상에서 제외(에러 발생은 PROMPT 한정).

        예: 부수효과만 있는 외부 스크립트 호출 자리 표시자.
        """
        recipe_dir = tmp_path / "empty-cmd"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: empty-cmd\n"
            "steps:\n"
            "  - name: noop\n"
            "    type: command\n"
            "    content: \"\"\n"
        )
        recipe = load_recipe(recipe_dir / "recipe.yaml")
        assert recipe.steps[0].content == ""

    def test_valid_step_keys_accepted(self, tmp_path):
        """``type``/``name``/``content``/``on_error``/``rollback`` 모두 지정해도 통과."""
        recipe_dir = tmp_path / "all-keys"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: all-keys\n"
            "steps:\n"
            "  - type: command\n"
            "    name: full\n"
            "    content: echo hi\n"
            "    on_error: continue\n"
            "    rollback: echo undo\n"
        )
        recipe = load_recipe(recipe_dir / "recipe.yaml")
        assert recipe.steps[0].name == "full"
        assert recipe.steps[0].rollback == "echo undo"


class TestDiscoverWithLegacyFallback:
    """BIZ-202: primary 디렉터리가 비어도 legacy 디렉터리에서 한 번 폴백 로드한다."""

    def _make_recipe(self, parent: Path, name: str) -> None:
        rdir = parent / name
        rdir.mkdir(parents=True)
        (rdir / "recipe.yaml").write_text(
            f"name: {name}\ndescription: x\nsteps: []\n"
        )

    def test_primary_only_when_legacy_missing(self, tmp_path):
        primary = tmp_path / "new"
        self._make_recipe(primary, "alpha")
        recipes = discover_recipes(primary, legacy_dir=tmp_path / "no-such-dir")
        assert {r.name for r in recipes} == {"alpha"}

    def test_legacy_fallback_loads_when_primary_empty(self, tmp_path, caplog):
        primary = tmp_path / "new"
        primary.mkdir()
        legacy = tmp_path / "old"
        self._make_recipe(legacy, "krstock")
        with caplog.at_level("WARNING"):
            recipes = discover_recipes(primary, legacy_dir=legacy)
        names = {r.name for r in recipes}
        assert names == {"krstock"}
        # deprecation 경고가 한 번 떨어져야 한다 — 봉합 한 번/제거 한 번 흐름.
        assert any("DEPRECATED" in rec.message for rec in caplog.records)

    def test_primary_wins_over_legacy_on_name_clash(self, tmp_path):
        """같은 이름이 양쪽에 있으면 primary 가 우선 — 마이그레이션 중 사용자가
        primary 에 손으로 새 버전을 적었다면 그게 살아야 한다."""
        primary = tmp_path / "new"
        legacy = tmp_path / "old"
        self._make_recipe(primary, "krstock")
        # legacy 의 동일 이름은 description 으로 식별 가능하게 차별화
        leg_dir = legacy / "krstock"
        leg_dir.mkdir(parents=True)
        (leg_dir / "recipe.yaml").write_text(
            "name: krstock\ndescription: LEGACY\nsteps: []\n"
        )
        recipes = discover_recipes(primary, legacy_dir=legacy)
        # primary 의 한 건만, description 은 LEGACY 가 아니어야 한다.
        assert len(recipes) == 1
        assert recipes[0].name == "krstock"
        assert recipes[0].description != "LEGACY"

    def test_same_primary_and_legacy_path_no_double_scan(self, tmp_path):
        """primary 와 legacy 가 같은 경로로 들어와도 중복으로 안 잡힌다."""
        primary = tmp_path / "shared"
        self._make_recipe(primary, "only")
        recipes = discover_recipes(primary, legacy_dir=primary)
        assert [r.name for r in recipes] == ["only"]

    def test_no_legacy_passed_means_no_fallback(self, tmp_path):
        primary = tmp_path / "new"
        primary.mkdir()
        recipes = discover_recipes(primary)
        assert recipes == []


class TestRecipeSettingsTimeout:
    """BIZ-423 — recipe.yaml `settings.timeout` 파싱/폴백 검증.

    구현 규약(테스트에 명시):
    - 유효한 양의 int 만 timeout 으로 채택한다.
    - 누락/비정상 값(음수, 0, 문자열, bool, 비-dict settings)은 기본 60초로
      폴백하며, RecipeParseError 로 레시피 전체를 죽이지 않는다.
    """

    def _write_recipe(self, tmp_path, settings_block: str) -> Path:
        rdir = tmp_path / "study-daily"
        rdir.mkdir(parents=True)
        rfile = rdir / "recipe.yaml"
        rfile.write_text(
            "name: study-daily\n"
            "description: command bridge\n"
            + settings_block
            + "steps:\n"
            "  - type: command\n"
            "    name: run\n"
            "    content: echo hi\n",
            encoding="utf-8",
        )
        return rfile

    def test_settings_timeout_preserved(self, tmp_path):
        """live agent-study-daily 와 동일한 settings.timeout: 180 이 보존된다."""
        rfile = self._write_recipe(tmp_path, "settings:\n  timeout: 180\n")
        recipe = load_recipe(rfile)
        assert recipe.settings.timeout == 180

    def test_missing_settings_defaults_to_60(self, tmp_path):
        rfile = self._write_recipe(tmp_path, "")
        recipe = load_recipe(rfile)
        assert recipe.settings.timeout == 60

    def test_negative_timeout_falls_back_to_60(self, tmp_path, caplog):
        rfile = self._write_recipe(tmp_path, "settings:\n  timeout: -1\n")
        with caplog.at_level("WARNING"):
            recipe = load_recipe(rfile)
        assert recipe.settings.timeout == 60
        assert any("timeout" in rec.message for rec in caplog.records)

    def test_zero_timeout_falls_back_to_60(self, tmp_path):
        rfile = self._write_recipe(tmp_path, "settings:\n  timeout: 0\n")
        assert load_recipe(rfile).settings.timeout == 60

    def test_non_numeric_timeout_falls_back_to_60(self, tmp_path, caplog):
        rfile = self._write_recipe(tmp_path, 'settings:\n  timeout: "abc"\n')
        with caplog.at_level("WARNING"):
            recipe = load_recipe(rfile)
        assert recipe.settings.timeout == 60
        assert any("timeout" in rec.message for rec in caplog.records)

    def test_bool_timeout_falls_back_to_60(self, tmp_path):
        """YAML `timeout: true` — bool 은 int 서브클래스지만 timeout 으로 채택 금지."""
        rfile = self._write_recipe(tmp_path, "settings:\n  timeout: true\n")
        assert load_recipe(rfile).settings.timeout == 60

    def test_non_dict_settings_falls_back_without_parse_failure(
        self, tmp_path, caplog,
    ):
        """settings 가 dict 가 아니어도 레시피 파싱 자체는 살아야 한다."""
        rfile = self._write_recipe(tmp_path, "settings: broken\n")
        with caplog.at_level("WARNING"):
            recipe = load_recipe(rfile)
        assert recipe.name == "study-daily"
        assert recipe.settings.timeout == 60
        assert any("settings" in rec.message for rec in caplog.records)
