"""
Gateway middleware — the central guard pipeline.

Order:
    1. Authentication (Gateway Secret or API Key)
    2. User validation (payment status)
    3. Rate limiting (Redis RPM)
    4. Model permission check
    5. Context length validation
    6. Budget reservation
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app import database as db
from app.config import get_settings
from app.models.schemas import ApiKey, ChatCompletionRequest, EmbeddingRequest, RerankRequest, ModelConfig
from app.redis_client import get_redis
from app.services.api_key import (
    check_ip_allowlist,
    verify_and_get_api_key_with_cache,
)
from app.services.budget import check_and_reserve_budget
from app.services.context_validation import validate_context_length
from app.services.user_management import check_and_sync_user_expiry

logger = structlog.get_logger(__name__)

# Paths that bypass the full middleware pipeline
_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
_PUBLIC_PREFIXES = ("/admin", "/v1/models")


class GatewayMiddleware(BaseHTTPMiddleware):
    """Central request guard pipeline."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip middleware for public paths and admin panel
        if request.url.path in _PUBLIC_PATHS or request.url.path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        start_time = time.time()
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.estimated_cost = 0.0
        request.state.api_key = None
        request.state.api_key_id = None
        request.state.app_id = None  # Init app_id

        try:
            # ── Phase 1: Authentication ──────────────────────────
            user_oid, api_key_id, api_key, app_id = await _authenticate(request)
            request.state.user_oid = user_oid
            request.state.api_key_id = api_key_id
            request.state.api_key = api_key
            request.state.app_id = app_id

            # ── Phase 2: User validation ─────────────────────────
            await _validate_user(user_oid)

            # ── Phase 3: Rate limiting ───────────────────────────
            if api_key:
                await _check_rate_limit(api_key)

            # ── Phases 4-6 only for chat completions POST ────────
            if (
                request.method == "POST"
                and "/v1/chat/completions" in request.url.path
            ):
                body = await request.json()
                chat_request = ChatCompletionRequest(**body)
                request.state.chat_request = chat_request

                # Phase 4: Model permission check
                model = await _get_and_check_model(
                    chat_request.model, api_key
                )
                request.state.model = model

                # Phase 5: Context length validation
                await validate_context_length(chat_request, model)

                # Phase 6: Budget reservation
                if api_key:
                    estimated_cost = await check_and_reserve_budget(
                        api_key, model, chat_request.max_tokens
                    )
                    request.state.estimated_cost = estimated_cost

            # ── Phases 4-6 for embeddings POST ───────────────────
            elif (
                request.method == "POST"
                and "/v1/embeddings" in request.url.path
            ):
                body = await request.json()
                embedding_request = EmbeddingRequest(**body)
                request.state.embedding_request = embedding_request

                # Phase 4: Model permission check
                model = await _get_and_check_model(
                    embedding_request.model, api_key
                )
                request.state.model = model

                # Phase 5: Skip context validation for embeddings

                # Phase 6: Budget reservation
                if api_key:
                    estimated_cost = await check_and_reserve_budget(
                        api_key, model, None
                    )
                    request.state.estimated_cost = estimated_cost

            # ── Phases 4-6 for rerank POST ───────────────────────
            elif (
                request.method == "POST"
                and "/v1/rerank" in request.url.path
            ):
                body = await request.json()
                rerank_request = RerankRequest(**body)
                request.state.rerank_request = rerank_request

                # Phase 4: Model permission check
                model = await _get_and_check_model(
                    rerank_request.model, api_key
                )
                request.state.model = model

                # Phase 5: Skip context validation for rerank

                # Phase 6: Budget reservation
                if api_key:
                    estimated_cost = await check_and_reserve_budget(
                        api_key, model, None
                    )
                    request.state.estimated_cost = estimated_cost

            # ── Continue ─────────────────────────────────────────
            response = await call_next(request)

            latency_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "request_completed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                latency_ms=latency_ms,
                app_id=app_id,
            )
            return response

        except HTTPException as e:
            logger.warning(
                "middleware_auth_error",
                request_id=request_id,
                status_code=e.status_code,
                detail=e.detail,
            )
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail},
            )

        except Exception as e:
            # Capture critical debug info for 500 errors
            import traceback
            
            # Sanitize headers
            safe_headers = dict(request.headers)
            if "authorization" in safe_headers:
                safe_headers["authorization"] = "[REDACTED]"
            if "x-gateway-secret" in safe_headers:
                safe_headers["x-gateway-secret"] = "[REDACTED]"
            
            # Attempt to capture body snippet (carefully)
            body_preview = "Could not capture"
            try:
                # Only if body hasn't been consumed or we can peek (Starlette Request streams)
                # But typically we can't re-read stream if consumed. 
                # This middleware is early in stack, but chat/embeddings endpoints consume it.
                # Just noting "Check logs for traceback" is often enough with unbuffered output.
                pass 
            except:
                pass

            logger.exception(
                "middleware_unhandled_error",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                headers=safe_headers,
                error=str(e),
                traceback=traceback.format_exc(),
            )
            # Re-raise or return 500 JSON? 
            # Original code raised HTTPException(500), which FastAPI handles.
            # But logging traceback explicitly here guarantees we see it.
            raise HTTPException(500, "Internal server error")


