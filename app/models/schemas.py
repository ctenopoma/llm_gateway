"""
Pydantic models / schemas for the LLM Gateway.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ── Database row models ──────────────────────────────────────────

class User(BaseModel):
    oid: str
    email: str
    display_name: Optional[str] = None
    payment_status: str = "active"
    payment_valid_until: date
    webhook_url: Optional[str] = None
    total_cost_cache: Decimal = Decimal("0.00")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_sync_at: Optional[datetime] = None


class App(BaseModel):
    app_id: str = Field(..., description="Unique identifier for the app (e.g. chat-app-v1)")
    name: str = Field(..., description="Display name of the app")
    owner_id: str
    is_active: bool = True
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AppCreate(BaseModel):
    app_id: str = Field(..., min_length=3, max_length=50, pattern="^[a-zA-Z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class AppUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None



class ApiKey(BaseModel):
    id: UUID
    user_oid: str
    hashed_key: str
    salt: str
    display_prefix: str
    allowed_models: Optional[list[str]] = None
    scopes: list[str] = Field(default_factory=lambda: ["chat.completions"])
    allowed_ips: Optional[list[str]] = None
    rate_limit_rpm: int = 60
    budget_monthly: Optional[Decimal] = None
    usage_current_month: Decimal = Decimal("0.00")
    last_reset_month: Optional[str] = None
    label: Optional[str] = None
    is_active: bool = True
    created_by: Optional[str] = None
    expires_at: Optional[datetime] = None
    replaced_by: Optional[UUID] = None
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None

    @field_validator("scopes", "allowed_models", "allowed_ips", mode="before")
    @classmethod
    def _parse_json_list(cls, v: Any) -> Any:
        """asyncpg returns JSONB columns as raw strings."""
        if isinstance(v, str):
            return json.loads(v)
        return v


class ModelConfig(BaseModel):
    """Represents a row in the Models table."""
    id: str
    litellm_name: str
    provider: str
    input_cost: Decimal
    output_cost: Decimal
    internal_cost: Decimal = Decimal("0")
    max_retries: int = 2
    fallback_models: list[str] = Field(default_factory=list)
    is_active: bool = True

    @field_validator("fallback_models", mode="before")
    @classmethod
    def _parse_json_list(cls, v: Any) -> Any:
        if isinstance(v, str):
            return json.loads(v)
        return v
    traffic_weight: float = 1.0
    model_family: Optional[str] = None
    context_window: int = 4096
    max_output_tokens: int = 2048
    supports_streaming: bool = True
    supports_functions: bool = False
    supports_vision: bool = False
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ModelEndpoint(BaseModel):
    id: UUID
    model_id: str
    endpoint_type: str
    base_url: str
    api_key_ref: Optional[str] = None
    routing_priority: int = 100
    routing_strategy: str = "round-robin"
    health_check_url: Optional[str] = None
    health_check_interval: int = 60
    health_check_timeout: int = 10
    next_check_at: Optional[datetime] = None
    timeout_seconds: int = 120
    max_concurrent_requests: int = 10
    model_config_json: Optional[dict[str, Any]] = Field(None, alias="model_config")
    is_active: bool = True
    last_health_check: Optional[datetime] = None
    health_status: str = "unknown"
    consecutive_failures: int = 0
    avg_latency_ms: int = 0
    total_requests: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"populate_by_name": True}


class UsageLog(BaseModel):
    id: Optional[int] = None
    user_oid: str
    api_key_id: Optional[UUID] = None
    app_id: Optional[str] = None  # NEW
    request_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    requested_model: str
    actual_model: str
    endpoint_id: Optional[UUID] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost: Decimal = Decimal("0")
    internal_cost: Decimal = Decimal("0")
    status: str = "pending"
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    latency_ms: Optional[int] = None
    ttft_ms: Optional[int] = None
    request_metadata: Optional[dict[str, Any]] = None


class AuditLog(BaseModel):
    id: Optional[int] = None
    admin_oid: str
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    timestamp: Optional[datetime] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


# ── API request / response models ────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stream: bool = False
    stop: Optional[list[str] | str] = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    type: Optional[str] = None
    details: Optional[dict[str, Any]] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApiKeyCreateResponse(BaseModel):
    id: str
    key: str
    display_prefix: str
    label: Optional[str] = None
    created_at: Optional[datetime] = None


class ApiKeyRotateRequest(BaseModel):
    admin_oid: str
    grace_period_hours: int = 24


class ApiKeyRotateResponse(BaseModel):
    old_key_id: str
    new_key_id: str
    new_key: str
    display_prefix: str
    expires_at: str
    grace_period_hours: int
    warning: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "2.3.0"


class PerformanceMetrics(BaseModel):
    metrics: dict[str, Any]
    database: dict[str, Any]
    redis: dict[str, Any]
