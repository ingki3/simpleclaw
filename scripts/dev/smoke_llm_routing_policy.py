#!/usr/bin/env python3
"""SimpleClaw LLM 라우팅 정책(no-secret) 스모크 스크립트 (BIZ-448/450).

/tmp 아래 임시 config 를 만들어 실제 LLMRouter 경로로 다음을 검증한다:
  - text-only 암묵 요청 → DeepSeek default (또는 empty 시 Gemini fallback)
  - 첨부 포함 암묵 요청 → Gemini multimodal
  - required structured JSON 요청 → DeepSeek default 가 fallback 없이 처리 (BIZ-450)
live config.yaml 은 절대 수정하지 않으며, API key 값은 존재 여부/길이만
출력한다 — 값 자체는 어떤 경로로도 출력 금지.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from simpleclaw.llm.models import LLMRequest, MultimodalAttachment
from simpleclaw.llm.router import create_router

DEV_ENV = Path("/Users/simplist/Dev/SimpleClaw/.env")
LIVE_ENV = Path("/Users/simplist/.simpleclaw/.env")


def _env_status(path: Path, key: str) -> tuple[bool, int]:
    """`.env` 파일에서 key 존재 여부와 값 길이만 확인한다 — 값은 반환/출력 금지."""
    if not path.exists():
        return False, 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip().startswith(key + "="):
            value = line.split("=", 1)[1].strip().strip("'\"")
            return bool(value), len(value)
    return False, 0


def _write_temp_config(directory: Path) -> Path:
    """임시 디렉터리에 라우팅 정책 config 와 병합 .env 사본을 작성한다."""
    config = directory / "config.yaml"
    config.write_text(
        """
llm:
  default: openrouter_deepseek_v4_pro
  fallback: gemini
  multimodal: gemini
  providers:
    openrouter_deepseek_v4_pro:
      provider: openai
      type: api
      model: deepseek/deepseek-v4-pro
      api_key_env: OPENROUTER_API_KEY
      base_url: https://openrouter.ai/api/v1
      default_headers:
        HTTP-Referer: https://simpleclaw.local
        X-Title: SimpleClaw routing smoke
      extra_body:
        reasoning:
          enabled: false
    gemini:
      type: api
      model: gemini-3.5-flash
      api_key_env: GEMINI_API_KEY
""".strip(),
        encoding="utf-8",
    )
    # load_llm_config 는 config.yaml 옆의 .env 에서 api_key_env 를 해소하므로
    # dev/live .env 를 임시 디렉터리로 병합 복사한다 (임시 파일은 컨텍스트
    # 종료 시 삭제).
    env_lines = []
    for source in (DEV_ENV, LIVE_ENV):
        if source.exists():
            env_lines.extend(source.read_text(encoding="utf-8", errors="ignore").splitlines())
    (directory / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    return config


async def main() -> int:
    for key in ("OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        found = False
        for path in (DEV_ENV, LIVE_ENV):
            ok, length = _env_status(path, key)
            if ok:
                print(key, "set", "len=", length, "source=", path)
                found = True
                break
        if not found:
            print(key, "missing")
            return 2

    with tempfile.TemporaryDirectory(prefix="simpleclaw-llm-routing-") as tmp:
        config = _write_temp_config(Path(tmp))
        router = create_router(config)
        print("default=", router.get_default_backend())
        print("fallback=", router.get_fallback_backend())
        print("multimodal=", router.get_multimodal_backend())
        print("backends=", router.list_backends())

        text_response = await router.send(
            LLMRequest(
                user_message="회의 준비 체크리스트 3개를 한국어로 짧게 알려주세요.",
                max_tokens=500,
            )
        )
        print("text_backend=", text_response.backend_name)
        print("text_model=", text_response.model)
        print("text_len=", len(text_response.text or ""))
        print("text_usage=", text_response.usage)
        print((text_response.text or "")[:500])

        multimodal_response = await router.send(
            LLMRequest(
                messages=[
                    {
                        "role": "user",
                        "content": "첨부 파일 형식을 보고 가능한 분석을 설명해 주세요.",
                        "attachments": [
                            MultimodalAttachment(
                                data=b"fake text file",
                                mime_type="text/plain",
                                name="note.txt",
                            )
                        ],
                    }
                ],
                max_tokens=500,
            )
        )
        print("multimodal_backend=", multimodal_response.backend_name)
        print("multimodal_model=", multimodal_response.model)
        print("multimodal_len=", len(multimodal_response.text or ""))

        # BIZ-450 — required structured output 이 default(DeepSeek) 에서 fallback
        # 없이 처리되는지 검증. TurnAnalysis 실제 schema 를 그대로 사용한다.
        from simpleclaw.agent.turn_analysis import TURN_ANALYSIS_RESPONSE_SCHEMA

        structured_response = await router.send(
            LLMRequest(
                system_prompt="Return only JSON matching the schema.",
                user_message=(
                    "Analyze this turn: 방금 질문에 대한 대응을 어떤 모델이 "
                    "했는지 확인해봐"
                ),
                response_mime_type="application/json",
                response_schema=TURN_ANALYSIS_RESPONSE_SCHEMA,
                require_structured_output=True,
                max_tokens=1200,
            )
        )
        print("structured_backend=", structured_response.backend_name)
        print("structured_model=", structured_response.model)
        print("structured_len=", len(structured_response.text or ""))
        print("structured_usage=", structured_response.usage)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