# ── Helper functions ─────────────────────────────────────────────


async def _authenticate(
    request: Request,
) -> tuple[str, Optional[str], Optional[ApiKey], Optional[str]]:
    """
    Returns (user_oid, api_key_id, api_key_object, app_id).
    Route 1: X-Gateway-Secret  →  user_oid from X-User-Oid header.
                                  X-App-Id required, checked against Apps table.
    Route 2: Bearer API key     →  verified from cache / DB.
              2a: API key only  →  bill to API key owner.
              2b: API key + X-User-Oid + X-App-Id
                                →  bill to specified user (delegated billing).
                                   Both user and app must be registered;
                                   supplying only one triggers 401.
    """
    settings = get_settings()

    # Route 1: Web App (Shared Secret)
    gateway_secret = request.headers.get("X-Gateway-Secret")
    if gateway_secret:
        if gateway_secret != settings.GATEWAY_SHARED_SECRET:
            raise HTTPException(401, "Invalid gateway secret")
        user_oid = request.headers.get("X-User-Oid")
        if not user_oid:
            raise HTTPException(401, "Missing X-User-Oid header")
        
        # App ID is REQUIRED for Shared Secret auth
        app_id = request.headers.get("X-App-Id")
        if not app_id:
            raise HTTPException(401, "Missing X-App-Id header (required for web app access)")
        # Validate app exists and is active
        row = await db.fetch_one("SELECT is_active FROM Apps WHERE app_id = $1", app_id)
        if not row:
            raise HTTPException(401, f"Invalid App ID: {app_id}")
        if not row["is_active"]:
            raise HTTPException(403, f"App is disabled: {app_id}")
            
        return user_oid, None, None, app_id


    # Route 2: API Key
    auth_header = request.headers.get("Authorization")
    if auth_header:
        if not auth_header.startswith("Bearer "):
            raise HTTPException(401, "Invalid Authorization header format")

        plaintext_key = auth_header[7:]
        api_key = await verify_and_get_api_key_with_cache(plaintext_key)

        if not api_key:
            raise HTTPException(401, "Invalid API key")

        if api_key.expires_at and api_key.expires_at < datetime.now():
            raise HTTPException(401, "API key expired")

        if api_key.allowed_ips:
            client_host = request.client.host if request.client else "unknown"
            await check_ip_allowlist(api_key, client_host)

        # ── Delegated billing: API key + X-User-Oid + X-App-Id ──
        # When both are present, bill the specified user instead of
        # the API key owner (e.g. Dify model registration scenario).
        #
        # Resolution order (highest priority first):
        #   1. URL query parameters: x_user_oid / x_app_id
        #   2. Request body top-level fields: x_user_oid / x_app_id
        #   3. Message content JSON: {"x_user_oid":"…","x_app_id":"…","message":"…"}
        #   4. HTTP headers: X-User-Oid / X-App-Id

        # Try to extract from request body (safe: no-op for GET or non-JSON)
        body_user_oid: str | None = None
        body_app_id: str | None = None
        msg_user_oid: str | None = None
        msg_app_id: str | None = None
        if request.method == "POST":
            try:
                body = await request.json()
                if isinstance(body, dict):
                    # Priority 2: top-level body fields
                    body_user_oid = body.get("x_user_oid")
                    body_app_id = body.get("x_app_id")

                    # Priority 3: delegation JSON embedded in message content
                    # Dify LLM nodes can only modify the message text, so users
                    # can embed delegation params as a JSON string in a user
                    # message.  Format:
                    #   {"x_user_oid":"…", "x_app_id":"…", "message":"actual text"}
                    # When detected the content is rewritten to the "message"
                    # value so the downstream LLM sees clean text.
                    if not body_user_oid and not body_app_id:
                        messages = body.get("messages")
                        if isinstance(messages, list):
                            msg_user_oid, msg_app_id = _extract_delegation_from_messages(messages)
            except Exception as exc:
                logger.debug(
                    "delegation_body_parse_skipped",
                    reason=str(exc),
                )

        delegated_user = (
            request.query_params.get("x_user_oid")
            or body_user_oid
            or msg_user_oid
            or request.headers.get("X-User-Oid")
        )
        delegated_app = (
            request.query_params.get("x_app_id")
            or body_app_id
            or msg_app_id
            or request.headers.get("X-App-Id")
        )

        if delegated_user or delegated_app:
            # Determine which source supplied the delegation
            _src = (
                "query_param" if request.query_params.get("x_user_oid") or request.query_params.get("x_app_id")
                else "body_top_level" if body_user_oid or body_app_id
                else "message_content" if msg_user_oid or msg_app_id
                else "header"
            )
            logger.info(
                "delegated_billing_resolved",
                source=_src,
                delegated_user=delegated_user,
                delegated_app=delegated_app,
            )
            # Both must be supplied together
            if not delegated_user:
                raise HTTPException(
                    401, "Missing user identifier (X-User-Oid header or x_user_oid query param required when app is specified)"
                )
            if not delegated_app:
                raise HTTPException(
                    401, "Missing app identifier (X-App-Id header or x_app_id query param required when user is specified)"
                )

            # Validate app exists and is active
            app_row = await db.fetch_one(
                "SELECT is_active FROM Apps WHERE app_id = $1", delegated_app
            )
            if not app_row:
                raise HTTPException(401, f"Invalid App ID: {delegated_app}")
            if not app_row["is_active"]:
                raise HTTPException(403, f"App is disabled: {delegated_app}")

            # Return delegated user_oid for billing;
            # API key object is still used for rate limiting / budget / model permissions.
            return delegated_user, str(api_key.id), api_key, delegated_app

        return api_key.user_oid, str(api_key.id), api_key, None

    raise HTTPException(401, "No authentication provided")



