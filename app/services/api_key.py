"""
API Key generation, verification, and Redis-cached lookup.

Uses SHA-256 + Salt for high-performance verification (<1ms).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Optional
from uuid import UUID

import structlog

from app import database as db
from app.models.schemas import ApiKey
from app.redis_client import get_redis

logger = structlog.get_logger(__name__)


# ── Key Generation ───────────────────────────────────────────────

def generate_api_key() -> tuple[str, str, str, str]:
    """
    Generate API key with SHA-256 + Salt.

    Returns:
        (plaintext_key, hashed_key, salt, display_prefix)
    """
    random_part = secrets.token_urlsafe(32)  # 43 chars
    plaintext_key = f"sk-gate-{random_part}"

    salt = secrets.token_hex(16)  # 32 chars hex

    hashed_key = hashlib.sha256(
        (plaintext_key + salt).encode("utf-8")
    ).hexdigest()

    display_prefix = plaintext_key[:15] + "..."

    return plaintext_key, hashed_key, salt, display_prefix


# ── Key Verification ─────────────────────────────────────────────

def verify_api_key_fast(plaintext_key: str, hashed_key: str, salt: str) -> bool:
    """
    Verify API key using SHA-256 (< 1ms).
    Uses constant-time comparison to prevent timing attacks.
    """
    computed_hash = hashlib.sha256(
        (plaintext_key + salt).encode("utf-8")
    ).hexdigest()
    return secrets.compare_digest(computed_hash, hashed_key)


# ── Redis-Cached Lookup ─────────────────────────────────────────

async def verify_and_get_api_key_with_cache(
    plaintext_key: str,
) -> Optional[ApiKey]:
    """
    Verify API key with Redis caching.

    Flow:
        1. Check Redis cache (TTL: 60s)
        2. If miss → verify against DB
        3. Cache result if valid
    """
    redis = get_redis()
    cache_key = f"apikey:{plaintext_key}"

    # Try cache first
    cached = await redis.get(cache_key)
    if cached:
        api_key_id = cached.decode("utf-8")
        api_key = await get_api_key_by_id(api_key_id)

        if api_key and api_key.is_active:
            if not api_key.expires_at or api_key.expires_at > datetime.now():
                return api_key

    # Cache miss or invalid — verify against DB
    api_key = await verify_against_db(plaintext_key)

    if api_key:
        await redis.setex(cache_key, 60, str(api_key.id))

    return api_key


async def verify_against_db(plaintext_key: str) -> Optional[ApiKey]:
    """
    Verify API key against database.
    Fetches all active keys and verifies SHA-256 hash.
    """
    rows = await db.fetch_all(
        """
        SELECT * FROM ApiKeys
        WHERE is_active = TRUE
        """
    )

    for row in rows:
        if verify_api_key_fast(plaintext_key, row["hashed_key"], row["salt"]):
            return ApiKey(**row)

    return None


async def get_api_key_by_id(api_key_id: str) -> Optional[ApiKey]:
    """Fetch an API key by its UUID."""
    row = await db.fetch_one(
        "SELECT * FROM ApiKeys WHERE id = $1", UUID(api_key_id)
    )
    return ApiKey(**row) if row else None


async def invalidate_api_key_cache(plaintext_key: str) -> None:
    """Remove an API key from Redis cache."""
    redis = get_redis()
    await redis.delete(f"apikey:{plaintext_key}")


async def check_ip_allowlist(api_key: ApiKey, client_ip: str) -> None:
    """Raise if client IP is not in the allowlist."""
    from fastapi import HTTPException

    if api_key.allowed_ips and client_ip not in api_key.allowed_ips:
        logger.warning(
            "ip_not_allowed",
            api_key_id=str(api_key.id),
            client_ip=client_ip,
        )
        raise HTTPException(403, "IP address not allowed")
