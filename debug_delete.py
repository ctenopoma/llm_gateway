
import asyncio
import os
from uuid import UUID

from app import database as db
from app.config import get_settings
from app.services.api_key import generate_api_key

async def main():
    await db.init_db()
    
    print("--- 1. Creating API Key ---")
    plaintext, hashed, salt, prefix = generate_api_key()
    user = await db.fetch_one("SELECT oid FROM Users LIMIT 1")
    if not user:
        print("No users found! Please create a user first.")
        return

    user_oid = user["oid"]
    print(f"User: {user_oid}")

    rows = await db.execute_returning(
        """
        INSERT INTO ApiKeys (
            user_oid, hashed_key, salt, display_prefix,
            rate_limit_rpm, label, created_by
        )
        VALUES ($1, $2, $3, $4, 60, 'debug-test', 'admin')
        RETURNING id
        """,
        user_oid, hashed, salt, prefix
    )
    key_id_uuid = rows[0]["id"]
    key_id_str = str(key_id_uuid)
    print(f"Created Key ID: {key_id_str} (Type: {type(key_id_uuid)})")

    print("\n--- 2. Deactivating API Key ---")
    res = await db.execute(
        "UPDATE ApiKeys SET is_active = FALSE WHERE id = $1",
        key_id_uuid
    )
    print(f"Deactivate result: {res}")

    print("\n--- 3. Deleting API Key ---")
    # Simulate exactly what admin.py does
    target_id = UUID(key_id_str)
    print(f"Target UUID: {target_id}")
    
    res = await db.execute(
        "DELETE FROM ApiKeys WHERE id = $1",
        target_id
    )
    print(f"Delete result: {res}")
    
    if res == "DELETE 0":
        print("!!! TEST FAILED: DELETE 0 returned !!!")
    else:
        print("TEST PASSED: Key deleted successfully")

    await db.close_db()

if __name__ == "__main__":
    asyncio.run(main())