async def _validate_user(user_oid: str) -> None:
    """
    Check user exists and payment is valid.
    Automatically sync expiry status if payment_valid_until has passed.
    """
    # Check and sync expiry status
    payment_status = await check_and_sync_user_expiry(user_oid)
    
    if payment_status is None:
        raise HTTPException(401, "User not found")
    
    if payment_status == "banned":
        raise HTTPException(403, "Account banned")
    
    if payment_status == "expired":
        raise HTTPException(403, "Payment expired")


async def _check_rate_limit(api_key: ApiKey) -> None:
    """Redis-based sliding window rate limiter (RPM)."""
    redis = get_redis()
    key = f"ratelimit:{api_key.id}"
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, 60)
    if current > api_key.rate_limit_rpm:
        raise HTTPException(
            429,
            detail={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": f"Rate limit of {api_key.rate_limit_rpm} RPM exceeded",
                }
            },
        )


async def _get_and_check_model(
    model_id: str, api_key: Optional[ApiKey]
) -> ModelConfig:
    """Load model config and check permissions."""
    row = await db.fetch_one(
        "SELECT * FROM Models WHERE id = $1 AND is_active = TRUE", model_id
    )
    if not row:
        raise HTTPException(404, f"Model '{model_id}' not found or inactive")

    model = ModelConfig(**row)

    if api_key and api_key.allowed_models:
        if model_id not in api_key.allowed_models:
            raise HTTPException(
                403,
                detail={
                    "error": {
                        "code": "model_not_allowed",
                        "message": f"API key does not have access to model '{model_id}'",
                    }
                },
            )

    return model


