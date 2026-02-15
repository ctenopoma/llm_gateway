"""
Usage logging — create pending log, finalize with actual tokens/cost.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import structlog

from app import database as db

logger = structlog.get_logger(__name__)


async def create_usage_log(
    *,
    user_oid: str,
    api_key_id: Optional[str],
    app_id: Optional[str] = None,  # NEW
    request_id: str,
    ip_address: Optional[str],
    user_agent: Optional[str],
    requested_model: str,
    request_metadata: Optional[dict[str, Any]] = None,
) -> int:
    """Insert a pending usage log and return its id."""
    import json

    rows = await db.execute_returning(
        """
        INSERT INTO UsageLogs (
            user_oid, api_key_id, app_id, request_id, ip_address, user_agent,
            requested_model, actual_model, status, request_metadata
        )
        VALUES ($1, $2, $3, $4, $5::inet, $6, $7, $7, 'pending', $8::jsonb)
        RETURNING id
        """,
        user_oid,
        UUID(api_key_id) if api_key_id else None,
        app_id,
        request_id,
        ip_address,
        user_agent,
        requested_model,
        json.dumps(request_metadata) if request_metadata else None,
    )
    return rows[0]["id"]



async def finalize_usage_log(
    *,
    log_id: int,
    actual_model: Optional[str],
    input_tokens: int,
    output_tokens: int,
    cost: Decimal,
    internal_cost: Decimal = Decimal("0"),
    status: str = "completed",
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    latency_ms: Optional[int] = None,
    ttft_ms: Optional[int] = None,
    endpoint_id: Optional[str] = None,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> None:
    """Update a usage log with final metrics."""
    await db.execute(
        """
        UPDATE UsageLogs
        SET actual_model           = COALESCE($1, actual_model),
            input_tokens           = $2,
            output_tokens          = $3,
            cost                   = $4,
            internal_cost          = $5,
            status                 = $6,
            error_code             = $7,
            error_message          = $8,
            latency_ms             = $9,
            ttft_ms                = $10,
            endpoint_id            = $11,
            cache_creation_tokens  = $12,
            cache_read_tokens      = $13,
            completed_at           = NOW()
        WHERE id = $14  AND created_at = (
            SELECT created_at FROM UsageLogs WHERE id = $14
        )
        """,
        actual_model,
        input_tokens,
        output_tokens,
        cost,
        internal_cost,
        status,
        error_code,
        error_message,
        latency_ms,
        ttft_ms,
        UUID(endpoint_id) if endpoint_id else None,
        cache_creation_tokens,
        cache_read_tokens,
        log_id,
    )


async def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    model_id: str,
) -> Decimal:
    """Calculate cost in JPY based on model pricing (per 1M tokens)."""
    row = await db.fetch_one(
        "SELECT input_cost, output_cost, internal_cost FROM Models WHERE id = $1",
        model_id,
    )
    if not row:
        logger.warning("model_not_found_for_cost", model_id=model_id)
        return Decimal("0")

    input_cost = Decimal(str(row["input_cost"]))
    output_cost = Decimal(str(row["output_cost"]))

    total = (
        Decimal(input_tokens) / Decimal("1000000") * input_cost
        + Decimal(output_tokens) / Decimal("1000000") * output_cost
    )
    return total.quantize(Decimal("0.0001"))


async def log_audit(
    *,
    admin_oid: str,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Insert an audit log entry."""
    import json

    await db.execute(
        """
        INSERT INTO AuditLogs (admin_oid, action, target_type, target_id, metadata, ip_address, user_agent)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::inet, $7)
        """,
        admin_oid,
        action,
        target_type,
        target_id,
        json.dumps(metadata) if metadata else None,
        ip_address,
        user_agent,
    )
