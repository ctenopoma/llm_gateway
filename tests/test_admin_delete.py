
import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

@pytest.mark.asyncio
async def test_delete_model_authed():
    model_id = "test-model"
    
    with patch("app.routers.admin.db.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch("app.routers.admin.db.execute", new_callable=AsyncMock) as mock_execute, \
         patch("app.routers.admin._verify_token", return_value=True):

        # probe returns a row (model exists)
        mock_fetch_one.return_value = {"id": model_id}
        # simulate successful delete
        mock_execute.return_value = "DELETE 1"

        response = client.delete(
            f"/admin/api/models/{model_id}",
            cookies={"admin_token": "valid-token"}
        )

        assert response.status_code == 200
        assert response.json() == {"status": "deleted", "id": model_id}
        
        mock_execute.assert_called_once()
        args = mock_execute.call_args[0]
        assert "DELETE FROM Models WHERE id = $1" in args[0]
        assert args[1] == model_id

@pytest.mark.asyncio
async def test_delete_model_not_found():
    model_id = "test-model"
    
    with patch("app.routers.admin.db.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch("app.routers.admin.db.execute", new_callable=AsyncMock) as mock_execute, \
         patch("app.routers.admin._verify_token", return_value=True):
     
        # probe returns None
        mock_fetch_one.return_value = None

        response = client.delete(
            f"/admin/api/models/{model_id}",
            cookies={"admin_token": "valid-token"}
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Model not found"

@pytest.mark.asyncio
async def test_delete_model_fk_check():
    model_id = "test-model"
    
    with patch("app.routers.admin.db.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch("app.routers.admin.db.execute", new_callable=AsyncMock) as mock_execute, \
         patch("app.routers.admin._verify_token", return_value=True):
     
        mock_fetch_one.return_value = {"id": model_id}
        # simulate FK error
        mock_execute.side_effect = Exception("violates foreign key constraint")

        response = client.delete(
            f"/admin/api/models/{model_id}",
            cookies={"admin_token": "valid-token"}
        )

        assert response.status_code == 409
        assert "使用されているため削除できません" in response.json()["detail"]

@pytest.mark.asyncio
async def test_delete_endpoint_authed():
    endpoint_id = str(uuid4())
    
    with patch("app.routers.admin.db.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
         patch("app.routers.admin.db.execute", new_callable=AsyncMock) as mock_execute, \
         patch("app.routers.admin._verify_token", return_value=True):

        # probe returns a row
        mock_fetch_one.return_value = {"id": uuid4()}
        # simulate successful delete
        mock_execute.return_value = "DELETE 1"

        response = client.delete(
            f"/admin/api/endpoints/{endpoint_id}",
            cookies={"admin_token": "valid-token"}
        )

        assert response.status_code == 200
        assert response.json() == {"status": "deleted", "id": endpoint_id}
        
        mock_execute.assert_called_once()
        args = mock_execute.call_args[0]
        assert "DELETE FROM ModelEndpoints WHERE id = $1" in args[0]
        assert str(args[1]) == endpoint_id
