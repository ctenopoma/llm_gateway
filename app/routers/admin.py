"""
Admin panel API router.

Provides REST endpoints for the management dashboard:
- Authentication (login / logout via JWT cookie)
- Dashboard KPI
- CRUD for Users, ApiKeys, Models, ModelEndpoints
- Read-only access to UsageLogs and AuditLogs
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import jwt
import structlog
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import database as db
from app.config import SYSTEM_ADMIN_OID, get_settings
from app.services.api_key import generate_api_key
from app.services.health_check import check_endpoint_health
from app.services.usage_log import log_audit
from app.services.user_management import bulk_sync_expired_users

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/api", tags=["admin"])


# ── JWT Helpers ──────────────────────────────────────────────────


def _create_token() -> str:
    settings = get_settings()
    payload = {
        "sub": "admin",
        "exp": datetime.utcnow() + timedelta(hours=settings.ADMIN_SESSION_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.ADMIN_JWT_SECRET, algorithm="HS256")


def _verify_token(token: str) -> bool:
    settings = get_settings()
    try:
        jwt.decode(token, settings.ADMIN_JWT_SECRET, algorithms=["HS256"])
        return True
    except jwt.PyJWTError:
        return False


async def require_admin(request: Request) -> None:
    """Dependency: verify admin JWT cookie on every protected endpoint."""
    token = request.cookies.get("admin_token")
    if not token or not _verify_token(token):
        raise HTTPException(401, "Unauthorized")


# ── Auth ─────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    settings = get_settings()
    if body.password != settings.ADMIN_PASSWORD:
        raise HTTPException(401, "Invalid password")

    token = _create_token()
    response.set_cookie(
        key="admin_token",
        value=token,
        httponly=True,
        samesite="strict",
        max_age=settings.ADMIN_SESSION_HOURS * 3600,
        path="/admin",
    )
    return {"status": "ok"}


@router.post("/logout", dependencies=[Depends(require_admin)])
async def logout(response: Response):
    response.delete_cookie("admin_token", path="/admin")
    return {"status": "ok"}


# ── Dashboard ────────────────────────────────────────────────────


@router.get("/dashboard", dependencies=[Depends(require_admin)])
async def dashboard():
    """Return KPI numbers for the dashboard."""
    users = await db.fetch_one("SELECT COUNT(*) AS cnt FROM Users")
    keys = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM ApiKeys WHERE is_active = TRUE"
    )
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_logs = await db.fetch_one(
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(cost), 0) AS total_cost "
        "FROM UsageLogs WHERE created_at >= $1",
        today_start,
    )
    endpoints = await db.fetch_all(
        "SELECT id, model_id, base_url, health_status, avg_latency_ms, "
        "total_requests, is_active FROM ModelEndpoints ORDER BY model_id"
    )
    recent_logs = await db.fetch_all(
        "SELECT request_id, user_oid, requested_model, actual_model, "
        "input_tokens, output_tokens, cost, latency_ms, status, created_at "
        "FROM UsageLogs ORDER BY created_at DESC LIMIT 10"
    )

    return {
        "users_count": users["cnt"] if users else 0,
        "active_api_keys": keys["cnt"] if keys else 0,
        "today_requests": today_logs["cnt"] if today_logs else 0,
        "today_cost": float(today_logs["total_cost"]) if today_logs else 0,
        "endpoints": _serialise_rows(endpoints),
        "recent_logs": _serialise_rows(recent_logs),
    }


# ── Billing ──────────────────────────────────────────────────────


@router.get("/billing", dependencies=[Depends(require_admin)])
async def billing(month: Optional[str] = None):
    """Return per-user monthly cost breakdown.

    Query params:
        month: YYYY-MM (defaults to current month)
    """
    if month:
        year, mon = month.split("-")
        start = datetime(int(year), int(mon), 1)
    else:
        now = datetime.utcnow()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month = start.strftime("%Y-%m")

    # Calculate next month start
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)

    rows = await db.fetch_all(
        """
        SELECT
            agg.user_oid,
            usr.email,
            usr.display_name,
            agg.requests,
            agg.input_tokens,
            agg.output_tokens,
            agg.total_cost
        FROM (
            SELECT
                l.user_oid,
                COUNT(*)             AS requests,
                SUM(l.input_tokens)  AS input_tokens,
                SUM(l.output_tokens) AS output_tokens,
                SUM(l.cost)          AS total_cost
            FROM UsageLogs l
            WHERE l.created_at >= $1 AND l.created_at < $2
              AND l.status = 'completed'
              AND l.user_oid != $3
            GROUP BY l.user_oid
        ) agg
        LEFT JOIN Users usr ON agg.user_oid = usr.oid
        ORDER BY agg.total_cost DESC
        """,
        start,
        end,
        SYSTEM_ADMIN_OID,
    )

    # Row-level email/display_name come from the JOIN
    data = []
    for r in rows:
        data.append({
            "user_oid": r["user_oid"],
            "email": r.get("email"),
            "display_name": r.get("display_name"),
            "requests": r["requests"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "total_cost": float(r["total_cost"]),
        })

    # Grand totals
    total_requests = sum(d["requests"] for d in data)
    total_cost = sum(d["total_cost"] for d in data)

    return {
        "month": month,
        "users": data,
        "total_requests": total_requests,
        "total_cost": total_cost,
    }


# ── Users ────────────────────────────────────────────────────────


class UserCreate(BaseModel):
    oid: str
    email: str
    display_name: Optional[str] = None
    payment_valid_until: date
    payment_status: str = "active"


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    webhook_url: Optional[str] = None
    payment_valid_until: Optional[str] = None


class StatusUpdate(BaseModel):
    payment_status: str


@router.post("/users/sync/bulk-expiry", dependencies=[Depends(require_admin)])
async def bulk_sync_expiry():
    """
    Bulk-check all users and mark expired ones if payment_valid_until has passed.
    Useful for periodic maintenance (cron job or scheduled task).
    """
    result = await bulk_sync_expired_users()
    return result


@router.get("/users", dependencies=[Depends(require_admin)])
async def list_users():
    rows = await db.fetch_all(
        "SELECT * FROM Users WHERE oid != $1 ORDER BY created_at DESC",
        SYSTEM_ADMIN_OID,
    )
    return _serialise_rows(rows)


@router.post("/users", dependencies=[Depends(require_admin)])
async def create_user(body: UserCreate, request: Request):
    # Check for existing user (OID or Email)
    existing = await db.fetch_one(
        "SELECT oid, email FROM Users WHERE oid = $1 OR email = $2",
        body.oid,
        body.email,
    )
    if existing:
        msg = "User with this OID already exists" if existing["oid"] == body.oid else "User with this Email already exists"
        raise HTTPException(409, msg)

    await db.execute(
        """
        INSERT INTO Users (oid, email, display_name, payment_status, payment_valid_until)
        VALUES ($1, $2, $3, $4, $5)
        """,
        body.oid,
        body.email,
        body.display_name,
        body.payment_status,
        body.payment_valid_until,
    )
    await log_audit(
        admin_oid=SYSTEM_ADMIN_OID,
        action="user_created",
        target_type="user",
        target_id=body.oid,
        metadata={
            "email": body.email,
            "display_name": body.display_name,
            "payment_status": body.payment_status,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "created"}


@router.put("/users/{oid}", dependencies=[Depends(require_admin)])
async def update_user(oid: str, body: UserUpdate, request: Request):
    if oid == SYSTEM_ADMIN_OID:
        raise HTTPException(403, "システム管理者ユーザーは変更できません")
    sets: list[str] = []
    args: list[Any] = []
    idx = 1

    if body.display_name is not None:
        sets.append(f"display_name = ${idx}")
        args.append(body.display_name)
        idx += 1
    if body.webhook_url is not None:
        sets.append(f"webhook_url = ${idx}")
        args.append(body.webhook_url)
        idx += 1
    if body.payment_valid_until is not None:
        sets.append(f"payment_valid_until = ${idx}")
        args.append(datetime.strptime(body.payment_valid_until, "%Y-%m-%d").date())
        idx += 1

    if not sets:
        raise HTTPException(400, "No fields to update")

    sets.append(f"updated_at = NOW()")
    args.append(oid)
    query = f"UPDATE Users SET {', '.join(sets)} WHERE oid = ${idx}"
    result = await db.execute(query, *args)
    if result == "UPDATE 0":
        raise HTTPException(404, "User not found")
    await log_audit(
        admin_oid=SYSTEM_ADMIN_OID,
        action="user_updated",
        target_type="user",
        target_id=oid,
        metadata={
            "display_name": body.display_name,
            "webhook_url": body.webhook_url,
            "payment_valid_until": body.payment_valid_until,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "updated"}


@router.patch("/users/{oid}/status", dependencies=[Depends(require_admin)])
async def update_user_status(oid: str, body: StatusUpdate, request: Request):
    if oid == SYSTEM_ADMIN_OID:
        raise HTTPException(403, "システム管理者ユーザーは変更できません")
    valid = {"active", "expired", "banned", "trial"}
    if body.payment_status not in valid:
        raise HTTPException(400, f"Invalid status. Must be one of: {valid}")
    result = await db.execute(
        "UPDATE Users SET payment_status = $1, updated_at = NOW() WHERE oid = $2",
        body.payment_status,
        oid,
    )
    if result == "UPDATE 0":
        raise HTTPException(404, "User not found")
    await log_audit(
        admin_oid=SYSTEM_ADMIN_OID,
        action="user_status_changed",
        target_type="user",
        target_id=oid,
        metadata={"payment_status": body.payment_status},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "updated"}


async def _get_user_related_counts(oid: str) -> dict:
    """Count all data related to a user across tables."""
    api_keys = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM ApiKeys WHERE user_oid = $1", oid
    )
    apps = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM Apps WHERE owner_id = $1", oid
    )
    usage_logs = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM UsageLogs WHERE user_oid = $1", oid
    )
    audit_logs = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM AuditLogs WHERE admin_oid = $1", oid
    )
    return {
        "api_keys": api_keys["cnt"] if api_keys else 0,
        "apps": apps["cnt"] if apps else 0,
        "usage_logs": usage_logs["cnt"] if usage_logs else 0,
        "audit_logs": audit_logs["cnt"] if audit_logs else 0,
    }


@router.get("/users/{oid}/delete-check", dependencies=[Depends(require_admin)])
async def check_user_deletable(oid: str):
    """
    Pre-flight check: return counts of all related data that would be
    affected if this user is deleted.
    """
    user = await db.fetch_one("SELECT oid, email, display_name FROM Users WHERE oid = $1", oid)
    if not user:
        raise HTTPException(404, "User not found")

    counts = await _get_user_related_counts(oid)

    return {
        "user_oid": oid,
        "email": user.get("email"),
        "display_name": user.get("display_name"),
        "related": counts,
        "has_blockers": counts["apps"] > 0 or counts["usage_logs"] > 0 or counts["audit_logs"] > 0,
    }


@router.delete("/users/{oid}", dependencies=[Depends(require_admin)])
async def delete_user(oid: str, request: Request, force: bool = False):
    """
    Delete a user (hard delete).
    - ApiKeys: cascade deleted automatically (FK ON DELETE CASCADE).
    - Apps, UsageLogs, AuditLogs: RESTRICT — require ?force=true to delete.
    """
    if oid == SYSTEM_ADMIN_OID:
        raise HTTPException(403, "システム管理者ユーザーは削除できません")

    user = await db.fetch_one("SELECT oid, email FROM Users WHERE oid = $1", oid)
    if not user:
        raise HTTPException(404, "User not found")

    logger.info("delete_user_request", user_oid=oid, email=user.get("email"), force=force)

    counts = await _get_user_related_counts(oid)
    has_blockers = counts["apps"] > 0 or counts["usage_logs"] > 0 or counts["audit_logs"] > 0

    if has_blockers and not force:
        parts = []
        if counts["api_keys"] > 0:
            parts.append(f"APIキー {counts['api_keys']}件")
        if counts["apps"] > 0:
            parts.append(f"アプリ {counts['apps']}件")
        if counts["usage_logs"] > 0:
            parts.append(f"利用ログ {counts['usage_logs']}件")
        if counts["audit_logs"] > 0:
            parts.append(f"監査ログ {counts['audit_logs']}件")
        detail = "、".join(parts)
        raise HTTPException(
            409,
            f"このユーザーには関連データがあるため削除できません: {detail}。"
            f"強制削除する場合はすべての関連データも削除されます。",
        )

    # force=true: clean up blocking references before deleting
    if has_blockers and force:
        await db.execute("DELETE FROM Apps WHERE owner_id = $1", oid)
        await db.execute("DELETE FROM UsageLogs WHERE user_oid = $1", oid)
        await db.execute("DELETE FROM AuditLogs WHERE admin_oid = $1", oid)
        logger.info(
            "delete_user_force_cleanup",
            user_oid=oid,
            counts=counts,
        )

    # Delete user (cascade deletes API keys)
    try:
        result = await db.execute("DELETE FROM Users WHERE oid = $1", oid)
    except Exception as e:
        error_msg = str(e)
        logger.error("delete_user_failed", user_oid=oid, error=error_msg)
        if "foreign key" in error_msg.lower() or "violates" in error_msg.lower():
            raise HTTPException(
                409,
                f"FK制約違反により削除できません: {error_msg}",
            )
        raise HTTPException(500, f"ユーザー削除中にエラーが発生しました: {error_msg}")

    if result == "DELETE 0":
        raise HTTPException(404, "User not found")

    logger.info("delete_user_success", user_oid=oid, email=user.get("email"), force=force)
    await log_audit(
        admin_oid=SYSTEM_ADMIN_OID,
        action="user_deleted",
        target_type="user",
        target_id=oid,
        metadata={
            "email": user.get("email"),
            "force": force,
            "deleted_counts": counts,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {
        "status": "deleted",
        "user_oid": oid,
        "deleted_counts": {
            "api_keys": counts["api_keys"],
            "apps": counts["apps"] if force else 0,
            "usage_logs": counts["usage_logs"] if force else 0,
            "audit_logs": counts["audit_logs"] if force else 0,
        },
    }


@router.post("/users/{oid}/sync-expiry", dependencies=[Depends(require_admin)])
async def sync_user_expiry_status(oid: str):
    """
    Check if user's payment has expired and update status accordingly.
    Returns the current status after check.
    """
    user = await db.fetch_one(
        "SELECT oid, email, payment_status, payment_valid_until FROM Users WHERE oid = $1",
        oid,
    )
    if not user:
        raise HTTPException(404, "User not found")
    
    today = datetime.now().date()
    valid_until = user.get("payment_valid_until")
    current_status = user.get("payment_status")
    
    # If valid_until has passed and status is not already 'expired' or 'banned', update it
    if valid_until and valid_until < today and current_status not in ("expired", "banned"):
        await db.execute(
            "UPDATE Users SET payment_status = 'expired', updated_at = NOW() WHERE oid = $1",
            oid,
        )
        logger.info("user_status_auto_expired", user_oid=oid, email=user.get("email"))
        new_status = "expired"
    else:
        new_status = current_status
    
    return {
        "user_oid": oid,
        "email": user.get("email"),
        "payment_valid_until": valid_until.isoformat() if valid_until else None,
        "payment_status": new_status,
        "synced": new_status != current_status,
    }


# ── API Keys ─────────────────────────────────────────────────────


class ApiKeyCreate(BaseModel):
    user_oid: str
    label: Optional[str] = None
    allowed_models: Optional[list[str]] = None
    scopes: list[str] = Field(default_factory=lambda: ["chat.completions"])
    allowed_ips: Optional[list[str]] = None
    rate_limit_rpm: int = 60
    budget_monthly: Optional[float] = None


@router.get("/api-keys", dependencies=[Depends(require_admin)])
async def list_api_keys():
    rows = await db.fetch_all(
        """
        SELECT k.*, u.email AS user_email
        FROM ApiKeys k
        LEFT JOIN Users u ON k.user_oid = u.oid
        ORDER BY k.created_at DESC
        """
    )
    return _serialise_rows(rows)


@router.post("/api-keys", dependencies=[Depends(require_admin)])
async def create_api_key_endpoint(body: ApiKeyCreate):
    # Check user exists
    user = await db.fetch_one("SELECT oid FROM Users WHERE oid = $1", body.user_oid)
    if not user:
        raise HTTPException(404, "User not found")

    plaintext, hashed, salt, prefix = generate_api_key()

    rows = await db.execute_returning(
        """
        INSERT INTO ApiKeys (
            user_oid, hashed_key, salt, display_prefix,
            allowed_models, scopes, allowed_ips,
            rate_limit_rpm, budget_monthly, label, created_by
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8, $9, $10, 'admin')
        RETURNING id
        """,
        body.user_oid,
        hashed,
        salt,
        prefix,
        json.dumps(body.allowed_models) if body.allowed_models else None,
        json.dumps(body.scopes),
        json.dumps(body.allowed_ips) if body.allowed_ips else None,
        body.rate_limit_rpm,
        Decimal(str(body.budget_monthly)) if body.budget_monthly is not None else None,
        body.label,
    )

    return {
        "id": str(rows[0]["id"]),
        "key": plaintext,
        "display_prefix": prefix,
        "label": body.label,
    }


@router.patch("/api-keys/{key_id}/deactivate", dependencies=[Depends(require_admin)])
async def deactivate_api_key(key_id: str):
    result = await db.execute(
        "UPDATE ApiKeys SET is_active = FALSE WHERE id = $1",
        UUID(key_id),
    )
    if result == "UPDATE 0":
        raise HTTPException(404, "API key not found")
    return {"status": "deactivated"}


@router.delete("/api-keys/{key_id}", dependencies=[Depends(require_admin)])
async def delete_api_key(key_id: UUID):
    logger.info("delete_api_key_request", key_id=str(key_id))
    
    target_uuid = key_id
    
    # PROBE: Check if it exists
    probe = await db.fetch_one("SELECT * FROM ApiKeys WHERE id = $1", target_uuid)
    if not probe:
        logger.error("delete_api_key_probe_failed", key_id=key_id)
        
        # Deep inspection
        all_keys = await db.fetch_all("SELECT * FROM ApiKeys")
        found_in_memory = False
        for row in all_keys:
            if row["id"] == target_uuid:
                found_in_memory = True
                logger.error("delete_api_key_mismatch", msg="FOUND IN MEMORY BUT NOT SQL", row_id=str(row["id"]), target=str(target_uuid))
                break
        
        if not found_in_memory:
            logger.error("delete_api_key_not_in_memory", msg="Key truly not found in DB snapshot", count=len(all_keys))

        raise HTTPException(404, "API key not found")

    logger.info("delete_api_key_probe_success", found_id=str(probe["id"]))

    result = await db.execute(
        "DELETE FROM ApiKeys WHERE id = $1",
        target_uuid,
    )
    logger.info("delete_api_key_result", result=result)
    
    if result == "DELETE 0":
        # This should theoretically not happen if probe succeeded, unless race condition or transaction weirdness
        raise HTTPException(500, "Failed to delete key although it exists")
        
    return {"status": "deleted"}


# ── Models ───────────────────────────────────────────────────────


class ModelCreate(BaseModel):
    id: str
    litellm_name: str
    provider: str
    input_cost: float
    output_cost: float
    internal_cost: float = 0
    max_retries: int = 2
    fallback_models: list[str] = Field(default_factory=list)
    is_active: bool = True
    traffic_weight: float = 1.0
    model_family: Optional[str] = None
    context_window: int = 4096
    max_output_tokens: int = 2048
    supports_streaming: bool = True
    supports_functions: bool = False
    supports_vision: bool = False
    description: Optional[str] = None


class ModelUpdate(BaseModel):
    litellm_name: Optional[str] = None
    provider: Optional[str] = None
    input_cost: Optional[float] = None
    output_cost: Optional[float] = None
    internal_cost: Optional[float] = None
    max_retries: Optional[int] = None
    fallback_models: Optional[list[str]] = None
    traffic_weight: Optional[float] = None
    model_family: Optional[str] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_streaming: Optional[bool] = None
    supports_functions: Optional[bool] = None
    supports_vision: Optional[bool] = None
    description: Optional[str] = None


@router.get("/models", dependencies=[Depends(require_admin)])
async def list_models():
    rows = await db.fetch_all("SELECT * FROM Models ORDER BY id")
    return _serialise_rows(rows)


@router.post("/models", dependencies=[Depends(require_admin)])
async def create_model(body: ModelCreate):
    await db.execute(
        """
        INSERT INTO Models (
            id, litellm_name, provider, input_cost, output_cost,
            internal_cost, max_retries, fallback_models, is_active,
            traffic_weight, model_family, context_window,
            max_output_tokens, supports_streaming, supports_functions,
            supports_vision, description
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        """,
        body.id,
        body.litellm_name,
        body.provider,
        Decimal(str(body.input_cost)),
        Decimal(str(body.output_cost)),
        Decimal(str(body.internal_cost)),
        body.max_retries,
        json.dumps(body.fallback_models),
        body.is_active,
        body.traffic_weight,
        body.model_family,
        body.context_window,
        body.max_output_tokens,
        body.supports_streaming,
        body.supports_functions,
        body.supports_vision,
        body.description,
    )
    return {"status": "created"}


@router.put("/models/{model_id}", dependencies=[Depends(require_admin)])
async def update_model(model_id: str, body: ModelUpdate):
    sets: list[str] = []
    args: list[Any] = []
    idx = 1

    for field_name, value in body.model_dump(exclude_none=True).items():
        if field_name == "fallback_models":
            sets.append(f"fallback_models = ${idx}::jsonb")
            args.append(json.dumps(value))
        elif field_name in ("input_cost", "output_cost", "internal_cost"):
            sets.append(f"{field_name} = ${idx}")
            args.append(Decimal(str(value)))
        else:
            sets.append(f"{field_name} = ${idx}")
            args.append(value)
        idx += 1

    if not sets:
        raise HTTPException(400, "No fields to update")

    sets.append("updated_at = NOW()")
    args.append(model_id)
    query = f"UPDATE Models SET {', '.join(sets)} WHERE id = ${idx}"
    result = await db.execute(query, *args)
    if result == "UPDATE 0":
        raise HTTPException(404, "Model not found")
    return {"status": "updated"}


@router.patch("/models/{model_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_model(model_id: str):
    result = await db.execute(
        "UPDATE Models SET is_active = NOT is_active, updated_at = NOW() WHERE id = $1",
        model_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(404, "Model not found")
    row = await db.fetch_one("SELECT is_active FROM Models WHERE id = $1", model_id)
    return {"status": "toggled", "is_active": row["is_active"] if row else None}


# ── Model Endpoints ──────────────────────────────────────────────


class EndpointCreate(BaseModel):
    model_id: str
    endpoint_type: str
    base_url: str
    api_key_ref: Optional[str] = None
    routing_priority: int = 100
    routing_strategy: str = "round-robin"
    health_check_url: Optional[str] = None
    health_check_interval: int = 60
    health_check_timeout: int = 10
    timeout_seconds: int = 120
    max_concurrent_requests: int = 10
    model_config_json: Optional[dict] = None


class EndpointUpdate(BaseModel):
    endpoint_type: Optional[str] = None
    base_url: Optional[str] = None
    api_key_ref: Optional[str] = None
    routing_priority: Optional[int] = None
    routing_strategy: Optional[str] = None
    health_check_url: Optional[str] = None
    health_check_interval: Optional[int] = None
    health_check_timeout: Optional[int] = None
    timeout_seconds: Optional[int] = None
    max_concurrent_requests: Optional[int] = None
    model_config_json: Optional[dict] = None


@router.get("/endpoints", dependencies=[Depends(require_admin)])
async def list_endpoints():
    rows = await db.fetch_all(
        """
        SELECT e.*, m.litellm_name AS model_name
        FROM ModelEndpoints e
        LEFT JOIN Models m ON e.model_id = m.id
        ORDER BY e.model_id, e.routing_priority
        """
    )
    return _serialise_rows(rows)


@router.post("/endpoints", dependencies=[Depends(require_admin)])
async def create_endpoint(body: EndpointCreate):
    # Check model exists
    model = await db.fetch_one("SELECT id FROM Models WHERE id = $1", body.model_id)
    if not model:
        raise HTTPException(404, "Model not found")

    rows = await db.execute_returning(
        """
        INSERT INTO ModelEndpoints (
            model_id, endpoint_type, base_url, api_key_ref,
            routing_priority, routing_strategy,
            health_check_url, health_check_interval, health_check_timeout,
            timeout_seconds, max_concurrent_requests, model_config
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
        RETURNING id
        """,
        body.model_id,
        body.endpoint_type,
        body.base_url,
        body.api_key_ref,
        body.routing_priority,
        body.routing_strategy,
        body.health_check_url,
        body.health_check_interval,
        body.health_check_timeout,
        body.timeout_seconds,
        body.max_concurrent_requests,
        json.dumps(body.model_config_json) if body.model_config_json else None,
    )
    return {"status": "created", "id": str(rows[0]["id"])}


@router.put("/endpoints/{endpoint_id}", dependencies=[Depends(require_admin)])
async def update_endpoint(endpoint_id: str, body: EndpointUpdate):
    sets: list[str] = []
    args: list[Any] = []
    idx = 1

    for field_name, value in body.model_dump(exclude_none=True).items():
        db_col = "model_config" if field_name == "model_config_json" else field_name
        if field_name == "model_config_json":
            sets.append(f"{db_col} = ${idx}::jsonb")
            args.append(json.dumps(value))
        else:
            sets.append(f"{db_col} = ${idx}")
            args.append(value)
        idx += 1

    if not sets:
        raise HTTPException(400, "No fields to update")

    sets.append("updated_at = NOW()")
    args.append(UUID(endpoint_id))
    query = f"UPDATE ModelEndpoints SET {', '.join(sets)} WHERE id = ${idx}"
    result = await db.execute(query, *args)
    if result == "UPDATE 0":
        raise HTTPException(404, "Endpoint not found")
    return {"status": "updated"}


@router.patch(
    "/endpoints/{endpoint_id}/toggle", dependencies=[Depends(require_admin)]
)
async def toggle_endpoint(endpoint_id: str):
    result = await db.execute(
        "UPDATE ModelEndpoints SET is_active = NOT is_active, updated_at = NOW() "
        "WHERE id = $1",
        UUID(endpoint_id),
    )
    if result == "UPDATE 0":
        raise HTTPException(404, "Endpoint not found")
    row = await db.fetch_one(
        "SELECT is_active FROM ModelEndpoints WHERE id = $1", UUID(endpoint_id)
    )
    return {"status": "toggled", "is_active": row["is_active"] if row else None}


@router.post(
    "/endpoints/{endpoint_id}/health-check", dependencies=[Depends(require_admin)]
)
async def trigger_endpoint_health_check(endpoint_id: str):
    # Fetch endpoint
    endpoint = await db.fetch_one(
        "SELECT * FROM ModelEndpoints WHERE id = $1", UUID(endpoint_id)
    )
    if not endpoint:
        raise HTTPException(404, "Endpoint not found")

    # Run check
    # check_endpoint_health expects a dict-like object (Record is sufficient)
    await check_endpoint_health(dict(endpoint))

    # Fetch updated
    updated = await db.fetch_one(
        "SELECT e.*, m.litellm_name AS model_name "
        "FROM ModelEndpoints e "
        "LEFT JOIN Models m ON e.model_id = m.id "
        "WHERE e.id = $1",
        UUID(endpoint_id),
    )
    return _serialise_rows([updated])[0]


# ── Usage Logs ───────────────────────────────────────────────────


@router.get("/usage-logs", dependencies=[Depends(require_admin)])
async def list_usage_logs(
    page: int = 1,
    per_page: int = 50,
    user_oid: Optional[str] = None,
    model: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if user_oid:
        conditions.append(f"user_oid = ${idx}")
        args.append(user_oid)
        idx += 1
    if model:
        conditions.append(f"(requested_model = ${idx} OR actual_model = ${idx})")
        args.append(model)
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1
    if date_from:
        conditions.append(f"created_at >= ${idx}")
        args.append(datetime.strptime(date_from, "%Y-%m-%d"))
        idx += 1
    if date_to:
        conditions.append(f"created_at < ${idx}")
        args.append(datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        idx += 1

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * per_page

    count_row = await db.fetch_one(
        f"SELECT COUNT(*) AS cnt FROM UsageLogs {where}", *args
    )
    total = count_row["cnt"] if count_row else 0

    rows = await db.fetch_all(
        f"SELECT * FROM UsageLogs {where} ORDER BY created_at DESC "
        f"LIMIT {per_page} OFFSET {offset}",
        *args,
    )

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "data": _serialise_rows(rows),
    }


# ── Audit Logs ───────────────────────────────────────────────────


@router.get("/audit-logs", dependencies=[Depends(require_admin)])
async def list_audit_logs(
    page: int = 1,
    per_page: int = 50,
    action: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if action:
        conditions.append(f"action = ${idx}")
        args.append(action)
        idx += 1
    if date_from:
        conditions.append(f"timestamp >= ${idx}")
        args.append(datetime.strptime(date_from, "%Y-%m-%d"))
        idx += 1
    if date_to:
        conditions.append(f"timestamp < ${idx}")
        args.append(datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        idx += 1

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * per_page

    count_row = await db.fetch_one(
        f"SELECT COUNT(*) AS cnt FROM AuditLogs {where}", *args
    )
    total = count_row["cnt"] if count_row else 0

    rows = await db.fetch_all(
        f"SELECT * FROM AuditLogs {where} ORDER BY timestamp DESC "
        f"LIMIT {per_page} OFFSET {offset}",
        *args,
    )

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "data": _serialise_rows(rows),
    }


# ── Helpers ──────────────────────────────────────────────────────


def _serialise_rows(rows: list[dict]) -> list[dict]:
    """Convert asyncpg row values to JSON-safe types."""
    result = []
    for row in rows:
        cleaned: dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, (datetime,)):
                if v.tzinfo is None:
                    # AsyncPG returns naive UTC datetimes by default
                    v = v.replace(tzinfo=timezone.utc)
                cleaned[k] = v.isoformat()
            elif isinstance(v, Decimal):
                cleaned[k] = float(v)
            elif isinstance(v, UUID):
                cleaned[k] = str(v)
            elif isinstance(v, bytes):
                cleaned[k] = v.decode("utf-8", errors="replace")
            else:
                cleaned[k] = v
        result.append(cleaned)
    return result
