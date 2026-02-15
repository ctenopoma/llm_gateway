"""
Context length validation — pre-request check to prevent OOM / wasted calls.
"""

from __future__ import annotations

import structlog
from fastapi import HTTPException

from app.models.schemas import ChatCompletionRequest, ModelConfig

logger = structlog.get_logger(__name__)


def estimate_tokens(text: str, model_family: str | None = None) -> int:
    """
    Approximate token count.

    Heuristic: ~4 characters per token for English/code,
    ~2 characters per token for CJK-heavy text.
    """
    if not text:
        return 0

    # Simple CJK detection ratio
    cjk_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff")
    cjk_ratio = cjk_chars / max(len(text), 1)

    if cjk_ratio > 0.3:
        chars_per_token = 2.0
    else:
        chars_per_token = 4.0

    return int(len(text) / chars_per_token)


async def validate_context_length(
    request: ChatCompletionRequest,
    model: ModelConfig,
) -> None:
    """
    Validate that request fits within model's context window.

    Prevents:
        - OOM errors on self-hosted models
        - Wasted API calls
        - Bad user experience
    """
    messages_text = "\n".join(
        f"{msg.role}: {msg.content}" for msg in request.messages
    )

    estimated_input_tokens = estimate_tokens(messages_text, model.model_family)
    requested_output = request.max_tokens or model.max_output_tokens
    total_tokens = estimated_input_tokens + requested_output

    if total_tokens > model.context_window:
        raise HTTPException(
            400,
            detail={
                "error": {
                    "code": "context_length_exceeded",
                    "message": "Request exceeds model context window",
                    "details": {
                        "estimated_input_tokens": estimated_input_tokens,
                        "requested_output_tokens": requested_output,
                        "total_tokens": total_tokens,
                        "context_window": model.context_window,
                        "model": model.id,
                    },
                }
            },
        )

    # Warn if close to limit (>80%)
    if total_tokens > model.context_window * 0.8:
        logger.warning(
            "context_window_near_limit",
            model_id=model.id,
            total_tokens=total_tokens,
            context_window=model.context_window,
            usage_percentage=round(total_tokens / model.context_window * 100, 1),
        )