def _try_parse_delegation_json(text: str) -> dict | None:
    """Try to parse a string as delegation JSON. Returns the parsed dict or None.

    Accepts both:
      - Full JSON: ``{"x_user_oid": "u", "x_app_id": "a", "message": "hi"}``
      - Bare key-value pairs (no outer braces):
        ``"x_user_oid": "u", "x_app_id": "a", "message": "hi"``

    The bare format is common when Dify's Jinja2 template engine consumes
    the outer ``{`` / ``}`` as part of its ``{{ }}`` variable syntax.
    """
    stripped = text.strip()

    # Fast path: already looks like a JSON object
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if (
            isinstance(parsed, dict)
            and "x_user_oid" in parsed
            and "x_app_id" in parsed
        ):
            return parsed

    # Fallback: bare key-value pairs without outer braces
    # e.g.  "x_user_oid": "test2", "x_app_id": "dify-prod", "message": "hello"
    if "x_user_oid" in stripped and "x_app_id" in stripped:
        wrapped = "{" + stripped + "}"
        try:
            parsed = json.loads(wrapped)
        except (json.JSONDecodeError, TypeError):
            return None
        if (
            isinstance(parsed, dict)
            and "x_user_oid" in parsed
            and "x_app_id" in parsed
        ):
            logger.debug(
                "delegation_json_auto_wrapped",
                original=stripped[:120],
            )
            return parsed

    return None


def _extract_delegation_from_messages(
    messages: list[dict],
) -> tuple[str | None, str | None]:
    """
    Scan user messages for embedded delegation JSON.

    Dify LLM nodes cannot add custom top-level body fields; the only
    controllable part is the message text.  Users can embed delegation
    parameters inside a user message as a JSON string:

        {"x_user_oid": "user-123", "x_app_id": "my-app", "message": "Hello!"}

    Supports both plain string content and multimodal (list) content:
      - String: ``"content": "{\"x_user_oid\": ...}"``
      - List:   ``"content": [{"type": "text", "text": "{\"x_user_oid\": ...}"}]``

    When found the message's ``content`` is **rewritten** in-place to
    contain only the ``message`` value, so the downstream LLM receives
    clean text.

    Returns (user_oid, app_id) or (None, None) if nothing found.
    """
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")

        # ── Case 1: content is a plain string ──
        if isinstance(content, str):
            parsed = _try_parse_delegation_json(content)
            if parsed:
                user_oid = str(parsed["x_user_oid"])
                app_id = str(parsed["x_app_id"])
                msg["content"] = parsed.get("message", "")
                logger.debug(
                    "delegation_extracted_from_message",
                    user_oid=user_oid,
                    app_id=app_id,
                    source="string_content",
                )
                return user_oid, app_id
            continue

        # ── Case 2: content is a list (multimodal / content-parts) ──
        if isinstance(content, list):
            for i, part in enumerate(content):
                if not isinstance(part, dict):
                    continue
                if part.get("type") != "text":
                    continue
                text = part.get("text", "")
                if not isinstance(text, str):
                    continue
                parsed = _try_parse_delegation_json(text)
                if parsed:
                    user_oid = str(parsed["x_user_oid"])
                    app_id = str(parsed["x_app_id"])
                    # Rewrite just this text part
                    content[i] = {"type": "text", "text": parsed.get("message", "")}
                    logger.debug(
                        "delegation_extracted_from_message",
                        user_oid=user_oid,
                        app_id=app_id,
                        source="list_content",
                    )
                    return user_oid, app_id

    return None, None
