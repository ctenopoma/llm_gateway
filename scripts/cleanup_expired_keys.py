"""
Cron script: deactivate expired API keys and notify users.
Run daily: 0 2 * * * python scripts/cleanup_expired_keys.py
"""

import asyncio
import os

import asyncpg
import httpx


async def cleanup_expired_keys():
    database_url = os.environ.get(
        "DATABASE_URL", "postgresql://gateway:gateway@localhost:5432/llm_gateway"
    )
    conn = await asyncpg.connect(database_url)

    try:
        rows = await conn.fetch(
            """
            UPDATE ApiKeys
            SET is_active = FALSE
            WHERE expires_at < NOW()
              AND is_active = TRUE
            RETURNING id, user_oid, label
            """
        )

        print(f"Deactivated {len(rows)} expired API keys")

        for row in rows:
            print(f"  - key={row['id']}  user={row['user_oid']}  label={row['label']}")

            # Notify user via webhook (if configured)
            user = await conn.fetchrow(
                "SELECT webhook_url FROM Users WHERE oid = $1", row["user_oid"]
            )
            if user and user["webhook_url"]:
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(
                            user["webhook_url"],
                            json={
                                "type": "api_key_expired",
                                "key_id": str(row["id"]),
                                "label": row["label"],
                            },
                        )
                except Exception as e:
                    print(f"  Webhook notification failed: {e}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(cleanup_expired_keys())
