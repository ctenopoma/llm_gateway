"""
Chat completions router — /v1/chat/completions

Proxies requests through LiteLLM with streaming support and budget monitoring.
"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from decimal import Decimal
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.exceptions import BudgetExceededException
from app.models.schemas import ChatCompletionRequest
from app.services.budget import BudgetReservationSystem
from app.services.error_sanitizer import classify_and_sanitize_error, sanitize_request_metadata
from app.services.usage_log import calculate_cost, create_usage_log, finalize_usage_log

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions")
async def chat_completions(request: Request):
    """
    OpenAI-compatible chat completions endpoint.

    Supports both streaming and non-streaming responses.
    """
    chat_request: ChatCompletionRequest = request.state.chat_request
    model_conf = request.state.model
    user_oid: str = request.state.user_oid
    api_key_id: Optional[str] = request.state.api_key_id
    app_id: Optional[str] = request.state.app_id
    estimated_cost: float = request.state.estimated_cost
    request_id: str = request.state.request_id

    start_time = time.time()

    # Sanitize metadata for logging (no prompt content)
    metadata = await sanitize_request_metadata(chat_request)

    # Create pending usage log
    log_id = await create_usage_log(
        user_oid=user_oid,
        api_key_id=api_key_id,
        app_id=app_id,
        request_id=request_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        requested_model=chat_request.model,
        request_metadata=metadata,
    )


    try:
        # Get the LiteLLM router from app state
        llm_router = request.app.state.llm_router

        # Build call kwargs
        call_kwargs = {
            "model": chat_request.model,
            "messages": [msg.model_dump() for msg in chat_request.messages],
        }
        if chat_request.max_tokens is not None:
            call_kwargs["max_tokens"] = chat_request.max_tokens
        if chat_request.temperature is not None:
            call_kwargs["temperature"] = chat_request.temperature
        if chat_request.top_p is not None:
            call_kwargs["top_p"] = chat_request.top_p
        if chat_request.frequency_penalty is not None:
            call_kwargs["frequency_penalty"] = chat_request.frequency_penalty
        if chat_request.presence_penalty is not None:
            call_kwargs["presence_penalty"] = chat_request.presence_penalty
        if chat_request.stop is not None:
            call_kwargs["stop"] = chat_request.stop

        if chat_request.stream:
            call_kwargs["stream"] = True
            response = await llm_router.acompletion(**call_kwargs)

            return StreamingResponse(
                _stream_processor(
                    response=response,
                    log_id=log_id,
                    model_conf=model_conf,
                    api_key_id=api_key_id,
                    estimated_cost=estimated_cost,
                    start_time=start_time,
                ),
                media_type="text/event-stream",
            )
        else:
            response = await llm_router.acompletion(**call_kwargs)
            latency_ms = int((time.time() - start_time) * 1000)

            # Extract usage
            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0
            actual_model = response.model or chat_request.model

            cost = await calculate_cost(
                input_tokens, output_tokens, 0, 0, chat_request.model
            )

            await finalize_usage_log(
                log_id=log_id,
                actual_model=actual_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
                latency_ms=latency_ms,
            )

            # Release budget reservation
            if api_key_id and estimated_cost > 0:
                await BudgetReservationSystem.release_reservation(
                    api_key_id, estimated_cost, float(cost)
                )

            return response.model_dump()

    except HTTPException:
        raise
    except Exception as e:
        return await _handle_llm_error(e, log_id, api_key_id, estimated_cost, start_time)


async def _stream_processor(
    *,
    response,
    log_id: int,
    model_conf,
    api_key_id: Optional[str],
    estimated_cost: float,
    start_time: float,
):
    """Process streaming response with budget monitoring (kill switch)."""
    actual_model = None
    input_tokens = 0
    output_tokens = 0
    first_token_time = None
    chunk_count = 0
    cost = Decimal("0")

    CHECK_INTERVAL = 50

    try:
        async for chunk in response:
            if first_token_time is None:
                first_token_time = time.time()

            # Forward chunk to client
            yield f"data: {json.dumps(chunk.model_dump())}\n\n"

            chunk_count += 1

            # Extract metadata
            if hasattr(chunk, "model") and chunk.model:
                actual_model = chunk.model

            if hasattr(chunk, "usage") and chunk.usage:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0

            # KILL SWITCH: periodic budget check
            if api_key_id and chunk_count % CHECK_INTERVAL == 0:
                current_cost = await calculate_cost(
                    input_tokens, output_tokens, 0, 0,
                    actual_model or model_conf.id,
                )

                from app.services.api_key import get_api_key_by_id

                api_key = await get_api_key_by_id(api_key_id)
                if api_key and api_key.budget_monthly:
                    projected = api_key.usage_current_month + current_cost
                    if projected >= api_key.budget_monthly:
                        logger.warning(
                            "budget_kill_switch_triggered",
                            api_key_id=api_key_id,
                            current_cost=float(current_cost),
                            usage=float(api_key.usage_current_month),
                            budget=float(api_key.budget_monthly),
                        )
                        raise BudgetExceededException()

        # Stream completed normally
        yield "data: [DONE]\n\n"

        latency_ms = int((time.time() - start_time) * 1000)
        ttft_ms = (
            int((first_token_time - start_time) * 1000)
            if first_token_time
            else None
        )

        cost = await calculate_cost(
            input_tokens, output_tokens, 0, 0,
            actual_model or model_conf.id,
        )

        await finalize_usage_log(
            log_id=log_id,
            actual_model=actual_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
        )

    except BudgetExceededException:
        cost = await calculate_cost(
            input_tokens, output_tokens, 0, 0,
            actual_model or model_conf.id,
        )
        await finalize_usage_log(
            log_id=log_id,
            actual_model=actual_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            status="cancelled",
            error_code="budget_exceeded_during_stream",
        )
        yield f'data: {json.dumps({"error": "Budget exceeded", "code": "budget_kill_switch"})}\n\n'
        yield "data: [DONE]\n\n"

    except asyncio.CancelledError:
        cost = await calculate_cost(
            input_tokens, output_tokens, 0, 0,
            actual_model or model_conf.id,
        )
        await finalize_usage_log(
            log_id=log_id,
            actual_model=actual_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            status="cancelled",
            error_code="client_disconnected",
        )

    finally:
        # Release budget reservation
        if api_key_id and estimated_cost > 0:
            await BudgetReservationSystem.release_reservation(
                api_key_id, estimated_cost, float(cost)
            )


async def _handle_llm_error(
    error: Exception,
    log_id: int,
    api_key_id: Optional[str],
    estimated_cost: float,
    start_time: float,
) -> JSONResponse:
    """Handle LLM errors with proper sanitization."""
    error_code, sanitized_message = classify_and_sanitize_error(error)
    latency_ms = int((time.time() - start_time) * 1000)

    logger.error(
        "llm_error_detailed",
        log_id=log_id,
        error_code=error_code,
        error_type=type(error).__name__,
        error_message=str(error),
        stack_trace=traceback.format_exc(),
    )

    await finalize_usage_log(
        log_id=log_id,
        actual_model=None,
        input_tokens=0,
        output_tokens=0,
        cost=Decimal("0"),
        status="failed",
        error_code=error_code,
        error_message=sanitized_message,
        latency_ms=latency_ms,
    )

    # Release budget reservation
    if api_key_id and estimated_cost > 0:
        await BudgetReservationSystem.release_reservation(
            api_key_id, estimated_cost, 0.0
        )

    return JSONResponse(
        status_code=502,
        content={
            "error": {
                "code": error_code,
                "message": sanitized_message,
                "type": "provider_error",
            }
        },
    )
