"""
LiteLLM Router builder with load balancing support.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from litellm import Router

from app import database as db

logger = structlog.get_logger(__name__)


def _resolve_api_key_ref(ref: str | None) -> str:
    """Resolve an api_key_ref to an actual secret (env var lookup)."""
    if not ref:
        return "EMPTY"
    return os.environ.get(ref, "EMPTY")


def build_litellm_params(endpoint: dict) -> dict[str, Any]:
    """Build LiteLLM parameters for a single endpoint."""
    return {
        "model": endpoint["litellm_name"],
        "api_base": endpoint["base_url"],
        "api_key": _resolve_api_key_ref(endpoint.get("api_key_ref")),
    }


async def build_router_with_load_balancing() -> Router:
    """
    Query active, healthy endpoints and build a LiteLLM Router
    with load-balancing weights.
    """
    rows = await db.fetch_all(
        """
        SELECT
            m.id          AS model_id,
            m.litellm_name,
            m.model_family,
            me.id         AS endpoint_id,
            me.base_url,
            me.api_key_ref,
            me.routing_strategy,
            me.routing_priority,
            me.max_concurrent_requests,
            me.health_status,
            me.avg_latency_ms
        FROM Models m
        INNER JOIN ModelEndpoints me ON m.id = me.model_id
        WHERE m.is_active = TRUE
          AND me.is_active = TRUE
          AND me.health_status IN ('healthy', 'degraded', 'unknown')
        ORDER BY m.id, me.routing_priority ASC
        """
    )

    # Group by model_id
    model_groups: dict[str, list[dict]] = {}
    for row in rows:
        mid = row["model_id"]
        model_groups.setdefault(mid, []).append(row)

    model_list: list[dict[str, Any]] = []

    for model_id, endpoints in model_groups.items():
        if len(endpoints) == 1:
            e = endpoints[0]
            model_list.append(
                {
                    "model_name": model_id,
                    "litellm_params": build_litellm_params(e),
                }
            )
        else:
            strategy = endpoints[0]["routing_strategy"]

            if strategy == "latency-based":
                total_inv = sum(1.0 / max(e["avg_latency_ms"], 1) for e in endpoints)
                for e in endpoints:
                    inv = 1.0 / max(e["avg_latency_ms"], 1)
                    model_list.append(
                        {
                            "model_name": model_id,
                            "litellm_params": build_litellm_params(e),
                            "weight": inv / total_inv,
                        }
                    )
            else:
                # round-robin / usage-based / random → equal weight
                for e in endpoints:
                    model_list.append(
                        {
                            "model_name": model_id,
                            "litellm_params": build_litellm_params(e),
                            "weight": 1.0 / len(endpoints),
                        }
                    )

    if not model_list:
        logger.warning("no_active_endpoints_found")
        # Return a router with an empty list — will fail at call time with a clear error
        return Router(model_list=[])

    logger.info("litellm_router_built", model_count=len(model_groups), endpoint_count=len(model_list))

    return Router(
        model_list=model_list,
        routing_strategy="usage-based-routing",
    )
