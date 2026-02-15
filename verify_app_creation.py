
import asyncio
from app import database as db

async def main():
    await db.init_db()
    
    app_id = "test-app-001"
    owner_id = "test-user-001" # Ensure this user matches debug_delete.py
    
    # Check if user exists
    user = await db.fetch_one("SELECT oid FROM Users WHERE oid = $1", owner_id)
    if not user:
        # Create user if not exists (for standalone test)
        await db.execute(
            "INSERT INTO Users (oid, email, payment_status, payment_valid_until) VALUES ($1, $2, 'active', NOW() + INTERVAL '1 year')",
            owner_id, "test@example.com"
        )
        print(f"Created user {owner_id}")
    
    print(f"--- Creating App {app_id} ---")
    try:
        await db.execute(
            """
            INSERT INTO Apps (app_id, name, owner_id, description)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (app_id) DO NOTHING
            """,
            app_id, "Test App", owner_id, "Description"
        )
        print("App created successfully (or already existed).")
        
        # Verify
        row = await db.fetch_one("SELECT * FROM Apps WHERE app_id = $1", app_id)
        print(f"Verified App: {row['app_id']} | Owner: {row['owner_id']}")
        
    except Exception as e:
        print(f"FAILED to create app: {e}")

    await db.close_db()

if __name__ == "__main__":
    asyncio.run(main())
