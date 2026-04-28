"""tool_schemas 모듈의 단위 테스트.

build_tool_definitions()가 올바른 ToolDefinition 목록을 조립하는지 검증한다.

주요 테스트 시나리오:
- 내장 도구 개수가 cron/스킬 유무에 따라 올바르게 변하는지
- execute_skill 도구가 스킬 목록에 따라 동적으로 포함/제외되는지
- 모든 도구가 ToolDefinition 필수 필드(name, description, parameters)를 갖추는지
- execute_skill 설명에 등록된 스킬 이름이 동적으로 삽입되는지
- 도구 네이밍 컨벤션(언더스코어)이 지켜지는지
"""

from __future__ import annotations

import pytest

from simpleclaw.agent.tool_schemas import build_tool_definitions
from simpleclaw.llm.models import ToolDefinition
from simpleclaw.skills.models import SkillDefinition


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_skill(name: str = "test_skill", description: str = "A test skill") -> SkillDefinition:
    """테스트용 SkillDefinition 생성 헬퍼."""
    return SkillDefinition(
        name=name,
        description=description,
        skill_dir=f"/skills/{name}",
        script_path=f"/skills/{name}/run.sh",
        trigger=f"/{name}",
    )


# ---------------------------------------------------------------------------
# 1. 도구 개수 검증 (cron 유무)
# ---------------------------------------------------------------------------

class TestToolCount:
    """cron 사용 가능 여부에 따른 내장 도구 개수를 검증한다."""

    def test_without_cron_returns_6_tools(self):
        """cron 비활성 시 기본 내장 도구 6개만 반환되어야 한다."""
        tools = build_tool_definitions(skills=[], cron_available=False)
        # 기본 내장 도구: memory_read, memory_write, file_read, file_write, web_search, shell_exec
        assert len(tools) == 6

    def test_with_cron_returns_7_tools(self):
        """cron 활성 시 기본 6개 + cron 1개 = 7개가 반환되어야 한다."""
        tools = build_tool_definitions(skills=[], cron_available=True)
        assert len(tools) == 7


# ---------------------------------------------------------------------------
# 2-3. execute_skill 포함/미포함
# ---------------------------------------------------------------------------

class TestExecuteSkill:
    """스킬 등록 여부에 따른 execute_skill 도구 포함/제외를 검증한다."""

    def test_with_skills_adds_execute_skill(self):
        """스킬이 1개 이상 등록되면 execute_skill 도구가 목록에 포함되어야 한다."""
        skills = [_make_skill("alpha"), _make_skill("beta")]
        tools = build_tool_definitions(skills=skills, cron_available=False)
        tool_names = [t.name for t in tools]
        assert "execute_skill" in tool_names

    def test_without_skills_no_execute_skill(self):
        """등록된 스킬이 없으면 execute_skill 도구가 목록에서 제외되어야 한다."""
        tools = build_tool_definitions(skills=[], cron_available=False)
        tool_names = [t.name for t in tools]
        assert "execute_skill" not in tool_names

    def test_with_skills_total_count(self):
        """스킬이 있으면 기본 6 + execute_skill 1 = 7."""
        skills = [_make_skill()]
        tools = build_tool_definitions(skills=skills, cron_available=False)
        assert len(tools) == 7

    def test_with_skills_and_cron_total_count(self):
        """스킬 + cron이면 기본 6 + cron 1 + execute_skill 1 = 8."""
        skills = [_make_skill()]
        tools = build_tool_definitions(skills=skills, cron_available=True)
        assert len(tools) == 8


# ---------------------------------------------------------------------------
# 4. 내장 도구 필수 필드 검증
# ---------------------------------------------------------------------------

