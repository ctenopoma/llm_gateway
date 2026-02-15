
import asyncio
import os
import json
import httpx
from dotenv import load_dotenv

# Load .env to get GATEWAY_SHARED_SECRET
load_dotenv()

GATEWAY_URL = "http://localhost:8000"
GATEWAY_SECRET = os.getenv("GATEWAY_SHARED_SECRET")
TEST_API_KEY = "sk-gate-xOSi87wHuqEEmSuv_7Ftb9vjPigzLYuf3iqzOG0v5Lo"
TEST_USER_OID = "test1"
TEST_APP_ID = "app1"

# Message history for the test
MESSAGES = [
    {"role": "user", "content": "Hello! Reply with 'Gateway Access Confirmed' if you receive this."}
]

async def send_chat_request(secret, user_oid, description, app_id=None):
    print(f"\n[{description}]")
    print(f"Secret: {secret[:5]}... | User: {user_oid} | App: {app_id}")
    
    headers = {
        "X-Gateway-Secret": secret,
        "X-User-Oid": user_oid,
        "Content-Type": "application/json"
    }
    if app_id:
        headers["X-App-Id"] = app_id
    
    payload = {
        "model": "llama3.2-3b-instruct",
        "messages": MESSAGES,
        "stream": True,
        "max_tokens": 50
    }
    
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("POST", f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload) as response:
                print(f"Status Code: {response.status_code}")
                
                if response.status_code != 200:
                    error_text = await response.read()
                    print(f"Error Response: {error_text.decode()}")
                    return

                print("Response Stream: ", end="", flush=True)
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            chunk = json.loads(line[6:])
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                print(content, end="", flush=True)
                        except json.JSONDecodeError:
                            pass
                print("\n[Stream Finished]")
                
        except httpx.ConnectError:
            print("ERROR: Could not connect to Gateway.")
        except Exception as e:
            print(f"ERROR: {e}")

async def send_apikey_chat_request(api_key, user_oid, app_id, description):
    """Send a chat request using API key + X-User-Oid + X-App-Id (delegated billing).
    
    In this mode, the API key authenticates the request but billing is charged
    to the user specified by X-User-Oid.
    """
    print(f"\n[{description}]")
    print(f"API Key: {api_key[:8]}... | User: {user_oid} | App: {app_id}")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-User-Oid": user_oid,
        "X-App-Id": app_id,
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "llama3.2-3b-instruct",
        "messages": MESSAGES,
        "stream": True,
        "max_tokens": 50
    }
    
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("POST", f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload) as response:
                print(f"Status Code: {response.status_code}")
                
                if response.status_code != 200:
                    error_text = await response.read()
                    print(f"Error Response: {error_text.decode()}")
                    return

                print("Response Stream: ", end="", flush=True)
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            chunk = json.loads(line[6:])
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                print(content, end="", flush=True)
                        except json.JSONDecodeError:
                            pass
                print("\n[Stream Finished]")
                
        except httpx.ConnectError:
            print("ERROR: Could not connect to Gateway.")
        except Exception as e:
            print(f"ERROR: {e}")


async def test_webapp_access():
    print(f"--- Testing Web App Access Security ---")
    
    if not GATEWAY_SECRET:
        print("ERROR: GATEWAY_SHARED_SECRET not found.")
        return

    # 1. Success Case — all three headers required (Shared Secret auth)
    await send_chat_request(GATEWAY_SECRET, TEST_USER_OID, "CASE 1: Valid Secret + User + App ID", app_id=TEST_APP_ID)

    # 2. Invalid Secret Case
    await send_chat_request("wrong-secret-key", TEST_USER_OID, "CASE 2: Invalid Secret", app_id=TEST_APP_ID)

    # 3. Invalid User Case
    await send_chat_request(GATEWAY_SECRET, "non-existent-user", "CASE 3: Valid Secret + Non-existent User", app_id=TEST_APP_ID)

    # 4. Missing App ID (should be 401)
    await send_chat_request(GATEWAY_SECRET, TEST_USER_OID, "CASE 4: Missing X-App-Id (Expected 401)")

    # 5. Delegated Billing — API Key + User + App ID
    #    Uses Bearer API key for auth, but billing is charged to the specified user.
    #    The API key provides rate limiting / budget / model permissions,
    #    while the user_oid determines who gets billed.
    if TEST_API_KEY:
        await send_apikey_chat_request(
            TEST_API_KEY, TEST_USER_OID, TEST_APP_ID,
            "CASE 5: API Key + User + App ID (Delegated Billing — user charged)"
        )
    else:
        print("\n[CASE 5: SKIPPED — TEST_API_KEY not set in .env]")


if __name__ == "__main__":
    asyncio.run(test_webapp_access())
