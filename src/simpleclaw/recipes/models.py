"""Data models for the recipe system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StepType(Enum):
    """Type of recipe step."""
    PROMPT = "prompt"
    COMMAND = "command"


@dataclass
class RecipeParameter:
    """A parameter defined in a recipe."""
    name: str
    description: str = ""
    required: bool = True
    default: str = ""


@dataclass
class RecipeStep:
    """A single step in a recipe."""
    step_type: StepType
    name: str = ""
    content: str = ""


@dataclass
class RecipeDefinition:
    """A complete recipe parsed from recipe.yaml."""
    name: str
    description: str = ""
    parameters: list[RecipeParameter] = field(default_factory=list)
    steps: list[RecipeStep] = field(default_factory=list)
    recipe_dir: str = ""


@dataclass
class StepResult:
    """Result of a single step execution."""
    step_name: str
    success: bool = True
    output: str = ""
    error: str = ""


@dataclass
class RecipeResult:
    """Result of a complete recipe execution."""
    recipe_name: str
    success: bool = True
    step_results: list[StepResult] = field(default_factory=list)
    failed_step: str = ""
    error: str = ""


class RecipeError(Exception):
    """Base class for recipe errors."""


class RecipeParseError(RecipeError):
    """Recipe YAML parsing or validation error."""


class RecipeExecutionError(RecipeError):
    """Recipe step execution failed."""
