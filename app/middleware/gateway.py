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
from app.models.schemas import ApiKey, ChatCompletionRequest, ModelConfig
from app.redis_client import get_redis
from app.services.api_key import (
    check_ip_allowlist,
    verify_and_get_api_key_with_cache,
)
from app.services.budget import check_and_reserve_budget
from app.services.context_validation import validate_context_length

logger = structlog.get_logger(__name__)

# Paths that bypass the full middleware pipeline
_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
_PUBLIC_PREFIXES = ("/admin",)


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
            logger.error(
                "middleware_unhandled_error",
                request_id=request_id,
                error=str(e),
            )
            raise HTTPException(500, "Internal server error")


# ── Helper functions ─────────────────────────────────────────────


async def _authenticate(
    request: Request,
) -> tuple[str, Optional[str], Optional[ApiKey], Optional[str]]:
    """
    Returns (user_oid, api_key_id, api_key_object, app_id).
    Route 1: X-Gateway-Secret  →  user_oid from X-User-Oid header.
                                  Optional X-App-Id checked against Apps table.
    Route 2: Bearer API key     →  verified from cache / DB.
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

        return api_key.user_oid, str(api_key.id), api_key, None

    raise HTTPException(401, "No authentication provided")



async def _validate_user(user_oid: str) -> None:
    """Check user exists and payment is valid."""
    row = await db.fetch_one(
        "SELECT payment_status, payment_valid_until FROM Users WHERE oid = $1",
        user_oid,
    )
    if not row:
        raise HTTPException(401, "User not found")
    if row["payment_status"] == "banned":
        raise HTTPException(403, "Account banned")
    if row["payment_status"] == "expired":
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
