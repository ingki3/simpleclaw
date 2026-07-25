"""DreamingPipeline service 분리 구조 회귀 테스트."""

import ast
import importlib
import inspect

import pytest

from simpleclaw.memory import (
    dreaming_active_projects,
    dreaming_cluster_pipeline,
    dreaming_language,
    dreaming_preflight,
    dreaming_runner,
    insight_meta,
)
from simpleclaw.memory.dreaming import DreamingPipeline


def test_dreaming_pipeline_binds_split_service_methods():
    """Facade 클래스가 분리된 service 함수들을 기존 method 이름으로 노출한다."""
    assert DreamingPipeline.run is dreaming_runner.run
    assert DreamingPipeline.summarize is dreaming_language.summarize
    assert DreamingPipeline.create_backup is dreaming_preflight.create_backup
    assert DreamingPipeline.apply_insight_meta is insight_meta.apply_insight_meta
    assert DreamingPipeline.update_active_projects is (
        dreaming_active_projects.update_active_projects
    )
    assert DreamingPipeline.assign_clusters_for_unprocessed is (
        dreaming_cluster_pipeline.assign_clusters_for_unprocessed
    )


def test_dreaming_pipeline_preserves_descriptor_methods():
    """staticmethod/property descriptor 성격이 분리 후에도 유지된다."""
    assert isinstance(DreamingPipeline.__dict__["_format_conversations"], staticmethod)
    assert isinstance(DreamingPipeline.__dict__["insight_store"], property)


@pytest.mark.parametrize(
    "module",
    [
        dreaming_active_projects,
        dreaming_cluster_pipeline,
        dreaming_language,
        dreaming_preflight,
        dreaming_runner,
        insight_meta,
    ],
)
def test_dreaming_service_modules_use_explicit_import_contract(module):
    """분리 서비스가 star import에 기대지 않아 Ruff가 참조 이름을 추적할 수 있다."""
    tree = ast.parse(inspect.getsource(module))

    assert not any(
        isinstance(node, ast.ImportFrom) and node.names[0].name == "*"
        for node in ast.walk(tree)
    )


def test_dreaming_facade_public_import_survives_service_reload():
    """서비스를 다시 import해도 기존 facade 공개 클래스와 바인딩이 유지된다."""
    dreaming = importlib.import_module("simpleclaw.memory.dreaming")

    assert dreaming.DreamingPipeline is DreamingPipeline
    assert DreamingPipeline.run is dreaming_runner.run


def test_dreaming_service_dependency_remains_monkeypatchable(monkeypatch):
    """명시 alias로 바꾼 뒤에도 서비스 모듈의 dependency seam을 patch할 수 있다."""
    sentinel = object()

    monkeypatch.setattr(dreaming_cluster_pipeline, "load_dreaming_prompt", sentinel)

    assert dreaming_cluster_pipeline.load_dreaming_prompt is sentinel
