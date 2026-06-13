"""DreamingPipeline service 분리 구조 회귀 테스트."""

from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory import (
    dreaming_active_projects,
    dreaming_cluster_pipeline,
    dreaming_language,
    dreaming_preflight,
    dreaming_runner,
)
from simpleclaw.memory import insight_meta


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
