"""EmbeddingService 단위 테스트.

검증 범위 (spec 005 Phase 2):
- enabled=False 시 어떤 호출도 None
- sentence-transformers 미설치 시 graceful degradation (load_failed 플래그)
- 모델 로드 실패 후 재시도하지 않음 (반복 실패 레이턴시 방지)
- e5 query/passage 프리픽스가 model.encode에 정확히 전달되는지
- 인코딩 결과가 float32 1-D 벡터로 정규화되는지
- 인코딩 단발 실패 시 None 반환하되 load_failed로 락하지 않음
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import numpy as np

from simpleclaw.memory.embedding_service import EmbeddingService


class TestDisabled:
    def test_disabled_service_returns_none(self):
        svc = EmbeddingService(model_name="dummy", enabled=False)
        assert svc.is_enabled is False
        assert svc.encode_query("hello") is None
        assert svc.encode_passage("hello") is None


class TestLoadFailure:
    def test_missing_sentence_transformers_disables_silently(self):
        svc = EmbeddingService(model_name="dummy", enabled=True)
        # sentence_transformers import를 ImportError로 강제
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            assert svc.encode_query("hello") is None
        assert svc.is_enabled is False  # load_failed 플래그가 켜짐

    def test_load_exception_disables_and_does_not_retry(self):
        svc = EmbeddingService(model_name="bad/model", enabled=True)
        fake_module = MagicMock()
        fake_module.SentenceTransformer = MagicMock(
            side_effect=RuntimeError("download failed")
        )
        call_count = {"n": 0}

        def counting_init(*args, **kwargs):
            call_count["n"] += 1
            raise RuntimeError("download failed")

        fake_module.SentenceTransformer = counting_init

        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            assert svc.encode_query("hello") is None
            assert svc.encode_passage("world") is None
            # 두 번째 호출에서 모델 생성자가 다시 불리지 않아야 함
            assert call_count["n"] == 1
        assert svc.is_enabled is False


class TestEncoding:
    def _patched_module(self, returned_vec: np.ndarray):
        """SentenceTransformer가 항상 returned_vec을 반환하는 가짜 모듈을 만든다."""
        captured: dict = {"calls": []}
        fake_model = MagicMock()
        fake_model.encode = MagicMock(
            side_effect=lambda text, **kw: (
                captured["calls"].append(text) or returned_vec
            )
        )
        fake_module = MagicMock()
        fake_module.SentenceTransformer = MagicMock(return_value=fake_model)
        return fake_module, captured

    def test_encode_query_uses_query_prefix(self):
        svc = EmbeddingService(model_name="dummy", enabled=True)
        vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        fake_module, captured = self._patched_module(vec)

        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            result = svc.encode_query("맥북 가격")

        assert result is not None
        assert captured["calls"] == ["query: 맥북 가격"]
        np.testing.assert_array_equal(result, vec)

    def test_encode_passage_uses_passage_prefix(self):
        svc = EmbeddingService(model_name="dummy", enabled=True)
        vec = np.array([0.5, 0.5], dtype=np.float32)
        fake_module, captured = self._patched_module(vec)

        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            result = svc.encode_passage("맥북은 240만원입니다")

        assert result is not None
        assert captured["calls"] == ["passage: 맥북은 240만원입니다"]

    def test_result_is_float32_1d(self):
        svc = EmbeddingService(model_name="dummy", enabled=True)
        # 모델이 float64를 반환해도 서비스가 float32로 정규화한다
        vec = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        fake_module, _ = self._patched_module(vec)

        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            result = svc.encode_query("test")

        assert result is not None
        assert result.dtype == np.float32
        assert result.ndim == 1

    def test_lazy_load_only_once(self):
        svc = EmbeddingService(model_name="dummy", enabled=True)
        vec = np.array([1.0], dtype=np.float32)
        fake_module, _ = self._patched_module(vec)

        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            svc.encode_query("a")
            svc.encode_query("b")
            svc.encode_passage("c")

        # 모델 생성자는 단 1회만 호출되어야 함 (lazy + cached)
        assert fake_module.SentenceTransformer.call_count == 1

    def test_encode_runtime_failure_returns_none_but_does_not_lock(self):
        """encode 단발 예외는 load_failed로 락하지 않는다(다음 호출은 다시 시도)."""
        svc = EmbeddingService(model_name="dummy", enabled=True)
        fake_model = MagicMock()
        # 첫 호출은 예외, 두 번째 호출은 성공
        fake_model.encode = MagicMock(
            side_effect=[RuntimeError("oom"), np.array([1.0], dtype=np.float32)]
        )
        fake_module = MagicMock()
        fake_module.SentenceTransformer = MagicMock(return_value=fake_model)

        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            assert svc.encode_query("a") is None
            assert svc.is_enabled is True  # 여전히 활성
            result = svc.encode_query("b")
            assert result is not None

    def test_unexpected_shape_returns_none(self):
        svc = EmbeddingService(model_name="dummy", enabled=True)
        # 2-D 출력 — 비정상
        bad_vec = np.zeros((2, 3), dtype=np.float32)
        fake_module, _ = self._patched_module(bad_vec)

        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            result = svc.encode_query("test")

        assert result is None
