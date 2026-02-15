
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

@pytest.mark.asyncio
async def test_list_models():
    # Mock database interactions
    # NOTE: The endpoint is implemented in app/routers/chat.py
    with patch("app.routers.chat.db.fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
         patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock) as mock_verify_key, \
         patch("app.middleware.gateway._validate_user", new_callable=AsyncMock) as mock_validate_user, \
         patch("app.middleware.gateway.check_ip_allowlist", new_callable=AsyncMock), \
         patch("app.middleware.gateway._check_rate_limit", new_callable=AsyncMock):
        
        # Mock returning two models
        mock_fetch_all.return_value = [
            {"id": "gpt-4", "created_at": datetime(2023, 1, 1), "provider": "openai"},
            {"id": "gpt-3.5-turbo", "created_at": datetime(2023, 1, 1), "provider": "openai"}
        ]

        # Mock successful auth
        from app.models.schemas import ApiKey
        mock_verify_key.return_value = ApiKey(
            id="123e4567-e89b-12d3-a456-426614174000",
            user_oid="user-123",
            hashed_key="hashed",
            salt="salt",
            display_prefix="sk-...",
            scopes=["chat.completions"] # default
        )

        response = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer valid_key"}
        )

        assert response.status_code == 200
        data = response.json()
        
        assert data["object"] == "list"
        assert len(data["data"]) == 2
        
        model_ids = [m["id"] for m in data["data"]]
        assert "gpt-4" in model_ids
        assert "gpt-3.5-turbo" in model_ids
        
        for model in data["data"]:
            assert model["object"] == "model"
            assert model["owned_by"] == "openai"
