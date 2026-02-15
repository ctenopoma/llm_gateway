from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app import database as db
from app.models.schemas import App, AppCreate
from app.routers.admin import require_admin

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/api/apps", tags=["apps"])


@router.get("", dependencies=[Depends(require_admin)])
async def list_apps(owner_id: Optional[str] = None):
    """List apps, optionally filtered by owner."""
    if owner_id:
        rows = await db.fetch_all(
            "SELECT * FROM Apps WHERE owner_id = $1 ORDER BY created_at DESC", owner_id
        )
    else:
        rows = await db.fetch_all("SELECT * FROM Apps ORDER BY created_at DESC")
    
    # Convert datetime/etc
    from app.routers.admin import _serialise_rows
    return _serialise_rows(rows)


@router.post("", dependencies=[Depends(require_admin)])
async def create_app(body: AppCreate, request: Request, owner_id: str):
    """Register a new app. owner_id is passed as query param for now (admin assigns)."""
    
    # Check if app_id exists
    existing = await db.fetch_one("SELECT app_id FROM Apps WHERE app_id = $1", body.app_id)
    if existing:
        raise HTTPException(409, "App ID already exists")

    # Check owner exists
    user = await db.fetch_one("SELECT oid FROM Users WHERE oid = $1", owner_id)
    if not user:
        raise HTTPException(404, "Owner (User) not found")

    await db.execute(
        """
        INSERT INTO Apps (app_id, name, owner_id, description)
        VALUES ($1, $2, $3, $4)
        """,
        body.app_id,
        body.name,
        owner_id,
        body.description,
    )
    
    logger.info("app_created", app_id=body.app_id, owner_id=owner_id, admin="admin") # TODO: get admin ID
    return {"status": "created", "app_id": body.app_id}


@router.delete("/{app_id}", dependencies=[Depends(require_admin)])
async def delete_app(app_id: str):
    """Delete an app."""
    result = await db.execute("DELETE FROM Apps WHERE app_id = $1", app_id)
    if result == "DELETE 0":
        raise HTTPException(404, "App not found")
    
    logger.info("app_deleted", app_id=app_id)
    return {"status": "deleted"}


@router.patch("/{app_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_app(app_id: str):
    """Toggle app active status."""
    result = await db.execute(
        "UPDATE Apps SET is_active = NOT is_active, updated_at = NOW() WHERE app_id = $1",
        app_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(404, "App not found")
        
    row = await db.fetch_one("SELECT is_active FROM Apps WHERE app_id = $1", app_id)
    row = await db.fetch_one("SELECT is_active FROM Apps WHERE app_id = $1", app_id)
    return {"status": "toggled", "is_active": row["is_active"] if row else None}

