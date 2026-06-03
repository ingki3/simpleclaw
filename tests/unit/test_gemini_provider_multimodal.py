"""Gemini provider multimodal attachment conversion tests."""

from simpleclaw.llm.models import MultimodalAttachment
from simpleclaw.llm.providers.gemini import GeminiProvider


def test_convert_messages_includes_text_and_inline_image_parts():
    provider = GeminiProvider.__new__(GeminiProvider)

    contents = provider._convert_messages([
        {
            "role": "user",
            "content": "이 이미지를 설명해줘",
            "attachments": [
                MultimodalAttachment(
                    data=b"jpeg-bytes",
                    mime_type="image/jpeg",
                    filename="photo.jpg",
                )
            ],
        }
    ])

    assert len(contents) == 1
    assert contents[0].role == "user"
    parts = contents[0].parts
    assert parts[0].text == "이 이미지를 설명해줘"
    assert parts[1].inline_data.mime_type == "image/jpeg"
    assert bytes(parts[1].inline_data.data) == b"jpeg-bytes"


def test_convert_messages_preserves_multiple_image_order():
    provider = GeminiProvider.__new__(GeminiProvider)

    contents = provider._convert_messages([
        {
            "role": "user",
            "content": "compare",
            "attachments": [
                {"data": b"first", "mime_type": "image/png"},
                {"data": b"second", "mime_type": "image/webp"},
            ],
        }
    ])

    parts = contents[0].parts
    assert [p.inline_data.mime_type for p in parts[1:]] == ["image/png", "image/webp"]
    assert [bytes(p.inline_data.data) for p in parts[1:]] == [b"first", b"second"]
