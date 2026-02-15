"""
Unit tests for context length validation.
"""

import pytest
from unittest.mock import MagicMock

from app.services.context_validation import estimate_tokens, validate_context_length
from app.models.schemas import ChatCompletionRequest, ChatMessage, ModelConfig
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
