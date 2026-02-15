"""
Async PostgreSQL connection pool using asyncpg.
"""

from __future__ import annotations

import asyncpg
import structlog
from typing import Any, Optional

from app.config import get_settings

logger = structlog.get_logger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def init_db() -> asyncpg.Pool:
    """Create and return the database connection pool."""
    global _pool
    settings = get_settings()
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=settings.DB_POOL_MIN_SIZE,
        max_size=settings.DB_POOL_MAX_SIZE,
    )
    logger.info("database_pool_created", min_size=settings.DB_POOL_MIN_SIZE, max_size=settings.DB_POOL_MAX_SIZE)
    return _pool


async def close_db() -> None:
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("database_pool_closed")


def get_pool() -> asyncpg.Pool:
    """Get the current connection pool (must be initialised first)."""
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Call init_db() first.")
    return _pool


async def fetch_all(query: str, *args: Any) -> list[dict]:
    """Execute a query and return all rows as dicts."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(row) for row in rows]


async def fetch_one(query: str, *args: Any) -> Optional[dict]:
    """Execute a query and return a single row as dict."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *args)
        return dict(row) if row else None


async def execute(query: str, *args: Any) -> str:
    """Execute a query (INSERT/UPDATE/DELETE) and return status."""
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


async def execute_returning(query: str, *args: Any) -> list[dict]:
    """Execute a query with RETURNING clause."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(row) for row in rows]


class Transaction:
    """Async context manager for database transactions."""

    def __init__(self) -> None:
        self._conn: Optional[asyncpg.Connection] = None
        self._tx: Optional[asyncpg.connection.transaction.Transaction] = None

    async def __aenter__(self) -> asyncpg.Connection:
        pool = get_pool()
        self._conn = await pool.acquire()
        self._tx = self._conn.transaction()
        await self._tx.start()
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._tx is not None:
            if exc_type is not None:
                await self._tx.rollback()
            else:
                await self._tx.commit()
        if self._conn is not None:
            pool = get_pool()
            await pool.release(self._conn)
