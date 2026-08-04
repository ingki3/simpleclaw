"""LLM 라우터: 설정 기반 백엔드 선택 및 메시지 라우팅.

config.yaml의 llm 섹션을 읽어 여러 LLM 프로바이더를 초기화하고,
요청마다 지정된(또는 기본) 백엔드로 메시지를 전달한다.

설계 결정:
  - 프로바이더 레지스트리를 지연 임포트로 구성하여 순환 임포트 방지
  - 초기화 실패한 프로바이더는 경고만 남기고 건너뛰어 부분 가용성 보장
"""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from simpleclaw.config import load_llm_config
from simpleclaw.llm.models import (
    BackendType,
    LLMBackend,
    LLMConfigError,
    LLMRequest,
    LLMResponse,
    LLMRoute,
)
from simpleclaw.llm.profiles import ProviderProfile, get_provider_profile
from simpleclaw.llm.providers.base import LLMProvider, TextDeltaCallback
from simpleclaw.llm.transports import get_transport_class
from simpleclaw.llm.usage import (
    USAGE_ROUTE_NAMES,
    USAGE_TASK_NAMES,
    LLMUsageEvent,
    LLMUsageSink,
    UsageDimensionRegistry,
    normalize_usage,
    sanitize_usage_dimension,
)
from simpleclaw.logging.trace_context import get_trace_id
from simpleclaw.runtime_budget import reserve_runtime_llm_call

logger = logging.getLogger(__name__)

_ValidatedResponse = TypeVar("_ValidatedResponse")
_ValidationRetryRequest = Callable[
    [LLMRequest, LLMResponse, Exception], LLMRequest | None
]

# Backward-compatible view for older tests/extensions that inspected the router
# registry directly. create_router() uses simpleclaw.llm.transports instead.
_PROVIDER_REGISTRY: dict[str, type] = {}


def _ensure_registry() -> None:
    """Populate the legacy provider registry view from transport classes."""
    if _PROVIDER_REGISTRY:
        return
    _PROVIDER_REGISTRY["claude"] = get_transport_class("anthropic")
    _PROVIDER_REGISTRY["openai"] = get_transport_class("openai_chat")
    _PROVIDER_REGISTRY["gemini"] = get_transport_class("gemini")
    _PROVIDER_REGISTRY["vertex_gemini"] = get_transport_class("vertex_gemini")
    _PROVIDER_REGISTRY["cli"] = get_transport_class("cli")


def _request_has_attachments(request: LLMRequest) -> bool:
    """현재 요청 messages에 provider-neutral multimodal attachments가 있는지 확인한다.

    BIZ-448 — orchestrator 는 현재 turn 의 user message dict 에 ``attachments`` 를
    싣는다. LLMRequest 자체에는 attachments 필드가 없으므로 messages 를 스캔한다.
    """
    for msg in request.messages or []:
        attachments = msg.get("attachments") if isinstance(msg, dict) else None
        if attachments:
            return True
    return False


def _response_is_empty_final(response: LLMResponse) -> bool:
    """응답이 사용자에게 보일 내용이 전혀 없는 empty final 인지 판정한다.

    BIZ-448 — 보이는 텍스트도 tool call 도 없으면 (예: reasoning budget 소진)
    fallback 재시도 대상이다.
    """
    return not (response.text or "").strip() and not response.tool_calls


