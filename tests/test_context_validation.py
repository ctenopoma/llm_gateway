"""
Unit tests for context length validation.
"""

import pytest
from unittest.mock import MagicMock

from app.services.context_validation import estimate_tokens, validate_context_length
from app.models.schemas import (
    ChatCompletionRequest, ChatMessage, ContentPart, ImageUrl, ModelConfig,
)
from decimal import Decimal


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_english_text(self):
        text = "Hello, how are you doing today?"  # 30 chars → ~7-8 tokens
        tokens = estimate_tokens(text)
        assert 5 <= tokens <= 15

    def test_cjk_text_uses_lower_ratio(self):
        text = "こんにちは今日はお元気ですか"  # 13 CJK chars
        tokens = estimate_tokens(text)
        # CJK should give more tokens per character count
        assert tokens > len(text) / 4

    def test_long_text_proportional(self):
        short = estimate_tokens("word " * 10)
        long_ = estimate_tokens("word " * 100)
        assert long_ > short


class TestValidateContextLength:
    @pytest.fixture
    def model(self):
        return ModelConfig(
            id="test-model",
            litellm_name="test/model",
            provider="test",
            input_cost=Decimal("100"),
            output_cost=Decimal("200"),
            context_window=100,
            max_output_tokens=50,
        )

    @pytest.mark.asyncio
    async def test_request_within_limit_passes(self, model):
        request = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Hi")],
            max_tokens=10,
        )
        # Should not raise
        await validate_context_length(request, model)

    @pytest.mark.asyncio
    async def test_request_exceeding_limit_raises(self, model):
        # Create a message that exceeds 100 tokens
        long_content = "word " * 500  # ~250 tokens estimated
        request = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content=long_content)],
            max_tokens=50,
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await validate_context_length(request, model)
        assert exc_info.value.status_code == 400


class TestVisionMessageSupport:
    """Tests for VLM (Vision Language Model) multimodal content support."""

    @pytest.fixture
    def vision_model(self):
        return ModelConfig(
            id="vision-model",
            litellm_name="openai/gpt-4o",
            provider="openai",
            input_cost=Decimal("250"),
            output_cost=Decimal("1000"),
            context_window=128000,
            max_output_tokens=4096,
            supports_vision=True,
        )

    @pytest.fixture
    def text_only_model(self):
        return ModelConfig(
            id="text-model",
            litellm_name="openai/gpt-4o-mini",
            provider="openai",
            input_cost=Decimal("15"),
            output_cost=Decimal("60"),
            context_window=128000,
            max_output_tokens=4096,
            supports_vision=False,
        )

    # ── ChatMessage schema tests ──────────────────────────────

    def test_text_only_message(self):
        msg = ChatMessage(role="user", content="Hello")
        assert msg.get_text_content() == "Hello"
        assert msg.has_vision_content() is False

    def test_multimodal_message_with_image_url(self):
        msg = ChatMessage(
            role="user",
            content=[
                ContentPart(type="text", text="What's in this image?"),
                ContentPart(
                    type="image_url",
                    image_url=ImageUrl(url="https://example.com/cat.png"),
                ),
            ],
        )
        assert msg.get_text_content() == "What's in this image?"
        assert msg.has_vision_content() is True

    def test_multimodal_message_with_base64_image(self):
        msg = ChatMessage(
            role="user",
            content=[
                ContentPart(type="text", text="Describe this"),
                ContentPart(
                    type="image_url",
                    image_url=ImageUrl(
                        url="data:image/png;base64,iVBORw0KGgo=",
                        detail="high",
                    ),
                ),
            ],
        )
        assert msg.has_vision_content() is True
        assert msg.get_text_content() == "Describe this"

    def test_multimodal_message_multiple_images(self):
        msg = ChatMessage(
            role="user",
            content=[
                ContentPart(type="text", text="Compare these images"),
                ContentPart(
                    type="image_url",
                    image_url=ImageUrl(url="https://example.com/a.png"),
                ),
                ContentPart(
                    type="image_url",
                    image_url=ImageUrl(url="https://example.com/b.png"),
                ),
            ],
        )
        assert msg.has_vision_content() is True
        assert msg.get_text_content() == "Compare these images"

    def test_multimodal_message_text_only_parts(self):
        """A message with content as list but only text parts is not vision."""
        msg = ChatMessage(
            role="user",
            content=[
                ContentPart(type="text", text="Part 1"),
                ContentPart(type="text", text="Part 2"),
            ],
        )
        assert msg.has_vision_content() is False
        assert msg.get_text_content() == "Part 1\nPart 2"

    def test_model_dump_text_message(self):
        msg = ChatMessage(role="user", content="Hello")
        dumped = msg.model_dump(exclude_none=True)
        assert dumped == {"role": "user", "content": "Hello"}

    def test_model_dump_multimodal_message(self):
        msg = ChatMessage(
            role="user",
            content=[
                ContentPart(type="text", text="What's this?"),
                ContentPart(
                    type="image_url",
                    image_url=ImageUrl(url="https://example.com/img.png"),
                ),
            ],
        )
        dumped = msg.model_dump(exclude_none=True)
        assert dumped["role"] == "user"
        assert isinstance(dumped["content"], list)
        assert dumped["content"][0] == {"type": "text", "text": "What's this?"}
        assert dumped["content"][1] == {
            "type": "image_url",
            "image_url": {"url": "https://example.com/img.png"},
        }

    # ── Context validation with VLM ──────────────────────────

    @pytest.mark.asyncio
    async def test_vision_request_passes_on_vision_model(self, vision_model):
        request = ChatCompletionRequest(
            model="vision-model",
            messages=[
                ChatMessage(
                    role="user",
                    content=[
                        ContentPart(type="text", text="Describe the image"),
                        ContentPart(
                            type="image_url",
                            image_url=ImageUrl(url="https://example.com/cat.png"),
                        ),
                    ],
                )
            ],
            max_tokens=100,
        )
        # Should not raise
        await validate_context_length(request, vision_model)

    @pytest.mark.asyncio
    async def test_vision_request_rejected_on_text_only_model(self, text_only_model):
        request = ChatCompletionRequest(
            model="text-model",
            messages=[
                ChatMessage(
                    role="user",
                    content=[
                        ContentPart(type="text", text="Describe the image"),
                        ContentPart(
                            type="image_url",
                            image_url=ImageUrl(url="https://example.com/cat.png"),
                        ),
                    ],
                )
            ],
            max_tokens=100,
        )
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await validate_context_length(request, text_only_model)
        assert exc_info.value.status_code == 400
        assert "vision_not_supported" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_text_request_passes_on_text_only_model(self, text_only_model):
        """Standard text requests should work fine on non-vision models."""
        request = ChatCompletionRequest(
            model="text-model",
            messages=[ChatMessage(role="user", content="Hello, world!")],
            max_tokens=100,
        )
        await validate_context_length(request, text_only_model)

    # ── Parse from raw dict (simulates incoming JSON) ────────

    def test_parse_multimodal_from_dict(self):
        """Validate that raw OpenAI-style JSON is correctly parsed."""
        raw = {
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://example.com/photo.jpg",
                                "detail": "low",
                            },
                        },
                    ],
                }
            ],
        }
        req = ChatCompletionRequest(**raw)
        assert len(req.messages) == 1
        msg = req.messages[0]
        assert isinstance(msg.content, list)
        assert msg.has_vision_content() is True
        assert msg.get_text_content() == "What is this?"
