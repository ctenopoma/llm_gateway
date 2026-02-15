
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

@pytest.mark.asyncio
async def test_chat_completion_invalid_model():
    # Mock authentication to succeed
    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock) as mock_auth, \
         patch("app.middleware.gateway.check_ip_allowlist", new_callable=AsyncMock), \
         patch("app.middleware.gateway._validate_user", new_callable=AsyncMock), \
         patch("app.middleware.gateway._check_rate_limit", new_callable=AsyncMock):  # MOCK REDIS
         
        from app.models.schemas import ApiKey
        # Return a valid API Key object
        mock_auth.return_value = ApiKey(
            id="123e4567-e89b-12d3-a456-426614174000",
            user_oid="user-123",
            hashed_key="hashed",
            salt="salt",
            display_prefix="sk-...",
            scopes=["chat.completions"]
        )

        # Mock database to return None for the model lookup in middleware
        with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = None # Model not found

            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "non-existent-model",
                    "messages": [{"role": "user", "content": "hello"}]
                },
                headers={"Authorization": "Bearer valid_key"}
            )
            
            assert response.status_code == 404
            assert response.json()["detail"] == "Model 'non-existent-model' not found or inactive"
            
@pytest.mark.asyncio
async def test_get_model_invalid():
    # Helper to mock auth for this test too
    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock) as mock_auth, \
         patch("app.middleware.gateway.check_ip_allowlist", new_callable=AsyncMock), \
         patch("app.middleware.gateway._validate_user", new_callable=AsyncMock), \
         patch("app.middleware.gateway._check_rate_limit", new_callable=AsyncMock):
         
        from app.models.schemas import ApiKey
        mock_auth.return_value = ApiKey(
            id="123e4567-e89b-12d3-a456-426614174000",
            user_oid="user-123",
            hashed_key="hashed",
            salt="salt",
            display_prefix="sk-...",
            scopes=["chat.completions"]
        )

        # Mock database for get model
        # Target the DB call in app/routers/chat.py
        with patch("app.routers.chat.db.fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = None

            response = client.get(
                "/v1/models/non-existent-model",
                headers={"Authorization": "Bearer valid_key"}
            )
            
            assert response.status_code == 404
            json_response = response.json()
            assert json_response["error"]["code"] == "model_not_found"

@pytest.mark.asyncio
async def test_use_model_not_in_list():
    """
    Test flow:
    1. Get list of models from /v1/models
    2. Try to use a model name that is NOT in that list
    3. Verify we get a 404 error
    """
    from datetime import datetime
    
    # helper to check if model is in list
    def is_model_in_list(model_name, models_list):
        for m in models_list:
            if m["id"] == model_name:
                return True
        return False
        
    # Mock auth success
    with patch("app.middleware.gateway.verify_and_get_api_key_with_cache", new_callable=AsyncMock) as mock_auth, \
         patch("app.middleware.gateway.check_ip_allowlist", new_callable=AsyncMock), \
         patch("app.middleware.gateway._validate_user", new_callable=AsyncMock), \
         patch("app.middleware.gateway._check_rate_limit", new_callable=AsyncMock):

        from app.models.schemas import ApiKey
        mock_auth.return_value = ApiKey(
            id="123e4567-e89b-12d3-a456-426614174000",
            user_oid="user-123",
            hashed_key="hashed",
            salt="salt",
            display_prefix="sk-...",
            scopes=["chat.completions"]
        )

        # 1. Mock /v1/models response
        # We need to mock app.routers.chat.db.fetch_all
        with patch("app.routers.chat.db.fetch_all", new_callable=AsyncMock) as mock_fetch_all_models:
             mock_fetch_all_models.return_value = [
                {"id": "gpt-4", "created_at": datetime(2023, 1, 1), "provider": "openai"},
                {"id": "gpt-3.5-turbo", "created_at": datetime(2023, 1, 1), "provider": "openai"}
             ]
             
             # Call /v1/models
             response_models = client.get(
                 "/v1/models",
                 headers={"Authorization": "Bearer valid_key"}
             )
             assert response_models.status_code == 200
             models_data = response_models.json()["data"]
             
             # 2. Pick a model NOT in the list
             invalid_model_name = "random-model-xyz"
             assert not is_model_in_list(invalid_model_name, models_data)
             
             # 3. Try to use it
             # The gateway middleware checks the DB for existence.
             # We must ensure that THIS check returns None (not found).
             with patch("app.middleware.gateway.db.fetch_one", new_callable=AsyncMock) as mock_fetch_one_gateway:
                 mock_fetch_one_gateway.return_value = None # Model not found in DB
                 
                 response_chat = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": invalid_model_name,
                        "messages": [{"role": "user", "content": "hello"}]
                    },
                    headers={"Authorization": "Bearer valid_key"}
                )
                 
                 # 4. Verify 404
                 assert response_chat.status_code == 404
                 assert response_chat.json()["detail"] == f"Model '{invalid_model_name}' not found or inactive"
