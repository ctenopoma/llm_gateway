
import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from app.config import get_settings
import asyncpg

async def main():
    settings = get_settings()
    print(f"Connecting to {settings.DATABASE_URL}...")
    
    conn = await asyncpg.connect(settings.DATABASE_URL)
    try:
        # Check if user exists
        user_oid = "user-123"
        row = await conn.fetchrow("SELECT oid FROM Users WHERE oid = $1", user_oid)
        
        if row:
            print(f"User {user_oid} already exists.")
            # Ensure payment status is active
            await conn.execute(
                "UPDATE Users SET payment_status = 'active', payment_valid_until = '2030-01-01' WHERE oid = $1",
                user_oid
            )
            print(f"Updated user {user_oid} to active status.")
        else:
            print(f"Creating user {user_oid}...")
            await conn.execute(
                """
                INSERT INTO Users (oid, email, display_name, payment_status, payment_valid_until)
                VALUES ($1, $2, $3, 'active', '2030-01-01')
                ON CONFLICT (oid) DO UPDATE 
                SET payment_status = 'active', payment_valid_until = '2030-01-01'
                """,
                user_oid, "webapp-test@example.com", "Web App Test User"
            )

            print(f"User {user_oid} created.")
            
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