class TestBuiltinToolRequiredFields:
    """모든 내장 도구가 name, description, parameters(type/properties/required)를 가지는지 검증."""

    @pytest.fixture()
    def all_tools(self) -> list[ToolDefinition]:
        skills = [_make_skill()]
        return build_tool_definitions(skills=skills, cron_available=True)

    def test_all_tools_are_tool_definitions(self, all_tools):
        """모든 도구가 ToolDefinition 인스턴스여야 한다."""
        for tool in all_tools:
            assert isinstance(tool, ToolDefinition)

    def test_all_tools_have_name(self, all_tools):
        """모든 도구에 빈 문자열이 아닌 name 필드가 존재해야 한다."""
        for tool in all_tools:
            assert tool.name, f"Tool missing name: {tool}"

    def test_all_tools_have_description(self, all_tools):
        """모든 도구에 빈 문자열이 아닌 description 필드가 존재해야 한다."""
        for tool in all_tools:
            assert tool.description, f"Tool {tool.name} missing description"

    def test_all_tools_have_parameters_type(self, all_tools):
        """모든 도구의 parameters.type이 'object'여야 한다 (JSON Schema 규약)."""
        for tool in all_tools:
            assert tool.parameters.get("type") == "object", (
                f"Tool {tool.name} parameters missing type=object"
            )

    def test_all_tools_have_parameters_properties(self, all_tools):
        """모든 도구의 parameters에 properties 키가 존재해야 한다."""
        for tool in all_tools:
            assert "properties" in tool.parameters, (
                f"Tool {tool.name} parameters missing 'properties'"
            )

    def test_all_tools_have_parameters_required(self, all_tools):
        """모든 도구의 parameters에 required 키가 존재해야 한다."""
        for tool in all_tools:
            assert "required" in tool.parameters, (
                f"Tool {tool.name} parameters missing 'required'"
            )


# ---------------------------------------------------------------------------
# 5. execute_skill 설명에 스킬 이름 동적 포함
# ---------------------------------------------------------------------------

class TestExecuteSkillDescription:
    """execute_skill 도구의 설명에 등록된 스킬 이름이 동적으로 포함되는지 검증한다."""

    def test_description_includes_skill_names(self):
        """execute_skill의 description에 등록된 모든 스킬 이름이 나열되어야 한다."""
        skills = [_make_skill("weather"), _make_skill("translate")]
        tools = build_tool_definitions(skills=skills, cron_available=False)
        exec_tool = next(t for t in tools if t.name == "execute_skill")
        # LLM이 사용 가능한 스킬을 알 수 있도록 설명에 이름이 포함되어야 함
        assert "weather" in exec_tool.description
        assert "translate" in exec_tool.description

    def test_skill_name_parameter_description_includes_names(self):
        """skill_name 파라미터의 description에도 유효한 스킬 이름이 포함되어야 한다."""
        skills = [_make_skill("deploy")]
        tools = build_tool_definitions(skills=skills, cron_available=False)
        exec_tool = next(t for t in tools if t.name == "execute_skill")
        skill_name_desc = exec_tool.parameters["properties"]["skill_name"]["description"]
        assert "deploy" in skill_name_desc


# ---------------------------------------------------------------------------
# 6. cron_available=False 시 cron 도구 제외
# ---------------------------------------------------------------------------

class TestCronExclusion:
    """cron_available 플래그에 따른 cron 도구 포함/제외를 검증한다."""

    def test_cron_excluded_when_not_available(self):
        """cron_available=False이면 cron 도구가 목록에서 제외되어야 한다."""
        tools = build_tool_definitions(skills=[], cron_available=False)
        tool_names = [t.name for t in tools]
        assert "cron" not in tool_names

    def test_cron_included_when_available(self):
        """cron_available=True이면 cron 도구가 목록에 포함되어야 한다."""
        tools = build_tool_definitions(skills=[], cron_available=True)
        tool_names = [t.name for t in tools]
        assert "cron" in tool_names


# ---------------------------------------------------------------------------
# 7. 도구 이름이 언더스코어 사용 (하이픈 미사용)
# ---------------------------------------------------------------------------

class TestToolNamingConvention:
    """도구 이름의 네이밍 컨벤션(언더스코어 사용)을 검증한다."""

    def test_all_tool_names_use_underscores(self):
        """모든 도구 이름에 하이픈이 없고 언더스코어만 사용되어야 한다 (API 호환성)."""
        skills = [_make_skill()]
        tools = build_tool_definitions(skills=skills, cron_available=True)
        for tool in tools:
            # LLM API가 하이픈을 함수 이름으로 허용하지 않는 경우가 있으므로 언더스코어만 사용
            assert "-" not in tool.name, (
                f"Tool name '{tool.name}' contains hyphen; use underscores for API compatibility"
            )
