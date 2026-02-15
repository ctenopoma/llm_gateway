# LLM Gateway ハンズオン・チュートリアル

このチュートリアルでは、LLM Gateway のセットアップから、実際の API 利用、そしてアプリケーション連携までをステップバイステップで体験します。

このガイドを完了すると、以下のことができるようになります。
1. Gateway の管理画面でユーザーと API キーを管理する
2. Python スクリプトから Gateway 経由で LLM とチャットする
3. 内部アプリケーション（Web アプリ）と Gateway を安全に連携させる

---

## ステップ 0: 事前準備

まずは環境を整えましょう。

### 1. サーバーの起動

まだ Gateway を起動していない場合は、新しいターミナルを開いて起動してください。

```bash
# Gatewayの起動 (Dockerを使用する場合)
docker-compose up -d

# または、Pythonで直接起動する場合
# uvicorn app.main:app --reload
```

ブラウザで `http://127.0.0.1:8000/health` にアクセスし、`{"status":"ok"}` が返ってくれば準備 OK です。

### 2. クライアント環境のセットアップ

サンプルスクリプトを実行するためのライブラリをインストールします。

```bash
pip install httpx openai python-dotenv
```

---

## ステップ 1: 管理画面を使ってみよう

Gateway には管理者向けのダッシュボードがあります。まずはここにログインして、Gateway の状態を確認しましょう。

1. ブラウザで **[http://127.0.0.1:8000/admin/login](http://127.0.0.1:8000/admin/login)** にアクセスします。
2. パスワードを入力してログインします。
   - デフォルトパスワード: `admin`
   - （変更している場合は `.env` ファイルの `ADMIN_PASSWORD` を確認してください）

**確認ポイント**:
- ダッシュボードが表示されましたか？
- 現在のユーザー数や API キーの数などのグラフが見えるはずです。

---

## ステップ 2: 初めての API リクエスト (`test_gateway.py`)

まずはシンプルに、発行した API キーを使ってチャットボットと会話してみましょう。

### 1. API キーの発行

管理画面で API キーを発行します。

1. 左メニューの **「API Keys」** をクリック。
2. **「+ New API Key」** ボタンをクリック。
3. 必要な情報を入力（User ID は適当な既存ユーザー、なければ User 画面で作ってください）し、**Create** をクリック。
4. **表示された API キー（`sk-gate-...`）をコピー** しておきます。（二度と表示されないので注意！）

### 2. スクリプトの編集

`examples/test_gateway.py` をエディタで開き、11行目の `API_KEY` を先ほどコピーしたキーに書き換えます。

```python
# examples/test_gateway.py

GATEWAY_URL = "http://localhost:8000"
API_KEY = "sk-gate-あなた-の-キー-を-ここ-に-ペースト"  # ← ここを書き換え
```

### 3. 実行

ターミナルでスクリプトを実行します。

```bash
python examples/test_gateway.py
```

**成功すると...**
以下のような応答が返ってきます。Gateway がリクエストを受け取り、背後の LLM (vLLM や Ollama など) に投げて、結果を返しています。

```
=== Chat Completion (non-streaming) ===
Status: 200
Response: Hello! I am an AI assistant...
```

---

## ステップ 3: アプリ連携フローの体験 (`test_app_registration.py`)

単なるボット利用ではなく、「自社の Web サービスに LLM 機能（チャット機能など）を組み込む」シナリオを体験します。
この場合、Web サーバー自体が「アプリ (App)」として Gateway に登録され、特別な権限でアクセスします。

このスクリプトは、以下の手順を**全自動**で行います。
1. 管理者としてログイン
2. アプリのオーナーとなるユーザーを作成
3. 新しい **App (`test-chat-app-v1`)** を登録
4. その App ID を使ってチャットを実行

### 実行

設定変更は不要です（デフォルトの `admin` パスワードで動作します）。

```bash
python examples/test_app_registration.py
```

**実行結果の解説**

```
[1] Logging in as Admin...
Login Status: 200  (管理者権限を取得しました)

[2] Registering App 'test-chat-app-v1'...
Create App Status: 200 (新しいアプリが登録されました)

[3] Chatting with X-App-Id: test-chat-app-v1...
Chat Status: 200
Response: Hello...
```

**何が起きた？**
管理画面を見てみてください。「Apps」メニュー（もしあれば）やデータベースに `test-chat-app-v1` が追加されています。
このフローにより、**「API キーをユーザーに配る」のではなく、「アプリがユーザーの代理として Gateway を叩く」** 構成が実現できます。

---

## ステップ 4: セキュリティチェック (`test_webapp_access.py`)

最後に、その「アプリ連携」が本当に安全かを確認します。
Gateway は、正しい「合言葉（Secret）」と「App ID」を持ったリクエストだけを通すはずです。

### 実行

```bash
python examples/test_webapp_access.py
```

**成功すると...**

```
--- Testing Web App Access Security ---

[CASE 1: Valid Secret + User + App ID]
Status Code: 200  (成功！正しい情報を持っているので通りました)

[CASE 2: Invalid Secret]
Status Code: 401  (拒否！Secretが間違っています)
Error Response: Invalid gateway secret

[CASE 4: Missing X-App-Id (Expected 401)]
Status Code: 401  (拒否！どのアプリからのアクセスか不明です)
```

このテストにより、**「許可されたアプリ以外は Gateway を勝手に使えない」** ことが確認できました。

---

## まとめ

お疲れ様でした！これで LLM Gateway の基本はマスターです。

- **管理画面**: ユーザーやキーの管理、コストの確認
- **API キー利用**: 手軽なツールや個人的な利用 (`test_gateway.py`)
- **App 連携**: Web サービスへの組み込み (`test_app_registration.py`)

次は、実際にあなたのアプリケーションに組み込んでみましょう！
