import asyncio
import os
import sys
import httpx
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GATEWAY_URL = "http://127.0.0.1:8000"
ADMIN_PASSWORD = "admin" # Default
TEST_USER_OID = "user-123"
SHARED_SECRET = "change-me-to-a-strong-random-secret"

async def test_app_flow():
    print("--- Testing App Registration Flow ---")
    
    async with httpx.AsyncClient() as client:
        # 1. Login as Admin
        print("\n[1] Logging in as Admin...")
        resp = await client.post(f"{GATEWAY_URL}/admin/api/login", json={"password": ADMIN_PASSWORD})
        print(f"Login Status: {resp.status_code}")
        if resp.status_code != 200:
            print("Login failed")
            return
            
        # 1.5 Check Dashboard (Verify Auth & Routing)
        print("\n[1.5] Checking Dashboard...")
        resp = await client.get(f"{GATEWAY_URL}/admin/api/dashboard")
        print(f"Dashboard Status: {resp.status_code}")
        
        # 1.6 Ensure Test User Exists
        print("\n[1.6] Creating/Updating Test User...")
        # Try to update first, if 404 then create (or just create and ignore error if exists?)
        # Admin api create user: POST /users
        user_payload = {
            "oid": TEST_USER_OID,
            "email": "test@example.com",
            "payment_valid_until": "2030-01-01",
            "payment_status": "active"
        }
        resp = await client.post(f"{GATEWAY_URL}/admin/api/users", json=user_payload)
        print(f"Create User Status: {resp.status_code}")
        if resp.status_code not in (200, 400, 409):
             print(f"User Create/Update warning: {resp.status_code} - {resp.text}")



        # 2. Register App
        app_id = "test-chat-app-v1"
        print(f"\n[2] Registering App '{app_id}'...")
        # First delete if exists (cleanup)
        await client.delete(f"{GATEWAY_URL}/admin/api/apps/{app_id}")
        
        resp = await client.post(
            f"{GATEWAY_URL}/admin/api/apps", 
            params={"owner_id": TEST_USER_OID},
            json={
                "app_id": app_id, 
                "name": "Test Chat App", 
                "description": "Integration test app"
            }
        )
        print(f"Create App Status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Error: {resp.text}")
            return

        # 3. Chat with App ID
        print(f"\n[3] Chatting with X-App-Id: {app_id}...")
        headers = {
            "X-Gateway-Secret": SHARED_SECRET,
            "X-User-Oid": TEST_USER_OID,
            "X-App-Id": app_id
        }
        payload = {
            "model": "llama3.2-3b-instruct",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 10
        }
        
        resp = await client.post(f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload)
        print(f"Chat Status: {resp.status_code}")
        print(f"Response: {resp.json().get('choices', [{}])[0].get('message', {}).get('content')}")
        
        # 4. Chat with Invalid App ID
        print(f"\n[4] Chatting with Invalid X-App-Id...")
        headers["X-App-Id"] = "invalid-app-id"
        resp = await client.post(f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload)
        print(f"Invalid Chat Status: {resp.status_code} (Expected 401)")
        
        # 5. Disable App and Chat
        print(f"\n[5] Disabling App...")
        # Re-login to get cookies again? No client has them.
        # But we need to use the admin API.
        resp = await client.patch(f"{GATEWAY_URL}/admin/api/apps/{app_id}/toggle")
        print(f"Toggle Status: {resp.status_code}")
        
        print(f"[6] Chatting with Disabled App...")
        headers["X-App-Id"] = app_id
        resp = await client.post(f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload)
        print(f"Disabled Chat Status: {resp.status_code} (Expected 403)")


if __name__ == "__main__":
    asyncio.run(test_app_flow())
