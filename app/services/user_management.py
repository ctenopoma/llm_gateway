"""
User management utilities.

Includes:
- Expiry status synchronization
- User validation
"""

from __future__ import annotations

from datetime import datetime

import structlog

from app import database as db

logger = structlog.get_logger(__name__)


async def check_and_sync_user_expiry(user_oid: str) -> str | None:
    """
    Check if user's payment has expired and update status if needed.
    
    Returns:
        The current payment_status ('active', 'expired', 'banned', 'trial'),
        or None if user not found.
    """
    user = await db.fetch_one(
        """
        SELECT oid, email, payment_status, payment_valid_until 
        FROM Users WHERE oid = $1
        """,
        user_oid,
    )
    
    if not user:
        return None
    
    today = datetime.now().date()
    valid_until = user.get("payment_valid_until")
    current_status = user.get("payment_status")
    
    # Auto-expire user if payment_valid_until has passed
    if (
        valid_until 
        and valid_until < today 
        and current_status not in ("expired", "banned")
    ):
        await db.execute(
            """
            UPDATE Users 
            SET payment_status = 'expired', updated_at = NOW() 
            WHERE oid = $1
            """,
            user_oid,
        )
        logger.info(
            "user_auto_expired",
            user_oid=user_oid,
            email=user.get("email"),
            valid_until=str(valid_until),
        )
        return "expired"
    
    return current_status


async def get_user_with_expiry_check(user_oid: str) -> dict | None:
    """
    Fetch user and automatically sync expiry status.
    
    Returns:
        User record with current payment_status, or None if not found
    """
    # First sync the expiry status
    status = await check_and_sync_user_expiry(user_oid)
    
    if status is None:
        return None
    
    # Then fetch the updated user record
    user = await db.fetch_one(
        "SELECT * FROM Users WHERE oid = $1",
        user_oid,
    )
    return dict(user) if user else None


async def bulk_sync_expired_users() -> dict:
    """
    Bulk-check and update all users whose payment_valid_until has passed.
    Should be run periodically (e.g., in a background task or cron job).
    
    Returns:
        {
            "checked": int (total users checked),
            "expired": int (newly expired users),
        }
    """
    today = datetime.now().date()
    
    # Find users whose payment has expired but status is not already expired/banned
    expired_users = await db.fetch_all(
        """
        SELECT oid, email, payment_valid_until
        FROM Users
        WHERE payment_valid_until < $1
          AND payment_status NOT IN ('expired', 'banned')
        """,
        today,
    )
    
    expired_count = len(expired_users)
    
    if expired_count > 0:
        # Bulk update
        await db.execute(
            """
            UPDATE Users
            SET payment_status = 'expired', updated_at = NOW()
            WHERE payment_valid_until < $1
              AND payment_status NOT IN ('expired', 'banned')
            """,
            today,
        )
        
        logger.info(
            "bulk_expire_users",
            count=expired_count,
            emails=[u.get("email") for u in expired_users],
        )
    
    # Total user count for reference
    total_count = await db.fetch_one("SELECT COUNT(*) AS cnt FROM Users")
    
    return {
        "checked": total_count["cnt"] if total_count else 0,
        "expired": expired_count,
        "timestamp": datetime.now().isoformat(),
    }
