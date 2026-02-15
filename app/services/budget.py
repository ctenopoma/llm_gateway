"""
Budget reservation system using Redis for race-condition prevention.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

import structlog
from fastapi import HTTPException

from app import database as db
from app.models.schemas import ApiKey, ModelConfig
from app.redis_client import get_redis

logger = structlog.get_logger(__name__)

# Lua script for atomic budget reservation
_RESERVE_LUA = """
local db_usage = tonumber(ARGV[1])
local budget_limit = tonumber(ARGV[2])
local estimated_cost = tonumber(ARGV[3])
local pending_key = KEYS[1]

-- Get current pending amount
local pending = tonumber(redis.call('GET', pending_key) or 0)

-- Check if total would exceed budget
if db_usage + pending + estimated_cost > budget_limit then
    return 0  -- Budget exceeded
end

-- Reserve budget
redis.call('INCRBYFLOAT', pending_key, estimated_cost)
redis.call('EXPIRE', pending_key, 300)  -- 5 minute TTL

return 1  -- Success
"""


class BudgetReservationSystem:
    """Redis-based budget reservation to prevent race conditions."""

    @staticmethod
    async def reserve_budget(
        api_key_id: str,
        estimated_cost: float,
    ) -> bool:
        """
        Reserve budget before processing request.
        Returns True if reservation successful.
        """
        redis = get_redis()

        db_usage_key = f"budget:db:{api_key_id}"
        pending_key = f"budget:pending:{api_key_id}"

        # Get current usage from DB (cached in Redis for 5 seconds)
        raw = await redis.get(db_usage_key)
        if raw is None:
            row = await db.fetch_one(
                "SELECT usage_current_month, budget_monthly FROM ApiKeys WHERE id = $1",
                api_key_id,
            )
            if not row:
                return False
            db_usage = float(row["usage_current_month"])
            budget_limit = float(row["budget_monthly"]) if row["budget_monthly"] is not None else None
            await redis.setex(db_usage_key, 5, str(db_usage))
        else:
            db_usage = float(raw)
            row = await db.fetch_one(
                "SELECT budget_monthly FROM ApiKeys WHERE id = $1",
                api_key_id,
            )
            budget_limit = float(row["budget_monthly"]) if row and row["budget_monthly"] is not None else None

        if budget_limit is None:
            return True  # No budget limit

        result = await redis.eval(
            _RESERVE_LUA,
            1,
            pending_key,
            str(db_usage),
            str(budget_limit),
            str(estimated_cost),
        )
        return bool(result)

    @staticmethod
    async def release_reservation(
        api_key_id: str,
        estimated_cost: float,
        actual_cost: float,
    ) -> None:
        """Release reservation and update DB with actual cost."""
        redis = get_redis()
        pending_key = f"budget:pending:{api_key_id}"

        # Reduce pending amount
        await redis.incrbyfloat(pending_key, -estimated_cost)

        # Update DB atomically
        await db.execute(
            """
            UPDATE ApiKeys
            SET usage_current_month = usage_current_month + $1,
                last_used_at = NOW()
            WHERE id = $2
            """,
            Decimal(str(actual_cost)),
            api_key_id,
        )

        # Invalidate cache
        await redis.delete(f"budget:db:{api_key_id}")


async def reset_monthly_budget(api_key_id: str, current_month: str) -> None:
    """Reset monthly budget counter for a new month."""
    await db.execute(
        """
        UPDATE ApiKeys
        SET usage_current_month = 0,
            last_reset_month = $1
        WHERE id = $2
        """,
        current_month,
        api_key_id,
    )
    logger.info("budget_monthly_reset", api_key_id=api_key_id, month=current_month)


async def check_and_reserve_budget(
    api_key: ApiKey,
    model: ModelConfig,
    max_tokens: Optional[int] = None,
) -> float:
    """
    Check budget and reserve estimated cost.
    Returns estimated_cost for later release.
    """
    # Auto-reset if new month
    current_month = datetime.now().strftime("%Y-%m")
    if api_key.last_reset_month != current_month:
        await reset_monthly_budget(str(api_key.id), current_month)
        api_key.usage_current_month = Decimal("0")

    # Skip if no budget limit
    if api_key.budget_monthly is None:
        return 0.0

    # Estimate cost based on max_tokens
    effective_max_tokens = max_tokens or model.context_window // 2
    estimated_cost = (
        (effective_max_tokens / 1_000_000) * float(model.input_cost)
        + (effective_max_tokens / 1_000_000) * float(model.output_cost)
    )

    success = await BudgetReservationSystem.reserve_budget(
        str(api_key.id),
        estimated_cost,
    )

    if not success:
        raise HTTPException(
            403,
            detail={
                "error": {
                    "code": "budget_exceeded",
                    "message": f"Monthly budget of ¥{api_key.budget_monthly} would be exceeded",
                    "current_usage": float(api_key.usage_current_month),
                    "budget": float(api_key.budget_monthly),
                }
            },
        )

    return estimated_cost
