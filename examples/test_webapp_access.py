
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

MODEL = "llama3.2-3b-instruct"


# ── Shared helpers ───────────────────────────────────────────────


async def _stream_response(response):
    """Read and print a streaming SSE response."""
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


# ── CASE helpers ─────────────────────────────────────────────────


async def send_chat_with_shared_secret(secret, user_oid, description, app_id=None):
    """Route 1: Shared Secret + X-User-Oid + X-App-Id (web app auth)."""
    print(f"\n[{description}]")
    print(f"Secret: {secret[:5]}... | User: {user_oid} | App: {app_id}")
    
    headers = {
        "X-Gateway-Secret": secret,
        "X-User-Oid": user_oid,
        "Content-Type": "application/json"
    }
    if app_id:
        headers["X-App-Id"] = app_id
    
    payload = {"model": MODEL, "messages": MESSAGES, "stream": True, "max_tokens": 50}
    
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("POST", f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload) as response:
                print(f"Status Code: {response.status_code}")
                await _stream_response(response)
        except httpx.ConnectError:
            print("ERROR: Could not connect to Gateway.")
        except Exception as e:
            print(f"ERROR: {e}")


async def send_chat_with_headers(api_key, user_oid, app_id, description):
    """Route 2b: API Key + X-User-Oid + X-App-Id headers (delegated billing via headers)."""
    print(f"\n[{description}]")
    print(f"API Key: {api_key[:12]}... | User: {user_oid} | App: {app_id}")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-User-Oid": user_oid,
        "X-App-Id": app_id,
        "Content-Type": "application/json"
    }
    
    payload = {"model": MODEL, "messages": MESSAGES, "stream": True, "max_tokens": 50}
    
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("POST", f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload) as response:
                print(f"Status Code: {response.status_code}")
                await _stream_response(response)
        except httpx.ConnectError:
            print("ERROR: Could not connect to Gateway.")
        except Exception as e:
            print(f"ERROR: {e}")


async def send_chat_with_query_params(api_key, user_oid, app_id, description):
    """Route 2b: API Key + URL query params (delegated billing via URL)."""
    print(f"\n[{description}]")
    url = f"{GATEWAY_URL}/v1/chat/completions?x_user_oid={user_oid}&x_app_id={app_id}"
    print(f"API Key: {api_key[:12]}... | URL: ...?x_user_oid={user_oid}&x_app_id={app_id}")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {"model": MODEL, "messages": MESSAGES, "stream": True, "max_tokens": 50}
    
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                print(f"Status Code: {response.status_code}")
                await _stream_response(response)
        except httpx.ConnectError:
            print("ERROR: Could not connect to Gateway.")
        except Exception as e:
            print(f"ERROR: {e}")


async def send_chat_with_body_params(api_key, user_oid, app_id, description):
    """Route 2b: API Key + body x_user_oid/x_app_id (delegated billing via body top-level fields)."""
    print(f"\n[{description}]")
    print(f"API Key: {api_key[:12]}... | Body: x_user_oid={user_oid}, x_app_id={app_id}")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": MODEL,
        "messages": MESSAGES,
        "stream": True,
        "max_tokens": 50,
        "x_user_oid": user_oid,
        "x_app_id": app_id,
    }
    
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("POST", f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload) as response:
                print(f"Status Code: {response.status_code}")
                await _stream_response(response)
        except httpx.ConnectError:
            print("ERROR: Could not connect to Gateway.")
        except Exception as e:
            print(f"ERROR: {e}")


async def send_chat_with_message_json(api_key, user_oid, app_id, user_message, description):
    """Route 2b: API Key + delegation JSON embedded in message content (Dify LLM node style).
    
    The user message content is a JSON string:
      {"x_user_oid": "...", "x_app_id": "...", "message": "actual user text"}
    Gateway parses this, extracts delegation params, and rewrites the message
    to clean text before sending to the LLM.
    """
    print(f"\n[{description}]")
    delegation_json = json.dumps({
        "x_user_oid": user_oid,
        "x_app_id": app_id,
        "message": user_message,
    })
    print(f"API Key: {api_key[:12]}... | Message content: {delegation_json[:60]}...")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": delegation_json}],
        "stream": True,
        "max_tokens": 50,
    }
    
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("POST", f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload) as response:
                print(f"Status Code: {response.status_code}")
                await _stream_response(response)
        except httpx.ConnectError:
            print("ERROR: Could not connect to Gateway.")
        except Exception as e:
            print(f"ERROR: {e}")


# ── Main test runner ─────────────────────────────────────────────


