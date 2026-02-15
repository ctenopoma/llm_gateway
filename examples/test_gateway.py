"""
LLM Gateway — APIキー認証テストスクリプト

Gateway経由でローカルvLLMにchat completionリクエストを送るサンプル。
OpenAI互換APIなので openai ライブラリでそのまま使えます。
"""

import httpx

GATEWAY_URL = "http://localhost:8000"
API_KEY = "sk-gate-PbdaCLiobPxuySPCYaS4Pwh5sMv6n2uZbJcfCUhheAw"


def test_chat_completion():
    """通常のchat completionリクエスト"""
    print("=== Chat Completion (non-streaming) ===")
    resp = httpx.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": "llama3.2-3b-instruct",
            "messages": [
                {"role": "user", "content": "Hello! Please reply in one sentence."}
            ],
            "max_tokens": 64,
        },
        timeout=30,
    )
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"Model: {data['model']}")
        print(f"Response: {data['choices'][0]['message']['content']}")
        print(f"Tokens: input={data['usage']['prompt_tokens']}, "
              f"output={data['usage']['completion_tokens']}")
    else:
        print(f"Error: {resp.text}")
    print()


def test_chat_completion_streaming():
    """ストリーミングリクエスト"""
    print("=== Chat Completion (streaming) ===")
    with httpx.stream(
        "POST",
        f"{GATEWAY_URL}/v1/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": "llama3.2-3b-instruct",
            "messages": [
                {"role": "user", "content": "Count from 1 to 5."}
            ],
            "max_tokens": 64,
            "stream": True,
        },
        timeout=30,
    ) as resp:
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            print("Response: ", end="", flush=True)
            for line in resp.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    import json
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        print(content, end="", flush=True)
            print()
        else:
            print(f"Error: {resp.text}")
    print()


def _get_openai_client():
    """OpenAIクライアントを取得（未インストール時はNone）"""
    try:
        from openai import OpenAI
        return OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=API_KEY)
    except ImportError:
        print("openai package not installed. Install with: pip install openai")
        return None


# ── OpenAI ライブラリを使ったテスト ──────────────────────────────


def test_openai_chat_completion():
    """OpenAIライブラリ — 通常のchat completion"""
    print("=== OpenAI: Chat Completion (non-streaming) ===")
    client = _get_openai_client()
    if not client:
        return

    response = client.chat.completions.create(
        model="llama3.2-3b-instruct",
        messages=[{"role": "user", "content": "What is 2+2? Reply in one word."}],
        max_tokens=16,
    )
    print(f"Model: {response.model}")
    print(f"Response: {response.choices[0].message.content}")
    print(f"Finish reason: {response.choices[0].finish_reason}")
    print(f"Tokens: input={response.usage.prompt_tokens}, "
          f"output={response.usage.completion_tokens}")
    print()


def test_openai_chat_completion_streaming():
    """OpenAIライブラリ — ストリーミング"""
    print("=== OpenAI: Chat Completion (streaming) ===")
    client = _get_openai_client()
    if not client:
        return

    stream = client.chat.completions.create(
        model="llama3.2-3b-instruct",
        messages=[{"role": "user", "content": "Count from 1 to 5."}],
        max_tokens=64,
        stream=True,
    )
    print("Response: ", end="", flush=True)
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            print(delta.content, end="", flush=True)
    print("\n")


def test_openai_system_prompt():
    """OpenAIライブラリ — systemメッセージ付き"""
    print("=== OpenAI: System Prompt ===")
    client = _get_openai_client()
    if not client:
        return

    response = client.chat.completions.create(
        model="llama3.2-3b-instruct",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that always replies in Japanese."},
            {"role": "user", "content": "What is the capital of France?"},
        ],
        max_tokens=64,
    )
    print(f"Response: {response.choices[0].message.content}")
    print()


def test_openai_multi_turn():
    """OpenAIライブラリ — マルチターン会話"""
    print("=== OpenAI: Multi-turn Conversation ===")
    client = _get_openai_client()
    if not client:
        return

    messages = [
        {"role": "user", "content": "My name is Alice."},
    ]

    # 1st turn
    resp1 = client.chat.completions.create(
        model="llama3.2-3b-instruct",
        messages=messages,
        max_tokens=64,
    )
    assistant_msg = resp1.choices[0].message.content
    print(f"Turn 1 - User: My name is Alice.")
    print(f"Turn 1 - Assistant: {assistant_msg}")

    # 2nd turn: ask about previous context
    messages.append({"role": "assistant", "content": assistant_msg})
    messages.append({"role": "user", "content": "What is my name?"})
    resp2 = client.chat.completions.create(
        model="llama3.2-3b-instruct",
        messages=messages,
        max_tokens=64,
    )
    print(f"Turn 2 - User: What is my name?")
    print(f"Turn 2 - Assistant: {resp2.choices[0].message.content}")

    print()


def test_openai_temperature():
    """OpenAIライブラリ — temperature / top_p パラメータ"""
    print("=== OpenAI: Temperature Parameter ===")
    client = _get_openai_client()
    if not client:
        return

    response = client.chat.completions.create(
        model="llama3.2-3b-instruct",
        messages=[{"role": "user", "content": "Say exactly: 'Hello World'"}],
        max_tokens=16,
        temperature=0.0,
    )
    print(f"Response (temp=0.0): {response.choices[0].message.content}")
    print()


def test_openai_invalid_api_key():
    """OpenAIライブラリ — 無効なAPIキーでのエラーハンドリング"""
    print("=== OpenAI: Invalid API Key ===")
    try:
        from openai import OpenAI, AuthenticationError
    except ImportError:
        return

    bad_client = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key="sk-invalid-key")
    try:
        bad_client.chat.completions.create(
            model="llama3.2-3b-instruct",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=16,
        )
        print("ERROR: Expected AuthenticationError but request succeeded!")
    except AuthenticationError as e:
        print(f"OK — Got expected AuthenticationError: {e.status_code}")
    except Exception as e:
        print(f"Got error (type={type(e).__name__}): {e}")
    print()


if __name__ == "__main__":
    print("=" * 60)
    print("  LLM Gateway テスト")
    print("=" * 60)
    print()

    # --- httpx 直接アクセス ---
    print("> httpx direct tests")
    print("-" * 40)
    test_chat_completion()
    test_chat_completion_streaming()

    # --- OpenAI ライブラリ ---
    print("> OpenAI library tests")
    print("-" * 40)
    test_openai_chat_completion()
    test_openai_chat_completion_streaming()

    test_openai_system_prompt()
    test_openai_multi_turn()
    test_openai_temperature()
    test_openai_invalid_api_key()

    print("=" * 60)
    print("  All tests done")
    print("=" * 60)
