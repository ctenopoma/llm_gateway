"""
Redis async client singleton.
"""

from __future__ import annotations

import redis.asyncio as aioredis
import structlog
from typing import Optional

from app.config import get_settings

logger = structlog.get_logger(__name__)

_redis: Optional[aioredis.Redis] = None


async def init_redis() -> aioredis.Redis:
    """Create and return the Redis client."""
    global _redis
    settings = get_settings()
    _redis = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=False,
    )
    # Verify connection
    await _redis.ping()
    logger.info("redis_connected", url=settings.REDIS_URL)
    return _redis


async def close_redis() -> None:
    """Close the Redis connection."""
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
        logger.info("redis_closed")


def get_redis() -> aioredis.Redis:
    """Get the current Redis client (must be initialised first)."""
    if _redis is None:
        raise RuntimeError("Redis is not initialised. Call init_redis() first.")
    return _redis
