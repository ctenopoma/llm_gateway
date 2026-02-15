"""
Error and log sanitization utilities.

CRITICAL: Never log prompt/response content.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.schemas import ChatCompletionRequest


async def sanitize_request_metadata(request: ChatCompletionRequest) -> dict[str, Any]:
    """
    Extract only metadata from a chat completion request.

    NEVER log:
        - messages[].content
        - response content
        - system prompts
        - user inputs
    """
    metadata: dict[str, Any] = {
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "top_p": request.top_p,
        "frequency_penalty": request.frequency_penalty,
        "presence_penalty": request.presence_penalty,
        "stream": request.stream,
        "model": request.model,
        "message_count": len(request.messages) if request.messages else 0,
    }

    if request.messages:
        metadata["message_roles"] = [msg.role for msg in request.messages]

    return metadata


def sanitize_error_message(error: str, max_length: int = 200) -> str:
    """
    Sanitize error message to prevent information leakage.

    Rules:
        - Truncate long messages
        - Remove internal paths
        - Remove sensitive configuration details
    """
    if len(error) > max_length:
        error = error[:max_length] + "... (truncated)"

    # Remove file paths
    error = re.sub(r"/[^\s]+\.py", "*.py", error)
    error = re.sub(r"/[^\s]+/", "[PATH]/", error)
    error = re.sub(r"[A-Z]:\\[^\s]+\.py", "*.py", error)
    error = re.sub(r"[A-Z]:\\[^\s]+\\\\", "[PATH]/", error)

    # Remove IP addresses
    error = re.sub(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", "[IP]", error)

    # Remove authentication tokens
    error = re.sub(r"Bearer [^\s]+", "Bearer [REDACTED]", error)
    error = re.sub(r"sk-[^\s]+", "sk-[REDACTED]", error)

    return error


def classify_and_sanitize_error(error: Exception) -> tuple[str, str]:
    """
    Classify error and return (error_code, sanitized_message).
    """
    error_str = str(error).lower()
    original_error = str(error)

    if "out of memory" in error_str or "oom" in error_str:
        return "oom_error", "Model ran out of memory. Try reducing max_tokens or prompt length."

    if "timeout" in error_str:
        return "timeout", "Request timed out. Model may be overloaded."

    if "rate limit" in error_str:
        return "rate_limit", "Provider rate limit exceeded. Please retry later."

    if "gpu" in error_str:
        return "gpu_error", "GPU unavailable or error occurred."

    if "model not loaded" in error_str or "not found" in error_str:
        return "model_not_loaded", "Model is not currently loaded."

    return "provider_error", sanitize_error_message(original_error, max_length=150)