async def test_webapp_access():
    print("=" * 60)
    print("  LLM Gateway — Web App Access & Delegated Billing Tests")
    print("=" * 60)

    # ── Section 1: Shared Secret auth ────────────────────────────
    print("\n--- Section 1: Shared Secret Auth (X-Gateway-Secret) ---")
    
    if not GATEWAY_SECRET:
        print("SKIP: GATEWAY_SHARED_SECRET not found in .env")
    else:
        # 1. Success — all three headers
        await send_chat_with_shared_secret(
            GATEWAY_SECRET, TEST_USER_OID,
            "CASE 1: Valid Secret + User + App ID (expect 200)",
            app_id=TEST_APP_ID,
        )
        # 2. Invalid Secret
        await send_chat_with_shared_secret(
            "wrong-secret-key", TEST_USER_OID,
            "CASE 2: Invalid Secret (expect 401)",
            app_id=TEST_APP_ID,
        )
        # 3. Missing App ID
        await send_chat_with_shared_secret(
            GATEWAY_SECRET, TEST_USER_OID,
            "CASE 3: Missing X-App-Id (expect 401)",
        )

    # ── Section 2: Delegated billing via headers ─────────────────
    print("\n--- Section 2: Delegated Billing via Headers ---")
    
    if not TEST_API_KEY:
        print("SKIP: TEST_API_KEY not set")
    else:
        await send_chat_with_headers(
            TEST_API_KEY, TEST_USER_OID, TEST_APP_ID,
            "CASE 4: API Key + X-User-Oid + X-App-Id headers (expect 200)",
        )

    # ── Section 3: Delegated billing via URL query params ────────
    print("\n--- Section 3: Delegated Billing via URL Query Params ---")
    
    if not TEST_API_KEY:
        print("SKIP: TEST_API_KEY not set")
    else:
        await send_chat_with_query_params(
            TEST_API_KEY, TEST_USER_OID, TEST_APP_ID,
            "CASE 5: API Key + URL ?x_user_oid=...&x_app_id=... (expect 200)",
        )

    # ── Section 4: Delegated billing via request body ────────────
    print("\n--- Section 4: Delegated Billing via Body Top-Level Fields ---")
    
    if not TEST_API_KEY:
        print("SKIP: TEST_API_KEY not set")
    else:
        await send_chat_with_body_params(
            TEST_API_KEY, TEST_USER_OID, TEST_APP_ID,
            "CASE 6: API Key + body x_user_oid/x_app_id (expect 200)",
        )

    # ── Section 5: Delegated billing via message content JSON ────
    print("\n--- Section 5: Delegated Billing via Message Content JSON (Dify LLM node) ---")
    
    if not TEST_API_KEY:
        print("SKIP: TEST_API_KEY not set")
    else:
        await send_chat_with_message_json(
            TEST_API_KEY, TEST_USER_OID, TEST_APP_ID,
            "Hello! Reply with 'Gateway Access Confirmed'.",
            "CASE 7: API Key + delegation JSON in message content (expect 200)",
        )

    # ── Section 6: Error cases ───────────────────────────────────
    print("\n--- Section 6: Error Cases ---")
    
    if TEST_API_KEY:
        # Body with only x_user_oid, no x_app_id → 401
        print(f"\n[CASE 8: Body x_user_oid only, no x_app_id (expect 401)]")
        headers = {"Authorization": f"Bearer {TEST_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": MODEL,
            "messages": MESSAGES,
            "max_tokens": 50,
            "x_user_oid": TEST_USER_OID,
        }
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload)
                print(f"Status Code: {resp.status_code}")
                if resp.status_code != 200:
                    print(f"Error: {resp.text}")
            except httpx.ConnectError:
                print("ERROR: Could not connect to Gateway.")

        # Body with non-existent app → 401
        print(f"\n[CASE 9: Body with non-existent app (expect 401)]")
        payload = {
            "model": MODEL,
            "messages": MESSAGES,
            "max_tokens": 50,
            "x_user_oid": TEST_USER_OID,
            "x_app_id": "nonexistent-app-999",
        }
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload)
                print(f"Status Code: {resp.status_code}")
                if resp.status_code != 200:
                    print(f"Error: {resp.text}")
            except httpx.ConnectError:
                print("ERROR: Could not connect to Gateway.")

        # Message content JSON with non-existent app → 401
        print(f"\n[CASE 10: Message JSON with non-existent app (expect 401)]")
        delegation_json = json.dumps({
            "x_user_oid": TEST_USER_OID,
            "x_app_id": "nonexistent-app-999",
            "message": "should fail",
        })
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": delegation_json}],
            "max_tokens": 50,
        }
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload)
                print(f"Status Code: {resp.status_code}")
                if resp.status_code != 200:
                    print(f"Error: {resp.text}")
            except httpx.ConnectError:
                print("ERROR: Could not connect to Gateway.")

    print("\n" + "=" * 60)
    print("  All tests completed.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_webapp_access())