class LLMRouter:
    """LLM 요청을 적절한 백엔드로 라우팅하는 중앙 허브."""

    def __init__(
        self,
        backends: dict[str, LLMBackend],
        providers: dict[str, LLMProvider],
        default_backend: str,
        fallback_backend: str | None = None,
        multimodal_backend: str | None = None,
        profiles: dict[str, ProviderProfile] | None = None,
        routes: dict[str, LLMRoute] | None = None,
        usage_sink: LLMUsageSink | None = None,
    ) -> None:
        """라우터를 초기화한다.

        Args:
            backends: 백엔드 이름 → LLMBackend 설정 매핑.
            providers: 백엔드 이름 → 초기화된 LLMProvider 인스턴스 매핑.
            default_backend: 요청에 백엔드가 지정되지 않았을 때 사용할 기본 이름.
            fallback_backend: 암묵적 라우팅에서 기본 백엔드가 실패하거나 empty
                final 을 반환할 때 1회 재시도할 백엔드 이름 (BIZ-448).
            multimodal_backend: 첨부가 포함된 암묵적 요청을 라우팅할 백엔드 이름
                (BIZ-448). 미가용 이름은 None 으로 무력화한다.
        """
        self._backends = backends
        self._providers = providers
        self._profiles = profiles or {}
        self._default = default_backend
        # 가용 provider 가 없는 정책 이름은 조용히 비활성화 — 부분 가용성 보장.
        self._fallback = fallback_backend if fallback_backend in providers else None
        self._multimodal = (
            multimodal_backend if multimodal_backend in providers else None
        )
        if routes is None:
            routes = {
                "default": LLMRoute(
                    name="default", primary=default_backend, retry=self._fallback
                )
            }
            if self._multimodal:
                routes["multimodal"] = LLMRoute(
                    name="multimodal", primary=self._multimodal, retry=self._fallback
                )
        self._routes = routes
        self._usage_sink = usage_sink
        self._usage_dimensions = UsageDimensionRegistry(
            provider_profiles=frozenset(
                profile.name for profile in self._profiles.values()
            )
            | frozenset({"generic"}),
            route_names=(frozenset(routes) | frozenset({"direct"})) & USAGE_ROUTE_NAMES,
            task_names=USAGE_TASK_NAMES,
        )
        if usage_sink is not None and hasattr(
            usage_sink, "configure_dimension_registry"
        ):
            usage_sink.configure_dimension_registry(self._usage_dimensions)

    async def _send_to_backend(
        self,
        backend_name: str,
        request: LLMRequest,
        on_text_delta: TextDeltaCallback | None = None,
        *,
        attempt_role: str = "primary",
        retry_reason: str | None = None,
        record_response: bool = True,
    ) -> LLMResponse:
        """선택된 backend에 전송하고 확정 가능한 attempt 결과를 기록한다.

        semantic validator를 사용하는 호출은 ``record_response=False``로 응답 기록을
        미룬다. provider 예외는 validator와 무관하게 이 경계에서 항상 기록한다.
        """
        provider = self._providers[backend_name]
        reservation = reserve_runtime_llm_call(request.max_tokens)
        if reservation is not None:
            request = replace(request, max_tokens=reservation.max_tokens)

        # BIZ-453 — reasoning hint 는 설정된 요청에만 kwargs 로 전달한다.
        # 미설정 요청의 호출 시그니처를 기존과 동일하게 유지해, reasoning
        # 파라미터가 없는 테스트 대역/구형 provider 대역의 회귀를 막는다.
        extra_kwargs: dict = {}
        if request.reasoning:
            extra_kwargs["reasoning"] = request.reasoning

        started = time.monotonic()
        response: LLMResponse | None = None
        try:
            if on_text_delta is not None:
                logger.info(
                    "Routing streaming request to backend '%s'",
                    sanitize_usage_dimension(
                        backend_name,
                        field="backend_name",
                    ),
                )
                response = await provider.stream(
                    request.system_prompt,
                    request.user_message,
                    request.messages,
                    request.tools,
                    system_blocks=request.system_blocks,
                    on_text_delta=on_text_delta,
                    max_tokens=request.max_tokens,
                    response_mime_type=request.response_mime_type,
                    response_schema=request.response_schema,
                    require_structured_output=request.require_structured_output,
                    **extra_kwargs,
                )
            else:
                logger.info(
                    "Routing request to backend '%s'",
                    sanitize_usage_dimension(
                        backend_name,
                        field="backend_name",
                    ),
                )
                response = await provider.send(
                    request.system_prompt,
                    request.user_message,
                    request.messages,
                    request.tools,
                    system_blocks=request.system_blocks,
                    max_tokens=request.max_tokens,
                    response_mime_type=request.response_mime_type,
                    response_schema=request.response_schema,
                    require_structured_output=request.require_structured_output,
                    **extra_kwargs,
                )
        except Exception as exc:
            self._record_usage_event(
                backend_name,
                request,
                attempt_role,
                retry_reason,
                "error",
                time.monotonic() - started,
                None,
                type(exc).__name__,
            )
            raise
        finally:
            if reservation is not None and response is None:
                reservation.complete(None)
        assert response is not None
        if reservation is not None:
            reservation.complete(
                response.usage if isinstance(response.usage, dict) else None
            )
        if record_response:
            status = "empty" if _response_is_empty_final(response) else "success"
            self._record_usage_event(
                backend_name,
                request,
                attempt_role,
                retry_reason,
                status,
                time.monotonic() - started,
                response,
                None,
            )
        return response

    def _record_usage_event(
        self,
        backend_name: str,
        request: LLMRequest,
        attempt_role: str,
        retry_reason: str | None,
        status: str,
        elapsed: float,
        response: LLMResponse | None,
        error_type: str | None,
    ) -> None:
        if self._usage_sink is None:
            return
        backend = self._backends[backend_name]
        profile = self._profiles.get(backend_name)
        event = LLMUsageEvent(
            event_id=str(uuid.uuid4()),
            occurred_at_utc=datetime.now(UTC).isoformat(),
            trace_id=get_trace_id(),
            backend_name=backend_name,
            provider_profile=profile.name
            if profile
            else (backend.profile or "generic"),
            model=(response.model if response else backend.model) or backend.model,
            route_name="direct"
            if request.backend_name
            else (request.route_name or "default"),
            task_name=request.usage_task or request.route_name or "chat",
            attempt_role=attempt_role,
            retry_reason=retry_reason,
            status=status,
            duration_ms=elapsed * 1000,
            usage=normalize_usage(response.usage if response else None),
            error_type=error_type,
        )
        try:
            if hasattr(self._usage_sink, "record_from_router"):
                self._usage_sink.record_from_router(event, self._usage_dimensions)
            else:
                self._usage_sink.record(event.sanitized(self._usage_dimensions))
        except Exception as exc:  # fail-open telemetry boundary
            logger.warning(
                "llm_usage_record_failed error_type=%s",
                sanitize_usage_dimension(type(exc).__name__, field="error_type"),
            )

    def _resolve_request(self, request: LLMRequest) -> tuple[str, str | None]:
        """Resolve the mutually-exclusive backend/route selectors."""
        if request.backend_name and request.route_name:
            raise LLMConfigError(
                "LLM request cannot set both backend_name and route_name"
            )
        if request.backend_name:
            return request.backend_name, None

        route_name = request.route_name
        if route_name is None:
            route_name = (
                "multimodal"
                if _request_has_attachments(request) and "multimodal" in self._routes
                else "default"
            )
        route = self._routes.get(route_name)
        if route is None:
            raise LLMConfigError(
                f"Unknown LLM route '{route_name}'. "
                f"Available: {', '.join(sorted(self._routes))}"
            )
        retry = route.retry if route.retry != route.primary else None
        return route.primary, retry

    @staticmethod
    def _required_capabilities(
        request: LLMRequest, on_text_delta: TextDeltaCallback | None
    ) -> set[str]:
        required = set(request.required_capabilities)
        if request.tools:
            required.add("tools")
        if on_text_delta is not None:
            required.add("streaming")
        if request.require_structured_output:
            required.add("structured_output")
        if _request_has_attachments(request):
            required.add("multimodal")
        return required

    def _incompatible_capabilities(
        self, backend_name: str, required: set[str]
    ) -> list[str]:
        profile = self._profiles.get(backend_name)
        if profile is None:
            return []
        return sorted(
            capability
            for capability in required
            if not bool(getattr(profile.capabilities, capability, False))
        )

    def _preflight_backend(self, backend_name: str, required: set[str]) -> None:
        if backend_name not in self._providers:
            raise LLMConfigError(
                f"Unknown backend '{backend_name}'. "
                f"Available: {', '.join(self._providers.keys())}"
            )
        missing = self._incompatible_capabilities(backend_name, required)
        if missing:
            raise LLMConfigError(
                f"Backend '{backend_name}' does not support required capabilities: "
                f"{', '.join(missing)}"
            )

    def _usable_retry(
        self, backend_name: str, retry_name: str | None, required: set[str]
    ) -> str | None:
        """Return a compatible retry backend only when a retry is actually needed."""
        if not retry_name:
            return None
        if retry_name not in self._providers or self._incompatible_capabilities(
            retry_name, required
        ):
            logger.warning(
                "Skipping incompatible or unavailable retry backend '%s' for '%s'",
                sanitize_usage_dimension(retry_name, field="backend_name"),
                sanitize_usage_dimension(backend_name, field="backend_name"),
            )
            return None
        return retry_name

    async def send(
        self,
        request: LLMRequest,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> LLMResponse:
        """요청을 적절한 백엔드로 라우팅하고 응답을 반환한다.

        BIZ-259: ``on_text_delta`` 콜백이 주어지면 프로바이더의 ``stream()`` 경로로
        전환되어 텍스트 델타가 생성될 때마다 콜백이 호출된다. 콜백이 None 이면
        기존 ``send()`` 경로를 그대로 사용 — 호출 측 회귀 0.

        Named routes own primary/retry selection. An explicit ``backend_name``
        remains a no-retry escape hatch. Selector-less attachment requests use
        the multimodal route when configured; other implicit requests use the
        default route. Provider errors and empty finals retry at most once.
        Streaming never retries because deltas may already have reached a sink.
        """
        backend_name, fallback_name = self._resolve_request(request)
        required = self._required_capabilities(request, on_text_delta)
        self._preflight_backend(backend_name, required)

        try:
            response = await self._send_to_backend(backend_name, request, on_text_delta)
        except Exception as exc:
            retry_name = (
                self._usable_retry(backend_name, fallback_name, required)
                if on_text_delta is None
                else None
            )
            if retry_name:
                logger.warning(
                    "Backend '%s' failed; retrying fallback '%s' error_type=%s",
                    sanitize_usage_dimension(backend_name, field="backend_name"),
                    sanitize_usage_dimension(retry_name, field="backend_name"),
                    sanitize_usage_dimension(type(exc).__name__, field="error_type"),
                )
                return await self._send_to_backend(
                    retry_name,
                    request,
                    None,
                    attempt_role="retry",
                    retry_reason="provider_error",
                )
            raise

        if (
            fallback_name
            and on_text_delta is None
            and _response_is_empty_final(response)
        ):
            retry_name = self._usable_retry(backend_name, fallback_name, required)
            if not retry_name:
                return response
            logger.warning(
                "Backend '%s' returned empty final; retrying fallback '%s'",
                sanitize_usage_dimension(backend_name, field="backend_name"),
                sanitize_usage_dimension(retry_name, field="backend_name"),
            )
            return await self._send_to_backend(
                retry_name,
                request,
                None,
                attempt_role="retry",
                retry_reason="empty_final",
            )
        return response

    async def send_validated(
        self,
        request: LLMRequest,
        validate_response: Callable[[LLMResponse], _ValidatedResponse],
        *,
        validation_retry_request: _ValidationRetryRequest | None = None,
    ) -> _ValidatedResponse:
        """Send through a route and retry once when semantic validation fails.

        A caller may validate an otherwise non-empty provider response (for
        example, parse a structured JSON contract) without learning a backend
        name or manually selecting the route retry.  By default this deliberately
        shares the same route-only primary/retry policy as :meth:`send`.

        ``validation_retry_request`` may return a narrowed request for one
        bounded recovery attempt.  In that explicit case only, a route without a
        distinct retry backend reuses the primary backend once.  Returning
        ``None`` preserves the normal route-only retry contract.
        """
        backend_name, fallback_name = self._resolve_request(request)
        required = self._required_capabilities(request, None)
        self._preflight_backend(backend_name, required)

        primary_started = time.monotonic()
        retry_request = request
        try:
            primary_response = await self._send_to_backend(
                backend_name, request, record_response=False
            )
        except Exception as exc:
            retry_name = self._usable_retry(backend_name, fallback_name, required)
            if not retry_name:
                raise
            retry_reason = "provider_error"
            error_type = type(exc).__name__
        else:
            if _response_is_empty_final(primary_response):
                self._record_usage_event(
                    backend_name,
                    request,
                    "primary",
                    None,
                    "empty",
                    time.monotonic() - primary_started,
                    primary_response,
                    None,
                )
                retry_name = self._usable_retry(backend_name, fallback_name, required)
                if not retry_name:
                    return validate_response(primary_response)
                retry_reason = "empty_final"
                error_type = None
            else:
                try:
                    validated = validate_response(primary_response)
                except Exception as exc:
                    self._record_usage_event(
                        backend_name,
                        request,
                        "primary",
                        "validation_error",
                        "error",
                        time.monotonic() - primary_started,
                        primary_response,
                        type(exc).__name__,
                    )
                    narrowed_retry_request = (
                        validation_retry_request(request, primary_response, exc)
                        if validation_retry_request is not None
                        else None
                    )
                    if narrowed_retry_request is not None and not isinstance(
                        narrowed_retry_request, LLMRequest
                    ):
                        raise TypeError(
                            "validation_retry_request must return LLMRequest or None"
                        )
                    retry_name = self._usable_retry(
                        backend_name, fallback_name, required
                    )
                    if not retry_name and narrowed_retry_request is not None:
                        retry_name = backend_name
                    if not retry_name:
                        raise
                    retry_request = narrowed_retry_request or request
                    retry_reason = "validation_error"
                    error_type = type(exc).__name__
                else:
                    self._record_usage_event(
                        backend_name,
                        request,
                        "primary",
                        None,
                        "success",
                        time.monotonic() - primary_started,
                        primary_response,
                        None,
                    )
                    return validated

        route_name = request.route_name or "default"
        logger.warning(
            "Validated response failed; backend=%s route=%s error_type=%s "
            "retry_backend=%s retry=true",
            sanitize_usage_dimension(backend_name, field="backend_name"),
            sanitize_usage_dimension(route_name, field="route_name"),
            sanitize_usage_dimension(error_type, field="error_type"),
            sanitize_usage_dimension(retry_name, field="backend_name"),
        )
        retry_started = time.monotonic()
        retry_response = await self._send_to_backend(
            retry_name,
            retry_request,
            attempt_role="retry",
            retry_reason=retry_reason,
            record_response=False,
        )
        retry_status = (
            "empty" if _response_is_empty_final(retry_response) else "success"
        )
        if retry_status == "empty":
            self._record_usage_event(
                retry_name,
                request,
                "retry",
                retry_reason,
                "empty",
                time.monotonic() - retry_started,
                retry_response,
                None,
            )
            return validate_response(retry_response)
        try:
            validated = validate_response(retry_response)
        except Exception as exc:
            self._record_usage_event(
                retry_name,
                request,
                "retry",
                "validation_error",
                "error",
                time.monotonic() - retry_started,
                retry_response,
                type(exc).__name__,
            )
            raise
        self._record_usage_event(
            retry_name,
            request,
            "retry",
            retry_reason,
            "success",
            time.monotonic() - retry_started,
            retry_response,
            None,
        )
        return validated

    def list_backends(self) -> list[str]:
        """등록된 모든 백엔드의 이름 목록을 반환한다."""
        return list(self._providers.keys())

    def validate_backend_name(
        self,
        backend_name: str | None,
        *,
        field_path: str = "backend_name",
    ) -> None:
        """설정 필드가 현재 등록된 backend alias를 가리키는지 검증한다.

        빈 값은 요청의 기본 route를 사용한다는 기존 계약이므로 허용한다. 명시된
        alias는 자동 fallback 없이 현재 router registry에 반드시 존재해야 한다.
        """
        if not backend_name:
            return
        if backend_name not in self._providers:
            available = ", ".join(self._providers.keys())
            raise LLMConfigError(
                f"{field_path}: unknown backend alias '{backend_name}'. "
                f"Available: {available}"
            )

    def get_default_backend(self) -> str:
        """기본 백엔드 이름을 반환한다."""
        return self._default

    def get_fallback_backend(self) -> str | None:
        """Fallback 백엔드 이름을 반환한다 (미설정/미가용 시 None)."""
        return self._fallback

    def get_multimodal_backend(self) -> str | None:
        """멀티모달 백엔드 이름을 반환한다 (미설정/미가용 시 None)."""
        return self._multimodal

    def get_backend_profile(self, backend_name: str) -> ProviderProfile | None:
        """백엔드에 연결된 provider profile을 반환한다."""
        return self._profiles.get(backend_name)

    def get_route(self, route_name: str) -> LLMRoute | None:
        """Return a normalized named route."""
        return self._routes.get(route_name)


def create_router(
    config_path: str | Path, *, usage_sink: LLMUsageSink | None = None
) -> LLMRouter:
    """config.yaml 설정으로부터 LLMRouter를 생성한다.

    Args:
        config_path: config.yaml 파일 경로.

    Returns:
        초기화된 LLMRouter 인스턴스.

    Raises:
        LLMConfigError: 프로바이더가 하나도 설정되지 않았거나 초기화에 실패한 경우.
    """
    config = load_llm_config(config_path)
    default_name = config.get("default", "")
    fallback_name = config.get("fallback")
    multimodal_name = config.get("multimodal")
    providers_config = config.get("providers", {})
    routes_config = config.get("routes", {})
    route_sources = config.get("route_sources", {})

    if not providers_config:
        raise LLMConfigError("No LLM providers configured in config.yaml")

    backends: dict[str, LLMBackend] = {}
    providers: dict[str, LLMProvider] = {}
    profiles: dict[str, ProviderProfile] = {}

    for name, pconf in providers_config.items():
        backend_type_str = pconf.get("type", "api")
        try:
            backend_type = BackendType(backend_type_str)
        except ValueError:
            raise LLMConfigError(
                f"Invalid backend type '{backend_type_str}' for provider '{name}'"
            )

        backend = LLMBackend(
            name=name,
            backend_type=backend_type,
            model=pconf.get("model", ""),
            transport=pconf.get("transport"),
            profile=pconf.get("profile"),
            api_key_env=pconf.get("api_key_env"),
            command=pconf.get("command"),
            args=pconf.get("args", []),
            timeout=pconf.get("timeout", 120),
        )
        backends[name] = backend
        profile = get_provider_profile(backend.profile or "generic")
        profiles[name] = profile

        if backend_type == BackendType.CLI:
            try:
                provider_cls = get_transport_class(backend.transport or "cli")
            except LLMConfigError as exc:
                logger.warning(
                    "Skipping provider '%s' (transport=%s profile=%s) error_type=%s",
                    sanitize_usage_dimension(name, field="backend_name"),
                    sanitize_usage_dimension(backend.transport, field="transport"),
                    sanitize_usage_dimension(backend.profile, field="provider_profile"),
                    sanitize_usage_dimension(type(exc).__name__, field="error_type"),
                )
                continue
            provider = provider_cls(
                command=backend.command,
                args=backend.args,
                timeout=backend.timeout,
                name=name,
            )
            providers[name] = provider
        else:
            try:
                provider_cls = get_transport_class(backend.transport or "")
            except LLMConfigError as exc:
                # ``openai_responses`` is a deliberately reserved extension
                # point.  Treat an explicit attempt to use it as a config
                # error rather than silently falling back to another backend.
                if backend.transport == "openai_responses":
                    raise
                logger.warning(
                    "Skipping provider '%s' (transport=%s profile=%s) error_type=%s",
                    sanitize_usage_dimension(name, field="backend_name"),
                    sanitize_usage_dimension(backend.transport, field="transport"),
                    sanitize_usage_dimension(backend.profile, field="provider_profile"),
                    sanitize_usage_dimension(type(exc).__name__, field="error_type"),
                )
                continue
            api_key = pconf.get("api_key", "")
            # BIZ-444 — 프로바이더가 EXTRA_CONFIG_KEYS 로 선언한 추가 설정
            # (예: vertex_gemini 의 project/location/credentials_path)만
            # config 블록에서 골라 전달한다. profile hook 의 request_extra_keys 는
            # 후속 route migration 이 provider-specific extras 를 profile 기준으로
            # 옮길 때 쓸 계약이며, 현재 transport 생성 시그니처와 충돌시키지 않는다.
            extra_kwargs = {
                key: pconf[key]
                for key in getattr(provider_cls, "EXTRA_CONFIG_KEYS", ())
                if key in pconf
            }
            if "profile" in inspect.signature(provider_cls).parameters:
                extra_kwargs["profile"] = profile
            try:
                provider = provider_cls(
                    model=backend.model,
                    api_key=api_key,
                    name=name,
                    **extra_kwargs,
                )
                providers[name] = provider
            except Exception as exc:
                logger.warning(
                    "Skipping provider '%s' (transport=%s profile=%s) error_type=%s",
                    sanitize_usage_dimension(name, field="backend_name"),
                    sanitize_usage_dimension(backend.transport, field="transport"),
                    sanitize_usage_dimension(backend.profile, field="provider_profile"),
                    sanitize_usage_dimension(type(exc).__name__, field="error_type"),
                )

    if default_name and default_name not in providers:
        logger.warning(
            "Default backend '%s' not available. Using first available.",
            sanitize_usage_dimension(default_name, field="backend_name"),
        )
        # 기본 백엔드가 사용 불가하면 첫 번째 가용 프로바이더로 대체
        default_name = next(iter(providers)) if providers else ""

    if not providers:
        raise LLMConfigError("No valid LLM providers could be initialized")

    # BIZ-448 — 미가용 정책 백엔드는 경고 후 비활성화 (부분 가용성 우선).
    if fallback_name and fallback_name not in providers:
        logger.warning(
            "Fallback backend '%s' not available; disabling fallback.",
            sanitize_usage_dimension(fallback_name, field="backend_name"),
        )
        fallback_name = None
    if multimodal_name and multimodal_name not in providers:
        logger.warning(
            "Multimodal backend '%s' not available; using default routing.",
            sanitize_usage_dimension(multimodal_name, field="backend_name"),
        )
        multimodal_name = None

    normalized_routes: dict[str, LLMRoute] = {}
    for route_name, route_config in routes_config.items():
        primary = route_config["primary"]
        retry = route_config.get("retry")
        source = route_sources.get(route_name, "explicit")

        if primary not in providers:
            if source == "explicit":
                raise LLMConfigError(
                    f"LLM route '{route_name}' primary backend '{primary}' is unavailable"
                )
            logger.warning(
                "Legacy LLM route '%s' primary backend '%s' is unavailable; "
                "using active default '%s'.",
                sanitize_usage_dimension(route_name, field="route_name"),
                sanitize_usage_dimension(primary, field="backend_name"),
                sanitize_usage_dimension(default_name, field="backend_name"),
            )
            primary = default_name

        if retry and retry not in providers:
            if source == "explicit":
                raise LLMConfigError(
                    f"LLM route '{route_name}' retry backend '{retry}' is unavailable"
                )
            logger.warning(
                "Legacy LLM route '%s' retry backend '%s' is unavailable; disabling retry.",
                sanitize_usage_dimension(route_name, field="route_name"),
                sanitize_usage_dimension(retry, field="backend_name"),
            )
            retry = None
        normalized_routes[route_name] = LLMRoute(
            name=route_name,
            primary=primary,
            retry=retry,
        )

    default_route = normalized_routes.get("default")
    if default_route is None:
        normalized_routes["default"] = LLMRoute("default", default_name, fallback_name)
    elif default_route.primary != default_name:
        # An explicit default is already validated above. Legacy/default
        # materialization must follow the provider selected after init failure.
        normalized_routes["default"] = LLMRoute(
            "default", default_name, default_route.retry
        )

    return LLMRouter(
        backends=backends,
        providers=providers,
        default_backend=default_name,
        fallback_backend=fallback_name,
        multimodal_backend=multimodal_name,
        profiles=profiles,
        routes=normalized_routes,
        usage_sink=usage_sink,
    )
