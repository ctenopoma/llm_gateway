"""
LLM Gateway — 統合テストスクリプト

Gateway経由で LLM / Embedding / Reranker の各エンドポイントをテストします。
事前にモデル一覧で各モデルが登録済みかを検証してから各テストを実施します。
"""

import json
import sys

import httpx

GATEWAY_URL = "http://localhost:8000"
API_KEY = "sk-gate-xOSi87wHuqEEmSuv_7Ftb9vjPigzLYuf3iqzOG0v5Lo"

# ── モデル名 ─────────────────────────────────────────────────────
LLM_MODEL = "llama3.2-3b-instruct"
EMBEDDING_MODEL = "ruri-v3-310m"
RERANKER_MODEL = "Qwen3-reranker"

HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# テスト結果集計
_results: list[tuple[str, bool, str]] = []   # (name, passed, detail)


def _record(name: str, passed: bool, detail: str = ""):
    _results.append((name, passed, detail))
    mark = "✓ PASS" if passed else "✗ FAIL"
    print(f"  [{mark}] {name}")
    if detail:
        print(f"         {detail}")


# =====================================================================
#  0. Model verification — /v1/models
# =====================================================================


def verify_models() -> dict[str, bool]:
    """
    /v1/models から登録モデルを取得し、必要なモデルが存在するか確認する。
    Returns dict mapping model_id -> found.
    """
    print("=== Model Verification ===")

    resp = httpx.get(f"{GATEWAY_URL}/v1/models", headers=HEADERS, timeout=10)
    if resp.status_code != 200:
        print(f"  ERROR: /v1/models returned {resp.status_code}: {resp.text}")
        return {}

    data = resp.json()
    registered = {m["id"]: m.get("owned_by", "?") for m in data.get("data", [])}

    print(f"  Registered models ({len(registered)}):")
    for mid, provider in registered.items():
        print(f"    - {mid}  (provider: {provider})")
    print()

    expected = {
        "LLM": LLM_MODEL,
        "Embedding": EMBEDDING_MODEL,
        "Reranker": RERANKER_MODEL,
    }
    found: dict[str, bool] = {}
    for label, model_id in expected.items():
        ok = model_id in registered
        found[model_id] = ok
        _record(f"{label} model registered: {model_id}", ok,
                "" if ok else "NOT FOUND — テストをスキップします")

    # 個別モデル取得もテスト
    for model_id in expected.values():
        if model_id in registered:
            r = httpx.get(f"{GATEWAY_URL}/v1/models/{model_id}", headers=HEADERS, timeout=10)
            _record(f"GET /v1/models/{model_id}", r.status_code == 200,
                    f"status={r.status_code}")

    print()
    return found


# =====================================================================
#  1. Chat Completion (LLM)
# =====================================================================


def test_chat_completion():
    """httpx — 通常のchat completionリクエスト"""
    print("=== Chat Completion (non-streaming) ===")
    resp = httpx.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers=HEADERS,
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "user", "content": "Hello! Please reply in one sentence."}
            ],
            "max_tokens": 64,
        },
        timeout=30,
    )
    ok = resp.status_code == 200
    if ok:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data["usage"]
        _record("Chat Completion (non-streaming)", True,
                f"tokens: in={tokens['prompt_tokens']}, out={tokens['completion_tokens']}")
        print(f"  Response: {content[:100]}")
    else:
        _record("Chat Completion (non-streaming)", False, f"status={resp.status_code}: {resp.text[:200]}")
    print()


def test_chat_completion_streaming():
    """httpx — ストリーミングリクエスト"""
    print("=== Chat Completion (streaming) ===")
    collected = []
    status = 0
    with httpx.stream(
        "POST",
        f"{GATEWAY_URL}/v1/chat/completions",
        headers=HEADERS,
        json={
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": "Count from 1 to 5."}],
            "max_tokens": 64,
            "stream": True,
        },
        timeout=30,
    ) as resp:
        status = resp.status_code
        if status == 200:
            for line in resp.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        collected.append(content)

    ok = status == 200 and len(collected) > 0
    _record("Chat Completion (streaming)", ok,
            f"chunks={len(collected)}" if ok else f"status={status}")
    if collected:
        print(f"  Response: {''.join(collected)[:100]}")
    print()


# =====================================================================
#  2. Embeddings
# =====================================================================


def test_embedding():
    """httpx — 埋め込みリクエスト（単一テキスト）"""
    print("=== Embedding (single text) ===")
    resp = httpx.post(
        f"{GATEWAY_URL}/v1/embeddings",
        headers=HEADERS,
        json={
            "model": EMBEDDING_MODEL,
            "input": "機械学習とは何ですか？",
        },
        timeout=30,
    )
    ok = resp.status_code == 200
    if ok:
        data = resp.json()
        emb = data["data"][0]["embedding"]
        _record("Embedding (single)", True, f"dim={len(emb)}")
        print(f"  First 5 values: {emb[:5]}")
        if "usage" in data:
            print(f"  Tokens: {data['usage']}")
    else:
        _record("Embedding (single)", False, f"status={resp.status_code}: {resp.text[:200]}")
    print()


