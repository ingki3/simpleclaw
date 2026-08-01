#!/usr/bin/env python3
"""OpenRouter Gemini routing no-send smoke (BIZ-524).

임시 config와 실제 ``LLMRouter``를 사용해 text, required structured output,
tool call, PNG image, PDF file 경로를 검증한다. Telegram/channel 코드는 호출하지
않으며 응답 본문과 credential 값은 출력하지 않는다. 성공 로그에는 backend,
model, status, marker 일치 여부만 남긴다.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
from pathlib import Path

from simpleclaw.agent.turn_analysis import TURN_ANALYSIS_RESPONSE_SCHEMA
from simpleclaw.llm.models import (
    LLMRequest,
    LLMResponse,
    MultimodalAttachment,
    ToolDefinition,
)
from simpleclaw.llm.router import create_router

DEV_ENV = Path("/Users/simplist/Dev/SimpleClaw/.env")
LIVE_ENV = Path("/Users/simplist/.simpleclaw/.env")
OPENROUTER_BACKEND = "openrouter_gemini_3_6_flash"
OPENROUTER_MODEL = "google/gemini-3.6-flash"

# 1x1 PNG. 파일 자체의 수용 여부를 검증하며 원문 bytes는 로그에 남기지 않는다.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _read_secret(key: str) -> str | None:
    """환경 또는 승인된 .env에서 secret을 읽되 값/길이/경로를 노출하지 않는다."""
    if value := os.environ.get(key):
        return value
    for path in (DEV_ENV, LIVE_ENV):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith(key + "="):
                value = line.split("=", 1)[1].strip().strip("'\"")
                if value:
                    return value
    return None


def _write_temp_config(directory: Path, api_key: str) -> Path:
    """OpenRouter 전용 임시 route config와 최소 credential 파일을 작성한다."""
    config = directory / "config.yaml"
    config.write_text(
        """
llm:
  routes:
    default: {primary: openrouter_deepseek_v4_pro, retry: openrouter_gemini_3_6_flash}
    turn_analysis: {primary: openrouter_gemini_3_6_flash}
    multimodal: {primary: openrouter_gemini_3_6_flash}
  providers:
    openrouter_deepseek_v4_pro:
      type: api
      model: deepseek/deepseek-v4-pro
      transport: openai_chat
      profile: openrouter
      api_key_env: OPENROUTER_API_KEY
      base_url: https://openrouter.ai/api/v1
      extra_body:
        reasoning:
          enabled: false
    openrouter_gemini_3_6_flash:
      type: api
      model: google/gemini-3.6-flash
      transport: openai_chat
      profile: openrouter-multimodal
      api_key_env: OPENROUTER_API_KEY
      base_url: https://openrouter.ai/api/v1
""".strip(),
        encoding="utf-8",
    )
    # TemporaryDirectory 자체가 소유자 전용이며 종료 시 credential 사본이 삭제된다.
    (directory / ".env").write_text(f"OPENROUTER_API_KEY={api_key}\n", encoding="utf-8")
    return config


def _pdf_with_marker(marker: str) -> bytes:
    """외부 PDF 라이브러리 없이 단일 페이지 marker PDF를 생성한다."""
    escaped = marker.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def _record(label: str, response: LLMResponse, *, marker_ok: bool = True) -> None:
    """응답 본문 없이 attribution과 검증 상태만 출력한다."""
    attribution_ok = (
        response.backend_name == OPENROUTER_BACKEND
        and response.model == OPENROUTER_MODEL
    )
    print(
        f"case={label} backend={response.backend_name} model={response.model} "
        f"status={'ok' if attribution_ok and marker_ok else 'failed'} "
        f"marker={marker_ok}"
    )
    if not attribution_ok or not marker_ok:
        raise RuntimeError(f"{label} smoke contract failed")


async def main() -> int:
    """Credential-gated OpenRouter Gemini parity smoke를 실행한다."""
    api_key = _read_secret("OPENROUTER_API_KEY")
    if not api_key:
        print("credential=missing")
        return 2
    print("credential=available")

    with tempfile.TemporaryDirectory(prefix="simpleclaw-openrouter-gemini-") as tmp:
        router = create_router(_write_temp_config(Path(tmp), api_key))

        text_marker = "BIZ524_TEXT_OK"
        text_response = await router.send(
            LLMRequest(
                route_name="turn_analysis",
                user_message=f"Return exactly {text_marker} and nothing else.",
                reasoning={"enabled": True, "effort": "low"},
                max_tokens=500,
            )
        )
        _record(text_marker, text_response, marker_ok=text_marker in text_response.text)

        structured_response = await router.send(
            LLMRequest(
                route_name="turn_analysis",
                system_prompt="Return only JSON matching the provided schema.",
                user_message="Analyze this ordinary conversational turn: 안녕하세요",
                response_mime_type="application/json",
                response_schema=TURN_ANALYSIS_RESPONSE_SCHEMA,
                require_structured_output=True,
                reasoning={"enabled": True, "effort": "low"},
                max_tokens=1200,
            )
        )
        structured = json.loads(structured_response.text)
        structured_ok = all(
            key in structured for key in TURN_ANALYSIS_RESPONSE_SCHEMA["required"]
        )
        _record("required_structured", structured_response, marker_ok=structured_ok)

        tool_marker = "BIZ524_TOOL_OK"
        tool_response = await router.send(
            LLMRequest(
                route_name="turn_analysis",
                system_prompt="You must call the only available tool exactly once.",
                user_message=f"Call smoke_marker with marker={tool_marker}.",
                tools=[
                    ToolDefinition(
                        name="smoke_marker",
                        description="Return a fixed smoke-test marker.",
                        parameters={
                            "type": "object",
                            "properties": {
                                "marker": {"type": "string", "enum": [tool_marker]}
                            },
                            "required": ["marker"],
                            "additionalProperties": False,
                        },
                    )
                ],
                reasoning={"enabled": True, "effort": "low"},
                max_tokens=500,
            )
        )
        tool_ok = bool(
            tool_response.tool_calls
            and tool_response.tool_calls[0].name == "smoke_marker"
            and tool_response.tool_calls[0].arguments.get("marker") == tool_marker
        )
        _record("forced_tool", tool_response, marker_ok=tool_ok)

        image_marker = "BIZ524_IMAGE_OK"
        image_response = await router.send(
            LLMRequest(
                route_name="multimodal",
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Confirm the attached PNG can be read, then return exactly "
                            f"{image_marker}."
                        ),
                        "attachments": [
                            MultimodalAttachment(
                                data=_PNG_BYTES,
                                mime_type="image/png",
                                name="pixel.png",
                            )
                        ],
                    }
                ],
                reasoning={"enabled": True, "effort": "low"},
                max_tokens=500,
            )
        )
        _record(
            image_marker, image_response, marker_ok=image_marker in image_response.text
        )

        pdf_marker = "BIZ524_PDF_OK"
        pdf_response = await router.send(
            LLMRequest(
                route_name="multimodal",
                messages=[
                    {
                        "role": "user",
                        "content": "Return exactly the marker written in the PDF.",
                        "attachments": [
                            MultimodalAttachment(
                                data=_pdf_with_marker(pdf_marker),
                                mime_type="application/pdf",
                                name="marker.pdf",
                            )
                        ],
                    }
                ],
                reasoning={"enabled": True, "effort": "low"},
                max_tokens=500,
            )
        )
        _record(pdf_marker, pdf_response, marker_ok=pdf_marker in pdf_response.text)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 - secret-safe CLI failure boundary
        print(f"smoke_error_type={type(exc).__name__}")
        raise SystemExit(1) from None
