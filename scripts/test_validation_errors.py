
import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import database as db
from app.config import get_settings
from app.main import app
from httpx import AsyncClient, ASGITransport

async def main():
    settings = get_settings()
    
    # Init DB (assumes DB is running)
    await db.init_db()

    # Ensure SYSTEM_ADMIN exists for Audit Logs FK
    from app.config import SYSTEM_ADMIN_OID
    await db.execute(
        """
        INSERT INTO Users (oid, email, display_name, payment_status, payment_valid_until)
        VALUES ($1, 'admin@system.local', 'System Admin', 'active', '2099-12-31')
        ON CONFLICT (oid) DO NOTHING
        """,
        SYSTEM_ADMIN_OID,
    )

    # Admin Token (Simulated)
    # in a real test we might need to login, but here we can try to inject cookie or mock
    # For simplicity, let's just use the login endpoint to get a token
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Login
        print("--- Logging in ---")
        res = await ac.post("/admin/api/login", json={"password": settings.ADMIN_PASSWORD})
        if res.status_code != 200:
            print(f"Login failed: {res.status_code} {res.text}")
            return
        
        # Get cookie
        cookies = res.cookies

        # 2. Test User Creation - Validation Error (Missing fields)
        print("\n--- Test 1: User Creation (Validation Error) ---")
        res = await ac.post("/admin/api/users", json={}, cookies=cookies)
        print(f"Status: {res.status_code}")
        if res.status_code == 422:
            print("PASS: Got 422 for empty body")
            print("Response:", res.json())
        else:
            print(f"FAIL: Expected 422, got {res.status_code}")
            print("Response:", res.text)

        # 3. Test User Creation - Validation Error (Invalid Date)
        print("\n--- Test 2: User Creation (Invalid Date) ---")
        # Note: Pydantic might reject "invalid-date" with 422
        payload = {
            "oid": "test-user-validation",
            "email": "test@example.com",
            "payment_valid_until": "invalid-date"
        }
        res = await ac.post("/admin/api/users", json=payload, cookies=cookies)
        print(f"Status: {res.status_code}")
        if res.status_code == 422:
             print("PASS: Got 422 for invalid date")
             print("Response:", res.json())
        else:
             print(f"FAIL: Expected 422, got {res.status_code}")
             print("Response:", res.text)

        # 4. Test User Creation - Success
        print("\n--- Test 3: User Creation (Success) ---")
        import random
        rnd = random.randint(1000,9999)
        valid_user = {
            "oid": f"test-user-{rnd}",
            "email": f"test{rnd}@example.com",
            "payment_valid_until": "2025-12-31"
        }
        res = await ac.post("/admin/api/users", json=valid_user, cookies=cookies)
        print(f"Status: {res.status_code}")
        if res.status_code == 200:
            print("PASS: User created")
        else:
            print(f"FAIL: Expected 200, got {res.status_code}")
            print(res.text)
            # If fail here, duplicate check test might be invalid
            if res.status_code != 409:
                return 

        # 5. Test User Creation - Duplicate (Conflict)
        print("\n--- Test 4: User Creation (Duplicate) ---")
        res = await ac.post("/admin/api/users", json=valid_user, cookies=cookies)
        print(f"Status: {res.status_code}")
        if res.status_code == 409:
            print("PASS: Got 409 for duplicate user")
            print("Response:", res.json())
        else:
            print(f"FAIL: Expected 409, got {res.status_code}")
            print("Response:", res.text)
            
        # Clean up
        if res.status_code == 409 or res.status_code == 200:
             await db.execute("DELETE FROM Users WHERE oid = $1", valid_user["oid"])
             print("Cleaned up user")

if __name__ == "__main__":
    asyncio.run(main())