def test_embedding_batch():
    """httpx — 埋め込みリクエスト（バッチ）"""
    print("=== Embedding (batch) ===")
    texts = [
        "深層学習は機械学習の一分野です。",
        "東京タワーは東京のランドマークです。",
        "Pythonはプログラミング言語です。",
    ]
    resp = httpx.post(
        f"{GATEWAY_URL}/v1/embeddings",
        headers=HEADERS,
        json={"model": EMBEDDING_MODEL, "input": texts},
        timeout=30,
    )
    ok = resp.status_code == 200
    if ok:
        data = resp.json()
        count = len(data["data"])
        _record("Embedding (batch)", count == len(texts),
                f"returned {count}/{len(texts)} embeddings")
    else:
        _record("Embedding (batch)", False, f"status={resp.status_code}: {resp.text[:200]}")
    print()


# =====================================================================
#  3. Reranker
# =====================================================================


def test_rerank():
    """httpx — リランキングリクエスト"""
    print("=== Rerank ===")
    query = "機械学習とは何ですか？"
    documents = [
        "深層学習はニューラルネットワークを多層に重ねた機械学習の手法です。",
        "東京タワーは1958年に完成した赤い電波塔です。",
        "教師あり学習は、ラベル付きデータからモデルを訓練する機械学習の手法です。",
        "Pythonは科学計算やデータ分析に広く使われるプログラミング言語です。",
        "強化学習はエージェントが試行錯誤を通じて最適な行動を学ぶ手法です。",
    ]
    resp = httpx.post(
        f"{GATEWAY_URL}/v1/rerank",
        headers=HEADERS,
        json={
            "model": RERANKER_MODEL,
            "query": query,
            "documents": documents,
            "top_n": 3,
        },
        timeout=60,
    )
    ok = resp.status_code == 200
    if ok:
        data = resp.json()
        results = data.get("results", [])
        _record("Rerank", len(results) > 0, f"top_n results={len(results)}")
        for r in results:
            idx = r["index"]
            score = r["relevance_score"]
            doc_preview = documents[idx][:50] + "..."
            print(f"    [{idx}] score={score:.4f}  {doc_preview}")
    else:
        _record("Rerank", False, f"status={resp.status_code}: {resp.text[:200]}")
    print()


# =====================================================================
#  4. OpenAI ライブラリを使ったテスト
# =====================================================================


def _get_openai_client():
    """OpenAIクライアントを取得（未インストール時はNone）"""
    try:
        from openai import OpenAI
        return OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=API_KEY)
    except ImportError:
        print("openai package not installed. Install with: pip install openai")
        return None


def test_openai_chat_completion():
    """OpenAIライブラリ — 通常のchat completion"""
    print("=== OpenAI: Chat Completion (non-streaming) ===")
    client = _get_openai_client()
    if not client:
        return

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": "What is 2+2? Reply in one word."}],
            max_tokens=16,
        )
        _record("OpenAI Chat Completion", True,
                f"response={response.choices[0].message.content}")
    except Exception as e:
        _record("OpenAI Chat Completion", False, str(e)[:200])
    print()


def test_openai_chat_completion_streaming():
    """OpenAIライブラリ — ストリーミング"""
    print("=== OpenAI: Chat Completion (streaming) ===")
    client = _get_openai_client()
    if not client:
        return

    try:
        stream = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": "Count from 1 to 5."}],
            max_tokens=64,
            stream=True,
        )
        chunks = []
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                chunks.append(delta.content)
        _record("OpenAI Chat Streaming", len(chunks) > 0,
                f"chunks={len(chunks)}")
    except Exception as e:
        _record("OpenAI Chat Streaming", False, str(e)[:200])
    print()


def test_openai_embedding():
    """OpenAIライブラリ — 埋め込み"""
    print("=== OpenAI: Embedding ===")
    client = _get_openai_client()
    if not client:
        return

    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input="機械学習とは何ですか？",
        )
        emb = response.data[0].embedding
        _record("OpenAI Embedding", len(emb) > 0,
                f"dim={len(emb)}, tokens={response.usage.prompt_tokens}")
    except Exception as e:
        _record("OpenAI Embedding", False, str(e)[:200])
    print()


def test_openai_system_prompt():
    """OpenAIライブラリ — systemメッセージ付き"""
    print("=== OpenAI: System Prompt ===")
    client = _get_openai_client()
    if not client:
        return

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that always replies in Japanese."},
                {"role": "user", "content": "What is the capital of France?"},
            ],
            max_tokens=64,
        )
        _record("OpenAI System Prompt", True,
                f"response={response.choices[0].message.content[:80]}")
    except Exception as e:
        _record("OpenAI System Prompt", False, str(e)[:200])
    print()


