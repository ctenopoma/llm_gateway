"""
FastAPI application entry point with lifespan management.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app import database as db
from app.database import close_db, init_db
from app.middleware.gateway import GatewayMiddleware
from app.redis_client import close_redis, init_redis
from app.routers import admin, chat, management
from app.services.health_check import health_check_loop
from app.services.load_balancer import build_router_with_load_balancing

logger = structlog.get_logger(__name__)

from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9), "JST")

def timestamper(logger, log_method, event_dict):
    event_dict["timestamp"] = datetime.now(JST).isoformat()
    return event_dict

# Configure structlog
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        timestamper,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)


async def log_cleanup_loop():
    """Periodically delete old usage and audit logs based on retention setting."""
    settings = get_settings()
    interval = 6 * 3600  # run every 6 hours
    while True:
        try:
            await asyncio.sleep(interval)
            if settings.LOG_RETENTION_DAYS <= 0:
                continue
            result_usage = await db.execute(
                "DELETE FROM UsageLogs WHERE created_at < NOW() - INTERVAL '1 day' * $1",
                settings.LOG_RETENTION_DAYS,
            )
            result_audit = await db.execute(
                "DELETE FROM AuditLogs WHERE timestamp < NOW() - INTERVAL '1 day' * $1",
                settings.LOG_RETENTION_DAYS,
            )
            logger.info(
                "log_cleanup_completed",
                retention_days=settings.LOG_RETENTION_DAYS,
                usage_logs=result_usage,
                audit_logs=result_audit,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("log_cleanup_error", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup / shutdown lifecycle."""
    logger.info("gateway_starting", version="2.3.0")

    # Init infrastructure
    await init_db()
    await init_redis()

    # Build LiteLLM router
    try:
        app.state.llm_router = await build_router_with_load_balancing()
    except Exception as e:
        logger.warning("llm_router_init_failed", error=str(e))
        app.state.llm_router = None

    # Start background health check
    health_task = asyncio.create_task(health_check_loop())

    # Start background log cleanup
    cleanup_task = asyncio.create_task(log_cleanup_loop())

    logger.info("gateway_started")

    yield

    # Shutdown
    health_task.cancel()
    cleanup_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    await close_redis()
    await close_db()
    logger.info("gateway_stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="LLM Gateway",
        version="2.3.0",
        description="Enterprise-grade LLM Gateway with authentication, budgeting, and load balancing",
        lifespan=lifespan,
    )

    # Middleware
    app.add_middleware(GatewayMiddleware)

    # Routers
    app.include_router(chat.router)
    app.include_router(management.router)

    from app.routers import apps
    app.include_router(apps.router)

    app.include_router(admin.router)



    # Admin static files
    admin_dir = Path(__file__).parent / "admin"
    
    app.mount(

        "/admin/static",
        StaticFiles(directory=str(admin_dir / "static")),
        name="admin-static",
    )

    # Admin HTML pages
    _templates_dir = admin_dir / "templates"

    @app.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
    async def admin_login_page():
        return (_templates_dir / "login.html").read_text(encoding="utf-8")

    @app.get("/admin/", response_class=HTMLResponse, include_in_schema=False)
    async def admin_index_page(request: Request):
        # Redirect to login if no valid JWT cookie
        from app.routers.admin import _verify_token

        token = request.cookies.get("admin_token")
        if not token or not _verify_token(token):
            from fastapi.responses import RedirectResponse

            return RedirectResponse("/admin/login")
        return (_templates_dir / "index.html").read_text(encoding="utf-8")

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL,
        reload=False,
    )
