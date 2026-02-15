# 📘 Antigravity LLM Gateway Technical Specification v2.4

**Version:** 2.4.0 (Enhanced with App Registration & Billing)  
**Framework:** Antigravity (Python Async)  
**Status:** DRAFT - Implementation Verification  
**Date:** 2026-02-15

---

## 1. 概要 (Overview)

v2.4 では、v2.3 の堅牢な基盤に加え、**内部アプリケーション連携**と**請求管理**の機能が強化されています。

### 1.1 新機能

- **App Registration**: Web サーバーなどの内部アプリから Gateway を利用するための `Apps` 管理機能。
- **Billing Dashboard**: ユーザーごとの月次コストを可視化する管理画面機能。

---

## 2. データベース設計 (Database Schema)

既存の `Users`, `ApiKeys`, `Models`, `ModelEndpoints`, `AuditLogs` に加え、`Apps` テーブルを追加し、`UsageLogs` を拡張しました。

### 2.1 Apps (NEW)

内部アプリケーション（Web UI, BFFなど）からのアクセスを管理するためのテーブル。

```sql
CREATE TABLE Apps (
    app_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    
    owner_id VARCHAR(36) NOT NULL REFERENCES Users(oid),
        -- アプリの所有者（請求先）
    
    is_active BOOLEAN DEFAULT TRUE,
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_apps_owner_id ON Apps(owner_id);
```

### 2.2 UsageLogs (Updated)

`app_id` カラムを追加し、アプリ経由の利用を追跡可能にしました。

```sql
CREATE TABLE UsageLogs (
    -- ... (既存カラム) ...
    app_id VARCHAR(50), -- REFERENCES Apps(app_id) (Logical reference)
    -- ...
) PARTITION BY RANGE (created_at);
```

---

## 3. アプリケーション認証 (App Authentication)

API キー認証に加え、内部アプリ向けの認証フローをサポートします。

### 3.1 認証ヘッダー

Web アプリケーションなどの信頼されたクライアントは、以下のヘッダーを使用して認証します。

- `X-Gateway-Secret`: Gateway とアプリ間で共有する秘密鍵 (`GATEWAY_SHARED_SECRET`)
- `X-User-Oid`: リクエストを行っているエンドユーザーの ID
- `X-App-Id`: 登録済みのアプリケーション ID

### 3.2 認証ロジック

```python
async def authenticate_request(request: Request):
    # Route 1: Web App (Shared Secret + App ID)
    if gateway_secret := request.headers.get("X-Gateway-Secret"):
        if gateway_secret != settings.GATEWAY_SHARED_SECRET:
            raise HTTPException(401, "Invalid gateway secret")
        
        app_id = request.headers.get("X-App-Id")
        if not app_id:
             raise HTTPException(401, "Missing X-App-Id")
        
        # Verify App exists and is active
        app = await db.fetch_one("SELECT * FROM Apps WHERE app_id = $1", app_id)
        if not app or not app['is_active']:
             raise HTTPException(403, "App invalid or disabled")

        user_oid = request.headers.get("X-User-Oid")
        if not user_oid:
            raise HTTPException(401, "Missing X-User-Oid header")
        
        # Verify User exists
        # ...
        
        return user_oid, None # No API Key ID
```

---

## 4. 管理 API (Admin API) Updates

管理画面向けの API が拡張されています。

### 4.1 Billing API (NEW)

**GET /admin/api/billing**

月次のユーザー別コスト集計を返します。

- **Query Params**: `month` (YYYY-MM, optional)
- **Response**:
  ```json
  {
    "month": "2026-02",
    "total_cost": 150.50,
    "total_requests": 5000,
    "users": [
      {
        "user_oid": "user-123",
        "email": "test@example.com",
        "requests": 120,
        "total_cost": 15.20
      }
      // ...
    ]
  }
  ```

### 4.2 Apps API (NEW)

**GET /admin/api/apps**
- 登録済みアプリの一覧を取得

**POST /admin/api/apps**
- 新規アプリ登録
- Params: `owner_id` (Query)
- Body: `{"app_id": "chat-v1", "name": "Chat App", "description": "..."}`

**DELETE /admin/api/apps/{app_id}**
- アプリ削除

**PATCH /admin/api/apps/{app_id}/toggle**
- アプリの有効/無効切り替え

---

## 5. その他 (Architecture & Performance)

その他の仕様（API キーのハッシュ化、ロードバランシング、予算管理の Redis 予約システムなど）は **v2.3 仕様書** に準拠します。

- **API Key Verification**: SHA-256 + Redis Cache
- **Budget Management**: Redis Reservation + Kill Switch
- **Load Balancing**: Usage/Latency based routing
- **Context Validation**: Pre-request checks

---

## 6. デプロイメント手順

v2.3 からの更新手順:

1. **データベース移行**:
   - `Apps` テーブルの作成
   - `UsageLogs` への `app_id` カラム追加（およびインデックス作成）

2. **環境変数**:
   - `GATEWAY_SHARED_SECRET` が設定されていることを確認

3. **再起動**:
   - Gateway コンテナの再ビルドと再起動
