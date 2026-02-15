"""
Endpoint health check background loop.

Uses next_check_at for efficient scheduling and exponential backoff on failures.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import structlog

from app import database as db
from app.config import get_settings
from app.services.error_sanitizer import sanitize_error_message

logger = structlog.get_logger(__name__)


async def health_check_loop() -> None:
    """
    Background task: poll endpoints that are due for a health check.
    Runs continuously while the application is alive.
    """
    settings = get_settings()

    while True:
        try:
            rows = await db.fetch_all(
                """
                SELECT * FROM ModelEndpoints
                WHERE is_active = TRUE
                  AND next_check_at <= NOW()
                ORDER BY next_check_at ASC
                LIMIT $1
                """,
                settings.HEALTH_CHECK_BATCH_SIZE,
            )

            if not rows:
                await asyncio.sleep(settings.HEALTH_CHECK_POLL_INTERVAL)
                continue

            tasks = [check_endpoint_health(row) for row in rows]
            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error("health_check_loop_error", error=str(e))

        await asyncio.sleep(settings.HEALTH_CHECK_POLL_INTERVAL)


async def check_endpoint_health(endpoint: dict) -> None:
    """Check a single endpoint and update DB status."""
    health_url = endpoint.get("health_check_url") or f"{endpoint['base_url']}/health"
    timeout = endpoint.get("health_check_timeout") or 10

    start = time.time()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(health_url)
            latency_ms = int((time.time() - start) * 1000)

            if response.status_code == 200:
                await db.execute(
                    """
                    UPDATE ModelEndpoints
                    SET health_status = 'healthy',
                        last_health_check = NOW(),
                        next_check_at = NOW() + make_interval(secs => health_check_interval * 60),
                        consecutive_failures = 0,
                        avg_latency_ms = (avg_latency_ms * 0.8 + $1 * 0.2)::INTEGER
                    WHERE id = $2
                    """,
                    latency_ms,
                    endpoint["id"],
                )
                logger.info(
                    "endpoint_health_check_passed",
                    endpoint_id=str(endpoint["id"]),
                    latency_ms=latency_ms,
                )
            else:
                await _mark_degraded(endpoint)

    except Exception as e:
        await _mark_failed(endpoint, str(e))


async def _mark_degraded(endpoint: dict) -> None:
    """Mark endpoint as degraded."""
    await db.execute(
        """
        UPDATE ModelEndpoints
        SET health_status = 'degraded',
            last_health_check = NOW(),
            next_check_at = NOW() + INTERVAL '30 seconds',
            consecutive_failures = consecutive_failures + 1
        WHERE id = $1
        """,
        endpoint["id"],
    )


async def _mark_failed(endpoint: dict, error: str) -> None:
    """Mark endpoint as down (with exponential backoff)."""
    new_failure_count = endpoint.get("consecutive_failures", 0) + 1
    new_status = "down" if new_failure_count >= 3 else "degraded"

    hc_interval = endpoint.get("health_check_interval", 60)
    next_check_delay = min(hc_interval * (2 ** new_failure_count), 300)

    await db.execute(
        """
        UPDATE ModelEndpoints
        SET health_status = $1,
            last_health_check = NOW(),
            next_check_at = NOW() + make_interval(secs => $2),
            consecutive_failures = $3
        WHERE id = $4
        """,
        new_status,
        float(next_check_delay),
        new_failure_count,
        endpoint["id"],
    )

    logger.error(
        "endpoint_health_check_failed",
        endpoint_id=str(endpoint["id"]),
        error=sanitize_error_message(error),
        status=new_status,
    )