def test_openai_multi_turn():
    """OpenAIライブラリ — マルチターン会話"""
    print("=== OpenAI: Multi-turn Conversation ===")
    client = _get_openai_client()
    if not client:
        return

    try:
        messages = [{"role": "user", "content": "My name is Alice."}]
        resp1 = client.chat.completions.create(
            model=LLM_MODEL, messages=messages, max_tokens=64,
        )
        assistant_msg = resp1.choices[0].message.content
        messages.append({"role": "assistant", "content": assistant_msg})
        messages.append({"role": "user", "content": "What is my name?"})
        resp2 = client.chat.completions.create(
            model=LLM_MODEL, messages=messages, max_tokens=64,
        )
        reply = resp2.choices[0].message.content
        has_alice = "alice" in reply.lower()
        _record("OpenAI Multi-turn", has_alice,
                f"reply={reply[:80]}" + (" (名前を記憶)" if has_alice else " (名前を忘れた?)"))
    except Exception as e:
        _record("OpenAI Multi-turn", False, str(e)[:200])
    print()


def test_openai_temperature():
    """OpenAIライブラリ — temperature パラメータ"""
    print("=== OpenAI: Temperature Parameter ===")
    client = _get_openai_client()
    if not client:
        return

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": "Say exactly: 'Hello World'"}],
            max_tokens=16,
            temperature=0.0,
        )
        _record("OpenAI Temperature", True,
                f"response={response.choices[0].message.content}")
    except Exception as e:
        _record("OpenAI Temperature", False, str(e)[:200])
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
            model=LLM_MODEL,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=16,
        )
        _record("Invalid API Key rejected", False, "request succeeded unexpectedly")
    except AuthenticationError as e:
        _record("Invalid API Key rejected", True, f"AuthenticationError {e.status_code}")
    except Exception as e:
        _record("Invalid API Key rejected", False, f"{type(e).__name__}: {e}")
    print()


# =====================================================================
#  Main
# =====================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  LLM Gateway 統合テスト")
    print("=" * 60)
    print()

    # ── Step 0: モデル検証 ───────────────────────────────────────
    print("▶ Step 0: Model verification via /v1/models")
    print("-" * 40)
    model_found = verify_models()

    llm_ok = model_found.get(LLM_MODEL, False)
    embed_ok = model_found.get(EMBEDDING_MODEL, False)
    rerank_ok = model_found.get(RERANKER_MODEL, False)

    # ── Step 1: LLM ─────────────────────────────────────────────
    print("▶ Step 1: LLM — Chat Completion")
    print("-" * 40)
    if llm_ok:
        test_chat_completion()
        test_chat_completion_streaming()
    else:
        print(f"  SKIP: {LLM_MODEL} が /v1/models に登録されていません\n")

    # ── Step 2: Embedding ────────────────────────────────────────
    print("▶ Step 2: Embedding")
    print("-" * 40)
    if embed_ok:
        test_embedding()
        test_embedding_batch()
    else:
        print(f"  SKIP: {EMBEDDING_MODEL} が /v1/models に登録されていません\n")

    # ── Step 3: Reranker ─────────────────────────────────────────
    print("▶ Step 3: Reranker")
    print("-" * 40)
    if rerank_ok:
        test_rerank()
    else:
        print(f"  SKIP: {RERANKER_MODEL} が /v1/models に登録されていません\n")

    # ── Step 4: OpenAI Library ───────────────────────────────────
    print("▶ Step 4: OpenAI Library — LLM")
    print("-" * 40)
    if llm_ok:
        test_openai_chat_completion()
        test_openai_chat_completion_streaming()
    else:
        print(f"  SKIP: {LLM_MODEL} 未登録\n")

    if embed_ok:
        test_openai_embedding()
    else:
        print(f"  SKIP: {EMBEDDING_MODEL} 未登録\n")

    print("▶ Step 5: OpenAI Library — Advanced")
    print("-" * 40)
    if llm_ok:
        test_openai_system_prompt()
        test_openai_multi_turn()
        test_openai_temperature()
    test_openai_invalid_api_key()

    # ── Summary ──────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  テスト結果サマリー")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    total = len(_results)

    for name, ok, detail in _results:
        mark = "✓" if ok else "✗"
        line = f"  {mark} {name}"
        if not ok and detail:
            line += f"  — {detail}"
        print(line)

    print()
    print(f"  PASSED: {passed}/{total}    FAILED: {failed}/{total}")
    if failed:
        print("  ⚠ 一部テストが失敗しています")
    else:
        print("  ✓ 全テスト合格")
    print("=" * 60)

    sys.exit(1 if failed else 0)
