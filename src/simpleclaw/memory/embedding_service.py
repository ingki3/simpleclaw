"""문장 임베딩 서비스 — 시맨틱 회상(spec 005 Phase 2)의 벡터 생성 계층.

주요 동작 흐름:
1. EmbeddingService 인스턴스 생성 시 모델 이름과 enabled 플래그만 보관한다(모델은 즉시 로드하지 않음).
2. 첫 ``encode_query()`` / ``encode_passage()`` 호출 시 lazy하게 SentenceTransformer를 로드한다.
3. e5 계열 모델 규격에 따라 query는 ``"query: "``, passage는 ``"passage: "`` 프리픽스로 인코딩한다.
4. 모델 로드 실패, sentence-transformers 미설치, 인코딩 예외 등 어떤 실패든 None을 반환하여
   상위 레이어가 슬라이딩 윈도우 모드로 자연 fallback 하도록 설계한다(서비스 가용성 보존).

설계 결정:
- ``sentence-transformers``는 PyTorch를 끌고 오는 무거운 의존성이라 import도 lazy로 처리한다.
  로컬 봇 기동 시간을 보호하고, 일부 환경(CI 등)에서 미설치 시에도 봇이 동작하게 한다.
- 모델 다운로드는 첫 인코딩 호출에서 발생하며, 이후 메모리 캐시에 상주한다.
- ``encode_query`` / ``encode_passage`` 분리는 e5 계열의 비대칭 검색 규격을 따른다.
  대칭 모델로 교체할 경우 하위 호환을 위해 두 메서드를 동일하게 동작시키도록 model_name을
  검사하는 식으로 진화시킬 수 있다(현재는 e5 단일 가정).
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# 임베딩 BLOB 직렬화 dtype — ConversationStore와 동일하게 float32 사용
_EMBEDDING_DTYPE = np.float32


class EmbeddingService:
    """문장 임베딩을 생성하는 lazy-loading 서비스.

    인스턴스 생성은 가볍고(모델 로드 없음), 첫 인코딩 호출 시 실제 모델을 메모리에 적재한다.
    어떤 실패도 예외를 던지지 않고 None을 반환하여 호출자가 RAG를 건너뛰도록 한다.
    """

    def __init__(
        self,
        model_name: str = "intfloat/multilingual-e5-small",
        enabled: bool = True,
    ) -> None:
        """임베딩 서비스를 초기화한다(모델 로드는 첫 호출 시까지 지연).

        Args:
            model_name: HuggingFace 모델 식별자. 기본값은 e5-small(118M, 384dim, 한/영).
            enabled: False면 어떤 인코딩 호출도 즉시 None을 반환한다(RAG 비활성화 스위치).
        """
        self._model_name = model_name
        self._enabled = enabled
        self._model: SentenceTransformer | None = None
        # 멀티턴 봇에서 동시 호출 시 중복 로드를 방지(첫 호출자만 로드, 나머지는 대기)
        self._load_lock = threading.Lock()
        # 한 번 로드가 실패하면 더 이상 시도하지 않는다(반복 실패로 인한 레이턴시 방지)
        self._load_failed = False

    @property
    def is_enabled(self) -> bool:
        """현재 RAG가 활성 상태인지 — 설정 + 모델 로드 가능 여부 종합."""
        return self._enabled and not self._load_failed

    @property
    def model_name(self) -> str:
        return self._model_name

    def _ensure_model(self) -> bool:
        """필요 시 SentenceTransformer를 로드하고 성공 여부를 반환한다.

        스레드 안전. 한 번 실패한 경우 재시도하지 않는다.
        """
        if self._model is not None:
            return True
        if not self._enabled or self._load_failed:
            return False

        with self._load_lock:
            # double-checked locking — 락 진입 후 다시 확인
            if self._model is not None:
                return True
            if self._load_failed:
                return False

            try:
                # lazy import: sentence-transformers는 PyTorch를 끌고오므로 모듈 로드 시점도 미룬다
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                # 의존성 미설치 → 조용히 비활성화 (CI/경량 환경 보호)
                logger.warning(
                    "sentence-transformers not installed; RAG disabled (%s)", exc
                )
                self._load_failed = True
                return False

            try:
                logger.info("Loading embedding model: %s", self._model_name)
                self._model = SentenceTransformer(self._model_name)
                logger.info("Embedding model loaded: %s", self._model_name)
                return True
            except Exception as exc:
                # 네트워크/디스크 오류, 모델 호환성 등 — RAG만 비활성화하고 봇은 계속
                logger.error(
                    "Failed to load embedding model '%s': %s",
                    self._model_name, exc,
                )
                self._load_failed = True
                return False

    def encode_query(self, text: str) -> np.ndarray | None:
        """검색 질의 문장을 임베딩한다(e5 규격: ``query: `` 프리픽스).

        Returns:
            float32 1-D numpy 벡터, 실패 시 None.
        """
        return self._encode(f"query: {text}")

    def encode_passage(self, text: str) -> np.ndarray | None:
        """저장(피검색) 문장을 임베딩한다(e5 규격: ``passage: `` 프리픽스).

        Returns:
            float32 1-D numpy 벡터, 실패 시 None.
        """
        return self._encode(f"passage: {text}")

    def _encode(self, text: str) -> np.ndarray | None:
        """모델이 준비된 경우 텍스트를 인코딩한다. 어떤 실패든 None을 반환한다."""
        if not self._ensure_model():
            return None
        try:
            # convert_to_numpy=True: torch tensor 대신 numpy 반환 → 의존성 누수 방지
            vec = self._model.encode(text, convert_to_numpy=True)  # type: ignore[union-attr]
            arr = np.asarray(vec, dtype=_EMBEDDING_DTYPE)
            if arr.ndim != 1 or arr.size == 0:
                logger.warning("encode produced unexpected shape: %s", arr.shape)
                return None
            return arr
        except Exception as exc:
            # 인코딩 단발 실패는 비활성화하지 않는다(다음 호출은 다시 시도)
            logger.error("Embedding encode failed: %s", exc)
            return None
