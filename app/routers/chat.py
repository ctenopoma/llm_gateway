"""
Chat completions router — /v1/chat/completions

Proxies requests through LiteLLM with streaming support and budget monitoring.
Embedding and rerank requests are proxied directly to backend endpoints
for maximum compatibility with self-hosted models (vLLM, Ollama, TGI, etc.).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from decimal import Decimal
from typing import Any, Optional

import httpx as httpx_lib
import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app import database as db
from app.exceptions import BudgetExceededException
from app.models.schemas import ChatCompletionRequest, EmbeddingRequest, RerankRequest
from app.services.budget import BudgetReservationSystem
from app.services.error_sanitizer import classify_and_sanitize_error, sanitize_request_metadata
from app.services.usage_log import calculate_cost, create_usage_log, finalize_usage_log

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["chat"])


# ── Models listing (OpenAI-compatible) ───────────────────────────


@router.get("/models")
async def list_models(request: Request):
    """
    OpenAI-compatible model listing endpoint.

    Returns all active models. Required by Dify and other
    OpenAI-compatible clients for model discovery / credential validation.
    """
    rows = await db.fetch_all(
        "SELECT id, provider, created_at FROM Models WHERE is_active = TRUE ORDER BY id"
    )
    data = []
    for row in rows:
        created = 0
        if row["created_at"]:
            created = int(row["created_at"].timestamp())
        data.append({
            "id": row["id"],
            "object": "model",
            "created": created,
            "owned_by": row["provider"],
        })
    return {"object": "list", "data": data}


@router.get("/models/{model_id}")
async def get_model(model_id: str, request: Request):
    """
    OpenAI-compatible single model retrieval.
    """
    row = await db.fetch_one(
        "SELECT id, provider, created_at FROM Models WHERE id = $1 AND is_active = TRUE",
        model_id,
    )
    if not row:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"The model '{model_id}' does not exist",
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                }
            },
        )
    created = int(row["created_at"].timestamp()) if row["created_at"] else 0
    return {
        "id": row["id"],
        "object": "model",
        "created": created,
        "owned_by": row["provider"],
    }


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

            # Return clean OpenAI-compatible response (exclude None fields
            # to avoid issues with strict clients like Dify)
            return _clean_openai_response(response.model_dump())

    except HTTPException:
        raise
    except Exception as e:
        return await _handle_llm_error(e, log_id, api_key_id, estimated_cost, start_time)


# ── Embeddings endpoint (OpenAI-compatible) ──────────────────────


@router.post("/embeddings")
async def embeddings(request: Request):
    """
    OpenAI-compatible embeddings endpoint.

    Proxies embedding requests directly to the backend endpoint (vLLM, etc.)
    for maximum compatibility. Does NOT go through LiteLLM Router.
    """
    embedding_request: EmbeddingRequest = request.state.embedding_request
    model_conf = request.state.model
    user_oid: str = request.state.user_oid
    api_key_id: Optional[str] = request.state.api_key_id
    app_id: Optional[str] = request.state.app_id
    estimated_cost: float = request.state.estimated_cost
    request_id: str = request.state.request_id

    start_time = time.time()

    # Sanitize metadata for logging
    metadata = {
        "input_count": len(embedding_request.input) if isinstance(embedding_request.input, list) else 1,
        "encoding_format": embedding_request.encoding_format,
    }

    # Create pending usage log
    log_id = await create_usage_log(
        user_oid=user_oid,
        api_key_id=api_key_id,
        app_id=app_id,
        request_id=request_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        requested_model=embedding_request.model,
        request_metadata=metadata,
    )

    try:
        endpoint = await _get_healthy_endpoint(embedding_request.model)
        base_url = endpoint["base_url"].rstrip("/")
        api_key = _resolve_endpoint_api_key(endpoint.get("api_key_ref"))
        timeout = endpoint.get("timeout_seconds", 120)

        # Build request payload
        payload: dict[str, Any] = {
            "model": embedding_request.model,
            "input": embedding_request.input,
        }
        if embedding_request.encoding_format is not None:
            payload["encoding_format"] = embedding_request.encoding_format
        if embedding_request.dimensions is not None:
            payload["dimensions"] = embedding_request.dimensions

        # Direct call to backend
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx_lib.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/embeddings",
                json=payload,
                headers=headers,
                timeout=timeout,
            )

        latency_ms = int((time.time() - start_time) * 1000)

        if resp.status_code != 200:
            logger.error(
                "embedding_backend_error",
                model=embedding_request.model,
                status=resp.status_code,
                body=resp.text[:500],
            )
            raise HTTPException(resp.status_code, resp.text)

        result = resp.json()

        # Extract usage
        input_tokens = 0
        usage = result.get("usage", {})
        if usage:
            input_tokens = usage.get("prompt_tokens", 0) or usage.get("total_tokens", 0) or 0

        cost = await calculate_cost(
            input_tokens, 0, 0, 0, embedding_request.model
        )

        await finalize_usage_log(
            log_id=log_id,
            actual_model=result.get("model") or embedding_request.model,
            input_tokens=input_tokens,
            output_tokens=0,
            cost=cost,
            latency_ms=latency_ms,
        )

        # Release budget reservation
        if api_key_id and estimated_cost > 0:
            await BudgetReservationSystem.release_reservation(
                api_key_id, estimated_cost, float(cost)
            )

        return _clean_openai_response(result)

    except HTTPException:
        raise
    except Exception as e:
        return await _handle_llm_error(e, log_id, api_key_id, estimated_cost, start_time)


# ── Rerank endpoint ──────────────────────────────────────────────


@router.post("/rerank")
async def rerank(request: Request):
    """
    Rerank endpoint (Jina / Cohere / vLLM compatible).

    Proxies rerank requests directly to the backend endpoint for maximum
    compatibility with self-hosted reranker models (e.g. Qwen3-reranker on vLLM).
    Does NOT go through LiteLLM Router (which lacks vLLM rerank support).
    """
    rerank_request: RerankRequest = request.state.rerank_request
    model_conf = request.state.model
    user_oid: str = request.state.user_oid
    api_key_id: Optional[str] = request.state.api_key_id
    app_id: Optional[str] = request.state.app_id
    estimated_cost: float = request.state.estimated_cost
    request_id: str = request.state.request_id

    start_time = time.time()

    # Sanitize metadata for logging
    metadata = {
        "document_count": len(rerank_request.documents),
        "top_n": rerank_request.top_n,
    }

    # Create pending usage log
    log_id = await create_usage_log(
        user_oid=user_oid,
        api_key_id=api_key_id,
        app_id=app_id,
        request_id=request_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        requested_model=rerank_request.model,
        request_metadata=metadata,
    )

    try:
        endpoint = await _get_healthy_endpoint(rerank_request.model)
        base_url = endpoint["base_url"].rstrip("/")
        api_key = _resolve_endpoint_api_key(endpoint.get("api_key_ref"))
        timeout = endpoint.get("timeout_seconds", 120)

        # Build Jina/vLLM rerank payload
        payload: dict[str, Any] = {
            "model": rerank_request.model,
            "query": rerank_request.query,
            "documents": rerank_request.documents,
        }
        if rerank_request.top_n is not None:
            payload["top_n"] = rerank_request.top_n
        if rerank_request.return_documents is not None:
            payload["return_documents"] = rerank_request.return_documents
        if rerank_request.max_chunks_per_doc is not None:
            payload["max_chunks_per_doc"] = rerank_request.max_chunks_per_doc
        if rerank_request.rank_fields is not None:
            payload["rank_fields"] = rerank_request.rank_fields

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # --- 3-tier fallback -------------------------------------------
        # 1. /rerank  (vLLM with --reranking)
        # 2. /score   (vLLM with --task score)
        # 3. /v1/chat/completions  (cross-encoder prompt + logprobs)
        # ---------------------------------------------------------------
        result = None
        async with httpx_lib.AsyncClient() as client:
            # ── Tier 1: /rerank ────────────────────────────────────
            resp = await client.post(
                f"{base_url}/rerank",
                json=payload,
                headers=headers,
                timeout=timeout,
            )

            if resp.status_code in (404, 405, 501):
                # ── Tier 2: /score ─────────────────────────────────
                score_payload = {
                    "model": rerank_request.model,
                    "text_1": rerank_request.query,
                    "text_2": [
                        doc if isinstance(doc, str) else str(doc)
                        for doc in rerank_request.documents
                    ],
                }
                resp = await client.post(
                    f"{base_url}/score",
                    json=score_payload,
                    headers=headers,
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    score_data = resp.json()
                    result = _convert_score_to_rerank(
                        score_data,
                        rerank_request.documents,
                        rerank_request.top_n,
                    )

            if result is None and resp.status_code not in (200,):
                # ── Tier 3: chat completions (cross-encoder) ───────
                logger.info(
                    "rerank_fallback_to_chat",
                    model=rerank_request.model,
                    reason=f"/rerank and /score unavailable (last status={resp.status_code})",
                )
                result = await _rerank_via_chat_completions(
                    client=client,
                    base_url=base_url,
                    headers=headers,
                    model=rerank_request.model,
                    query=rerank_request.query,
                    documents=rerank_request.documents,
                    top_n=rerank_request.top_n,
                    timeout=timeout,
                )

        latency_ms = int((time.time() - start_time) * 1000)

        if result is None:
            if resp.status_code != 200:
                logger.error(
                    "rerank_backend_error",
                    model=rerank_request.model,
                    status=resp.status_code,
                    body=resp.text[:500],
                )
                raise HTTPException(resp.status_code, resp.text)
            result = resp.json()

        # Estimate input tokens
        total_chars = len(rerank_request.query)
        for doc in rerank_request.documents:
            total_chars += len(doc) if isinstance(doc, str) else sum(len(str(v)) for v in doc.values())
        estimated_tokens = total_chars // 4

        cost = await calculate_cost(
            estimated_tokens, 0, 0, 0, rerank_request.model
        )

        await finalize_usage_log(
            log_id=log_id,
            actual_model=rerank_request.model,
            input_tokens=estimated_tokens,
            output_tokens=0,
            cost=cost,
            latency_ms=latency_ms,
        )

        # Release budget reservation
        if api_key_id and estimated_cost > 0:
            await BudgetReservationSystem.release_reservation(
                api_key_id, estimated_cost, float(cost)
            )

        return _clean_openai_response(result)

    except HTTPException:
        raise
    except Exception as e:
        return await _handle_llm_error(e, log_id, api_key_id, estimated_cost, start_time)


# ── Shared helpers for direct backend proxy ──────────────────────


async def _get_healthy_endpoint(model_id: str) -> dict:
    """
    Get a healthy active endpoint for a given model.
    Returns a dict with base_url, api_key_ref, timeout_seconds.
    """
    row = await db.fetch_one(
        """
        SELECT base_url, api_key_ref, timeout_seconds
        FROM ModelEndpoints
        WHERE model_id = $1 AND is_active = TRUE
          AND health_status IN ('healthy', 'degraded', 'unknown')
        ORDER BY routing_priority ASC
        LIMIT 1
        """,
        model_id,
    )
    if not row:
        raise HTTPException(
            502,
            detail={
                "error": {
                    "code": "no_healthy_endpoint",
                    "message": f"No healthy endpoint available for model '{model_id}'",
                    "type": "provider_error",
                }
            },
        )
    return dict(row)


def _resolve_endpoint_api_key(api_key_ref: str | None) -> str | None:
    """Resolve an api_key_ref environment variable to the actual key."""
    if not api_key_ref:
        return None
    val = os.environ.get(api_key_ref)
    return val if val and val != "EMPTY" else None


def _convert_score_to_rerank(
    score_data: dict,
    documents: list,
    top_n: int | None,
) -> dict:
    """
    Convert vLLM /score response to Jina/Cohere rerank format.

    /score returns: {"data": [{"index": 0, "score": 0.95}, ...]}
    rerank expects: {"results": [{"index": 0, "relevance_score": 0.95}, ...]}
    """
    items = score_data.get("data", [])
    results = []
    for item in items:
        results.append({
            "index": item.get("index", 0),
            "relevance_score": item.get("score", 0.0),
        })
    # Sort by score descending
    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    if top_n is not None:
        results = results[:top_n]
    return {"results": results}


import math


async def _rerank_via_chat_completions(
    *,
    client: httpx_lib.AsyncClient,
    base_url: str,
    headers: dict,
    model: str,
    query: str,
    documents: list,
    top_n: int | None,
    timeout: float,
) -> dict:
    """
    Fallback: rerank via /v1/chat/completions using cross-encoder prompt.

    Works with Qwen3-reranker and similar thinking models that were loaded
    without --reranking or --task score flags.

    Strategy:
      - Disable thinking via chat_template_kwargs.
      - Send each (query, document) pair as a "yes/no relevance" prompt.
      - Request logprobs for the first token.
      - Compute score as P(yes) / (P(yes) + P(no)) for a normalised [0,1] score.
    """
    SYSTEM_PROMPT = (
        "Judge whether the Document is relevant to the Query. "
        "Output only \"yes\" or \"no\"."
    )

    async def _score_one(idx: int, doc: str) -> dict:
        doc_text = doc if isinstance(doc, str) else str(doc)
        user_content = f"<Query>{query}</Query>\n<Document>{doc_text}</Document>"

        chat_payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 1,
            "temperature": 0.0,
            "logprobs": True,
            "top_logprobs": 20,
            # Disable thinking for Qwen3-style models so the first
            # token is directly "yes" or "no".
            "chat_template_kwargs": {"enable_thinking": False},
        }

        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            json=chat_payload,
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code != 200:
            logger.warning(
                "rerank_chat_fallback_error",
                index=idx,
                status=resp.status_code,
                body=resp.text[:200],
            )
            return {"index": idx, "relevance_score": 0.0}

        data = resp.json()
        choice = data.get("choices", [{}])[0]

        # ── Extract score from logprobs ──────────────────────────
        score = 0.0
        logprobs_content = (
            choice.get("logprobs", {}) or {}
        ).get("content", [])

        if logprobs_content:
            top_lps = logprobs_content[0].get("top_logprobs", [])

            yes_logprob: float | None = None
            no_logprob: float | None = None
            for lp in top_lps:
                tok = lp.get("token", "").strip().lower()
                if tok in ("yes", "yes.") and yes_logprob is None:
                    yes_logprob = lp["logprob"]
                elif tok in ("no", "no.") and no_logprob is None:
                    no_logprob = lp["logprob"]

            if yes_logprob is not None and no_logprob is not None:
                # Normalised: P(yes) / (P(yes) + P(no))
                p_yes = math.exp(yes_logprob)
                p_no = math.exp(no_logprob)
                score = p_yes / (p_yes + p_no)
            elif yes_logprob is not None:
                score = math.exp(yes_logprob)
            else:
                # "yes" not found in top_logprobs → very low relevance
                score = 0.0
        else:
            # No logprobs — fall back to text matching
            text = (choice.get("message", {}) or {}).get("content", "").strip().lower()
            score = 1.0 if text.startswith("yes") else 0.0

        return {"index": idx, "relevance_score": round(float(score), 6)}

    # Score all documents concurrently
    tasks = [_score_one(i, doc) for i, doc in enumerate(documents)]
    scored = await asyncio.gather(*tasks)

    # Sort by score descending
    results = sorted(scored, key=lambda x: x["relevance_score"], reverse=True)
    if top_n is not None:
        results = results[:top_n]

    return {"results": results}


def _clean_openai_response(data: dict) -> dict:
    """
    Remove None values and internal fields from LiteLLM response
    to ensure strict OpenAI-compatible JSON output.

    Some clients (e.g. Dify) fail on unexpected null fields like
    system_fingerprint=null, completion_tokens_details=null, etc.
    """
    def _strip_none(obj):
        if isinstance(obj, dict):
            return {
                k: _strip_none(v)
                for k, v in obj.items()
                if v is not None and not k.startswith("_")
            }
        if isinstance(obj, list):
            return [_strip_none(item) for item in obj]
        return obj

    return _strip_none(data)


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

            # Forward chunk to client (clean None fields for strict clients)
            yield f"data: {json.dumps(_clean_openai_response(chunk.model_dump()))}\n\n"

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
